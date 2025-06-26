"""
Gmail interface for the reservation-automation project.

Responsibilities
----------------
1. Connect to Gmail via OAuth2 (token cached in `config/token.json`).
2. List **unread** messages with PDF attachments from `allowed_senders`.
3. Parse reservation-number and edition from subject/body (Hebrew patterns).
4. Call `notion_handler.search_notion_by_reservation_number` to see whether
   we already have that reservation and, if so, which edition is stored.
5. Download the PDF **only** if the incoming edition is newer.
6. Leave the *first N* newest unread e-mails untouched so they can be shown
   live in class (`RESERVE_FOR_DEMO = 10`).
7. Return a list of dicts with file paths and metadata.

Fetch reservation PDFs from Gmail.

Key points
----------
* Adjustable `EMAIL_STATE` – 'unread', 'read', or 'all'.
* Downloads PDFs only when the incoming edition is newer than what
  already exists in Notion.
* Skips files already present in <repo>/downloads/.
* Leaves the newest RESERVE_FOR_DEMO messages untouched for live demos.
"""

from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from src.utils.paths import REPO_ROOT, SECRETS_PATH
from src.notion_handler import search_notion_by_reservation_number
from src.logger import log_event

# ───────────────────────────────  CONFIG  ──────────────────────────────── #

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

EMAIL_STATE = "unread"         # 'unread' | 'read' | 'all'
MAX_RESULTS = 100              # Gmail fetch limit
RESERVE_FOR_DEMO = 0          # keep newest N messages untouched (only if 'unread')

DOWNLOAD_DIR = REPO_ROOT / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Regex for reservation & edition
RE_RES_INFO = re.compile(
    r"(?:מס[.\s-]*)?(\d{6,})"          # reservation number
    r".*?"
    r"מהדורה(?:\s*מס[.\s]*)?\s*(\d+)",  # edition number
    re.DOTALL,
)

# ───────────────────────────  SECRETS / PATHS  ─────────────────────────── #

secrets: Dict[str, Any] = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))

ALLOWED_SENDERS = {addr.lower() for addr in secrets["allowed_senders"]}
CLIENT_SECRET = REPO_ROOT / "config" / secrets["gmail_secret_file"]
TOKEN_PATH = REPO_ROOT / "config" / secrets["gmail_token_file"]

# ───────────────────────────  AUTH UTIL  ────────────────────────────────── #


def _get_gmail_creds() -> Credentials:
    creds: Credentials | None = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")

    return creds


# ───────────────────────────  MAIN FUNCTION  ────────────────────────────── #


def fetch_new_emails(
    max_results: int = MAX_RESULTS, email_state: str = EMAIL_STATE
) -> List[Dict[str, Any]]:
    """
    Fetches reservation emails, downloads newer-edition PDFs, returns metadata.

    Returns
    -------
    List[dict] with keys:
        file_path, reservation_number, edition, gmail_msg_id,
        sender, email_date
    """
    creds = _get_gmail_creds()
    service = build("gmail", "v1", credentials=creds)

    # --- Gmail search query -------------------------------------------------
    from_query = " OR ".join(f"from:{addr}" for addr in ALLOWED_SENDERS)
    state_query = {"unread": "is:unread", "read": "-is:unread", "all": ""}[email_state]
    q_parts = ["has:attachment", "filename:pdf", from_query, state_query]
    q = " ".join(p for p in q_parts if p)

    raw_results = service.users().messages().list(
        userId="me",
        q=q,
        maxResults=max_results + (RESERVE_FOR_DEMO if email_state == "unread" else 0),
    ).execute()

    msgs_meta = raw_results.get("messages", [])
    if email_state == "unread":          # reserve first N for demo
        msgs_meta = msgs_meta[RESERVE_FOR_DEMO:]

    if not msgs_meta:
        print("📭 No matching messages.")
        return []

    existing_files = {p.name for p in DOWNLOAD_DIR.glob("*.pdf")}
    downloaded: List[Dict[str, Any]] = []

    for meta in msgs_meta:
        msg_id = meta["id"]
        msg = service.users().messages().get(
            userId="me", id=msg_id, format="full"
        ).execute()

        # ----- headers & sender -------------------------------------------
        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        sender_hdr = headers.get("From", "")
        m_sender = re.search(r"<([^>]+)>", sender_hdr)
        sender_email = (m_sender.group(1) if m_sender else sender_hdr).lower()

        if sender_email not in ALLOWED_SENDERS:
            continue

        # ----- make corpus for regex --------------------------------------
        corpus = headers.get("Subject", "") + "\n" + msg.get("snippet", "")
        for prt in msg["payload"].get("parts", []):
            if prt.get("mimeType", "").startswith("text/plain"):
                txt = prt["body"].get("data")
                if txt:
                    corpus += base64.urlsafe_b64decode(txt).decode("utf-8", "ignore")

        m = RE_RES_INFO.search(corpus)
        if not m:
            continue

        res_num = int(m.group(1))
        edition_int = int(m.group(2))

        # ----- check against Notion ---------------------------------------
        page_id, stored_edition = search_notion_by_reservation_number(res_num)
        if page_id and edition_int <= stored_edition:
            continue  # nothing new

        # ----- handle attachments -----------------------------------------
        for part in msg["payload"].get("parts", []):
            orig_name = part.get("filename")
            if not (orig_name and orig_name.lower().endswith(".pdf")):
                continue

            # Normalised file name:  RES_<res#>_<edition>.pdf
            new_name = f"RES_{res_num}_{edition_int}.pdf"
            if new_name in existing_files:
                print(f"Skip (already on disk): {new_name}")
                continue

            att_id = part["body"].get("attachmentId")
            if not att_id:
                continue

            att = service.users().messages().attachments().get(
                userId="me", messageId=msg_id, id=att_id
            ).execute()
            file_bytes = base64.urlsafe_b64decode(att["data"].encode("UTF-8"))

            file_path = DOWNLOAD_DIR / new_name
            file_path.write_bytes(file_bytes)

            log_event(module="email_handler",
                      event="pdf_downloaded",
                      reservation_number=res_num,
                      edition=edition_int,
                      filename=new_name)

            downloaded.append({
                "file_path": str(file_path),
                "reservation_number": res_num,
                "edition": edition_int,
                "gmail_msg_id": msg_id,
                "sender": sender_email,
                "email_date": headers.get("Date", ""),
            })

            existing_files.add(new_name)  # avoid duplicates

        # ----- mark read if we were looking at unread ----------------------
        if email_state == "unread":
            service.users().messages().modify(
                userId="me", id=msg_id, body={"removeLabelIds": ["UNREAD"]}
            ).execute()

    return downloaded

