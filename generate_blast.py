"""
generate_blast.py
-----------------
Scrapes all configured legal tech news sources, scores articles for
NetDocuments sales relevance, picks the top 10, generates a styled HTML
email, and sends it via the local Outlook client (win32com).

Usage
-----
    python generate_blast.py               # scrape + send
    python generate_blast.py --preview     # scrape + save HTML, don't send

Schedule via Task Scheduler using setup_scheduled_task.bat.

Configuration
-------------
Edit the constants in the CONFIG section below.
"""

import argparse
import csv
import datetime
import json
import logging
import os
import re
import sys
from pathlib import Path

import feedparser
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# CONFIG — edit these before running
# ---------------------------------------------------------------------------

RECIPIENTS        = "patric.thomas@netdocuments.com"   # comma-separated for multiple
EMAIL_SUBJECT     = "⚖️ NetDocuments LegalTech News Blast – {date}"
TOP_N             = 10          # number of regular articles to include
ARTICLES_PER_SITE = 15          # how many to fetch per source before scoring
MAX_AGE_DAYS      = 7           # ignore articles older than this many days
OUTPUT_DIR        = Path(__file__).parent   # where to save the HTML preview file

# ---------------------------------------------------------------------------
# SCORING — keywords and their weights for NetDocuments sales relevance
# Higher score = more relevant to include in the blast
# ---------------------------------------------------------------------------

SCORE_RULES = [
    # Direct NetDocuments signal
    (30, ["netdocuments", "net documents"]),

    # Competitor intel
    (18, ["imanage", "opentext", "worldox", "docuware", "sharepoint dms",
          "legal server", "alfresco"]),

    # Document management core
    (14, ["document management", "dms", "document workflow", "knowledge management",
          "document automation", "matter management", "email management"]),

    # AI in legal (high priority)
    (12, ["legal ai", "ai legal", "generative ai", "gen ai", "genai",
          "large language model", "llm", "ai adoption", "ai tools",
          "ai platform", "claude", "anthropic", "chatgpt", "copilot"]),

    # Law firm operations
    (10, ["law firm efficiency", "law firm technology", "legal tech", "legaltech",
          "legal technology", "legal innovation", "legal ops", "legal operations",
          "billable hour", "roi", "return on investment"]),

    # Cloud & security (NetDocuments differentiators)
    (9,  ["cloud", "security", "cybersecurity", "compliance", "gdpr",
          "data governance", "zero trust", "data privacy", "encryption"]),

    # Big firm / enterprise signals
    (8,  ["biglaw", "amlaw", "law firm", "in-house", "general counsel",
          "chief legal officer", "clo", "legal department", "outside counsel"]),

    # Migration / switching
    (10, ["migration", "migrate", "migrator", "switching", "replace", "legacy"]),

    # General legal business
    (4,  ["legalweek", "legal week", "bar exam", "attorney", "lawyer",
          "contract", "litigation", "e-discovery", "ediscovery"]),
]

# Penalise articles that are clearly off-topic for a sales team
PENALTY_RULES = [
    (-15, ["politics", "trump", "supreme court ruling", "jeanine pirro",
           "morning docket", "see also —", "bar pass", "law school ranking"]),
]

# ---------------------------------------------------------------------------
# SEGMENT SCORING MODIFIERS
# Applied on top of base SCORE_RULES depending on selected prospect segment.
# "Strategic" applies no modifiers — it uses base scores as-is.
# ---------------------------------------------------------------------------

SEGMENT_SCORE_MODIFIERS = {
    # SML — Small & Medium Law Firms
    # Boost: practice management platforms, small-firm pain points, billing & pricing signals
    # Penalise: BigLaw content that's irrelevant to SMB prospects
    "SML": [
        (12, ["practice management", "legal practice management", "lpms", "clio",
              "mycase", "smokeball", "actionstep", "filevine", "cosmolex", "pclaw",
              "rocketmatter", "lawpay", "bill4time"]),
        (10, ["small firm", "solo practice", "boutique law", "small law firm",
              "solo attorney", "small and medium", "mid-size firm", "small practice"]),
        (8,  ["billing", "time tracking", "invoicing", "flat fee", "legal billing",
              "accounts receivable", "trust accounting", "iolta"]),
        (6,  ["pricing", "affordable", "cost-effective", "subscription", "per user",
              "value pricing", "fixed fee"]),
        (4,  ["client intake", "client portal", "online payments", "legal forms",
              "self-service", "automation for small"]),
        (-6, ["biglaw", "amlaw 100", "amlaw 200", "global law firm", "magic circle",
              "vault top", "national law firm"]),
    ],

    # International — Non-US Focused Coverage
    # Boost: UK, EU, APAC, Canada, Middle East, cross-border legal content
    "International": [
        (12, ["united kingdom", "uk law", "england and wales", "scottish law",
              "irish legal", "england", "wales", "scotland"]),
        (10, ["european", "europe", "eu regulation", "eu law", "eu legal",
              "gdpr enforcement", "european commission", "eu directive"]),
        (8,  ["australia", "australian law", "new zealand", "apac", "asia pacific",
              "law society australia"]),
        (8,  ["canada", "canadian law", "ontario bar", "law society ontario",
              "law society of canada"]),
        (6,  ["middle east", "uae", "dubai legal", "singapore", "hong kong legal",
              "india legal", "south africa law"]),
        (6,  ["global law firm", "cross-border", "international legal",
              "multinational", "international arbitration", "foreign jurisdiction"]),
        (5,  ["solicitors regulation", "sra", "legal services board",
              "bar standards board", "law society", "barrister", "solicitor"]),
        (4,  ["civil law jurisdiction", "common law", "international bar",
              "legal profession reform"]),
    ],

    # Strategic — Default scoring, no modifiers
    "Strategic": [],
}

