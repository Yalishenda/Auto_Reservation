"""
Turn raw PDF text into a structured reservation dict via OpenAI ChatCompletion.


"""

from __future__ import annotations
import json, time
from pathlib import Path
from typing import Any, Dict

from src.logger import log_event

import openai

# ────────────────────────────────────────────────────────────
#  Detect SDK generation and import proper exception classes
# ────────────────────────────────────────────────────────────
try:                                # new SDK ≥1.0
    from openai import APITimeoutError, APIConnectionError, RateLimitError, OpenAIError
    USE_NEW_CLIENT = True
except ImportError:                 # old SDK <1.0
    from openai.error import APITimeoutError, APIConnectionError, RateLimitError, OpenAIError  # type: ignore
    USE_NEW_CLIENT = False

# ────────────────────────────────────────────────────────────
#  Secrets / model
# ────────────────────────────────────────────────────────────
try:
    from src.utils.paths import SECRETS_PATH
except ImportError:
    SECRETS_PATH = Path(__file__).resolve().parents[2] / "config" / "secrets.json"

CONFIG = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
MODEL  = CONFIG.get("openai_model", "gpt-4o-mini")
openai.api_key = CONFIG["openai_api_key"]

if USE_NEW_CLIENT:
    client = openai.OpenAI(api_key=openai.api_key)   # single client instance

# ────────────────────────────────────────────────────────────
#  JSON schema & prompt
# ────────────────────────────────────────────────────────────
_REQUIRED_KEYS = {
    "reservation_number": int,
    "edition": int,
    "order_limit": (int, float),
    "faculty_email": str,
    "faculty_name": str,
    "date": str,
    "number_of_people": (int, type(None)),
    "reserved_table": bool,
    "status": str,
    "additional_description": str,
}

SYS = (
    "You are a JSON extraction engine for Hebrew PDF purchase orders. "
    "Return only valid JSON matching the schema. "
    "Identify key fields using Hebrew labels. Follow these rules:"
    "- reservation_number: after 'מס' הזמנה'"
    "- edition: 0 = initial issue, 1 = update (from 'מהדורה מספר')"
    "- order_limit: number from the line with 'ערך הזמנה ב- שקל ישראלי חדש'"
    "- faculty_email: first valid email, near department name"
    "- faculty_name: match full line or joined lines; prefer known names from university"
    "- date: use date from free-text (e.g. 'לתאריך'), or fallback to issue date"
    "- number_of_people: if missing, use 0"
    "- reserved_table: true only if text contains 'בהגשה' and not negated. In any other cases will be false"
    "- status: 'cancelled' if canceled, 'updated' if edition ≥1 and not cancelled, else 'future_order'"
    "- additional_description: use any free-text that describes the event"
    "Do not add explanations. Return only a valid JSON object."
)
FACULTY_LIST = [
    'המחלקה לכלכלה', 'המחלקה לכימיה', 'המחלקה למדעי החיים', 'המחלקה למדעי המוח', 'המחלקה למדעי המחשב',
    'המחלקה למתמטיקה', 'מזכירות אקדמית', 'המחלקה לתרבות צרפת', 'המחלקה לתרבות יידיש ע״ש רינה קוסטה',
    'ביה״ס למינהל עסקים', 'המחלקה לפסיכולוגיה', 'המכון למוליכות על', 'הפקולטה להנדסה', 'המחלקה לניהול',
    'המחלקה לערבית', 'המחלקה לפילוסופיה יהודית', 'לשכת הנשיא', 'המחלקה למדעי הרוח',
    'המחלקה ללימודים בין תחומיים', 'ליאת וינקלר כדורי', 'התוכנית ללימודי מגדר', 'המחלקה למשפטים ע״ש יעקב הרצוג',
    'לישכת המזכיר האקדמי', 'המחלקה ללוגיסטיקה', 'מדור בטחון', 'מתנ״ס קריית אונו', 'מערך פיתוח משאבים',
    'המחלקה לפילוסופיה', 'לשכת משנה לנשיא', 'המחלקה לספרות עם ישראל', 'לשכת סגן הנשיא למחקר',
    'תקשורת אקדמית', 'לשכת הרקטור', 'ב. א. רב תחומי למדעי הרוח', 'לשכת רב הקמפוס', 'המחלקה לפיסיקה',
    'ביה״ס לאופטומטריה ומדעי הראייה', 'לשכת ראש מינהל ודיקן הסטודנטים', 'לשכת דיקן הסטודנטים',
    'המחלקה ללימודי ארץ ישראל וארכיאולוגיה', 'המחלקה ללימודי המזרח התיכון', 'המחלקה למדעי המדינה',
    'מינהל התוכניות הייעודיות', 'המחלקה לפיזיקה', 'ביה״ס לעבודה סוציאלית', 'המחלקה לספרות משווה',
    'מחלקת תפעול', 'מגביות', 'זרועת בטחון', 'לשכת סגן הרקטור', 'מרכז דהן', 'מינהלת הפקולטה למדעי יהדות',
    'המחלקה לחינוך', 'לשכת סמנכ\"ל משאבי אנוש', 'המחלקה ליהדות', 'המחלקה לתולדות ישראל ויהדות זמננו',
    'מחלקת בטחון ובטיחות', 'מינהלת הפקולטה למדעים מדויקים', 'מחלקת רכש מכרזים והתקשרויות',
    'המחלקה למדעי חברה ובריאות', 'אגף לקשרי חוץ ולפיתוח משאבים', 'מינהלת הפקולטה למדעי החברה',
    'המחלקה לאומנות יהודית', 'המחלקה לתנ\"ך ע\"ש זלמן שמיר'
]

