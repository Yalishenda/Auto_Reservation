from __future__ import annotations


from typing import Any, Dict, Optional, Tuple
import json
import re

from notion_client import Client

# ──────────────────────────────  PATHS & SECRETS  ──────────────────────────── #
from utils.paths import SECRETS_PATH       # <root>/config/secrets.json


def _load_secrets() -> Dict[str, Any]:
    return json.loads(SECRETS_PATH.read_text(encoding="utf-8"))


_SECRETS = _load_secrets()
_NOTION_TOKEN = _SECRETS["notion_token"]
_DATABASE_ID  = _SECRETS["notion_database_id"]

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
    "reservation_number": ("booking_num",      "title"),
    "edition":            ("edition",          "select"),   # now "0"/"1"/"2"
    "order_limit":        ("order_limit",      "number"),
    "sender_email":       ("email",            "email"),
    "faculty_name":       ("faculty",          "select"),
    "date":               ("date",             "date"),
    "number_of_people":   ("number_of_seats",  "number"),
    "reserved_table":     ("setting",          "select"),   # maps to 'הגשה'
    "from_counter":       ("setting",          "select"),   # maps to 'דלפק'
    "status":             ("status",           "status"),
    "total_with_vat":     ("total_with_vat",   "number"),
    "invoice_num":        ("invoice_num",      "number"),
}

_SETTING_LABELS = {"reserved_table": "הגשה", "from_counter": "דלפק"}


def _select_payload(name: str) -> Dict[str, Any]:
    """Return {'name': name}; Notion will create the option if it doesn’t exist."""
    return {"name": str(name)}


# ───────────────────────────  DATA → NOTION PROPS  ─────────────────────────── #
def _data_to_properties(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Transform normalised dict into Notion 'properties' payload.
    Ignores keys not in _FIELD_MAP or whose value is None.
    """
    props: Dict[str, Any] = {}

    for in_key, val in data.items():
        if val is None or in_key not in _FIELD_MAP:
            continue

        col, col_type = _FIELD_MAP[in_key]

        if col_type == "title":
            props[col] = {"title": [{"type": "text", "text": {"content": str(val)}}]}

        elif col_type == "number":
            props[col] = {"number": float(val)}

        elif col_type == "email":
            props[col] = {"email": str(val)}

        elif col_type == "select":
            # special handling for setting
            if in_key in {"reserved_table", "from_counter"}:
                if val is True:
                    val = _SETTING_LABELS[in_key]
                else:
                    # False / None → no change
                    continue
            props[col] = {"select": _select_payload(val)}

        elif col_type == "status":
            props[col] = {"status": _select_payload(val)}

        elif col_type == "date":
            iso = str(val)
            if re.fullmatch(r"\d{2}/\d{2}/\d{4}", iso):          # DD/MM/YYYY → YYYY-MM-DD
                d, m, y = iso.split("/")
                iso = f"{y}-{m}-{d}"
            props[col] = {"date": {"start": iso}}

    return props


# ────────────────────────────────  PUBLIC API  ─────────────────────────────── #
def search_notion_by_reservation_number(
    reservation_number: int | str,
) -> Tuple[Optional[str], Optional[int]]:
    """
    Look up by booking_num title.
    Returns (page_id, stored_edition_int) or (None, None).
    """
    res = _notion.databases.query(
        _DATABASE_ID,
        filter={
            "property": "booking_num",
            "title": {"equals": str(reservation_number).strip()},
        },
        page_size=1,
    )

    if res["results"]:
        page = res["results"][0]
        page_id = page["id"]
        select_obj = page["properties"]["edition"].get("select")
        edition_int = int(select_obj["name"]) if select_obj else 0
        return page_id, edition_int
    return None, None


def create_notion_entry(data: Dict[str, Any]) -> str:
    payload = {
        "parent": {"database_id": _DATABASE_ID},
        "properties": _data_to_properties(data),
    }
    page = _notion.pages.create(**payload)
    return page["id"]


def update_notion_entry(page_id: str, data: Dict[str, Any]) -> None:
    props = _data_to_properties(data)
    if props:
        _notion.pages.update(page_id=page_id, properties=props)





"""
# ───────────────────────────────  SELF-TEST  ──────────────────────────────── #
if __name__ == "__main__":
    import pprint

    pprint.pprint(get_database_structure(), sort_dicts=False)
    pid, ed = search_notion_by_reservation_number(1624251)
    print("Found:", pid, ed)

"""

