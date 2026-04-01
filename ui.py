"""
ui.py
-----
Desktop GUI wrapper for the multi-site news scraper.

Run directly:
    python ui.py

Or as a packaged .exe built with build_exe.bat.

Layout
------
  +------------------------------------------------------+
  |  NetDocuments LegalTech News Scraper                  |
  +------------------------------------------------------+
  |  Select sources:      Segment: [Strategic] Add Source |
  |  News Sources                                         |
  |  Artificial Lawyer  Legaltech News                    |
  |  Competitor Blogs                                     |
  |  iManage Blog       Harvey.ai Blog                    |
  |                                                       |
  |  [        Run Scraper        ]                        |
  |  Status: Ready                          0 articles    |
  +------------------------------------------------------+
  |  Source | Title                    | URL             |
  +------------------------------------------------------+
"""

# PyInstaller / multiprocessing safety -- must be before any other imports
import multiprocessing
multiprocessing.freeze_support()

import csv
import json
import os
import pathlib
import queue
import sys
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import feedparser
import requests
from bs4 import BeautifulSoup

# Import site configurations from the same directory
try:
    from sites_config import SITES, COMPETITOR_SITES
except ImportError:
    # If running as a frozen .exe, sites_config may be in the same folder
    sys.path.insert(0, os.path.dirname(sys.executable))
    from sites_config import SITES, COMPETITOR_SITES

# Import blast generation from generate_blast (graceful if missing)
try:
    from generate_blast import curate, build_html, send_via_outlook, EMAIL_SUBJECT, MAX_AGE_DAYS, RECIPIENTS
    _BLAST_AVAILABLE = True
except ImportError:
    MAX_AGE_DAYS = 7
    RECIPIENTS = "your.name@netdocuments.com"
    _BLAST_AVAILABLE = False


# ---------------------------------------------------------------------------
# Scraping logic (self-contained; avoids botasaurus multiprocessing in GUI)
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _safe_text(tag) -> str:
    # separator=" " prevents words merging across inline elements (links, spans)
    if not tag:
        return ""
    return " ".join(tag.get_text(separator=" ", strip=True).split())


def _safe_href(tag, base_url: str) -> str:
    if not tag:
        return ""
    href = tag.get("href", "")
    if not href or href.startswith("#"):
        return ""
    return urljoin(base_url, href) if base_url else href


