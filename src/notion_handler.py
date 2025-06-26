from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, List
import json
import re

from src.logger import log_event
from src.notifier import notify_change
import traceback

from notion_client import Client
from src.utils.paths import SECRETS_PATH       # <root>/config/secrets.json

def _load_secrets() -> Dict[str, Any]:
    return json.loads(SECRETS_PATH.read_text(encoding="utf-8"))

_SECRETS = _load_secrets()
_NOTION_TOKEN = _SECRETS["notion_token"]
_DATABASE_ID = _SECRETS["notion_database_id"]

_notion = Client(auth=_NOTION_TOKEN)

# ───────────────────────  DATABASE STRUCTURE (cached)  ─────────────────────── #
def get_database_structure() -> Dict[str, Any]:
    db = _notion.databases.retrieve(_DATABASE_ID)
    result: Dict[str, Any] = {}
    for col, spec in db["properties"].items():
        col_type = spec["type"]
        entry: Dict[str, Any] = {"type": col_type}
        if col_type in {"select", "multi_select", "status"}:
            entry["options"] = [opt["name"] for opt in spec[col_type]["options"]]
        result[col] = entry
    return result

_DB_SCHEMA = get_database_structure()

# ─────────────────────────────  FIELD → COLUMN MAP  ────────────────────────── #
_FIELD_MAP = {
    "reservation_number":     ("booking_num",       "title"),
    "edition":                ("edition",           "select"),
    "order_limit":            ("order_limit",       "number"),
    "faculty_email":          ("faculty_email",     "email"),
    "sender_email":           ("email",             "email"),
    "faculty_name":           ("faculty",           "select"),
    "date":                   ("date",              "date"),
    "number_of_people":       ("number_of_seats",   "number"),
    "status":                 ("status",            "status"),
    "total_with_vat":         ("total_with_vat",    "number"),
    "invoice_num":            ("invoice_num",       "number"),
    "reserved_table":         ("setting",           "select"),  # see logic below
}

_SETTING_LABELS = {
    True: "הגשה",     # reserved_table = True
    False: "דלפק"     # reserved_table = False
}

def _select_payload(name: str) -> Dict[str, Any]:
    return {"name": str(name)}

def _data_to_properties(data: Dict[str, Any]) -> Dict[str, Any]:
    props: Dict[str, Any] = {}

    for in_key, val in data.items():
        if val is None or in_key not in _FIELD_MAP:
            continue

        col, col_type = _FIELD_MAP[in_key]

        if in_key == "reserved_table":
            val = _SETTING_LABELS.get(bool(val))  # Map to 'הגשה' or 'דלפק'

        if col_type == "title":
            props[col] = {"title": [{"type": "text", "text": {"content": str(val)}}]}

        elif col_type == "number":
            props[col] = {"number": float(val)}

        elif col_type == "email":
            props[col] = {"email": str(val)}

        elif col_type == "select":
            props[col] = {"select": _select_payload(val)}

        elif col_type == "status":
            props[col] = {"status": _select_payload(val)}

        elif col_type == "date":
            iso = str(val)
            if re.fullmatch(r"\d{2}/\d{2}/\d{4}", iso):
                d, m, y = iso.split("/")
                iso = f"{y}-{m}-{d}"
            props[col] = {"date": {"start": iso}}

    # Add fixed default
    props["confirmed_booking"] = {"checkbox": False}

    return props

# ────────────────────────────────  PUBLIC API  ─────────────────────────────── #

def search_notion_by_reservation_number(
    reservation_number: int | str,
    *, debug: bool = False,
) -> Tuple[Optional[str], Optional[int]]:
    """
    Return (page_id, stored_edition_int) for the FIRST page whose *title*
    equals the given reservation number.  If not found → (None, None).

    Notes
    -----
    • The “booking_num” property is the DB’s *title* column.
      Filtering with `"title": {"equals": ...}` is therefore correct.
    • If the edition select is empty we default to 0.
    """
    res: Dict[str, Any] = _notion.databases.query(
        _DATABASE_ID,
        filter={
            "property": "booking_num",
            "title": {"equals": str(reservation_number).strip()},
        },
        page_size=1,
    )

    if debug:
        print("DEBUG query result:", json.dumps(res, ensure_ascii=False, indent=2))

    if res["results"]:
        page = res["results"][0]
        page_id = page["id"]

        # Edition is a “select” value — can be None.
        sel = page["properties"]["edition"]["select"]
        edition_int = int(sel["name"]) if sel and sel.get("name") else 0
        return page_id, edition_int

    return None, None


