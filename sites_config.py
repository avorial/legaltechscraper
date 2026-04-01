"""
sites_config.py
---------------
Configuration for each news source the scraper will target.

Each entry defines:
  - name:        Human-readable site name
  - url:         The page to scrape (section front / index)
  - article_sel: CSS selector that identifies each article/card container
  - title_sel:   CSS selector (relative to article) for the headline text
  - link_sel:    CSS selector (relative to article) for the <a> tag with the URL
  - desc_sel:    CSS selector (relative to article) for a summary/description (optional)
  - base_url:    Prepended to relative links (leave "" if links are already absolute)
  - competitor:  True for competitor sites (goes in Competitor Watch section, not scored)

Tips for maintenance
--------------------
If a site redesigns and the scraper returns 0 results, open the page in your
browser, right-click the headline, and "Inspect Element" to find the new selector.
You can also run add_site.py to interactively discover new selectors.
"""

SITES: list[dict] = [
    {
        # MH Magazine WordPress theme. Titles sit in <h3 class="entry-title"><a>.
        # Using h3.entry-title as the container so the link is a direct child.
        "name": "Artificial Lawyer",
        "url": "https://www.artificiallawyer.com",
        "article_sel": "h3.entry-title",
        "title_sel": "a",
        "link_sel": "a",
        "desc_sel": None,
        "base_url": "https://www.artificiallawyer.com",
    },
    {
        # ABA Law Practice Division flagship legal tech publication.
        # RSS preferred for reliability; covers tools, trends, and practice management.
        # Replaced Legal Dive (which shut down February 2025) - March 2026.
        "name": "Law Technology Today",
        "url": "https://www.lawtechnologytoday.org",
        "rss": "https://www.lawtechnologytoday.org/feed/",
        "article_sel": "article, div.post",
        "title_sel": "h2 a, h3 a",
        "link_sel": "h2 a, h3 a",
        "desc_sel": "p",
        "base_url": "https://www.lawtechnologytoday.org",
    },
    {
        # ALM / Law.com Nuxt.js platform. Uses data-cy test attributes which are
        # much more stable than CSS classes (intentionally kept across redesigns).
        # Container: <li data-cy="article">  Title/link: <a data-cy="story-link">
        # Description: <p data-cy="story-summary">
        "name": "Legaltech News",
        "url": "https://www.law.com/legaltechnews/",
        "article_sel": 'li[data-cy="article"]',
        "title_sel": 'a[data-cy="story-link"]',
        "link_sel": 'a[data-cy="story-link"]',
        "desc_sel": 'p[data-cy="story-summary"]',
        "base_url": "https://www.law.com",
    },
    {
        # WordPress. Bob Ambrogi's LawSites — premier US legal tech blog.
        # Articles are in <article> or <div class="post"> with <h2> title links.
        "name": "LawNext (LawSites)",
        "url": "https://www.lawnext.com",
        "article_sel": "article, div.post",
        "title_sel": "h2 a, h1 a",
        "link_sel": "h2 a, h1 a",
        "desc_sel": "p",
        "base_url": "https://www.lawnext.com",
    },
    {
        # WordPress. UK-based Legal IT Insider — leading European legal tech publication.
        # Uses RSS feed for reliability (HTML fallback selectors kept for reference).
        "name": "Legal IT Insider",
        "url": "https://legaltechnology.com/category/latest-news/",
        "rss": "https://legaltechnology.com/feed/",
        "article_sel": "article, div.post",
        "title_sel": "h3 a, h2 a",
        "link_sel": "h3 a, h2 a",
        "desc_sel": "p",
        "base_url": "https://legaltechnology.com",
    },
    {
        # RSS only. 3 Geeks and a Law Blog — law practice management & legal tech commentary.
        "name": "3 Geeks and a Law Blog",
        "url": "https://www.geeklawblog.com/feed",
        "rss": "https://www.geeklawblog.com/feed",
        "article_sel": None,
        "title_sel": None,
        "link_sel": None,
        "desc_sel": None,
        "base_url": "",
    },
    {
        # RSS only. Lawyerist — practice management, tools, and running a modern law firm.
        "name": "Lawyerist",
        "url": "https://lawyerist.com/feed/",
        "rss": "https://lawyerist.com/feed/",
        "article_sel": None,
        "title_sel": None,
        "link_sel": None,
        "desc_sel": None,
        "base_url": "",
    },
    {
        # RSS only. Above the Law — legal industry news, BigLaw, and legal tech coverage.
        # abovethelaw.com itself blocks all non-browser requests (Cloudflare), but their
        # Feedburner mirror is publicly accessible.
        "name": "Above the Law",
        "url": "https://feeds.feedburner.com/abovethelaw",
        "rss": "https://feeds.feedburner.com/abovethelaw",
        "article_sel": None,
        "title_sel": None,
        "link_sel": None,
        "desc_sel": None,
        "base_url": "",
    },
    {
        # RSS only. Legal Mosaic — Jordan Furlong's analysis of the changing legal market.
        # WordPress site; direct HTML also accessible but RSS is more reliable.
        "name": "Legal Mosaic",
        "url": "https://www.legalmosaic.com/feed/",
        "rss": "https://www.legalmosaic.com/feed/",
        "article_sel": None,
        "title_sel": None,
        "link_sel": None,
        "desc_sel": None,
        "base_url": "",
    },
]

