"""
scraper.py
----------
Botasaurus-powered scraper for multiple news sites.

Uses the @request decorator (lightweight, sends browser-like headers) for all
sites by default.  If a site is protected by JavaScript rendering (Cloudflare,
heavy SPAs), swap its entry to use scrape_with_browser() instead.

Each scraper function receives a list of site config dicts and returns a flat
list of article dicts, one per article found.
"""

from datetime import datetime, timezone
from urllib.parse import urljoin

import feedparser
from bs4 import BeautifulSoup
from botasaurus.request import request, Request
from botasaurus.soupify import soupify

from sites_config import SITES


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _safe_text(tag) -> str:
    """Return stripped text from a BS4 tag, or '' if tag is None."""
    return tag.get_text(strip=True) if tag else ""


def _safe_href(tag, base_url: str) -> str:
    """Return an absolute URL from a BS4 <a> tag's href, or ''."""
    if not tag:
        return ""
    href = tag.get("href", "")
    if not href or href.startswith("#"):
        return ""
    return urljoin(base_url, href) if base_url else href


def _parse_articles(soup, config: dict) -> list[dict]:
    """
    Given a BeautifulSoup object and a site config dict, extract article
    data using the configured CSS selectors.

    Falls back gracefully: if an article element doesn't contain the
    expected child selectors, it is skipped rather than crashing.
    """
    results = []
    scraped_at = datetime.now(timezone.utc).isoformat()
    base_url = config.get("base_url", "")

    containers = soup.select(config["article_sel"])
    if not containers:
        print(f"  [WARN] No article containers found for {config['name']} "
              f"(selector: {config['article_sel']!r})")
        return results

    seen_urls: set[str] = set()

    for container in containers:
        title_tag = container.select_one(config["title_sel"])
        link_tag = container.select_one(config["link_sel"])

        title = _safe_text(title_tag)
        url = _safe_href(link_tag, base_url)

        # Skip empty or duplicate entries
        if not title or not url:
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)

        desc_sel = config.get("desc_sel")
        description = ""
        if desc_sel:
            desc_tag = container.select_one(desc_sel)
            description = _safe_text(desc_tag)

        results.append({
            "source": config["name"],
            "title": title,
            "url": url,
            "description": description,
            "scraped_at": scraped_at,
        })

    return results


# ---------------------------------------------------------------------------
# RSS scraper  (used for sites that expose a feed)
# ---------------------------------------------------------------------------

def _scrape_rss(config: dict) -> list[dict]:
    """Fetch and parse an RSS/Atom feed. Returns list of article dicts."""
    name = config["name"]
    rss_url = config["rss"]
    print(f"  Scraping {name} (RSS) → {rss_url}")

    try:
        feed = feedparser.parse(rss_url)
    except Exception as exc:
        print(f"  [ERROR] {name}: {exc}")
        return []

    if not feed.entries:
        print(f"  [WARN] {name}: feed returned 0 entries")
        return []

    scraped_at = datetime.now(timezone.utc).isoformat()
    results = []
    seen: set[str] = set()

    for entry in feed.entries:
        title = (entry.get("title") or "").strip()
        url = (entry.get("link") or "").strip()
        if not title or not url or url in seen:
            continue
        seen.add(url)

        raw_desc = entry.get("summary") or ""
        if not raw_desc and entry.get("content"):
            raw_desc = entry["content"][0].get("value", "")
        desc = BeautifulSoup(raw_desc, "html.parser").get_text(strip=True)[:300] if raw_desc else ""

        results.append({
            "source": name,
            "title": title,
            "url": url,
            "description": desc,
            "scraped_at": scraped_at,
        })

    print(f"  ✓ {name}: {len(results)} articles")
    return results


# ---------------------------------------------------------------------------
# @request scraper  (used for most news sites)
# ---------------------------------------------------------------------------

@request(
    # Rotating a realistic User-Agent is handled automatically by botasaurus.
    # Add parallel=True and data=[...list of configs...] to scrape concurrently.
    parallel=4,           # scrape up to 4 sites concurrently
    cache=False,          # set to True during dev to avoid re-hitting sites
    output=None,          # we handle our own output in main.py
)
def scrape_site(req: Request, site_config: dict) -> list[dict]:
    """
    Scrape a single news site using lightweight HTTP requests.

    Args:
        req:         Botasaurus Request object (pre-configured with browser headers).
        site_config: One entry from SITES in sites_config.py.

    Returns:
        List of article dicts, or [] on error.
    """
    # RSS sites bypass the HTTP request entirely
    if site_config.get("rss"):
        return _scrape_rss(site_config)

    name = site_config["name"]
    url = site_config["url"]
    print(f"  Scraping {name} → {url}")

    try:
        response = req.get(url, timeout=20)
    except Exception as exc:
        print(f"  [ERROR] Could not fetch {name}: {exc}")
        return []

    if not response or response.status_code != 200:
        status = response.status_code if response else "no response"
        print(f"  [ERROR] {name} returned HTTP {status}")
        return []

    soup = soupify(response)
    articles = _parse_articles(soup, site_config)
    print(f"  ✓ {name}: {len(articles)} articles found")
    return articles


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def scrape_all_sites() -> list[dict]:
    """
    Run the scraper against every site in SITES and return a combined list
    of all article dicts.

    Usage:
        from scraper import scrape_all_sites
        articles = scrape_all_sites()
    """
    print(f"\nStarting scraper for {len(SITES)} site(s)...\n")

    # Pass the full list to botasaurus — it will distribute across parallel workers
    nested_results = scrape_site(data=SITES)

    # scrape_site returns a list of lists (one inner list per site); flatten it
    all_articles: list[dict] = []
    for site_result in nested_results:
        if isinstance(site_result, list):
            all_articles.extend(site_result)
        elif isinstance(site_result, dict):          # single-article edge case
            all_articles.append(site_result)

    print(f"\nTotal articles collected: {len(all_articles)}\n")
    return all_articles
