"""
add_site.py
-----------
Interactive helper to discover CSS selectors for a new site and append it
to SITES in sites_config.py.

Run:
    python add_site.py

The script will:
1. Ask you for the site's name and URL.
2. Fetch the page and print the top-level tag structure so you can
   identify the right selectors.
3. Let you enter selectors one by one.
4. Test the selectors live and show sample output.
5. Append the new config to sites_config.py.

Requirements: botasaurus must be installed.
"""

import ast
import re
import textwrap
from urllib.parse import urljoin

import requests
from botasaurus.soupify import soupify


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_page(url: str):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp


def preview_selector(soup, sel: str, base_url: str, limit: int = 3) -> None:
    tags = soup.select(sel)
    if not tags:
        print(f"  ⚠  No elements matched selector: {sel!r}")
        return
    print(f"  ✓  {len(tags)} elements matched. Showing up to {limit}:")
    for tag in tags[:limit]:
        text = tag.get_text(strip=True)[:100]
        href = tag.get("href", "")
        if href:
            href = urljoin(base_url, href)
            print(f"     TEXT: {text!r}   HREF: {href}")
        else:
            print(f"     TEXT: {text!r}")


def append_to_config(entry: dict) -> None:
    """Append a new site dict to sites_config.py's SITES list."""
    with open("sites_config.py", "r", encoding="utf-8") as f:
        content = f.read()

    # Find the closing ] of SITES and insert before it
    entry_str = textwrap.indent(repr(entry) + ",\n", "    ")
    new_content = re.sub(r"(\]\s*)$", f"\n{entry_str}\\1", content, flags=re.MULTILINE)

    with open("sites_config.py", "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"\n✅ Site '{entry['name']}' added to sites_config.py")


# ---------------------------------------------------------------------------
# Main interactive flow
# ---------------------------------------------------------------------------

def main():
    print("\n=== Add a New News Site ===\n")

    name = input("Site name (e.g. Reuters Technology): ").strip()
    url = input("Section URL to scrape (e.g. https://www.reuters.com/technology/): ").strip()
    base_url = input("Base URL for relative links (leave blank if links are absolute): ").strip()

    print(f"\nFetching {url} …")
    try:
        resp = fetch_page(url)
    except Exception as e:
        print(f"Error fetching page: {e}")
        return

    soup = soupify(resp)

    print("\n--- Enter CSS selectors (press Enter to skip optional ones) ---")
    print("Tip: open DevTools on the page and use 'Copy selector' for accuracy.\n")

    article_sel = input("Article container selector (e.g. article, div.story-card): ").strip()
    preview_selector(soup, article_sel, base_url or url)

    title_sel = input("\nTitle selector (relative to container, e.g. h2, .headline): ").strip()
    containers = soup.select(article_sel)
    if containers:
        preview_selector(containers[0], title_sel, base_url or url)

    link_sel = input("\nLink <a> selector (relative to container, e.g. a, h2 a): ").strip()
    if containers:
        preview_selector(containers[0], link_sel, base_url or url)

    desc_sel = input("\nDescription selector (optional, press Enter to skip): ").strip() or None

    entry = {
        "name": name,
        "url": url,
        "article_sel": article_sel,
        "title_sel": title_sel,
        "link_sel": link_sel,
        "desc_sel": desc_sel,
        "base_url": base_url,
    }

    print("\nConfig entry to be added:")
    for k, v in entry.items():
        print(f"  {k}: {v!r}")

    confirm = input("\nAdd this site to sites_config.py? [y/N]: ").strip().lower()
    if confirm == "y":
        append_to_config(entry)
    else:
        print("Aborted. No changes made.")


if __name__ == "__main__":
    main()