# ---------------------------------------------------------------------------
# SCRAPING
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

log = logging.getLogger("blast")


def _safe_text(tag) -> str:
    if tag is None:
        return ""
    # separator=" " + split() prevents words merging across inline elements
    return " ".join(tag.get_text(separator=" ", strip=True).split())


def _is_within_age_limit(entry) -> bool:
    """Return True if the RSS entry is within MAX_AGE_DAYS, or if its date is unknown."""
    pub = entry.get("published_parsed") or entry.get("updated_parsed")
    if pub is None:
        return True  # no date info — include it
    try:
        pub_dt = datetime.datetime(*pub[:6], tzinfo=datetime.timezone.utc)
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=MAX_AGE_DAYS)
        return pub_dt >= cutoff
    except (TypeError, ValueError):
        return True  # can't parse date — include it


def scrape_rss(config: dict) -> list[dict]:
    is_competitor = config.get("competitor", False)
    try:
        feed = feedparser.parse(config["rss"])
        articles = []
        for entry in feed.entries[:ARTICLES_PER_SITE]:
            # Skip articles older than MAX_AGE_DAYS
            if not _is_within_age_limit(entry):
                continue
            title = (entry.get("title") or "").strip()
            url   = (entry.get("link")  or "").strip()
            desc  = ""
            if entry.get("summary"):
                desc = " ".join(
                    BeautifulSoup(entry.summary, "lxml").get_text(separator=" ", strip=True).split()
                )[:400]
            if title and url:
                articles.append({
                    "source":     config["name"],
                    "title":      title,
                    "url":        url,
                    "desc":       desc,
                    "competitor": is_competitor,
                })
        log.info("  %s (RSS): %d articles (≤%dd)", config["name"], len(articles), MAX_AGE_DAYS)
        return articles
    except Exception as exc:
        log.warning("  %s (RSS) failed: %s", config["name"], exc)
        return []


def scrape_html(config: dict) -> list[dict]:
    is_competitor = config.get("competitor", False)
    try:
        resp = requests.get(config["url"], headers=HEADERS, timeout=10)
        resp.raise_for_status()
    except Exception as exc:
        log.warning("  %s (HTML) failed: %s", config["name"], exc)
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    containers = soup.select(config["article_sel"])
    articles, seen = [], set()

    for c in containers[:ARTICLES_PER_SITE]:
        title_tag = c.select_one(config["title_sel"])
        link_tag  = c.select_one(config["link_sel"])
        # Fallback: <a>-as-container pattern (Harvey.ai, Legora)
        if link_tag is None and c.name == "a":
            link_tag = c
        if not title_tag or not link_tag:
            continue
        title = _safe_text(title_tag)
        href  = link_tag.get("href", "")
        if href and not href.startswith("http"):
            href = config["base_url"] + href
        if not title or not href or href in seen:
            continue
        seen.add(href)
        desc = ""
        if config.get("desc_sel"):
            d_tag = c.select_one(config["desc_sel"])
            if d_tag:
                desc = _safe_text(d_tag)[:400]
        articles.append({
            "source":     config["name"],
            "title":      title,
            "url":        href,
            "desc":       desc,
            "competitor": is_competitor,
        })

    log.info("  %s (HTML): %d articles", config["name"], len(articles))
    return articles


def fetch_all_articles() -> tuple[list[dict], list[dict]]:
    """
    Scrape all regular news sources AND competitor sites.

    Returns:
        (regular_articles, competitor_articles) — two separate lists.
        Regular articles are scored and ranked; competitor articles get their
        own 'Competitor Watch' section in the blast.
    """
    sys.path.insert(0, str(Path(__file__).parent))
    from sites_config import SITES, COMPETITOR_SITES

    regular_articles: list[dict] = []
    competitor_articles: list[dict] = []

    log.info("=== Scraping regular news sources (%d) ===", len(SITES))
    for site in SITES:
        log.info("Fetching %s …", site["name"])
        if site.get("rss"):
            regular_articles.extend(scrape_rss(site))
        else:
            regular_articles.extend(scrape_html(site))

    log.info("=== Scraping competitor sites (%d) ===", len(COMPETITOR_SITES))
    for site in COMPETITOR_SITES:
        log.info("Fetching %s …", site["name"])
        if site.get("rss"):
            competitor_articles.extend(scrape_rss(site))
        else:
            competitor_articles.extend(scrape_html(site))

    log.info(
        "Total: %d regular articles from %d sources | %d competitor articles from %d sources",
        len(regular_articles), len(SITES),
        len(competitor_articles), len(COMPETITOR_SITES),
    )
    return regular_articles, competitor_articles


# ---------------------------------------------------------------------------
# SCORING & CURATION
# ---------------------------------------------------------------------------

