"""
PDF extraction utilities for the reservation-automation project.

* Camelot-py (stream flavour)          → captures rectangular tables
* pdfplumber                           → grabs *all* page text
* BiDi preview helpers                 → render Hebrew RTL correctly *for console*
   (the raw text is kept in logical order for GPT).

Changes 2025-06-22
------------------
* `extract_full_text()` now **cuts off** the repetitive footer that always begins
  with “• לבירורים בנושא הזמנה …”, so GPT won’t waste tokens on it.
* Console preview shows whether truncation happened.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Any
import re
import json
import camelot           # camelot-py
import pdfplumber
import pandas as pd
from bidi.algorithm import get_display
from openpyxl.styles.builtins import output

# ───────── paths ─────────
try:
    from src.utils.paths import REPO_ROOT, SECRETS_PATH
except ImportError:
    REPO_ROOT = Path(__file__).resolve().parents[2]
    SECRETS_PATH = REPO_ROOT / "config" / "secrets.json"
DOWNLOADS_DIR = REPO_ROOT / "downloads"
with SECRETS_PATH.open(encoding="utf-8") as f:
    CONFIG = json.load(f)

RESTAURANT_EMAIL = CONFIG.get("business_email", "").strip().lower()

# ───────── RTL helpers (console only) ─────────
def _rtl(s: str) -> str:
    return get_display(s, base_dir="R")

def _preview_df(df: pd.DataFrame) -> pd.DataFrame:
    return df.apply(lambda col: col.map(lambda v: _rtl(str(v))))

# ───────── improved footer cut pattern ─────────
STOP_RE = re.compile(r"לבירורים\s*בנושא\s*הזמנה", re.UNICODE)
# ───────── extraction ─────────
def extract_tables(pdf_path: Path) -> pd.DataFrame:
    tables = camelot.read_pdf(str(pdf_path), pages="all", flavor="stream", edge_tol=300)
    return pd.concat([t.df for t in tables], ignore_index=True) if tables.n else pd.DataFrame()

def extract_full_text(pdf_path: Path) -> str:
    lines: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for ln in text.splitlines():
                lines.append(ln)

    # ── Remove header: drop first 2 lines ──
    if len(lines) > 2:
        lines = lines[2:]

    # ── Remove lines containing restaurant email ──
    lines = [ln for ln in lines if RESTAURANT_EMAIL not in ln.lower()]

    clean: list[str] = []
    truncated = False
    for ln in lines:
        visual_line = re.sub(r"[^\S\r\n]+", " ", ln).strip()
        logical_line = get_display(visual_line)

        if STOP_RE.search(logical_line):
            truncated = True
            #print("✂️  Footer removed starting at:",
            #      _rtl(logical_line[:40]), "...")
            break
        clean.append(visual_line.strip())

    # Final visual formatting
    fixed_lines = [_rtl(ln) for ln in clean]
    return "\n".join(fixed_lines)

def extract_pdf_data(pdf_path: Path) -> Dict[str, Any]:
    return {"dataframe": extract_tables(pdf_path),
            "full_text": extract_full_text(pdf_path)}
