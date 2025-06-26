# 🍽️ Restaurant Reservation-Automation

Automatically turns University **purchase-order e-mails** (PDF
attachments) into structured reservations in a Notion database, sends Telegram
alerts, and keeps a monthly CSV audit log—all for less than **$1 / year** in
GPT cost.

---

## ✨ Features

| Step | Module | Highlights |
|------|--------|------------|
| **1 · Fetch e-mails** | `email_handler.py` | Gmail API, OAuth token cache, regex pulls **reservation #** + **edition**, saves PDF as `RES_<num>_<ed>.pdf` |
| **2 · Parse PDF** | `pdf_reader.py` | Camelot-py (tables) + pdfplumber (text), footer cut, RTL safe |
| **3 · Extract fields** | `text_analyzer.py` | GPT-4o-mini → strict JSON; VAT rule; detects *cancelled* orders |
| **4 · Persist** | `notion_handler.py` | Auto-discovers DB schema; creates/updates; ignores changes if page already **paid** / **invoice_sent** |
| **5 · Alert** | `notifier.py` | Telegram bot: live NEW / UPDATE; daily digest for today & tomorrow |
| **6 · Log** | `logger.py` | Append-only CSV (`logs/YYYY-MM-reservations.csv`) |
| **7 · Orchestrate** | `main.py` | CLI flags, early duplicate guard, timezone aware |

---

## 🗂️ Project Layout

├── src/

│ ├── main.py

│ ├── email_handler.py

│ ├── pdf_reader.py

│ ├── text_analyzer.py

│ ├── notion_handler.py

│ ├── notifier.py

│ └── logger.py

├── utils/paths.py

├── downloads/ # temp PDFs

├── logs/

│ └── 2025-06-reservations.csv

└── config/

├── secrets.example.json

└── gmail_client_secret.json



---

## ⚡ Quick Start

```bash
# 1. clone & install
git clone https://github.com/Yalishenda/Auto_Reservation.git
cd Auto_Reservation
poetry install

# 2. copy & fill secrets
cp config/secrets.example.json config/secrets.json
#   ↳ add: openai_api_key, notion_token, notion_database_id,
#          allowed_senders[], telegram_bot_token, telegram_chat_id,
#          gmail_secret_file, gmail_token_file

# 3. first-time Gmail OAuth
poetry run python -m src.email_handler --help     # opens browser consent

# 4. run pipeline
poetry run python -m src.main --state unread

🔧 CLI Options (main.py)
Flag	Default	Purpose
`--state unread	read	all`
--max N	20	Max PDFs/e-mails to process
--from-downloads	off	Ignore Gmail, process PDFs already in downloads/
--digest-only	off	Skip processing; just send daily digest

🤖 OpenAI Cost
≈ 1.5k tokens / PDF → $0.001 (GPT-4o-mini June-2025 pricing)

Early edition guard avoids needless calls → < $1 / 1 000 PDFs / year


🤝 Contributing
Fork → feature branch → PR.

Follow black 24-line length.

Include log lines for new modules (logger.log_event).

Keep secrets out of commits!

📄 License
MIT © 2025 Aleksandr Zubkov / Bar-Ilan University School of High-Tech & Cybersecurity / Data-Science Class DS18