def get_existing_page_status(
    reservation_number: int | str,
    *, debug: bool = False,
) -> Optional[str]:
    """
    Return the *lower-cased* status name for the FIRST matching page or None.
    Example → "future_order", "invoice_sent", etc.
    """
    res: Dict[str, Any] = _notion.databases.query(
        _DATABASE_ID,
        filter={
            "property": "booking_num",
            "title": {"equals": str(reservation_number).strip()},
        },
        page_size=1,
    )

    if debug:
        print("DEBUG query result:", json.dumps(res, ensure_ascii=False, indent=2))

    if res["results"]:
        page = res["results"][0]
        status_obj = page["properties"]["status"]["status"]  # may be None
        return status_obj["name"].lower() if status_obj else None
    return None

def get_future_reservations() -> List[Dict[str, Any]]:
    """
    Return all Notion pages with status = 'future_order'.
    """
    res = _notion.databases.query(
        _DATABASE_ID,
        filter={
            "property": "status",
            "status": {"equals": "future_order"}
        },
        page_size=300,  # adjust if needed
    )
    return res.get("results", [])


def create_notion_entry(data: Dict[str, Any]) -> str:
    """Create page, add optional description, log success / error."""
    try:
        additional_description = data.get("additional_description")

        payload = {
            "parent": {"database_id": _DATABASE_ID},
            "properties": _data_to_properties(data),
        }
        page = _notion.pages.create(**payload)
        page_id = page["id"]
        notif = {
            "booking_num": data["reservation_number"],
            "order_limit": data.get("order_limit", 0),  # safe
            "faculty": data.get("faculty_name", ""),
            "date": data.get("date", ""),
            "reserved_table": bool(data.get("reserved_table")),
        }
        notify_change(notif, updated=False)

        if additional_description:
            _notion.blocks.children.append(
                page_id,
                children=[{
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{
                            "type": "text",
                            "text": {"content": additional_description}
                        }]
                    },
                }],
            )

        # ----- logging -----
        log_event(
            module="notion_handler",
            event="notion_created",
            reservation_number=data.get("reservation_number"),
            edition=data.get("edition"),
            notion_page_id=page_id,
            status_after=data.get("status"),
            message="page created",
        )
        return page_id

    except Exception as e:
        log_event(
            module="notion_handler",
            event="notion_error",
            reservation_number=data.get("reservation_number"),
            edition=data.get("edition"),
            message=f"create failed: {e}",
        )
        raise




def update_notion_entry(page_id: str, data: Dict[str, Any]) -> None:
    """Patch existing page, log success / error."""
    try:
        # --- fetch current page once for status check -------------
        current_page = _notion.pages.retrieve(page_id=page_id)
        before_status = (
            current_page["properties"]["status"]["status"]["name"]
            if current_page["properties"]["status"]["status"] else ""
        )

        # --- Do not downgrade a terminal status -------------------
        if before_status in {"invoice_sent", "paid"}:
            data = {k: v for k, v in data.items() if k != "status"}

        props = _data_to_properties(data)
        if props:
            _notion.pages.update(page_id=page_id, properties=props)

            # Telegram
            notif = {
                "booking_num":     data["reservation_number"],
                "order_limit":     data.get("order_limit", 0),
                "faculty":         data.get("faculty_name", ""),
                "date":            data.get("date", ""),
                "reserved_table":  bool(data.get("reserved_table")),
            }
            notify_change(notif, updated=True)

            # Logging
            log_event(
                module="notion_handler",
                event="notion_updated",
                reservation_number=data.get("reservation_number"),
                edition=data.get("edition"),
                notion_page_id=page_id,
                status_before=before_status,
                status_after=data.get("status", before_status),
                message="page updated",
            )

    except Exception as e:
        log_event(
            module="notion_handler",
            event="notion_error",
            reservation_number=data.get("reservation_number"),
            edition=data.get("edition"),
            notion_page_id=page_id,
            message=f"update failed: {e}",
        )
        raise

