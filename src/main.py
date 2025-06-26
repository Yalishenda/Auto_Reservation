"""
src/main.py  –  End-to-end reservation pipeline
------------------------------------------------
1. (optional)   Fetch e-mails → download PDFs to downloads/
2.              For every PDF (mail or local) do:
      • OCR / parse → full_text  (pdf_reader)
      • GPT extract → dict      (text_analyzer)
      • Decide create / update  (notion_handler)
3.              Log every step and send Telegram alerts (notifier)
4. Once per run send a digest for today / tomorrow (notifier)

Run examples
------------
# Normal hourly run (scan just unread mail)
poetry run python -m src.main

# Re-scan ALL mail (read+unread), process at most 5 messages
poetry run python -m src.main --state all --max 5

# Ignore Gmail – process whatever PDFs are already in downloads/
poetry run python -m src.main --from-downloads
"""

from __future__ import annotations
import argparse, traceback, zoneinfo
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

# ── project modules ──────────────────────────────────────────────────────────
from src.logger import log_event                 # CSV logger
from src.email_handler import fetch_new_emails   # Gmail downloader
from src.pdf_reader import extract_pdf_data      # Camelot + pdfplumber
from src.text_analyzer import analyze_reservation_text    # OpenAI extraction
from src.notion_handler import (                 # CRUD for Notion DB
    search_notion_by_reservation_number,
    get_existing_page_status,
    get_future_reservations,
    create_notion_entry,
    update_notion_entry,
)
from src.notifier import notify_daily_digest     # Telegram daily digest
# (notifier.notify_change is already called in notion_handler)

# ── constants ────────────────────────────────────────────────────────────────
IL_TZ = zoneinfo.ZoneInfo("Asia/Jerusalem")
TERMINAL_STATUSES = {"closed", "cancelled", "invoice_sent", "paid"}


# ═════════════════════════════════ helper functions ══════════════════════════
def process_pdf(pdf_path: Path, res_num: int, edition: int) -> None:
    """
    Process ONE PDF end-to-end.

    Parameters
    ----------
    pdf_path : Path   – local .PDF file
    res_num  : int    – reservation (booking) number
    edition  : int    – edition number (0 / 1 / …)

    Notes
    -----
    • GPT extraction gives us *all* fields except we trust the e-mail-parsed
      reservation / edition instead of GPT’s guess.
    • If page exists with TERMINAL status we skip to avoid duplicates.
    • Logs every success / skip / error.
    • Early exit if page already exists with same/greater edition & terminal status.
    • If GPT marks status == "cancelled", keep only minimal fields.
    """
    log_event(module="main", event="start_pdf",
              reservation_number=res_num, edition=edition,
              filename=pdf_path.name)

    # ---------- EARLY DUPLICATE / TERMINAL CHECK ----------
    page_id, stored_ed = search_notion_by_reservation_number(res_num)
    status_now = get_existing_page_status(res_num)

    if page_id and edition <= stored_ed and status_now in TERMINAL_STATUSES:
        log_event(module="main", event="skip_terminal",
                  reservation_number=res_num, edition=edition,
                  message=f"status={status_now} storedEd={stored_ed}")
        return

    try:
        # ---------- 1) Parse PDF + GPT ----------
        full_text = extract_pdf_data(pdf_path)["full_text"]
        gpt = analyze_reservation_text(full_text)

        # Compare GPT reservation with filename / email value
        if int(gpt["reservation_number"]) != res_num:
            log_event(module="main", event="res_num_mismatch",
                      reservation_number=res_num, edition=edition,
                      message=f"GPT={gpt['reservation_number']}")

        # Enforce authoritative values
        gpt["reservation_number"] = res_num
        gpt["edition"] = edition

        # ---------- 2) Minimal dict for cancellations ----------
        if gpt["status"] == "cancelled":
            gpt = {
                "reservation_number": res_num,
                "edition": edition,
                "status": "cancelled",
            }

        # ---------- 3) Create or Update ----------
        if page_id is None:  # create
            create_notion_entry(gpt)
        else:  # maybe update
            if edition > stored_ed:
                update_notion_entry(page_id, gpt)
            else:
                log_event(module="main", event="skip_old_edition",
                          reservation_number=res_num, edition=edition,
                          message=f"stored={stored_ed}")
                return

        log_event(module="main", event="pdf_ok",
                  reservation_number=res_num, edition=edition)

    except Exception:
        log_event(module="main", event="pdf_error",
                  reservation_number=res_num,
                  message=traceback.format_exc())


def build_daily_digest() -> None:
    """
    Query Notion for booked pages with status 'future_order'
    and date == today|tomorrow. Send one Telegram digest.
    """
    today     = datetime.now(IL_TZ).date()
    tomorrow  = today + timedelta(days=1)

    rows: List[Dict] = []
    for pg in get_future_reservations():
        props = pg["properties"]
        try:
            res_date = datetime.fromisoformat(
                props["date"]["date"]["start"]
            ).date()
        except Exception:
            continue

        if res_date not in (today, tomorrow):
            continue

        rows.append({
            "booking_num": props["booking_num"]["title"][0]["plain_text"],
            "order_limit": props["order_limit"]["number"] or 0,
            "faculty":     props["faculty"]["select"]["name"]
                           if props["faculty"]["select"] else "",
            "date":        res_date.strftime("%d/%m/%Y"),
            "reserved_table": (
                props["setting"]["select"]["name"] == "הגשה"
                if props["setting"]["select"] else False),
        })

    notify_daily_digest(rows)
    log_event(module="main", event="digest_sent",
              message=f"{len(rows)} rows")


# ═══════════════════════════ CLI + orchestrator ══════════════════════════════
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--state", choices=("unread", "read", "all"), default="unread",
                   help="Which Gmail messages to scan.")
    p.add_argument("--max", type=int, default=20,
                   help="Limit PDFs processed (0 = unlimited).")
    p.add_argument("--digest-only", action="store_true",
                   help="Skip mail/PDF processing; send digest only.")
    p.add_argument("--from-downloads", action="store_true",
                   help="Process every *.pdf in downloads/ instead of Gmail.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    log_event(module="main", event="run_start", message=str(vars(args)))

    try:
        # ----------------------- step A: gather PDFs -----------------------
        pdf_jobs: List[Tuple[Path, int, int]] = []  # (path, res#, edition)

        if not args.digest_only:
            if args.from_downloads:
                # ⇢ derive res#/edition from filename ..._<num>_<ed>.PDF
                for p in Path("downloads").glob("*.pdf"):
                    try:
                        res = int(p.stem.split("_")[-2])
                        ed  = int(p.stem.split("_")[-1])
                        pdf_jobs.append((p, res, ed))
                    except Exception:
                        log_event(module="main", event="name_parse_fail",
                                  filename=p.name)
            else:
                # ⇢ pull from Gmail
                mails = fetch_new_emails(email_state=args.state)
                if args.max:
                    mails = mails[: args.max]
                for m in mails:
                    pdf_jobs.append((
                        Path(m["file_path"]),
                        m["reservation_number"],
                        m["edition"],
                    ))

        # ----------------------- step B: process PDFs ----------------------
        for path, res, ed in pdf_jobs:
            process_pdf(path, res, ed)

        # ----------------------- step C: daily digest ----------------------
        build_daily_digest()

        log_event(module="main", event="run_ok")

    except Exception:
        log_event(module="main", event="pipeline_error",
                  message=traceback.format_exc())
        raise


if __name__ == "__main__":
    main()