def score_article(article: dict, segment: str = "Strategic",
                  investigate_term: str = "") -> int:
    """Score an article for NetDocuments sales relevance, adjusted for segment.
    investigate_term: user-defined phrase scored at 30 pts (same as NetDocuments).
    """
    text = (article["title"] + " " + article.get("desc", "")).lower()
    score = 0
    for weight, keywords in SCORE_RULES:
        if any(kw in text for kw in keywords):
            score += weight
    for penalty, keywords in PENALTY_RULES:
        if any(kw in text for kw in keywords):
            score += penalty   # penalty is negative
    # Apply segment-specific boosts/penalties on top of base score
    for boost, keywords in SEGMENT_SCORE_MODIFIERS.get(segment, []):
        if any(kw in text for kw in keywords):
            score += boost
    # Investigate term: 30-point boost for user-specified phrase
    if investigate_term and investigate_term.strip().lower() in text:
        score += 30
    return score


def curate(articles: list[dict], n: int = TOP_N, segment: str = "Strategic",
           investigate_term: str = "") -> list[dict]:
    """Score and return the top N non-competitor articles, weighted for the given segment.
    investigate_term: phrase that earns a 30-pt bonus — same weight as NetDocuments.
    """
    seen_titles = set()
    unique = []
    for a in articles:
        # Skip competitor articles — they go in their own section
        if a.get("competitor"):
            continue
        key = re.sub(r"\W+", "", a["title"].lower())[:60]
        if key not in seen_titles:
            seen_titles.add(key)
            a["score"] = score_article(a, segment=segment, investigate_term=investigate_term)
            unique.append(a)

    ranked = sorted(unique, key=lambda x: x["score"], reverse=True)
    top = ranked[:n]
    log.info("Top %d articles selected for segment '%s' (scores: %s)",
             n, segment, [a["score"] for a in top])
    return top


