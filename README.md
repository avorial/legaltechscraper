# LegalTech News Scraper

A desktop app that scrapes legal technology news, scores articles by relevance to **NetDocuments** and your chosen segment, and drafts a formatted email blast via Outlook — in one click.

---

## Screenshots

#V2 UI

<img width="918" height="843" alt="NewsScraperv2pic" src="https://github.com/user-attachments/assets/2f47498d-5cf2-421d-be77-a0fec8e0c970" />

#Email blast example

<img width="507" height="1049" alt="emailblast" src="https://github.com/user-attachments/assets/9359278c-b6c0-4193-8b31-8c6fbbddb8cb" />

## What It Does

1. Fetches articles from 20+ legal tech news sources (RSS feeds + web scraping)
2. Scores each article based on keyword relevance, segment focus, and competitor mentions
3. Curates the top articles into a polished HTML email
4. Opens a pre-addressed Outlook draft ready to send

---

## Two Versions

| Feature | NewsScraperv1 (`ui.py`) | NewsScraperv2 (`ui2.py`) |
|---|---|---|
| UI Framework | tkinter (classic) | CustomTkinter (modern) |
| Look & Feel | Standard Windows widgets | Rounded cards, gradient header, dark log panel |
| Segment Selector | Dropdown | Segmented button |
| Progress Indicator | Status label | Animated progress bar |
| EXE Build Script | `build.bat` | `build_v2.bat` |

Both versions share the same scraping engine (`generate_blast.py`) and settings file.

---

## Features

- **Segment targeting** — Strategic, SML, or International; each segment boosts different keywords
- **Competitor tracking** — checkboxes for iManage, Clio, Filevine, and more; matching articles get flagged
- **Custom investigate term** — type any phrase to give it +30 scoring points (same weight as "netdocuments")
- **Custom email recipient** — override the default To: address without editing code
- **Settings persistence** — segment, email, investigate term, and checkbox selections are saved between sessions (`%APPDATA%\LegalTechScraper\settings.json`)
- **Deduplication** — strips duplicate articles by URL before curating
- **Outlook integration** — generates a styled HTML email and opens it as a draft

---

## Scoring System

Articles are scored out of a possible ~200 points. Higher = more relevant.

| Signal | Points |
|---|---|
| "netdocuments" in title/body | +30 |
| Segment keyword match (Strategic/SML/International) | +20 |
| Competitor mention (per competitor) | +15 |
| Legal tech keyword (e-discovery, matter management, etc.) | +10 |
| Custom investigate term | +30 |
| Recency (published today) | +10 |

---

## News Sources

Includes RSS feeds and scraped pages from:

- Legaltech News, Above the Law, Law Technology Today
- The American Lawyer, Legal IT Insider, ILTA
- Artificial Lawyer, LawSites (Bob Ambrogi), Lawyerist
- Legal Futures, Wolters Kluwer, Thomson Reuters
- Law.com, Relativity Blog, iManage Blog
- Clio Blog, NetDocuments Blog, and more

---

## Getting Started

### Run from Source
```bash
pip install requests beautifulsoup4 feedparser customtkinter
python ui2.py        # v2 (recommended)
# or
python ui.py         # v1
```

### Run the EXE

Download the latest release from the [Releases](../../releases) page and run `NewsScraperv2.exe`. No Python installation required.

---

## Building the EXE
```bat
# v2
build_v2.bat

# v1
build.bat
```

Output lands in `dist\NewsScraperv2.exe` (or `dist\NewsScraper.exe`).

> **Note:** CustomTkinter must be installed at  
> `%APPDATA%\Python\Python314\site-packages\customtkinter`  
> for the v2 build script to find it. Adjust the path in `build_v2.bat` if your Python version differs.

---

## File Structure
```
legaltechnewsscraper/
├── ui.py               # v1 UI (tkinter)
├── ui2.py              # v2 UI (CustomTkinter)
├── generate_blast.py   # Scraping engine, scoring, Outlook integration
├── sites_config.py     # News source definitions (URLs, types, segments)
├── build.bat           # PyInstaller build script for v1
├── build_v2.bat        # PyInstaller build script for v2
└── README.md
```

---

## Customizing Sources

Edit `sites_config.py` to add, remove, or adjust news sources. Each entry specifies:
- `url` — RSS feed or webpage URL
- `type` — `"rss"` or `"scrape"`
- `segments` — which segments this source is relevant to

---

## Customizing Scoring

Edit the `KEYWORDS` and `SEGMENT_KEYWORDS` dictionaries in `generate_blast.py` to tune what scores highly.

---

## Requirements

- Python 3.9+
- `requests`, `beautifulsoup4`, `feedparser`, `customtkinter`
- Microsoft Outlook (desktop) for email draft generation
- Windows (Outlook COM automation is Windows-only)
