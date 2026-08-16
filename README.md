# 📒 Digitálny zošit

Jednoduchá webová aplikácia na evidenciu zákazníkov a zákaziek.

Aplikácia je vytvorená v Pythone pomocou FastAPI, SQLAlchemy, Jinja2 a SQLite.

---

## 🛠️ Použité technológie

- Python
- FastAPI
- SQLAlchemy
- SQLite
- Jinja2
- Alembic
- HTML
- CSS
- JavaScript

---

## 📁 Základná štruktúra projektu

```text
digitalny-zosit/
│
├── alembic/
│   ├── versions/
│   ├── env.py
│   └── ...
│
├── static/
│   └── style.css
│
├── templates/
│   ├── index.html
│   └── customer.html
│
├── .env
├── .env.example
├── .gitignore
├── alembic.ini
├── database.py
├── main.py
├── models.py
├── README.md
└── digitalny-zosit.db