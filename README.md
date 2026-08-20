# 📒 Digitálny zošit

Jednoduchá webová aplikácia na evidenciu zákazníkov a zákaziek pre remeselníkov.

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

## 🚀 Ako spustiť projekt

### 1. Nainštaluj závislosti

```powershell
pip install -r requirements.txt
```

### 2. Priprav `.env` súbor

Skopíruj `.env.example` do `.env`:

```powershell
copy .env.example .env
```

Potom v `.env` nastav:

- **`SECRET_KEY`** – náhodný reťazec pre šifrovanie prihlasovacej session. Vygeneruješ ho príkazom:

  ```powershell
  python -c "import secrets; print(secrets.token_hex(32))"
  ```

- **`ADMIN_USERNAME`** – prihlasovacie meno (predvolené `admin`)

- **`ADMIN_PASSWORD_HASH`** – hash prihlasovacieho hesla, nie samotné heslo. Vygeneruješ ho príkazom:

  ```powershell
  python -c "from auth import hash_password; print(hash_password('tvoje-heslo'))"
  ```

  Výstup (celý reťazec `salt$hash`) skopíruj do `.env`.

### 3. Spusti databázové migrácie

```powershell
alembic upgrade head
```

**Ak appku aktualizuješ z predchádzajúcej verzie** (už máš databázu s dátami), táto migrácia pridá nové tabuľky pre faktúry a fakturačné údaje bez straty existujúcich zákazníkov/zákaziek.

### 4. Spusti aplikáciu

```powershell
uvicorn main:app --reload
```

Aplikácia beží na `http://127.0.0.1:8000` a pri prvom vstupe ťa presmeruje na `/login`.

---

## 🔐 Prihlásenie

Appka má jednoduché prihlásenie s jedným admin účtom (session-based, cookie).

- HTML stránky (dashboard, detail zákazníka, formuláre) vyžadujú prihlásenie – neprihláseného používateľa presmerujú na `/login`.
- JSON API endpointy (`GET /customers`, `GET /jobs`, `GET /invoices`) bez prihlásenia vrátia `401 Unauthorized`.
- Odhlásenie je dostupné tlačidlom "Odhlásiť sa" v hornej lište.

---

## 🧾 Fakturácia

V rámci prípravy na povinnú elektronickú fakturáciu (Peppol/eFaktúra, ktorá sa pre platiteľov DPH stáva povinnou od 1.1.2027) appka obsahuje základný fakturačný modul:

1. **Nastavenia → fakturačné údaje firmy** (`/settings`) – vyplň názov, IČO, DIČ, IČ DPH (ak si platiteľ), adresu a IBAN. Tieto údaje sa použijú ako dodávateľ na každej faktúre.
2. **Zákazníci** – v úprave zákazníka môžeš voliteľne doplniť jeho IČO/DIČ/IČ DPH (pre firemných odberateľov).
3. **Vytvorenie faktúry** – na detaile zákazníka klikni "+ Nová faktúra" (alebo "🧾 Vyfakturovať" priamo pri konkrétnej zákazke). Faktúra podporuje viacero položiek s rôznymi sadzbami DPH (0 %, 5 %, 19 %, 23 %) a automaticky počíta rekapituláciu DPH.
4. **Číslovanie** – faktúry sa číslujú automaticky v tvare `RRRRPPP` (napr. `2026001`), samostatne pre každý rok.
5. **PDF export** – na detaile faktúry je tlačidlo "Stiahnuť PDF", ktoré vygeneruje faktúru na stiahnutie/tlač (podporuje slovenskú diakritiku).
6. **Stav faktúry** – Návrh / Odoslaná / Uhradená / Po splatnosti / Stornovaná.

**Dôležité:** appka zatiaľ negeneruje Peppol BIS XML formát ani sa nenapája na Digitálneho poštára – to je plánovaná ďalšia fáza. Dátový model (IČO/DIČ/IČ DPH, štruktúrované položky, presné sadzby DPH) je ale navrhnutý tak, aby bol na túto fázu pripravený.

---

## ✅ Testy

```powershell
pytest
```

Testy pokrývajú CRUD operácie na zákazníkoch a zákazkách, validáciu vstupov, prihlasovanie/odhlasovanie, aj fakturačný modul (číslovanie, výpočet DPH, generovanie PDF). Bežia proti oddelenej in-memory SQLite databáze a nezasahujú do reálnych dát.

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
├── routers/
│   ├── auth.py          # /login, /logout
│   ├── company.py       # /settings - fakturačné údaje firmy
│   ├── customers.py     # CRUD zákazníkov
│   ├── invoices.py      # CRUD faktúr, PDF export
│   └── jobs.py           # CRUD zákaziek
│
├── static/
│   ├── fonts/            # DejaVu Sans (diakritika v PDF)
│   └── style.css
│
├── templates/
│   ├── index.html
│   ├── customer.html
│   ├── edit_customer.html
│   ├── edit_job.html
│   ├── invoice_form.html
│   ├── invoice_detail.html
│   ├── settings.html
│   └── login.html
│
├── tests/
│   ├── test_main.py      # CRUD, validácia
│   ├── test_auth.py      # prihlásenie, odhlásenie
│   └── test_invoices.py  # fakturácia, PDF, číslovanie
│
├── auth.py                # hashovanie hesla, session dependencies
├── database.py
├── invoice_pdf.py         # generovanie PDF faktúr
├── invoice_utils.py       # číslovanie faktúr, výpočet DPH
├── main.py
├── models.py
├── schemas.py              # Pydantic modely (validácia)
├── templates_config.py    # zdieľaná Jinja2Templates inštancia
│
├── .env.example
├── .gitignore
├── alembic.ini
├── requirements.txt
└── README.md
```