# ---------------------------------------------------------------------------
# HTML GENERATION
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{subject}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #f0f4f8; font-family: 'Segoe UI', Arial, sans-serif; color: #1a202c; }}
  a {{ color: #1a56db; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .wrapper {{ max-width: 680px; margin: 32px auto; background: #fff;
              border-radius: 10px; overflow: hidden;
              box-shadow: 0 4px 24px rgba(0,0,0,.10); }}
  .header {{ background: linear-gradient(135deg,#1a56db 0%,#0e3a9e 100%);
             padding: 32px 36px 28px; }}
  .header-logo {{ font-size:11px; font-weight:700; letter-spacing:2px;
                  color:rgba(255,255,255,.7); text-transform:uppercase;
                  margin-bottom:10px; }}
  .header h1 {{ font-size:26px; font-weight:800; color:#fff;
                line-height:1.2; margin-bottom:8px; }}
  .header .date {{ font-size:13px; color:rgba(255,255,255,.75); font-weight:500; }}
  .segment-badge {{ display:inline-block; font-size:10px; font-weight:800;
                    border-radius:4px; padding:2px 8px; letter-spacing:.5px;
                    text-transform:uppercase; margin-top:8px; }}
  .header .tagline {{ font-size:13px; color:rgba(255,255,255,.85); margin-top:10px;
                      border-top:1px solid rgba(255,255,255,.2); padding-top:10px; }}
  .intro {{ padding:24px 36px 16px; border-bottom:1px solid #e8edf2; }}
  .intro p {{ font-size:14px; line-height:1.7; color:#4a5568; }}
  .articles {{ padding:8px 36px 16px; }}
  .article {{ padding:20px 0; border-bottom:1px solid #e8edf2; }}
  .article:last-child {{ border-bottom:none; }}
  .article-num {{ display:inline-block; background:#1a56db; color:#fff;
                  font-size:11px; font-weight:800; border-radius:4px;
                  padding:2px 7px; margin-bottom:8px; letter-spacing:.5px; }}
  .article-source {{ font-size:11px; font-weight:600; color:#718096;
                     text-transform:uppercase; letter-spacing:.8px; margin-bottom:5px; }}
  .article h2 {{ font-size:16px; font-weight:700; line-height:1.4; margin-bottom:8px; }}
  .article h2 a {{ color:#1a202c; }}
  .article h2 a:hover {{ color:#1a56db; }}
  .desc {{ font-size:13px; color:#4a5568; line-height:1.6; margin-bottom:8px; }}
  .why {{ background:#eef3ff; border-left:3px solid #1a56db;
          padding:10px 14px; border-radius:0 6px 6px 0; }}
  .why.nd {{ background:#fef9e7; border-left-color:#f39c12; }}
  .why .label {{ font-size:10px; font-weight:800; color:#1a56db;
                 text-transform:uppercase; letter-spacing:1px; margin-bottom:4px; }}
  .why.nd .label {{ color:#b7770d; }}
  .why p {{ font-size:13px; line-height:1.65; color:#2d3748; }}

  /* ── Competitor Watch section ─────────────────────────────────────── */
  .comp-section {{ border-top:3px solid #c53030; }}
  .comp-header {{ background:#fff5f5; padding:20px 36px 14px;
                  display:flex; align-items:flex-start; gap:14px; }}
  .comp-header-icon {{ font-size:26px; flex-shrink:0; margin-top:2px; }}
  .comp-header h2 {{ font-size:16px; font-weight:800; color:#c53030;
                     margin-bottom:4px; }}
  .comp-header p {{ font-size:12px; color:#742a2a; line-height:1.5; }}
  .comp-articles {{ padding:0 36px 8px; }}
  .comp-article {{ padding:14px 0; border-bottom:1px solid #fed7d7; }}
  .comp-article:last-child {{ border-bottom:none; }}
  .comp-badge {{ display:inline-block; background:#fed7d7; color:#c53030;
                 font-size:10px; font-weight:800; border-radius:4px;
                 padding:2px 7px; margin-bottom:6px; text-transform:uppercase;
                 letter-spacing:.5px; }}
  .comp-article h3 {{ font-size:14px; font-weight:700; line-height:1.4; }}
  .comp-article h3 a {{ color:#2d3748; }}
  .comp-article h3 a:hover {{ color:#c53030; }}
  .comp-desc {{ font-size:12px; color:#718096; line-height:1.5; margin-top:5px; }}

  .footer {{ background:#1a202c; padding:24px 36px; text-align:center; }}
  .footer p {{ font-size:12px; color:#718096; line-height:1.7; }}
  .footer a {{ color:#90cdf4; }}
  .footer .sources-line {{ margin-top:8px; font-size:11px; color:#4a5568; }}
</style>
</head>
<body>
<div class="wrapper">
  <div class="header">
    <div class="header-logo">NetDocuments</div>
    <h1>⚖️ LegalTech News Blast</h1>
    <div class="date">{date_long} &nbsp;·&nbsp; Top {n} Stories</div>
    {segment_badge}
    <div class="tagline">Curated for the NetDocuments sales team — what's happening in legal tech and why it matters to your deals.</div>
  </div>
  <div class="intro">
    <p>This week's scan of <strong>{total}</strong> articles across <strong>{num_sources}</strong> leading legal tech publications. Below are the stories most likely to move conversations with prospects and customers.</p>
  </div>
  <div class="articles">
{article_blocks}
  </div>
{competitor_section}
  <div class="footer">
    <p>Generated by the <strong style="color:#90cdf4;">NetDocuments LegalTech News Scraper</strong> &nbsp;·&nbsp; {date_short}</p>
    <p class="sources-line">Sources: Artificial Lawyer · Legaltech News · LawNext · Legal IT Insider · 3 Geeks and a Law Blog · Lawyerist · Above the Law · Legal Mosaic</p>
  </div>
</div>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# SALES ANGLES — 30 specific, actionable commentaries
# First matching keyword wins. Order matters — more specific first.
# ---------------------------------------------------------------------------

SALES_ANGLES = {
    # ── NetDocuments direct ────────────────────────────────────────────────
    "netdocuments": (
        "nd",
        "⭐ NetDocuments Feature",
        "Third-party coverage of NetDocuments is rare and powerful. Forward this to any prospect "
        "currently evaluating DMS options — it's more credible than anything we can say ourselves. "
        "Drop it in a follow-up email post-demo with a one-line intro: "
        "'Saw this today, thought you'd find it relevant.'"
    ),

    # ── Migration / switching ──────────────────────────────────────────────
    "legacy system": (
        "nd",
        "⭐ Legacy Replacement Signal",
        "Firms still on legacy DMS platforms face mounting pressure from security audits, "
        "AI integration gaps, and remote-work friction. This story validates the urgency. "
        "Use it to open: 'Are you still on [legacy system]? We hear this a lot — "
        "here's how other firms have solved it.'"
    ),
    "migrat": (
        "nd",
        "⭐ Migration Signal",
        "Any firm talking publicly about migration is in active evaluation mode — this is a "
        "warm-to-hot prospect signal. Lead with how NetDocuments' Migration Accelerator and "
        "dedicated migration team take the risk and pain out of switching. "
        "Ask: 'Who handles your document migration — IT or a vendor?'"
    ),
    "switching": (
        "nd",
        "⭐ Switching Intent Signal",
        "A firm actively discussing switching vendors has already decided to move — "
        "they're just choosing which path. Get in now. Lead with total cost of ownership "
        "and implementation risk reduction, two areas where NetDocuments consistently "
        "wins competitive evaluations."
    ),

    # ── Competitor intel ───────────────────────────────────────────────────
    "imanage": (
        "",
        "🔍 iManage Intel",
        "iManage is your most common head-to-head competitor. Read this before any competitive "
        "deal. Key differentiators to reinforce: NetDocuments is built cloud-native (not "
        "cloud-adapted), our AI is embedded — not bolted on, and our security certifications "
        "exceed iManage's. Ask: 'Are you evaluating iManage? Let me show you a direct comparison.'"
    ),
    "opentext": (
        "",
        "🔍 OpenText Intel",
        "OpenText deals are won by highlighting agility and support. OpenText is known for "
        "complex implementations, long support queues, and a fragmented product portfolio "
        "from acquisitions. NetDocuments offers a single, purpose-built legal DMS with faster "
        "time-to-value. Frame it as 'enterprise complexity vs. legal-first simplicity.'"
    ),
    "worldox": (
        "",
        "🔍 Worldox Intel",
        "Worldox clients are among the most loyal — and most overdue for a modern platform. "
        "Many smaller and mid-size firms still run Worldox on-prem. Use this to trigger the "
        "modernization conversation: cloud access, mobile, AI readiness, and zero-trust "
        "security are all things Worldox cannot offer. "
        "Soft opener: 'Are you still on Worldox? We have a fast-path migration.'"
    ),
    "sharepoint": (
        "",
        "🔍 SharePoint Competitive Angle",
        "SharePoint is often the 'we'll just use what we already have' objection. Use this "
        "story to reinforce that SharePoint requires heavy customization for legal use, lacks "
        "proper matter-centric organization, has weak email management, and creates compliance "
        "headaches. Ask: 'Is your team spending time on SharePoint admin instead of legal work?'"
    ),
    "docuware": (
        "",
        "🔍 DocuWare Competitive Intel",
        "DocuWare is a general-purpose document management system, not built for legal. Use "
        "this story to highlight how NetDocuments is purpose-built for law firms — with "
        "matter-centric filing, native email management, and legal-specific compliance "
        "workflows that a generic DMS like DocuWare simply doesn't offer."
    ),

    # ── Document management & core pain ───────────────────────────────────
    "document management": (
        "",
        "💡 Core DMS Pain Point",
        "This story validates the core problem NetDocuments solves. Use it in opening "
        "conversations to establish the pain before presenting the solution. "
        "A good opener: 'I saw this article — are you dealing with anything similar? "
        "We're hearing this from a lot of firms right now.'"
    ),
    "matter management": (
        "",
        "💡 Matter-Centric Workflow Signal",
        "Matter-centric filing is one of NetDocuments' sharpest differentiators. If a firm "
        "is struggling with organizing documents by matter, client, or practice group, lead "
        "with ndMatterPlan and the way NetDocuments structures work the way lawyers think. "
        "Ask: 'How does your team currently organize documents around a matter?'"
    ),
    "knowledge management": (
        "",
        "💡 Knowledge Management Signal",
        "Law firms are under pressure to stop reinventing the wheel on every matter. This is "
        "a direct opening for NetDocuments' knowledge management capabilities — surfacing "
        "precedents, templates, and prior work product right from within the DMS. "
        "Ask: 'How does your firm currently capture and reuse institutional knowledge?'"
    ),
    "email management": (
        "",
        "💡 Email Filing Pain Point",
        "Email filing is one of the top complaints from lawyers about any DMS. Use this story "
        "to talk about NetDocuments' native Outlook integration and how we make email-to-matter "
        "filing nearly effortless. "
        "Ask: 'What percentage of your attorneys actually file emails consistently today?'"
    ),
    "contract management": (
        "",
        "💡 Contract Lifecycle Signal",
        "Contract management tools need a document home — and that's NetDocuments. Many firms "
        "buy CLM software without thinking about where contracts live post-signature. "
        "NetDocuments integrates with leading CLM platforms and ensures contracts are stored, "
        "searchable, and governed properly. Ask: 'Where do your executed contracts live today?'"
    ),

    # ── AI in legal ────────────────────────────────────────────────────────
    "generative ai": (
        "",
        "💡 GenAI Integration Signal",
        "Every GenAI tool in legal needs a reliable, well-governed document layer underneath. "
        "This is your moment to position NetDocuments as the foundation — not just another AI "
        "feature. Ask: 'Which AI tools are you piloting? Have you thought about where those "
        "tools will pull documents from?'"
    ),
    "large language model": (
        "",
        "💡 LLM Implementation Signal",
        "LLMs require clean, well-organized document repositories to work accurately. Firms "
        "with fragmented or poorly governed document stores will get hallucinations and "
        "compliance risk. NetDocuments' structured, matter-centric architecture is the ideal "
        "LLM document layer. Use this to start a 'data readiness for AI' conversation."
    ),
    "ai adoption": (
        "",
        "💡 AI Adoption Curve Signal",
        "Firms that haven't adopted AI yet are starting to feel competitive pressure. "
        "This gives you a conversation opener: 'Are you feeling pressure internally to adopt AI? "
        "What's holding your firm back?' Then connect AI readiness directly to document "
        "governance — you can't build on a shaky foundation."
    ),
    "copilot": (
        "",
        "💡 Microsoft Copilot Signal",
        "Microsoft Copilot for Legal is a real buying trigger — firms evaluating it need to "
        "understand that Copilot works best with a well-governed document store. "
        "NetDocuments' Microsoft 365 integration and ndLink make it the optimal document "
        "layer for firms going all-in on the Microsoft ecosystem."
    ),

    # ── Cloud & security ───────────────────────────────────────────────────
    "data breach": (
        "",
        "🔒 Breach Urgency Signal",
        "Data breaches at law firms make headlines — and shake loose budget that was "
        "previously stuck. This is a powerful urgency opener, especially if the breach "
        "involved a similar firm. Ask: 'Has your firm done a recent assessment of your "
        "document security posture? What happens to your documents if credentials are compromised?'"
    ),
    "cybersecurity": (
        "",
        "🔒 Security Urgency Signal",
        "A cybersecurity incident at a law firm is a disaster: client data, privilege, and "
        "reputation all at risk. Use this to reinforce NetDocuments' zero-trust architecture, "
        "SOC 2 Type II certification, and purpose-built legal security model. "
        "Ask: 'When was your last security audit of your document environment?'"
    ),
    "data governance": (
        "",
        "🔒 Governance & Risk Signal",
        "Data governance is no longer just a compliance checkbox — it's a client requirement. "
        "Many large clients now audit their outside counsel's data practices. "
        "NetDocuments' audit trails, retention policies, and access controls are built for "
        "exactly this. Ask: 'Are any of your clients asking about your document governance policies?'"
    ),
    "gdpr": (
        "",
        "🔒 Regulatory Compliance Signal",
        "GDPR, CCPA, and emerging state-level data laws create direct liability for firms "
        "without proper document governance. Use this to highlight NetDocuments' geo-residency "
        "options, built-in compliance workflows, and the fact that we're purpose-built for "
        "the regulatory environment legal teams operate in."
    ),

    # ── Law firm operations ────────────────────────────────────────────────
    "practice management": (
        "",
        "💡 Practice Management Signal",
        "Firms evaluating or upgrading practice management tools are also reviewing their "
        "entire legal tech stack — including document management. Ask: 'As you evaluate "
        "practice management options, have you also looked at your DMS? The two systems work "
        "best when they're built to integrate.'"
    ),
    "law firm merger": (
        "",
        "💡 M&A Prospect Signal",
        "Law firm mergers create immediate document management headaches — two different "
        "systems, two filing structures, and the need to consolidate quickly. This is one "
        "of the strongest buying triggers in legal tech. Reach out to both firms and ask: "
        "'How are you planning to handle document management post-merger?'"
    ),
    "lateral": (
        "",
        "💡 Lateral Hire Signal",
        "Lateral partner moves bring new clients, matters, and documents — and often expose "
        "gaps in a firm's document infrastructure. A heavy lateral season is a good time to "
        "ask: 'When attorneys join from other firms, how does your team onboard them into "
        "your DMS? Are they filing documents consistently from day one?'"
    ),
    "legal operations": (
        "",
        "💡 Legal Ops Buyer Signal",
        "Legal operations professionals are increasingly the economic buyer or key influencer "
        "for DMS decisions. Lead with ROI, workflow efficiency metrics, and how NetDocuments "
        "reduces administrative burden on attorneys — all things Legal Ops teams are measured on. "
        "Ask: 'Who owns the DMS decision at your firm — IT, Legal Ops, or the managing partner?'"
    ),
    "general counsel": (
        "",
        "💡 In-House Counsel Signal",
        "General Counsel and in-house legal teams are a growing market for NetDocuments. "
        "Unlike law firms, in-house teams live inside a corporate IT environment and need a "
        "DMS that integrates cleanly with Microsoft 365 while maintaining matter structure "
        "and privilege protection. Use this story to open that specific conversation."
    ),
    "digital transformation": (
        "",
        "💡 Digital Transformation Initiative",
        "Firms undergoing digital transformation have budget, executive sponsorship, and "
        "urgency to modernize. Position NetDocuments as the anchor of a modern legal tech "
        "stack — not just a file server replacement. "
        "Ask: 'Is your firm in the middle of a broader digital transformation effort? "
        "Where does document management fit in that roadmap?'"
    ),
    "workflow automation": (
        "",
        "💡 Automation Efficiency Signal",
        "Document workflow automation is a major efficiency driver for law firms. "
        "NetDocuments' integration ecosystem — ndOffice, ndMail, and API partners — enables "
        "sophisticated automated workflows without heavy IT investment. "
        "Ask: 'What document workflows are your attorneys spending the most time on manually?'"
    ),
    "remote work": (
        "",
        "💡 Remote / Hybrid Access Signal",
        "Hybrid and remote legal work is now permanent, and it exposed every firm's document "
        "access gaps. NetDocuments was built cloud-native for exactly this model. "
        "Ask: 'How do your attorneys access documents when they're outside the office? "
        "Is that experience as good as being at their desk?'"
    ),
    "e-discovery": (
        "",
        "💡 eDiscovery Adjacency Signal",
        "eDiscovery readiness starts with how documents are organized and governed in the DMS. "
        "Firms with poor document management face exponentially higher eDiscovery costs. "
        "Use this to bridge the conversation: 'A well-structured DMS cuts eDiscovery costs "
        "dramatically — is that something your firm has connected?'"
    ),
    "roi": (
        "",
        "💡 CFO / Business Case Opener",
        "ROI is the battleground for legal tech spend right now — finance stakeholders want "
        "numbers, not features. Use this story to anchor the business case conversation. "
        "NetDocuments has documented ROI case studies showing reduced IT overhead, faster "
        "document retrieval, and lower eDiscovery costs. Ask: 'Who else is involved in this "
        "decision — do you have a finance or operations stakeholder we should include?'"
    ),
    "legalweek": (
        "",
        "💡 Market Pulse",
        "Conference coverage shapes what buyers are thinking and asking about. Use these "
        "takeaways to speak the same language as prospects who attended — reference the same "
        "trends they heard on stage, and position NetDocuments as the platform that's already "
        "ahead of where the market is heading."
    ),
}

DEFAULT_ANGLE = (
    "",
    "💡 Sales Angle",
    "This story reflects trends shaping your buyers' thinking. Use it to open a timely, "
    "relevant conversation about how NetDocuments fits into their evolving tech strategy. "
    "A simple opener: 'Saw this today — is this something your firm is dealing with?'"
)


def _assign_angles(articles: list[dict]) -> list[tuple | None]:
    """
    Assign a UNIQUE sales angle to each article in one pre-rendering pass.

    No two articles in the same email will share the same label/commentary —
    so the email reads as curated rather than templated.

    Algorithm for each article (in order):
      1. Collect every keyword from SALES_ANGLES that matches the article text.
      2. From those matches, pick the first whose label hasn't been used yet.
      3. If none left unused, try the DEFAULT_ANGLE (once).
      4. If everything is exhausted, return None — the "Why it matters" block
         is omitted entirely (no block is cleaner than a repeated one).

    Returns a list of (css_class, label, commentary) tuples — or None — with
    one entry per article, in the same order as the input list.
    """
    used_labels: set[str] = set()
    results: list[tuple | None] = []

    for article in articles:
        text = (article["title"] + " " + article.get("desc", "")).lower()
        assigned: tuple | None = None

        # Find the first keyword match whose label hasn't been used yet
        for kw, (css_class, label, commentary) in SALES_ANGLES.items():
            if kw in text and label not in used_labels:
                assigned = (css_class, label, commentary)
                used_labels.add(label)
                break

        # No unused keyword match — try the default angle
        if assigned is None:
            _, def_label, _ = DEFAULT_ANGLE
            if def_label not in used_labels:
                assigned = DEFAULT_ANGLE
                used_labels.add(def_label)
            # else: assigned stays None -> "Why it matters" block omitted

        results.append(assigned)

    return results


def _article_block(rank: int, article: dict, angle: tuple | None = None) -> str:
    """
    Render one article card.  `angle` is pre-assigned by _assign_angles() so
    no two cards in the same email share the same "Why it matters" label.
    If angle is None the "Why it matters" block is omitted cleanly.
    """
    desc_block = ""
    if article.get("desc"):
        clean = article["desc"].replace("<", "&lt;").replace(">", "&gt;")
        desc_block = f'      <div class="desc">{clean}</div>\n'
    title_esc = article["title"].replace("<", "&lt;").replace(">", "&gt;")

    why_block = ""
    if angle is not None:
        css_class, label, commentary = angle
        why_class = f'why {css_class}'.strip()
        why_block = (
            f'      <div class="{why_class}">\n'
            f'        <div class="label">{label}</div>\n'
            f'        <p>{commentary}</p>\n'
            f'      </div>\n'
        )

    return (
        f'    <div class="article">\n'
        f'      <div class="article-num">#{rank}</div>\n'
        f'      <div class="article-source">{article["source"]}</div>\n'
        f'      <h2><a href="{article["url"]}" target="_blank">{title_esc}</a></h2>\n'
        f'{desc_block}'
        f'{why_block}'
        f'    </div>\n'
    )


def _competitor_article_block(article: dict) -> str:
    title_esc = article["title"].replace("<", "&lt;").replace(">", "&gt;")
    desc_block = ""
    if article.get("desc"):
        clean = article["desc"].replace("<", "&lt;").replace(">", "&gt;")
        desc_block = f'    <div class="comp-desc">{clean}</div>\n'
    return (
        f'  <div class="comp-article">\n'
        f'    <div class="comp-badge">{article["source"]}</div>\n'
        f'    <h3><a href="{article["url"]}" target="_blank">{title_esc}</a></h3>\n'
        f'{desc_block}'
        f'  </div>\n'
    )


def _build_competitor_section(competitor_articles: list[dict]) -> str:
    """Build the Competitor Watch HTML section. Returns '' if no competitor articles."""
    if not competitor_articles:
        return ""

    # Deduplicate by title
    seen, unique = set(), []
    for a in competitor_articles:
        key = re.sub(r"\W+", "", a["title"].lower())[:60]
        if key not in seen:
            seen.add(key)
            unique.append(a)

    blocks = "".join(_competitor_article_block(a) for a in unique)
    n = len(unique)
    return (
        f'  <div class="comp-section">\n'
        f'    <div class="comp-header">\n'
        f'      <div class="comp-header-icon">🔍</div>\n'
        f'      <div>\n'
        f'        <h2>Competitor Watch</h2>\n'
        f'        <p>{n} article{"s" if n != 1 else ""} from competitor blogs &amp; newsrooms this week</p>\n'
        f'      </div>\n'
        f'    </div>\n'
        f'  <div class="comp-articles">\n'
        f'{blocks}'
        f'  </div>\n'
        f'  </div>\n'
    )


def build_html(top_articles: list[dict], all_articles: list[dict],
               competitor_articles: list[dict] | None = None,
               segment: str = "Strategic") -> str:
    sys.path.insert(0, str(Path(__file__).parent))
    from sites_config import SITES

    now        = datetime.datetime.now()
    day        = str(now.day)   # no leading zero, cross-platform
    date_long  = now.strftime(f"%A, %B {day}, %Y")
    date_short = now.strftime("%Y-%m-%d")
    subject    = EMAIL_SUBJECT.format(date=now.strftime(f"%B {day}, %Y"))

    # Assign unique sales angles before rendering — no two articles share the same label
    angles = _assign_angles(top_articles)
    blocks = "".join(_article_block(i + 1, a, angle) for i, (a, angle) in enumerate(zip(top_articles, angles)))

    # Competitor articles can come from two places:
    # 1. The explicit competitor_articles argument (generate_blast.main)
    # 2. Articles in all_articles that have competitor=True (ui.py path)
    comp_articles = list(competitor_articles or [])
    if not comp_articles:
        comp_articles = [a for a in all_articles if a.get("competitor")]

    competitor_section = _build_competitor_section(comp_articles)

    # Build segment badge HTML (shown in email header when not Strategic)
    if segment == "Strategic":
        segment_badge = ""
    else:
        badge_color = {"SML": "#7c3aed", "International": "#0369a1"}.get(segment, "#374151")
        segment_badge = (
            f'<div><span class="segment-badge" '
            f'style="background:{badge_color};color:white;">'
            f'{segment} Segment</span></div>'
        )

    return HTML_TEMPLATE.format(
        subject            = subject,
        date_long          = date_long,
        date_short         = date_short,
        n                  = len(top_articles),
        total              = len(all_articles),
        num_sources        = len(SITES),
        article_blocks     = blocks,
        competitor_section = competitor_section,
        segment_badge      = segment_badge,
    )


# ---------------------------------------------------------------------------
# EMAIL SENDING (win32com → local Outlook)
# ---------------------------------------------------------------------------

def send_via_outlook(html: str, subject: str, recipients: str = ""):
    """
    Saves the blast as a .eml file on the Desktop and opens it in Outlook.
    Outlook loads it as a ready-to-send draft — just hit Send to deliver.
    This approach works with both classic Outlook and the new Outlook (olk)
    and requires no app passwords or SMTP credentials.
    """
    import base64

    # RFC 2047-encode subject so non-ASCII chars (emoji, em-dash) survive
    subject_b64 = base64.b64encode(subject.encode("utf-8")).decode("ascii")
    subject_encoded = f"=?utf-8?b?{subject_b64}?="

    boundary = "=_NDBlastBoundary"
    html_b64 = base64.b64encode(html.encode("utf-8")).decode("ascii")

    # Wrap long base64 lines at 76 chars (RFC 2045)
    html_b64_wrapped = "\r\n".join(
        html_b64[i:i+76] for i in range(0, len(html_b64), 76)
    )

    eml = (
        "MIME-Version: 1.0\r\n"
        f"Subject: {subject_encoded}\r\n"
        f"To: {recipients or RECIPIENTS}\r\n"
        f"From: {recipients or RECIPIENTS}\r\n"
        f'Content-Type: multipart/alternative; boundary="{boundary}"\r\n'
        "\r\n"
        f"--{boundary}\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "Content-Transfer-Encoding: quoted-printable\r\n"
        "\r\n"
        "NetDocuments LegalTech News Blast - please view in HTML.\r\n"
        "\r\n"
        f"--{boundary}\r\n"
        "Content-Type: text/html; charset=utf-8\r\n"
        "Content-Transfer-Encoding: base64\r\n"
        "\r\n"
        f"{html_b64_wrapped}\r\n"
        f"--{boundary}--\r\n"
    )

    # Save to Desktop so it's easy to find
    desktop = Path.home() / "Desktop"
    ts       = datetime.datetime.now().strftime("%Y%m%d")
    eml_path = desktop / f"ND_LegalTech_Blast_{ts}.eml"
    # Write as ASCII — body is base64, subject is RFC2047-encoded, all safe
    eml_path.write_text(eml, encoding="ascii")
    log.info("Draft saved: %s", eml_path)

    # Open in default mail client (Outlook) as a ready-to-send draft
    import subprocess
    subprocess.Popen(["cmd", "/c", "start", "", str(eml_path)])
    log.info("Opened in Outlook — review and click Send.")


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate and send the ND LegalTech News Blast")
    parser.add_argument(
        "--preview", action="store_true",
        help="Open the HTML in a browser for visual preview; do NOT open Outlook draft"
    )
    parser.add_argument(
        "--save-csv", action="store_true",
        help="Also save all scraped articles to a timestamped CSV"
    )
    parser.add_argument(
        "--segment", default="Strategic",
        choices=["SML", "Strategic", "International"],
        help="Scoring segment: SML, Strategic (default), or International"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # 1 — Scrape (returns regular and competitor articles separately)
    log.info("=== Scraping sources ===")
    regular_articles, competitor_articles = fetch_all_articles()
    all_articles = regular_articles + competitor_articles

    # 2 — Optionally save CSV
    if args.save_csv:
        ts  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = OUTPUT_DIR / f"news_articles_{ts}.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["source","title","url","desc","competitor"])
            writer.writeheader()
            writer.writerows(all_articles)
        log.info("Saved CSV: %s", csv_path)

    # 3 — Curate (scores only regular articles, competitors excluded)
    log.info("=== Scoring and curating top %d (segment: %s) ===", TOP_N, args.segment)
    top = curate(regular_articles, TOP_N, segment=args.segment)

    # 4 — Build HTML (includes competitor watch section)
    log.info("=== Building HTML ===")
    html = build_html(top, regular_articles, competitor_articles, segment=args.segment)

    # 5 — Save HTML file (always, for debugging)
    ts        = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path = OUTPUT_DIR / f"blast_{ts}.html"
    html_path.write_text(html, encoding="utf-8")
    log.info("HTML saved: %s", html_path)

    # 6 — Send or preview
    now     = datetime.datetime.now()
    day     = str(now.day)
    subject = EMAIL_SUBJECT.format(date=now.strftime(f"%B {day}, %Y"))

    if args.preview:
        import webbrowser
        log.info("Preview mode — opening in browser (not sending email)")
        webbrowser.open(html_path.as_uri())
    else:
        log.info("=== Sending email ===")
        send_via_outlook(html, subject)
        log.info("=== Done ===")


if __name__ == "__main__":
    main()
