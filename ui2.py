"""
ui2.py
------
NetDocuments LegalTech News Scraper  —  v2
Modern desktop UI built with CustomTkinter.

Run:   python ui2.py
Build: build_v2.bat
"""

# PyInstaller / multiprocessing safety
import multiprocessing
multiprocessing.freeze_support()

import csv
import json
import os
import pathlib
import queue
import sys
import threading
import webbrowser
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import feedparser
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Site / blast imports
# ---------------------------------------------------------------------------
try:
    from sites_config import SITES, COMPETITOR_SITES
except ImportError:
    sys.path.insert(0, os.path.dirname(sys.executable))
    from sites_config import SITES, COMPETITOR_SITES

try:
    from generate_blast import (
        curate, build_html, send_via_outlook,
        EMAIL_SUBJECT, MAX_AGE_DAYS, RECIPIENTS,
    )
    _BLAST_AVAILABLE = True
except ImportError:
    MAX_AGE_DAYS = 7
    RECIPIENTS = "your.name@netdocuments.com"
    _BLAST_AVAILABLE = False

# ---------------------------------------------------------------------------
# CustomTkinter global theme
# ---------------------------------------------------------------------------
ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
PRIMARY    = "#1d4ed8"   # blue-700
PRIMARY_H  = "#1e40af"   # blue-800
SUCCESS    = "#15803d"   # green-700
DANGER     = "#dc2626"   # red-600
PURPLE     = "#7c3aed"   # violet-600
PURPLE_H   = "#6d28d9"
TEAL       = "#0369a1"   # sky-700
TEAL_H     = "#075985"
BG         = "#f8fafc"   # slate-50
CARD       = "#ffffff"
FG         = "#1e293b"   # slate-800
MUTED      = "#64748b"   # slate-500
BORDER     = "#e2e8f0"   # slate-200
MONO       = "Consolas"

SEGMENT_COLORS = {
    "Strategic":     (PRIMARY,  PRIMARY_H),
    "SML":           (PURPLE,   PURPLE_H),
    "International": (TEAL,     TEAL_H),
}

# ---------------------------------------------------------------------------
# HTTP headers for scraping
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


# ---------------------------------------------------------------------------
# Scraping utilities  (self-contained copy, avoids importing ui.py)
# ---------------------------------------------------------------------------