# ---------------------------------------------------------------------------
# COMPETITOR SITES
# Articles from these sources appear in the "Competitor Watch" section of the
# blast rather than being ranked alongside regular news. They are scraped the
# same way but bypass the scoring system entirely.
#
# NOTE: CSS selectors verified by live browser inspection March 2026.
# Run add_site.py to re-discover selectors if a site redesigns.
# ---------------------------------------------------------------------------

COMPETITOR_SITES: list[dict] = [
    {
        # iManage resource center blog.
        # Container: div.card-listing-item  |  Title: p.heading (NOT an <a>)  |  Link: <a>
        "name": "iManage Blog",
        "url": "https://imanage.com/resources/resource-center/blog/",
        "article_sel": ".card-listing-item",
        "title_sel": "p[class*='heading']",
        "link_sel": "a",
        "desc_sel": ".listing-summary-clamp, [class*='summary']",
        "base_url": "https://imanage.com",
        "competitor": True,
    },
    {
        # iManage news / press releases — same CMS structure as the blog.
        "name": "iManage News",
        "url": "https://imanage.com/resources/resource-center/news/?sort=date",
        "article_sel": ".card-listing-item",
        "title_sel": "p[class*='heading']",
        "link_sel": "a",
        "desc_sel": ".listing-summary-clamp, [class*='summary']",
        "base_url": "https://imanage.com",
        "competitor": True,
    },
    {
        # Harvey.ai blog — Next.js site where the <a> tag IS the card container.
        # The scraper falls back to the container's own href when link_sel finds nothing
        # (BeautifulSoup won't find a nested <a> inside an <a>).
        "name": "Harvey.ai Blog",
        "url": "https://www.harvey.ai/blog",
        "article_sel": "a[href*='/blog/']",
        "title_sel": "h2, h3",
        "link_sel": "a",
        "desc_sel": "p",
        "base_url": "https://www.harvey.ai",
        "competitor": True,
    },
    {
        # Legora blog — Framer site where the <a> tag IS the card container.
        "name": "Legora Blog",
        "url": "https://legora.com/blog",
        "article_sel": "a[href*='/blog/']",
        "title_sel": "h1, h2, h3, h4",
        "link_sel": "a",
        "desc_sel": "p",
        "base_url": "https://legora.com",
        "competitor": True,
    },
    {
        # Legora newsroom — same Framer pattern, <a> wraps each item.
        "name": "Legora Newsroom",
        "url": "https://legora.com/newsroom",
        "article_sel": "a[href*='/newsroom/']",
        "title_sel": "h1, h2, h3, h4",
        "link_sel": "a",
        "desc_sel": "p",
        "base_url": "https://legora.com",
        "competitor": True,
    },
    {
        # Clio blog — legal practice management software. WordPress with RSS.
        # RSS preferred; HTML selectors kept as fallback.
        "name": "Clio Blog",
        "url": "https://www.clio.com/blog/",
        "rss": "https://www.clio.com/blog/feed/",
        "article_sel": "article, .post, [class*='blog-card'], [class*='post-card']",
        "title_sel": "h2 a, h3 a, .entry-title a",
        "link_sel": "h2 a, h3 a, .entry-title a",
        "desc_sel": "p, .entry-summary, [class*='excerpt']",
        "base_url": "https://www.clio.com",
        "competitor": True,
    },
    {
        # Clio press releases and news coverage.
        "name": "Clio Press",
        "url": "https://www.clio.com/about/press/",
        "article_sel": "article, li, .press-item, [class*='news-item'], [class*='press-release']",
        "title_sel": "h2 a, h3 a, h4 a, a[href*='/press/']",
        "link_sel": "h2 a, h3 a, h4 a, a[href*='/press/']",
        "desc_sel": "p",
        "base_url": "https://www.clio.com",
        "competitor": True,
    },
]

# Convenience: all sites combined (used by scraper.py and main.py)
ALL_SITES: list[dict] = SITES + COMPETITOR_SITES
