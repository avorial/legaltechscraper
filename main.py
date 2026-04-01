"""
main.py
-------
Entry point for the multi-site news scraper.

Run:
    python main.py

Output:
    output/news_articles_<YYYYMMDD_HHMMSS>.csv
"""

import csv
import os
from datetime import datetime

from scraper import scrape_all_sites


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OUTPUT_DIR = "output"
CSV_COLUMNS = ["source", "title", "url", "description", "scraped_at"]


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------

def save_to_csv(articles: list[dict]) -> str:
    """
    Write a list of article dicts to a timestamped CSV file in OUTPUT_DIR.

    Returns the path to the saved file.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"news_articles_{timestamp}.csv"
    filepath = os.path.join(OUTPUT_DIR, filename)

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=CSV_COLUMNS,
            extrasaction="ignore",   # drop any unexpected keys gracefully
        )
        writer.writeheader()
        writer.writerows(articles)

    return filepath


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------

def print_summary(articles: list[dict]) -> None:
    """Print a brief breakdown of results by source."""
    if not articles:
        print("No articles were collected.")
        return

    from collections import Counter
    counts = Counter(a["source"] for a in articles)

    print("=" * 50)
    print("SCRAPE SUMMARY")
    print("=" * 50)
    for source, count in sorted(counts.items()):
        print(f"  {source:<25} {count:>4} articles")
    print("-" * 50)
    print(f"  {'TOTAL':<25} {len(articles):>4} articles")
    print("=" * 50)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    articles = scrape_all_sites()

    if not articles:
        print("\n⚠  No articles were scraped. Check your selectors in sites_config.py.")
        return

    filepath = save_to_csv(articles)
    print_summary(articles)
    print(f"\n✅ Results saved to: {filepath}\n")

    # Preview the first 5 rows in the terminal
    print("First 5 articles:")
    print("-" * 80)
    for art in articles[:5]:
        print(f"[{art['source']}] {art['title']}")
        print(f"  {art['url']}")
        if art.get("description"):
            desc = art["description"]
            print(f"  {desc[:120]}{'...' if len(desc) > 120 else ''}")
        print()


if __name__ == "__main__":
    main()