def _safe_text(tag) -> str:
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
    pub = entry.get("published_parsed") or entry.get("updated_parsed")
    if pub is None:
        return True
    try:
        pub_dt = datetime(*pub[:6], tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
        return pub_dt >= cutoff
    except (TypeError, ValueError):
        return True


def scrape_one_site_rss(config: dict, log_fn=None) -> list:
    name = config["name"]
    is_competitor = config.get("competitor", False)
    if log_fn:
        log_fn(f"-> Fetching {name}...")
    try:
        feed = feedparser.parse(config["rss"])
    except Exception as exc:
        if log_fn:
            log_fn(f"  X {name}: {exc}")
        return []
    if not feed.entries:
        if log_fn:
            log_fn(f"  ! {name}: 0 entries")
        return []
    scraped_at = datetime.now(timezone.utc).isoformat()
    results, seen, skipped = [], set(), 0
    for entry in feed.entries:
        if not _rss_entry_is_recent(entry):
            skipped += 1
            continue
        title = (entry.get("title") or "").strip()
        url   = (entry.get("link")  or "").strip()
        if not title or not url or url in seen:
            continue
        seen.add(url)
        raw = entry.get("summary") or ""
        if not raw and entry.get("content"):
            raw = entry["content"][0].get("value", "")
        desc = " ".join(
            BeautifulSoup(raw, "html.parser").get_text(separator=" ", strip=True).split()
        )[:300] if raw else ""
        results.append({
            "source": name, "title": title, "url": url,
            "description": desc, "scraped_at": scraped_at,
            "competitor": is_competitor,
        })
    if log_fn:
        msg = f"  OK {name}: {len(results)} articles"
        if skipped:
            msg += f" ({skipped} too old)"
        log_fn(msg)
    return results


def scrape_one_site(config: dict, log_fn=None) -> list:
    if config.get("rss"):
        return scrape_one_site_rss(config, log_fn)
    name     = config["name"]
    base_url = config.get("base_url", "")
    is_competitor = config.get("competitor", False)
    if log_fn:
        log_fn(f"-> Fetching {name}...")
    try:
        resp = requests.get(config["url"], headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        if log_fn:
            log_fn(f"  X {name}: {exc}")
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    scraped_at = datetime.now(timezone.utc).isoformat()
    results, seen = [], set()
    for container in soup.select(config["article_sel"]):
        title_tag = container.select_one(config["title_sel"])
        link_tag  = container.select_one(config["link_sel"])
        if link_tag is None and container.name == "a":
            link_tag = container
        title       = _safe_text(title_tag)
        article_url = _safe_href(link_tag, base_url)
        if not title or not article_url or article_url in seen:
            continue
        seen.add(article_url)
        desc = ""
        if config.get("desc_sel"):
            desc = _safe_text(container.select_one(config["desc_sel"]))
        results.append({
            "source": name, "title": title, "url": article_url,
            "description": desc, "scraped_at": scraped_at,
            "competitor": is_competitor,
        })
    if log_fn:
        log_fn(f"  OK {name}: {len(results)} articles")
    return results


# ---------------------------------------------------------------------------
# User-added sites
# ---------------------------------------------------------------------------

def _user_sites_path() -> str:
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
           else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "user_sites.json")


def _load_user_sites() -> list:
    path = _user_sites_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _load_all_sites() -> list:
    built_in = list(SITES) + list(COMPETITOR_SITES)
    names    = {s["name"] for s in built_in}
    extras   = [s for s in _load_user_sites() if s["name"] not in names]
    return built_in + extras


# ---------------------------------------------------------------------------
# Persistent settings  (%APPDATA%\LegalTechScraper\settings.json)
# ---------------------------------------------------------------------------

def _settings_path() -> pathlib.Path:
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    folder  = pathlib.Path(appdata) / "LegalTechScraper"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "settings.json"


def _load_settings() -> dict:
    p = _settings_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_settings(data: dict) -> None:
    try:
        _settings_path().write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main application  (CustomTkinter)
# ---------------------------------------------------------------------------

class NewsScraperV2(ctk.CTk):

    def __init__(self):
        super().__init__()
        self.title("NetDocuments LegalTech News Scraper  v2")
        self.geometry("960x780")
        self.minsize(760, 580)
        self.configure(fg_color=BG)
        self.resizable(True, True)

        # Runtime state
        self._articles: list = []
        self._running  = False
        self._log_queue:    queue.Queue = queue.Queue()
        self._result_queue: queue.Queue = queue.Queue()
        self._log_visible = False

        # Settings vars
        self._segment_var    = tk.StringVar(value="Strategic")
        self._email_var      = tk.StringVar(value=RECIPIENTS)
        self._investigate_var = tk.StringVar(value="")

        # Site checkbox vars
        self._all_sites  = _load_all_sites()
        self._site_vars  = {s["name"]: tk.BooleanVar(value=True) for s in self._all_sites}

        self._build_ui()
        self._apply_settings(_load_settings())
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_queues()

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _build_ui(self):

        # ── Header bar ───────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color=PRIMARY, corner_radius=0, height=68)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        hdr_inner = ctk.CTkFrame(hdr, fg_color="transparent")
        hdr_inner.pack(side="left", padx=20, pady=10)

        ctk.CTkLabel(
            hdr_inner,
            text="NETDOCUMENTS",
            font=ctk.CTkFont(family="Segoe UI", size=9, weight="bold"),
            text_color="#93c5fd",
        ).pack(anchor="w")
        ctk.CTkLabel(
            hdr_inner,
            text="⚖️  LegalTech News Scraper",
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
            text_color="white",
        ).pack(anchor="w")

        ctk.CTkLabel(
            hdr,
            text=" v2 ",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color="white",
            fg_color="#3b82f6",
            corner_radius=8,
        ).pack(side="right", padx=20)

        # ── Scrollable body ─────────────────────────────────────────────────
        body = ctk.CTkScrollableFrame(self, fg_color=BG, scrollbar_button_color=BORDER,
                                      scrollbar_button_hover_color="#cbd5e1")
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)

        # ── Control card ─────────────────────────────────────────────────────
        ctrl = ctk.CTkFrame(body, fg_color=CARD, corner_radius=14,
                            border_width=1, border_color=BORDER)
        ctrl.pack(fill="x", padx=16, pady=(14, 0))

        # Row: "SELECT SOURCES" + Segment segmented button
        row_top = ctk.CTkFrame(ctrl, fg_color="transparent")
        row_top.pack(fill="x", padx=16, pady=(14, 8))

        ctk.CTkLabel(
            row_top,
            text="SELECT SOURCES",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color=MUTED,
        ).pack(side="left")

        seg_frame = ctk.CTkFrame(row_top, fg_color="transparent")
        seg_frame.pack(side="right")
        ctk.CTkLabel(
            seg_frame,
            text="Segment:",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=MUTED,
        ).pack(side="left", padx=(0, 6))

        self._seg_btn = ctk.CTkSegmentedButton(
            seg_frame,
            values=["Strategic", "SML", "International"],
            variable=self._segment_var,
            command=self._on_segment_change,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            selected_color=PRIMARY,
            selected_hover_color=PRIMARY_H,
            unselected_color=BORDER,
            unselected_hover_color="#cbd5e1",
            text_color=FG,
            text_color_disabled=MUTED,
            fg_color=BORDER,
            corner_radius=8,
            height=32,
        )
        self._seg_btn.pack(side="left")

        # Source checkboxes in two columns
        cb_frame = ctk.CTkFrame(ctrl, fg_color="transparent")
        cb_frame.pack(fill="x", padx=16, pady=(0, 8))
        cb_frame.columnconfigure((0, 1), weight=1)

        news_sites = [s for s in self._all_sites if not s.get("competitor")]
        comp_sites  = [s for s in self._all_sites if s.get("competitor")]

        # Left column — News Sources
        left = ctk.CTkFrame(cb_frame, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nw", padx=(0, 12))
        ctk.CTkLabel(
            left,
            text="News Sources",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=PRIMARY,
        ).pack(anchor="w", pady=(0, 5))

        for site in news_sites:
            ctk.CTkCheckBox(
                left,
                text=site["name"],
                variable=self._site_vars[site["name"]],
                font=ctk.CTkFont(size=12),
                checkbox_width=16, checkbox_height=16,
                corner_radius=4,
                fg_color=PRIMARY, hover_color=PRIMARY_H,
            ).pack(anchor="w", pady=2)

        # Right column — Competitor Blogs
        if comp_sites:
            right = ctk.CTkFrame(cb_frame, fg_color="transparent")
            right.grid(row=0, column=1, sticky="nw", padx=(12, 0))
            ctk.CTkLabel(
                right,
                text="Competitor Blogs",
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=DANGER,
            ).pack(anchor="w", pady=(0, 5))

            for site in comp_sites:
                ctk.CTkCheckBox(
                    right,
                    text=site["name"],
                    variable=self._site_vars[site["name"]],
                    font=ctk.CTkFont(size=12),
                    checkbox_width=16, checkbox_height=16,
                    corner_radius=4,
                    fg_color=DANGER, hover_color="#b91c1c",
                ).pack(anchor="w", pady=2)

        # Divider
        ctk.CTkFrame(ctrl, fg_color=BORDER, height=1).pack(fill="x", padx=16, pady=(4, 12))

        # Email + Investigate row
        fields = ctk.CTkFrame(ctrl, fg_color="transparent")
        fields.pack(fill="x", padx=16, pady=(0, 12))
        fields.columnconfigure(1, weight=1)
        fields.columnconfigure(3, weight=1)

        ctk.CTkLabel(
            fields, text="Email:",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=FG,
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))

        ctk.CTkEntry(
            fields,
            textvariable=self._email_var,
            font=ctk.CTkFont(size=12),
            placeholder_text="you@netdocuments.com",
            height=34, corner_radius=8,
            border_color=BORDER,
        ).grid(row=0, column=1, sticky="ew", padx=(0, 20))

        ctk.CTkLabel(
            fields, text="Investigate:",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=FG,
        ).grid(row=0, column=2, sticky="w", padx=(0, 8))

        invest_inner = ctk.CTkFrame(fields, fg_color="transparent")
        invest_inner.grid(row=0, column=3, sticky="ew")
        invest_inner.columnconfigure(0, weight=1)

        ctk.CTkEntry(
            invest_inner,
            textvariable=self._investigate_var,
            font=ctk.CTkFont(size=12),
            placeholder_text="e.g. clio, harvey, ai contracts…",
            height=34, corner_radius=8,
            border_color=BORDER,
        ).grid(row=0, column=0, sticky="ew")

        ctk.CTkLabel(
            invest_inner,
            text="= 30 pts",
            font=ctk.CTkFont(size=10),
            text_color=MUTED,
        ).grid(row=0, column=1, padx=(8, 0))

        # Run button
        self._run_btn = ctk.CTkButton(
            ctrl,
            text="▶    Run Scraper",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            fg_color=PRIMARY,
            hover_color=PRIMARY_H,
            height=42,
            corner_radius=10,
            command=self._start_scrape,
        )
        self._run_btn.pack(fill="x", padx=16, pady=(0, 16))

        # ── Status bar ───────────────────────────────────────────────────────
        status_card = ctk.CTkFrame(body, fg_color=CARD, corner_radius=10,
                                   border_width=1, border_color=BORDER, height=46)
        status_card.pack(fill="x", padx=16, pady=(10, 0))
        status_card.pack_propagate(False)

        status_inner = ctk.CTkFrame(status_card, fg_color="transparent")
        status_inner.pack(fill="both", expand=True, padx=14, pady=0)

        self._status_lbl = ctk.CTkLabel(
            status_inner,
            text="Status: Ready",
            font=ctk.CTkFont(size=12),
            text_color=FG,
        )
        self._status_lbl.pack(side="left")

        right_grp = ctk.CTkFrame(status_inner, fg_color="transparent")
        right_grp.pack(side="right")

        self._count_lbl = ctk.CTkLabel(
            right_grp,
            text="0 articles",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=FG,
        )
        self._count_lbl.pack(side="left", padx=(0, 12))

        self._export_btn = ctk.CTkButton(
            right_grp,
            text="Export CSV",
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=SUCCESS, hover_color="#166534",
            width=92, height=30, corner_radius=6,
            command=self._export_csv,
            state="disabled",
        )
        self._export_btn.pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            right_grp,
            text="Clear",
            font=ctk.CTkFont(size=11),
            fg_color=BORDER, hover_color="#cbd5e1",
            text_color=FG,
            width=60, height=30, corner_radius=6,
            command=self._clear_results,
        ).pack(side="left")

        # ── Progress bar (packed when running) ───────────────────────────────
        self._progress = ctk.CTkProgressBar(
            body, mode="indeterminate",
            fg_color=BORDER, progress_color=PRIMARY,
            height=4, corner_radius=2,
        )

        # ── Results table ─────────────────────────────────────────────────────
        table_card = ctk.CTkFrame(body, fg_color=CARD, corner_radius=14,
                                  border_width=1, border_color=BORDER)
        table_card.pack(fill="both", expand=False, padx=16, pady=(8, 0))

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "V2.Treeview",
            background=CARD, foreground=FG,
            rowheight=28, fieldbackground=CARD,
            borderwidth=0, font=("Segoe UI", 10),
        )
        style.configure(
            "V2.Treeview.Heading",
            background="#f1f5f9", foreground=MUTED,
            font=("Segoe UI", 9, "bold"), relief="flat",
        )
        style.map(
            "V2.Treeview",
            background=[("selected", "#dbeafe")],
            foreground=[("selected", FG)],
        )

        cols = ("source", "title", "url", "description")
        self._tree = ttk.Treeview(
            table_card, columns=cols, show="headings",
            selectmode="browse", style="V2.Treeview",
        )
        col_cfg = {
            "source":      ("Source",      115, False),
            "title":       ("Title",        275, True),
            "url":         ("URL",          200, True),
            "description": ("Description", 260, True),
        }
        for col, (heading, width, stretch) in col_cfg.items():
            self._tree.heading(col, text=heading, anchor="w")
            self._tree.column(col, width=width, minwidth=60, stretch=stretch, anchor="w")

        vsb = ttk.Scrollbar(table_card, orient="vertical",   command=self._tree.yview)
        hsb = ttk.Scrollbar(table_card, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=4)
        vsb.grid(row=0, column=1, sticky="ns",  pady=4)
        hsb.grid(row=1, column=0, sticky="ew",  padx=(4, 0))
        table_card.rowconfigure(0, weight=1)
        table_card.columnconfigure(0, weight=1)
        self._tree.bind("<Double-1>", self._on_row_click)

        # Hint label
        ctk.CTkLabel(
            body,
            text="Double-click a row to open the article in your browser",
            font=ctk.CTkFont(size=9),
            text_color=MUTED,
        ).pack(anchor="e", padx=20, pady=(2, 0))

        # ── Log section ───────────────────────────────────────────────────────
        self._log_toggle_btn = ctk.CTkButton(
            body,
            text="▶  Show Log",
            font=ctk.CTkFont(size=11),
            fg_color="transparent", hover_color=BORDER,
            text_color=PRIMARY,
            anchor="w",
            width=100, height=26,
            command=self._toggle_log,
        )
        self._log_toggle_btn.pack(anchor="w", padx=16, pady=(8, 0))

        self._log_box = ctk.CTkTextbox(
            body,
            font=ctk.CTkFont(family=MONO, size=10),
            fg_color="#0f172a",
            text_color="#4ade80",
            height=130,
            corner_radius=10,
            state="disabled",
            wrap="none",
        )

    # -----------------------------------------------------------------------
    # Segment
    # -----------------------------------------------------------------------

    def _on_segment_change(self, value=None):
        value = value or self._segment_var.get()
        fg, hov = SEGMENT_COLORS.get(value, (PRIMARY, PRIMARY_H))
        self._run_btn.configure(fg_color=fg, hover_color=hov)
        self._seg_btn.configure(selected_color=fg, selected_hover_color=hov)

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
        self._run_btn.configure(state="disabled", text="⏳  Running…")
        self._export_btn.configure(state="disabled")
        self._progress.pack(fill="x", padx=16, pady=(6, 0))
        self._progress.start()
        self._set_status("Scraping…", PRIMARY)
        self._log("--- Scrape started ---")

        threading.Thread(
            target=self._worker,
            args=(selected,),
            daemon=True,
        ).start()

    def _worker(self, sites: list):
        all_articles: list = []
        for i, site in enumerate(sites, 1):
            self._log_queue.put(("status", f"Scraping {site['name']}… ({i}/{len(sites)})"))
            arts = scrape_one_site(site, log_fn=lambda m: self._log_queue.put(("log", m)))
            all_articles.extend(arts)
        self._result_queue.put(all_articles)

    def _poll_queues(self):
        try:
            while True:
                kind, msg = self._log_queue.get_nowait()
                if kind == "status":
                    self._set_status(msg, PRIMARY)
                else:
                    self._log(msg)
        except queue.Empty:
            pass

        try:
            articles = self._result_queue.get_nowait()
            self._on_scrape_done(articles)
        except queue.Empty:
            pass

        self.after(100, self._poll_queues)

    def _on_scrape_done(self, articles: list):
        self._running = False
        self._progress.stop()
        self._progress.pack_forget()
        self._run_btn.configure(state="normal", text="▶    Run Scraper")
        self._articles = articles

        self._tree.delete(*self._tree.get_children())
        for a in articles:
            self._tree.insert("", "end", values=(
                a["source"], a["title"], a["url"], a.get("description", ""),
            ))

        n = len(articles)
        self._count_lbl.configure(text=f"{n} article{'s' if n != 1 else ''}")

        if n > 0:
            self._set_status(f"Done — {n} articles collected", SUCCESS)
            self._export_btn.configure(state="normal")
            self._log(f"--- Done: {n} articles ---")
            if _BLAST_AVAILABLE:
                threading.Thread(target=self._generate_blast, args=(articles,), daemon=True).start()
        else:
            self._set_status("Done — no articles found", DANGER)
            self._log("--- No articles found ---")

    def _generate_blast(self, articles: list):
        try:
            normalised  = [{**a, "desc": a.get("description", "")} for a in articles]
            segment     = self._segment_var.get()
            email       = self._email_var.get().strip()
            investigate = self._investigate_var.get().strip()
            self._log(f"Building blast… (segment: {segment})")
            top  = curate(normalised, segment=segment, investigate_term=investigate)
            html = build_html(top, normalised, segment=segment)
            now  = datetime.now()
            subj = EMAIL_SUBJECT.format(date=now.strftime(f"%B {now.day}, %Y"))
            send_via_outlook(html, subj, recipients=email)
            self._log("Blast ready — Outlook draft opened on your Desktop.")
        except Exception as exc:
            self._log(f"Blast generation failed: {exc}")

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _set_status(self, msg: str, color: str = FG):
        self._status_lbl.configure(text=f"Status: {msg}", text_color=color)

    def _log(self, msg: str):
        self._log_box.configure(state="normal")
        self._log_box.insert("end", msg + "\n")
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _toggle_log(self):
        self._log_visible = not self._log_visible
        if self._log_visible:
            self._log_box.pack(fill="x", padx=16, pady=(2, 6))
            self._log_toggle_btn.configure(text="▼  Hide Log")
        else:
            self._log_box.pack_forget()
            self._log_toggle_btn.configure(text="▶  Show Log")

    def _export_csv(self):
        if not self._articles:
            messagebox.showinfo("Nothing to export", "Run the scraper first.")
            return
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=f"news_articles_{ts}.csv",
            title="Save articles as CSV",
        )
        if not path:
            return
        cols = ["source", "title", "url", "description", "scraped_at"]
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=cols, extrasaction="ignore").writeheader()
                csv.DictWriter(f, fieldnames=cols, extrasaction="ignore").writerows(self._articles)
            messagebox.showinfo("Saved", f"CSV saved to:\n{path}")
            self._log(f"Exported {len(self._articles)} articles → {path}")
        except OSError as exc:
            messagebox.showerror("Save failed", str(exc))

    def _clear_results(self):
        self._tree.delete(*self._tree.get_children())
        self._articles = []
        self._count_lbl.configure(text="0 articles")
        self._set_status("Ready")
        self._export_btn.configure(state="disabled")
        self._log("--- Cleared ---")

    def _on_row_click(self, _event):
        sel = self._tree.selection()
        if sel:
            vals = self._tree.item(sel[0], "values")
            if len(vals) >= 3 and vals[2]:
                webbrowser.open(vals[2])

    # -----------------------------------------------------------------------
    # Settings persistence
    # -----------------------------------------------------------------------

    def _apply_settings(self, s: dict):
        seg = s.get("segment", "")
        if seg in ("Strategic", "SML", "International"):
            self._segment_var.set(seg)
            self._seg_btn.set(seg)
            self._on_segment_change(seg)
        email = s.get("email", "")
        if email:
            self._email_var.set(email)
        self._investigate_var.set(s.get("investigate", ""))
        sources = s.get("sources", {})
        for name, var in self._site_vars.items():
            if name in sources:
                var.set(bool(sources[name]))

    def _save_current_settings(self):
        _save_settings({
            "segment":     self._segment_var.get(),
            "email":       self._email_var.get(),
            "investigate": self._investigate_var.get(),
            "sources":     {n: v.get() for n, v in self._site_vars.items()},
        })

    def _on_close(self):
        self._save_current_settings()
        self.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = NewsScraperV2()
    app.mainloop()