def _rss_entry_is_recent(entry) -> bool:
    """Return True if the RSS entry is within MAX_AGE_DAYS, or if its date is unknown."""
    pub = entry.get("published_parsed") or entry.get("updated_parsed")
    if pub is None:
        return True  # no date info -- include it
    try:
        pub_dt = datetime(*pub[:6], tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
        return pub_dt >= cutoff
    except (TypeError, ValueError):
        return True  # can't parse date -- include it


def scrape_one_site_rss(config: dict, log_fn=None) -> list[dict]:
    """Fetch and parse an RSS/Atom feed. Returns list of article dicts."""
    name = config["name"]
    rss_url = config["rss"]
    is_competitor = config.get("competitor", False)

    if log_fn:
        log_fn(f"-> Fetching {name}...")

    try:
        feed = feedparser.parse(rss_url)
    except Exception as exc:
        if log_fn:
            log_fn(f"  X {name}: {exc}")
        return []

    if not feed.entries:
        if log_fn:
            log_fn(f"  ! {name}: feed returned 0 entries ({rss_url})")
        return []

    scraped_at = datetime.now(timezone.utc).isoformat()
    results = []
    seen: set[str] = set()
    age_skipped = 0

    for entry in feed.entries:
        # Skip articles older than MAX_AGE_DAYS
        if not _rss_entry_is_recent(entry):
            age_skipped += 1
            continue

        title = (entry.get("title") or "").strip()
        url = (entry.get("link") or "").strip()

        if not title or not url or url in seen:
            continue
        seen.add(url)

        # Description: prefer 'summary', fall back to 'content', strip any HTML tags
        raw_desc = entry.get("summary") or ""
        if not raw_desc and entry.get("content"):
            raw_desc = entry["content"][0].get("value", "")
        # separator=" " + split() prevents words merging across inline HTML elements
        desc = " ".join(
            BeautifulSoup(raw_desc, "html.parser").get_text(separator=" ", strip=True).split()
        )[:300] if raw_desc else ""

        results.append({
            "source":     name,
            "title":      title,
            "url":        url,
            "description": desc,
            "scraped_at": scraped_at,
            "competitor": is_competitor,
        })

    if log_fn:
        total_raw = len(feed.entries)
        kept = len(results)
        if age_skipped > 0:
            log_fn(f"  OK {name}: {total_raw} found / {kept} current (<={MAX_AGE_DAYS}d)")
        else:
            log_fn(f"  OK {name}: {kept} articles (<={MAX_AGE_DAYS}d)")
    return results


def scrape_one_site(config: dict, log_fn=None) -> list[dict]:
    """Scrape a single site -- uses RSS if configured, otherwise HTML."""
    if config.get("rss"):
        return scrape_one_site_rss(config, log_fn)

    name = config["name"]
    url = config["url"]
    base_url = config.get("base_url", "")
    is_competitor = config.get("competitor", False)

    if log_fn:
        log_fn(f"-> Fetching {name}...")

    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        if log_fn:
            log_fn(f"  X {name}: {exc}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    scraped_at = datetime.now(timezone.utc).isoformat()

    containers = soup.select(config["article_sel"])
    if not containers and log_fn:
        log_fn(f"  ! {name}: no containers matched '{config['article_sel']}'")

    results = []
    seen: set[str] = set()

    for container in containers:
        title_tag = container.select_one(config["title_sel"])
        link_tag = container.select_one(config["link_sel"])
        # Fallback: Harvey.ai / Legora use <a> as the card container itself;
        # BeautifulSoup won't find a nested <a> inside <a>, so use the container.
        if link_tag is None and container.name == "a":
            link_tag = container

        title = _safe_text(title_tag)
        article_url = _safe_href(link_tag, base_url)

        if not title or not article_url or article_url in seen:
            continue
        seen.add(article_url)

        desc = ""
        if config.get("desc_sel"):
            desc_tag = container.select_one(config["desc_sel"])
            desc = _safe_text(desc_tag)

        results.append({
            "source":     name,
            "title":      title,
            "url":        article_url,
            "description": desc,
            "scraped_at": scraped_at,
            "competitor": is_competitor,
        })

    if log_fn:
        log_fn(f"  OK {name}: {len(results)} articles")
    return results


# ---------------------------------------------------------------------------
# User-added sites  (persisted in user_sites.json next to the app)
# ---------------------------------------------------------------------------

def _user_sites_path() -> str:
    """Return path to user_sites.json, writable in both Python and .exe modes."""
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
           else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "user_sites.json")


def _load_user_sites() -> list[dict]:
    path = _user_sites_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_user_site(entry: dict) -> None:
    sites = _load_user_sites()
    # Replace if same name already exists
    sites = [s for s in sites if s["name"] != entry["name"]]
    sites.append(entry)
    with open(_user_sites_path(), "w", encoding="utf-8") as f:
        json.dump(sites, f, indent=2, ensure_ascii=False)


def _load_all_sites() -> list[dict]:
    """Return built-in SITES, COMPETITOR_SITES, and any user-added sites."""
    all_built_in = list(SITES) + list(COMPETITOR_SITES)
    built_in_names = {s["name"] for s in all_built_in}
    extras = [s for s in _load_user_sites() if s["name"] not in built_in_names]
    return all_built_in + extras


# ---------------------------------------------------------------------------
# Persistent user settings  (%APPDATA%\LegalTechScraper\settings.json)
# ---------------------------------------------------------------------------

def _settings_path() -> pathlib.Path:
    """Return path to settings.json (auto-creates the folder if needed)."""
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    folder = pathlib.Path(appdata) / "LegalTechScraper"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "settings.json"


def _load_settings() -> dict:
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_settings(data: dict) -> None:
    try:
        _settings_path().write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------

APP_TITLE = "NetDocuments LegalTech News Scraper"
APP_WIDTH = 900
APP_HEIGHT = 720
ACCENT = "#2563EB"       # blue
ACCENT_HOVER = "#1D4ED8"
BG = "#F8FAFC"
FG = "#1E293B"
CARD_BG = "#FFFFFF"
BORDER = "#E2E8F0"
GREEN = "#16A34A"
RED = "#DC2626"
PURPLE = "#7C3AED"
TEAL = "#0369A1"
MONO = ("Consolas", "Courier New", "monospace")

SEGMENT_COLORS = {
    "Strategic":     ACCENT,
    "SML":           PURPLE,
    "International": TEAL,
}
SEGMENT_LABELS = {
    "Strategic":     "Strategic  --  Standard scoring",
    "SML":           "SML  --  Small & Medium Law",
    "International": "International  --  Non-US focus",
}


class NewsScraper(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry(f"{APP_WIDTH}x{APP_HEIGHT}")
        self.minsize(700, 550)
        self.configure(bg=BG)
        self.resizable(True, True)

        # State
        self._articles: list[dict] = []
        self._running = False
        self._log_queue: queue.Queue = queue.Queue()
        self._result_queue: queue.Queue = queue.Queue()

        # Segment selection (affects blast scoring)
        self._segment_var = tk.StringVar(value="Strategic")

        # Email field — who the blast draft is addressed to
        self._email_var = tk.StringVar(value=RECIPIENTS)

        # Investigate field — phrase scored at 30 pts (same as NetDocuments)
        self._investigate_var = tk.StringVar(value="")

        # All sites (built-in + user-added) and their checkbox vars
        self._all_sites: list[dict] = _load_all_sites()
        self._site_vars: dict[str, tk.BooleanVar] = {
            s["name"]: tk.BooleanVar(value=True) for s in self._all_sites
        }

        self._build_ui()
        self._poll_queues()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._apply_settings(_load_settings())

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _build_ui(self):
        self._apply_style()

        # -- Header ----------------------------------------------------------
        header = tk.Frame(self, bg=ACCENT, pady=12)
        header.pack(fill="x")
        tk.Label(
            header, text="NetDocuments LegalTech News Scraper",
            bg=ACCENT, fg="white",
            font=("Segoe UI", 16, "bold"),
        ).pack(side="left", padx=20)

        # -- Controls card ---------------------------------------------------
        ctrl_frame = tk.Frame(self, bg=CARD_BG, bd=0, relief="flat")
        ctrl_frame.pack(fill="x", padx=16, pady=(14, 0))
        self._add_border(ctrl_frame)

        # Row 0: "Select sources" label + segment dropdown + Add Source button
        label_row = tk.Frame(ctrl_frame, bg=CARD_BG)
        label_row.grid(row=0, column=0, columnspan=3, sticky="ew", padx=14, pady=(12, 4))

        tk.Label(
            label_row, text="Select sources to scrape:",
            bg=CARD_BG, fg=FG, font=("Segoe UI", 10, "bold"),
        ).pack(side="left")

        # Add Source button (far right -- packed first so it stays rightmost)
        tk.Button(
            label_row, text="Add Source",
            bg=BG, fg=ACCENT, font=("Segoe UI", 9, "bold"),
            relief="flat", bd=0, padx=8, pady=2, cursor="hand2",
            activebackground=BG, activeforeground=ACCENT_HOVER,
            command=self._open_add_source,
        ).pack(side="right")

        # Segment combobox then label -- both packed side="right".
        # side="right" stacks right-to-left, so: combobox packed first lands
        # left-of-Add-Source; label packed second lands left-of-combobox.
        # Visual order: [Select sources:] ... [Segment:] [Strategic v] [Add Source]
        self._segment_cb = ttk.Combobox(
            label_row,
            textvariable=self._segment_var,
            values=["Strategic", "SML", "International"],
            state="readonly",
            width=13,
            font=("Segoe UI", 9),
        )
        self._segment_cb.pack(side="right", padx=(0, 16))

        self._segment_lbl = tk.Label(
            label_row, text="Segment:",
            bg=CARD_BG, fg=FG, font=("Segoe UI", 9, "bold"),
        )
        self._segment_lbl.pack(side="right", padx=(0, 4))

        self._segment_var.trace_add("write", self._on_segment_change)
        self._on_segment_change()  # set initial button color

        # Checkbox container -- split into News / Competitor sections
        self._cb_frame = tk.Frame(ctrl_frame, bg=CARD_BG)
        self._cb_frame.grid(row=1, column=0, columnspan=2, sticky="w")
        self._rebuild_site_checkboxes()

        # -- Email + Investigate row -----------------------------------------
        field_row = tk.Frame(ctrl_frame, bg=CARD_BG)
        field_row.grid(row=2, column=0, columnspan=3, sticky="ew",
                       padx=14, pady=(8, 4))

        tk.Label(
            field_row, text="Email:",
            bg=CARD_BG, fg=FG, font=("Segoe UI", 9, "bold"),
        ).pack(side="left")
        tk.Entry(
            field_row, textvariable=self._email_var,
            font=("Segoe UI", 9), width=30,
            relief="solid", bd=1,
        ).pack(side="left", padx=(4, 24))

        tk.Label(
            field_row, text="Investigate:",
            bg=CARD_BG, fg=FG, font=("Segoe UI", 9, "bold"),
        ).pack(side="left")
        tk.Entry(
            field_row, textvariable=self._investigate_var,
            font=("Segoe UI", 9), width=22,
            relief="solid", bd=1,
        ).pack(side="left", padx=(4, 0))
        tk.Label(
            field_row, text="(ranks same as NetDocuments)",
            bg=CARD_BG, fg="#94A3B8", font=("Segoe UI", 8),
        ).pack(side="left", padx=(6, 0))

        # Run button (column 2, spans checkbox rows)
        self._run_btn = tk.Button(
            ctrl_frame, text="Run Scraper",
            bg=ACCENT, fg="white",
            font=("Segoe UI", 11, "bold"),
            relief="flat", bd=0, padx=20, pady=8, cursor="hand2",
            command=self._start_scrape,
            activebackground=ACCENT_HOVER, activeforeground="white",
        )
        self._run_btn.grid(row=1, column=2, padx=(24, 14), pady=6, sticky="ew")
        ctrl_frame.columnconfigure(2, weight=1)

        # Status bar inside the card
        status_row = tk.Frame(ctrl_frame, bg=CARD_BG)
        status_row.grid(row=99, column=0, columnspan=4, sticky="ew", padx=14, pady=(8, 12))
        self._status_lbl = tk.Label(
            status_row, text="Status: Ready",
            bg=CARD_BG, fg=FG, font=("Segoe UI", 9),
        )
        self._status_lbl.pack(side="left")
        self._count_lbl = tk.Label(
            status_row, text="0 articles",
            bg=CARD_BG, fg=FG, font=("Segoe UI", 9, "bold"),
        )
        self._count_lbl.pack(side="right")

        # Progress bar
        self._progress = ttk.Progressbar(
            ctrl_frame, mode="indeterminate", style="Blue.Horizontal.TProgressbar",
        )
        self._progress.grid(row=100, column=0, columnspan=4, sticky="ew", padx=14, pady=(0, 10))
        self._progress.grid_remove()   # hidden until running

        # -- Results table ---------------------------------------------------
        table_header = tk.Frame(self, bg=BG)
        table_header.pack(fill="x", padx=16, pady=(10, 2))
        tk.Label(
            table_header, text="Results", bg=BG, fg=FG,
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left")

        table_frame = tk.Frame(self, bg=CARD_BG, bd=0)
        table_frame.pack(fill="both", expand=True, padx=16)
        self._add_border(table_frame)

        cols = ("source", "title", "url", "description")
        self._tree = ttk.Treeview(
            table_frame, columns=cols, show="headings",
            selectmode="browse", style="Results.Treeview",
        )
        col_cfg = {
            "source":      ("Source",      110, False),
            "title":       ("Title",        260, True),
            "url":         ("URL",          220, True),
            "description": ("Description", 260, True),
        }
        for col, (heading, width, stretch) in col_cfg.items():
            self._tree.heading(col, text=heading, anchor="w")
            self._tree.column(col, width=width, minwidth=60, stretch=stretch, anchor="w")

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self._tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        # Click row -> open URL
        self._tree.bind("<Double-1>", self._on_row_double_click)

        # -- Log section (collapsible) ---------------------------------------
        self._log_visible = tk.BooleanVar(value=False)
        log_toggle = tk.Button(
            self, text="Show log",
            bg=BG, fg=ACCENT, relief="flat", bd=0,
            font=("Segoe UI", 9), cursor="hand2",
            command=self._toggle_log,
            activebackground=BG, activeforeground=ACCENT_HOVER,
        )
        log_toggle.pack(anchor="w", padx=16, pady=(6, 0))
        self._log_toggle_btn = log_toggle

        self._log_frame = tk.Frame(self, bg=CARD_BG, bd=0)
        self._log_frame.pack(fill="x", padx=16, pady=(2, 0))
        self._add_border(self._log_frame)
        self._log_text = tk.Text(
            self._log_frame, height=5, state="disabled",
            bg="#0F172A", fg="#94A3B8",
            font=(MONO[0], 9),
            relief="flat", bd=0, wrap="none",
            insertbackground="white",
        )
        self._log_text.pack(fill="x", padx=4, pady=4)
        self._log_frame.pack_forget()  # hidden by default

        # -- Footer buttons --------------------------------------------------
        footer = tk.Frame(self, bg=BG)
        footer.pack(fill="x", padx=16, pady=10)

        self._export_btn = tk.Button(
            footer, text="Export CSV",
            bg=GREEN, fg="white",
            font=("Segoe UI", 10, "bold"),
            relief="flat", bd=0, padx=16, pady=7, cursor="hand2",
            command=self._export_csv,
            activebackground="#15803D", activeforeground="white",
            state="disabled",
        )
        self._export_btn.pack(side="left")

        tk.Button(
            footer, text="Clear",
            bg=BORDER, fg=FG,
            font=("Segoe UI", 10),
            relief="flat", bd=0, padx=16, pady=7, cursor="hand2",
            command=self._clear_results,
            activebackground="#CBD5E1", activeforeground=FG,
        ).pack(side="left", padx=(8, 0))

        tk.Label(
            footer, text="Double-click a row to open the article in your browser",
            bg=BG, fg="#94A3B8", font=("Segoe UI", 8),
        ).pack(side="right")

    # -----------------------------------------------------------------------
    # Settings persistence
    # -----------------------------------------------------------------------

    def _apply_settings(self, settings: dict) -> None:
        """Restore segment, email, investigate, and checkbox states from saved settings."""
        seg = settings.get("segment", "")
        if seg in ("Strategic", "SML", "International"):
            self._segment_var.set(seg)
        email = settings.get("email", "")
        if email:
            self._email_var.set(email)
        self._investigate_var.set(settings.get("investigate", ""))
        sources = settings.get("sources", {})
        for name, var in self._site_vars.items():
            if name in sources:
                var.set(bool(sources[name]))

    def _save_current_settings(self) -> None:
        """Write current segment, email, investigate, and checkbox states to disk."""
        data = {
            "segment": self._segment_var.get(),
            "email":   self._email_var.get(),
            "investigate": self._investigate_var.get(),
            "sources": {name: var.get() for name, var in self._site_vars.items()},
        }
        _save_settings(data)

    def _on_close(self) -> None:
        """Save settings then close the window."""
        self._save_current_settings()
        self.destroy()

    # -----------------------------------------------------------------------
    # Segment selector
    # -----------------------------------------------------------------------

    def _on_segment_change(self, *_):
        """Update the run button color when segment changes."""
        seg = self._segment_var.get()
        color = SEGMENT_COLORS.get(seg, ACCENT)
        hover = color

        if hasattr(self, "_run_btn"):
            self._run_btn.configure(bg=color, activebackground=hover)

    # -----------------------------------------------------------------------
    # Dynamic source management
    # -----------------------------------------------------------------------

    def _rebuild_site_checkboxes(self):
        """Clear and redraw the checkbox grid, split into News Sources and Competitor Blogs."""
        for w in self._cb_frame.winfo_children():
            w.destroy()

        news_sites = [s for s in self._all_sites if not s.get("competitor")]
        comp_sites  = [s for s in self._all_sites if s.get("competitor")]

        grid_row = 0

        # -- News Sources section --------------------------------------------
        tk.Label(
            self._cb_frame, text="News Sources",
            bg=CARD_BG, fg=ACCENT, font=("Segoe UI", 9, "bold"),
        ).grid(row=grid_row, column=0, columnspan=2, sticky="w", padx=14, pady=(8, 2))
        grid_row += 1

        for idx, site in enumerate(news_sites):
            name = site["name"]
            if name not in self._site_vars:
                self._site_vars[name] = tk.BooleanVar(value=True)
            cb = ttk.Checkbutton(
                self._cb_frame, text=name,
                variable=self._site_vars[name],
                style="Site.TCheckbutton",
            )
            cb.grid(row=grid_row + idx // 2, column=idx % 2, sticky="w", padx=14, pady=2)
        grid_row += (len(news_sites) + 1) // 2

        # -- Competitor Blogs section ----------------------------------------
        if comp_sites:
            tk.Label(
                self._cb_frame, text="Competitor Blogs",
                bg=CARD_BG, fg=RED, font=("Segoe UI", 9, "bold"),
            ).grid(row=grid_row, column=0, columnspan=2, sticky="w", padx=14, pady=(10, 2))
            grid_row += 1

            for idx, site in enumerate(comp_sites):
                name = site["name"]
                if name not in self._site_vars:
                    self._site_vars[name] = tk.BooleanVar(value=True)
                cb = ttk.Checkbutton(
                    self._cb_frame, text=name,
                    variable=self._site_vars[name],
                    style="CompSite.TCheckbutton",
                )
                cb.grid(row=grid_row + idx // 2, column=idx % 2, sticky="w", padx=14, pady=2)

    def _refresh_site_list(self):
        """Reload all sites (built-in + user) and rebuild the checkboxes."""
        self._all_sites = _load_all_sites()
        self._rebuild_site_checkboxes()

    def _open_add_source(self):
        AddSourceDialog(self)

    def _apply_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure("Results.Treeview",
                        background=CARD_BG, foreground=FG,
                        rowheight=26, fieldbackground=CARD_BG,
                        borderwidth=0, font=("Segoe UI", 9))
        style.configure("Results.Treeview.Heading",
                        background=BORDER, foreground=FG,
                        font=("Segoe UI", 9, "bold"), relief="flat")
        style.map("Results.Treeview",
                  background=[("selected", "#DBEAFE")],
                  foreground=[("selected", FG)])

        style.configure("Site.TCheckbutton",
                        background=CARD_BG, foreground=FG,
                        font=("Segoe UI", 10))

        # Competitor checkboxes in a slightly muted red
        style.configure("CompSite.TCheckbutton",
                        background=CARD_BG, foreground="#7F1D1D",
                        font=("Segoe UI", 10))

        style.configure("Blue.Horizontal.TProgressbar",
                        troughcolor=BORDER, background=ACCENT, thickness=4)

    @staticmethod
    def _add_border(frame: tk.Frame):
        """Give a frame a subtle rounded border using a 1px highlight."""
        frame.configure(highlightbackground=BORDER, highlightthickness=1)

    # -----------------------------------------------------------------------
    # Scraping
    # -----------------------------------------------------------------------

    def _start_scrape(self):
        selected = [s for s in self._all_sites if self._site_vars[s["name"]].get()]
        if not selected:
            messagebox.showwarning("No sources selected",
                                   "Please tick at least one source before running.")
            return

        self._running = True
        self._run_btn.config(state="disabled", text="Running...")
        self._export_btn.config(state="disabled")
        self._progress.grid()
        self._progress.start(12)
        self._set_status("Scraping...", color=ACCENT)
        self._log("--- Scrape started ---")

        thread = threading.Thread(
            target=self._worker,
            args=(selected,),
            daemon=True,
        )
        thread.start()

    def _worker(self, sites: list[dict]):
        """Background thread: scrape sites and push results into queues."""
        all_articles: list[dict] = []
        for idx, site in enumerate(sites, 1):
            self._log_queue.put(("status", f"Scraping {site['name']}... ({idx}/{len(sites)})"))
            articles = scrape_one_site(site, log_fn=lambda msg: self._log_queue.put(("log", msg)))
            all_articles.extend(articles)

        self._result_queue.put(all_articles)

    # -----------------------------------------------------------------------
    # Queue polling (runs on main thread via after())
    # -----------------------------------------------------------------------

    def _poll_queues(self):
        # Drain log queue
        try:
            while True:
                kind, msg = self._log_queue.get_nowait()
                if kind == "status":
                    self._set_status(msg, color=ACCENT)
                elif kind == "log":
                    self._log(msg)
        except queue.Empty:
            pass

        # Check for finished results
        try:
            articles = self._result_queue.get_nowait()
            self._on_scrape_done(articles)
        except queue.Empty:
            pass

        self.after(100, self._poll_queues)

    def _on_scrape_done(self, articles: list[dict]):
        self._running = False
        self._progress.stop()
        self._progress.grid_remove()
        self._run_btn.config(state="normal", text="Run Scraper")

        self._articles = articles

        # Populate table
        self._tree.delete(*self._tree.get_children())
        for art in articles:
            self._tree.insert("", "end", values=(
                art["source"],
                art["title"],
                art["url"],
                art.get("description", ""),
            ))

        n = len(articles)
        self._count_lbl.config(text=f"{n} article{'s' if n != 1 else ''}")

        if n > 0:
            self._set_status(f"Done -- {n} articles collected", color=GREEN)
            self._export_btn.config(state="normal")
            self._log(f"--- Scrape complete: {n} articles ---")
            if _BLAST_AVAILABLE:
                threading.Thread(target=self._generate_blast, args=(articles,), daemon=True).start()
        else:
            self._set_status("Done -- no articles found. Check selectors in sites_config.py", color=RED)
            self._log("--- No articles found ---")

    def _generate_blast(self, articles: list[dict]):
        """Curate top articles, build HTML email, and open as Outlook draft.

        Articles with competitor=True are automatically routed to the
        Competitor Watch section by build_html(); curate() skips them.
        The selected segment controls which scoring weights are applied.
        """
        try:
            # generate_blast uses 'desc'; ui.py uses 'description' -- normalise
            normalised = [
                {**a, "desc": a.get("description", "")}
                for a in articles
            ]
            segment     = self._segment_var.get()
            email       = self._email_var.get().strip()
            investigate = self._investigate_var.get().strip()
            self._log(f"Building news blast... (segment: {segment})")
            top  = curate(normalised, segment=segment, investigate_term=investigate)
            html = build_html(top, normalised, segment=segment)
            _now = datetime.now()
            subj = EMAIL_SUBJECT.format(date=_now.strftime(f"%B {_now.day}, %Y"))
            send_via_outlook(html, subj, recipients=email)
            self._log("Blast ready -- Outlook draft opened on your Desktop.")
        except Exception as exc:
            self._log(f"Blast generation failed: {exc}")

    # -----------------------------------------------------------------------
    # Export
    # -----------------------------------------------------------------------

    def _export_csv(self):
        if not self._articles:
            messagebox.showinfo("Nothing to export", "Run the scraper first.")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"news_articles_{timestamp}.csv"

        filepath = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=default_name,
            title="Save articles as CSV",
        )
        if not filepath:
            return  # user cancelled

        cols = ["source", "title", "url", "description", "scraped_at"]
        try:
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(self._articles)
            messagebox.showinfo("Saved", f"CSV saved to:\n{filepath}")
            self._log(f"Exported {len(self._articles)} articles -> {filepath}")
        except OSError as exc:
            messagebox.showerror("Save failed", str(exc))

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _clear_results(self):
        self._tree.delete(*self._tree.get_children())
        self._articles = []
        self._count_lbl.config(text="0 articles")
        self._set_status("Ready")
        self._export_btn.config(state="disabled")
        self._log("--- Cleared ---")

    def _set_status(self, msg: str, color: str = FG):
        self._status_lbl.config(text=f"Status: {msg}", fg=color)

    def _log(self, msg: str):
        """Append a line to the log widget (thread-safe via queue polling)."""
        self._log_text.config(state="normal")
        self._log_text.insert("end", msg + "\n")
        self._log_text.see("end")
        self._log_text.config(state="disabled")

    def _toggle_log(self):
        if self._log_visible.get():
            self._log_frame.pack_forget()
            self._log_toggle_btn.config(text="Show log")
            self._log_visible.set(False)
        else:
            self._log_frame.pack(fill="x", padx=16, pady=(2, 0))
            self._log_toggle_btn.config(text="Hide log")
            self._log_visible.set(True)

    def _on_row_double_click(self, _event):
        """Open the selected article URL in the default browser."""
        selection = self._tree.selection()
        if not selection:
            return
        values = self._tree.item(selection[0], "values")
        if len(values) >= 3 and values[2]:
            import webbrowser
            webbrowser.open(values[2])


# ---------------------------------------------------------------------------
# Add Source dialog
# ---------------------------------------------------------------------------

class AddSourceDialog(tk.Toplevel):
    """
    Modal dialog for adding a new news source.

    Flow:
      1. User enters a name and any URL (website or RSS feed).
      2. "Test Connection" auto-detects RSS vs HTML and shows a live article count.
      3. For HTML sites, CSS selector fields appear for fine-tuning.
      4. "Save Source" writes to user_sites.json and refreshes the main window.
    """

    def __init__(self, parent: NewsScraper):
        super().__init__(parent)
        self._parent = parent
        self.title("Add News Source")
        self.geometry("520x540")
        self.minsize(480, 460)
        self.configure(bg=BG)
        self.resizable(False, True)
        self.grab_set()   # block the main window until closed

        self._detected: str = ""   # "rss" | "html" | ""
        self._build()
        self.after(100, lambda: self._name_entry.focus_set())

    # -----------------------------------------------------------------------
    # Layout
    # -----------------------------------------------------------------------

    def _build(self):
        # Header
        hdr = tk.Frame(self, bg=ACCENT, pady=10)
        hdr.pack(fill="x")
        tk.Label(hdr, text="Add News Source",
                 bg=ACCENT, fg="white", font=("Segoe UI", 13, "bold"),
                 ).pack(side="left", padx=16)

        # Scrollable body
        body = tk.Frame(self, bg=BG, padx=18, pady=14)
        body.pack(fill="both", expand=True)

        def lbl(text, row):
            tk.Label(body, text=text, bg=BG, fg=FG,
                     font=("Segoe UI", 9, "bold")
                     ).grid(row=row, column=0, sticky="w", pady=(6, 1))

        def entry(var, row):
            e = tk.Entry(body, textvariable=var, font=("Segoe UI", 10), width=46)
            e.grid(row=row, column=0, sticky="ew", pady=(0, 2))
            return e

        # Name
        lbl("Source name", 0)
        self._name_var = tk.StringVar()
        self._name_entry = entry(self._name_var, 1)

        # URL
        lbl("URL  --  paste a website address or an RSS feed link", 2)
        self._url_var = tk.StringVar()
        entry(self._url_var, 3)

        # Test button
        self._test_btn = tk.Button(
            body, text="Test Connection",
            bg=ACCENT, fg="white", font=("Segoe UI", 10, "bold"),
            relief="flat", bd=0, padx=12, pady=6, cursor="hand2",
            activebackground=ACCENT_HOVER, activeforeground="white",
            command=self._test,
        )
        self._test_btn.grid(row=4, column=0, sticky="w", pady=(10, 4))

        # Result label
        self._result_lbl = tk.Label(body, text="", bg=BG, fg=FG,
                                    font=("Segoe UI", 9),
                                    wraplength=460, justify="left")
        self._result_lbl.grid(row=5, column=0, sticky="w", pady=(0, 4))

        # HTML selector fields (hidden until needed)
        self._html_frame = tk.LabelFrame(
            body, text="CSS Selectors  (auto-filled with common defaults)",
            bg=BG, fg=FG, font=("Segoe UI", 9, "bold"), padx=10, pady=8,
        )
        sel_fields = [
            ("Article container",              "_sel_article", "article, div.post"),
            ("Title selector",                 "_sel_title",   "h2 a, h3 a"),
            ("Link selector",                  "_sel_link",    "h2 a, h3 a"),
            ("Description selector (optional)","_sel_desc",    "p"),
            ("Base URL for relative links",    "_sel_base",    ""),
        ]
        for i, (label, attr, default) in enumerate(sel_fields):
            tk.Label(self._html_frame, text=label, bg=BG, fg=FG,
                     font=("Segoe UI", 8)
                     ).grid(row=i * 2, column=0, sticky="w")
            var = tk.StringVar(value=default)
            setattr(self, attr, var)
            tk.Entry(self._html_frame, textvariable=var,
                     font=("Segoe UI", 9), width=44
                     ).grid(row=i * 2 + 1, column=0, sticky="ew", pady=(0, 4))
        self._html_frame.columnconfigure(0, weight=1)

        body.columnconfigure(0, weight=1)

        # Footer
        footer = tk.Frame(self, bg=BG, padx=18, pady=10)
        footer.pack(fill="x", side="bottom")

        tk.Button(
            footer, text="Cancel",
            bg=BORDER, fg=FG, font=("Segoe UI", 10),
            relief="flat", bd=0, padx=14, pady=6, cursor="hand2",
            activebackground="#CBD5E1", activeforeground=FG,
            command=self.destroy,
        ).pack(side="left")

        self._save_btn = tk.Button(
            footer, text="Save Source",
            bg=GREEN, fg="white", font=("Segoe UI", 10, "bold"),
            relief="flat", bd=0, padx=14, pady=6, cursor="hand2",
            activebackground="#15803D", activeforeground="white",
            state="disabled",
            command=self._save,
        )
        self._save_btn.pack(side="right")

    # -----------------------------------------------------------------------
    # Test logic
    # -----------------------------------------------------------------------

    def _test(self):
        url = self._url_var.get().strip()
        if not url:
            self._result_lbl.config(text="Please enter a URL first.", fg=RED)
            return

        self._test_btn.config(state="disabled", text="Testing...")
        self._result_lbl.config(text="Connecting...", fg=FG)
        self._html_frame.grid_remove()
        self._save_btn.config(state="disabled")
        self.update()

        # Run test in a background thread so the dialog stays responsive
        threading.Thread(target=self._run_test, args=(url,), daemon=True).start()

    def _run_test(self, url: str):
        # 1. Try RSS
        try:
            feed = feedparser.parse(url)
            if feed.entries:
                n = len(feed.entries)
                self.after(0, self._on_rss_ok, n)
                return
        except Exception:
            pass

        # 2. Try plain HTTP
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            self.after(0, self._on_html_ok)
        except Exception as exc:
            self.after(0, self._on_error, str(exc))

    def _on_rss_ok(self, count: int):
        self._detected = "rss"
        self._result_lbl.config(
            text=f"RSS feed detected -- {count} articles found. Ready to save!",
            fg=GREEN,
        )
        self._html_frame.grid_remove()
        self._save_btn.config(state="normal")
        self._test_btn.config(state="normal", text="Test Connection")

    def _on_html_ok(self):
        self._detected = "html"
        self._result_lbl.config(
            text="Page loaded (no RSS detected). "
                 "Adjust the CSS selectors below if needed, then save.",
            fg=ACCENT,
        )
        self._html_frame.grid(row=6, column=0, sticky="ew", pady=(4, 4))
        self._save_btn.config(state="normal")
        self._test_btn.config(state="normal", text="Test Connection")

    def _on_error(self, msg: str):
        self._detected = ""
        self._result_lbl.config(text=f"Could not connect: {msg}", fg=RED)
        self._save_btn.config(state="disabled")
        self._test_btn.config(state="normal", text="Test Connection")

    # -----------------------------------------------------------------------
    # Save
    # -----------------------------------------------------------------------

    def _save(self):
        name = self._name_var.get().strip()
        url  = self._url_var.get().strip()

        if not name or not url:
            messagebox.showwarning("Missing fields",
                                   "Please fill in both Name and URL.", parent=self)
            return

        if self._detected == "rss":
            entry = {
                "name": name, "url": url, "rss": url,
                "article_sel": None, "title_sel": None,
                "link_sel": None, "desc_sel": None, "base_url": "",
            }
        else:
            desc_sel = self._sel_desc.get().strip() or None
            entry = {
                "name": name, "url": url,
                "article_sel": self._sel_article.get().strip() or "article",
                "title_sel":   self._sel_title.get().strip()   or "h2 a",
                "link_sel":    self._sel_link.get().strip()     or "h2 a",
                "desc_sel":    desc_sel,
                "base_url":    self._sel_base.get().strip(),
            }

        _save_user_site(entry)
        self._parent._refresh_site_list()
        messagebox.showinfo("Source added",
                            f"'{name}' has been added to your sources!", parent=self)
        self.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = NewsScraper()
    app.mainloop()
