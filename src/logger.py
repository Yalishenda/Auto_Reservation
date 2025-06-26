"""
Lightweight CSV logger that rolls over monthly.

Call:
    from logger import log_event
    log_event(module="email_handler",
              event="pdf_downloaded",
              reservation_number=1647075,
              edition=1,
              filename="BIU_PO_ST_V_40032089_1.PDF")
"""

from __future__ import annotations
import csv, datetime, json, os
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from src.utils.paths import REPO_ROOT
except ImportError:
    REPO_ROOT = Path(__file__).resolve().parents[2]

LOG_DIR = REPO_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Master column order
_COLUMNS = [
    "timestamp",
    "module",
    "event",
    "reservation_number",
    "edition",
    "filename",
    "notion_page_id",
    "status_before",
    "status_after",
    "token_usage",
    "message",
]

def _current_log_path() -> Path:
    """ logs/2025-06-reservations.csv """
    now = datetime.datetime.now(datetime.timezone.utc)          # ← fixed
    fname = f"{now:%Y-%m}-reservations.csv"
    return LOG_DIR / fname

def _ensure_header(path: Path) -> None:
    if not path.exists():
        with path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=_COLUMNS).writeheader()

def log_event(
    *,
    module: str,
    event: str,
    reservation_number: Optional[int] = None,
    edition: Optional[int] = None,
    filename: str | None = None,
    notion_page_id: str | None = None,
    status_before: str | None = None,
    status_after: str | None = None,
    token_usage: int | None = None,
    message: str | None = "",
    extra: Dict[str, Any] | None = None,      # for future flexibility
) -> None:
    """
    Append one event row. Missing fields stay blank in CSV.
    """
    row = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc)  # ← fixed
        .isoformat(timespec="seconds"),
        "module": module,
        "event": event,
        "reservation_number": reservation_number or "",
        "edition": edition or "",
        "filename": filename or "",
        "notion_page_id": notion_page_id or "",
        "status_before": status_before or "",
        "status_after": status_after or "",
        "token_usage": token_usage or "",
        "message": message or "",
    }
    if extra:   # stash any unknown keys at the end (JSON-encoded)
        row["message"] = (row["message"] + " | " if row["message"] else "") + json.dumps(extra, ensure_ascii=False)

    path = _current_log_path()
    _ensure_header(path)
    with path.open("a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=_COLUMNS).writerow(row)

    # Optional console echo for dev
    print(f"🪵 log_event: {module}:{event} → {path.name}")


