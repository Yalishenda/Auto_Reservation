"""
Simple Telegram notifier
• Fires a message every time a reservation page is created or updated.
• Sends a daily digest of *today* / *tomorrow* reservations.

Uses the lightweight **telebot** (pyTelegramBotAPI) package:
    poetry add pyTelegramBotAPI
"""

from __future__ import annotations
import datetime as _dt
import json
from pathlib import Path
from typing import Dict, List

import telebot                             # pip install pyTelegramBotAPI

# ───────── config ─────────
try:
    from src.utils.paths import SECRETS_PATH
except ImportError:
    SECRETS_PATH = Path(__file__).resolve().parents[2] / "config" / "secrets.json"

_cfg: Dict[str, str] = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
BOT   = telebot.TeleBot(_cfg["telegram_bot_token"], parse_mode="HTML")
CHAT  = _cfg["telegram_chat_id"]

# ───────── helpers ─────────
def _human(msg: Dict[str, str | int | float]) -> str:
    """
    Build one-line human-readable summary.
    BEHAGASHA (בהגשה) flag is printed in CAPS.
    """
    table_flag = " <b>בהגשה</b>" if msg.get("reserved_table") else ""
    date_str   = msg.get("date", "")
    return (f"📄 {msg['booking_num']}  |  ₪{msg['order_limit']:.2f}  |  "
            f"{msg['faculty']}  |  {date_str}{table_flag}")

# ───────── public API ─────────
def notify_change(data: Dict, *, updated: bool = False) -> None:
    """
    Called from notion_handler after page create/update.
    Required keys in `data`:
        booking_num, order_limit, faculty_name, date, reserved_table
    """
    prefix = "🔄 עדכון הזמנה" if updated else "🆕 הזמנה חדשה"
    text   = f"{prefix}\n{_human(data)}"
    BOT.send_message(CHAT, text)

def notify_daily_digest(res_list: List[Dict]) -> None:
    """
    Send a digest (one message) containing today's + tomorrow's reservations.
    `res_list` – list of dicts as above.
    """
    if not res_list:
        return
    header = "📅 הזמנות להיום ומחר"
    body   = "\n".join(_human(r) for r in res_list)
    BOT.send_message(CHAT, f"{header}\n{body}")
