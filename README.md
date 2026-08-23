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

## 🧭 Navigácia

- **`/`** – dashboard (štatistiky, termíny, posledné zákazky, rýchly prístup)
- **`/zakaznici`** – zoznam všetkých zákazníkov (vyhľadávanie, pridanie nového)
- **`/zakazky`** – zoznam všetkých zákaziek naprieč zákazníkmi (filtrovanie podľa stavu)
- **`/faktury`** – zoznam všetkých faktúr (filtrovanie podľa stavu)
- **`/settings`** – fakturačné údaje firmy, logo, podpis/pečiatka

---

## 🧾 Fakturácia

V rámci prípravy na povinnú elektronickú fakturáciu (Peppol/eFaktúra, ktorá sa pre platiteľov DPH stáva povinnou od 1.1.2027) appka obsahuje základný fakturačný modul:

1. **Nastavenia → fakturačné údaje firmy** (`/settings`) – vyplň názov, IČO, DIČ, IČ DPH (ak si platiteľ), adresu a IBAN. Tieto údaje sa použijú ako dodávateľ na každej faktúre.
2. **Zákazníci** – v úprave zákazníka môžeš voliteľne doplniť jeho IČO/DIČ/IČ DPH (pre firemných odberateľov).
3. **Vytvorenie faktúry** – na detaile zákazníka klikni "+ Nová faktúra" (alebo "🧾 Vyfakturovať" priamo pri konkrétnej zákazke). Faktúra podporuje viacero položiek s rôznymi sadzbami DPH (0 %, 5 %, 19 %, 23 %) a automaticky počíta rekapituláciu DPH.
4. **Číslovanie** – faktúry sa číslujú automaticky v tvare `RRRRPPP` (napr. `2026001`), samostatne pre každý rok.
5. **PDF export** – na detaile faktúry je tlačidlo "Stiahnuť PDF", ktoré vygeneruje faktúru na stiahnutie/tlač (podporuje slovenskú diakritiku).
6. **Stav faktúry** – Návrh / Odoslaná / Uhradená / Po splatnosti / Stornovaná.
7. **Peppol XML export** – na detaile faktúry je tlačidlo "Peppol XML (návrh)", ktoré vygeneruje faktúru vo formáte UBL 2.1 / Peppol BIS Billing 3.0 (rovnaká štruktúra, akú vyžaduje pripravovaná povinná e-fakturácia).
8. **Logo a podpis/pečiatka** – v Nastaveniach môžeš nahrať logo firmy (zobrazí sa v hlavičke PDF faktúry) a obrázok podpisu/pečiatky (zobrazí sa pri sume na konci faktúry). Podporované formáty: PNG, JPG (max. 2 MB).
9. **QR platobný kód** – PDF faktúra obsahuje QR kód pre platbu podľa slovenského štandardu PAY by square (naskenovateľný väčšinou slovenských bankových aplikácií). Vygeneruje sa automaticky, ak má firma v Nastaveniach vyplnený IBAN.
10. **Spôsob úhrady** – pri vytváraní faktúry si vyberieš Prevodom / Hotovosť / Kartou.
11. **SWIFT/BIC a web firmy** – voliteľné polia v Nastaveniach, zobrazia sa na faktúre, ak sú vyplnené.

**Dôležité o Peppol XML exporte:** appka sama neposiela faktúry cez Peppol sieť (to si vyžaduje registráciu cez certifikovaného poskytovateľa, tzv. Digitálny poštár). Vygenerovaný XML je **návrh/príprava dát** v správnej štruktúre – priamo ho môžeš odovzdať svojmu poskytovateľovi pri jeho zapájaní. Pred ostrým používaním si u poskytovateľa over najmä:
- presný **Peppol scheme ID** kód (pole "Peppol schéma ID" v Nastaveniach – nechaj prázdne, kým ti ho nepridelí)
- či XML prejde ich validátorom (Peppol má prísne validačné pravidlá, tzv. Schematron)

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
│   ├── customers_list.html
│   ├── jobs_list.html
│   ├── invoices_list.html
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
│   ├── test_invoices.py  # fakturácia, PDF, Peppol XML, číslovanie
│   └── test_uploads.py   # logo/podpis - upload, validácia, náhľad
│
├── uploads/                # nahraté logo/podpis (negituje sa, .gitignore)
│
├── auth.py                # hashovanie hesla, session dependencies
├── database.py
├── invoice_pdf.py         # generovanie PDF faktúr
├── invoice_utils.py       # číslovanie faktúr, výpočet DPH
├── peppol_xml.py          # export do Peppol BIS 3.0 XML formátu
├── qr_payment.py          # QR platobný kód (PAY by square)
├── uploads_utils.py       # nahrávanie loga/podpisu
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