USER_TMPL = f"""
Extract the following JSON structure based on the rules provided.
Faculty names may appear partially and across multiple lines – reconstruct if needed.
Try to match to known faculties when possible, from this list:

{FACULTY_LIST}

Return ONLY the JSON:
{{
  "reservation_number": "<int>",
  "edition": "<0/1/2 int>",
  "order_limit": "<int/float>",
  "faculty_email": "str",
  "faculty_name": "hebrew str",
  "date": "dd/mm/yyyy str",
  "number_of_people": "<int>",
  "reserved_table": "<true/false>",
  "status": "str",
  "additional_description": "str"
}}

PDF TEXT:
{{txt}}
"""

# ────────────────────────────────────────────────────────────
#  Helper: call OpenAI compatible with both SDKs
# ────────────────────────────────────────────────────────────
def _chat_completion(messages: list[dict[str, str]], retries: int = 3) -> tuple[str, int]:
    delay = 2
    for attempt in range(1, retries + 1):
        try:
            if USE_NEW_CLIENT:
                resp = client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    temperature=0,
                )
                usage_total = resp.usage.total_tokens
                content = resp.choices[0].message.content
            else:  # legacy SDK
                resp = openai.ChatCompletion.create(
                    model=MODEL,
                    messages=messages,
                    temperature=0,
                )
                usage_total = resp["usage"]["total_tokens"]
                content = resp["choices"][0]["message"]["content"]

            #print(f"🔑 OpenAI usage: {usage_total} tokens")
            return content, usage_total

        except (RateLimitError, APITimeoutError, APIConnectionError, OpenAIError) as e:
            if attempt == retries:
                raise
            print(f"⚠️  {e} — retry {attempt}/{retries}")
            time.sleep(delay)
            delay *= 2    # exponential backoff


# ────────────────────────────────────────────────────────────
#  Validation
# ────────────────────────────────────────────────────────────
def _validate(obj: Dict[str, Any]) -> Dict[str, Any]:
    for k, typ in _REQUIRED_KEYS.items():
        if k not in obj:
            raise ValueError(f"Missing key {k}")
        if not isinstance(obj[k], typ):
            if obj[k] is not None or type(None) not in (typ if isinstance(typ, tuple) else (typ,)):
                raise TypeError(f"{k} expected {typ}, got {type(obj[k])}")
    if obj["status"] not in {"future_order", "cancelled", "updated"}:
        raise ValueError("Invalid status value")
    return obj

# ────────────────────────────────────────────────────────────
#  Public function
# ────────────────────────────────────────────────────────────
def analyze_reservation_text(full_text: str) -> Dict[str, Any]:
    prompt = USER_TMPL.replace("{txt}", full_text.strip())

    raw, token_total = _chat_completion(
        [{"role": "system", "content": SYS},
         {"role": "user",   "content": prompt}]
    )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"GPT returned invalid JSON: {e}") from e

    data = _validate(data)

    # ---------- logging ----------
    log_event(
        module="text_analyzer",
        event="gpt_parsed",
        reservation_number=data["reservation_number"],
        edition=data["edition"],
        token_usage=token_total,
        message="GPT extraction OK",
    )

    return data



        
