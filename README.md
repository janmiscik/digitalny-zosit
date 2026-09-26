# 📒 Digitálny zošit

Webová aplikácia na fakturáciu, cenové ponuky a evidenciu zákazníkov/zákaziek pre remeselníkov a živnostníkov.

Aplikácia je vytvorená v Pythone pomocou FastAPI, SQLAlchemy, Jinja2 a SQLite. Je zámerne **jednopoužívateľská** (jeden živnostník/remeselník na inštanciu appky) - to sa premieta do viacerých architektonických rozhodnutí (napr. globálny rate limiter na prihlásenie namiesto per-IP, jedna sada fakturačných údajov firmy).

---

## 🛠️ Použité technológie

- Python, FastAPI, Uvicorn
- SQLAlchemy + Alembic (migrácie)
- SQLite
- Jinja2 (šablóny)
- ReportLab (generovanie PDF)
- Pillow (overenie skutočného typu, auto-resize a EXIF orientácia nahrávaných obrázkov)
- HTML, CSS, JavaScript (bez frontend frameworku)

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

- **`DATABASE_URL`** – cesta k SQLite súboru (predvolené `sqlite:///./digitalny-zosit.db`). Ak paralelne testuješ viacero verzií appky s rôznymi DB súbormi, **vždy si over tento riadok pred spúšťaním migrácií** - ľahko sa stane, že appka beží nad iným súborom, než si myslíš.

- **`SECRET_KEY`** – náhodný reťazec pre šifrovanie prihlasovacej session:

  ```powershell
  python -c "import secrets; print(secrets.token_hex(32))"
  ```

- **`ADMIN_USERNAME`** – prihlasovacie meno (predvolené `admin`)

- **`ADMIN_PASSWORD_HASH`** – hash prihlasovacieho hesla, nie samotné heslo:

  ```powershell
  python -c "from auth import hash_password; print(hash_password('tvoje-heslo'))"
  ```

  Výstup (celý reťazec `salt$hash`) skopíruj do `.env`.

- **`SESSION_HTTPS_ONLY`** – či sa prihlasovacia cookie posiela iba cez HTTPS (predvolené `true`). Nechaj `true` v produkcii (Railway a pod.). Iba ak appku spúšťaš lokálne cez obyčajné `http://localhost`, nastav na `false` - inak sa neprihlásiš (prehliadač cookie s príznakom `Secure` cez obyčajné http vôbec neuloží).

### 3. Spusti databázové migrácie

```powershell
alembic upgrade head
```

Appka **nespúšťa `create_all()` automaticky** - schéma sa spravuje výhradne cez Alembic. Ak migrácie nie sú spustené, appka pri štarte zlyhá s jasnou hláškou (nie tichou nekonzistenciou schémy).

### 4. Spusti aplikáciu

```powershell
uvicorn main:app --reload
```

Aplikácia beží na `http://127.0.0.1:8000` a pri prvom vstupe ťa presmeruje na `/login`.

---

## 🔐 Prihlásenie a bezpečnosť

- Jednoduché prihlásenie s jedným admin účtom (session-based, cookie), heslo hashované cez PBKDF2.
- HTML stránky vyžadujú prihlásenie – neprihláseného používateľa presmerujú na `/login`. JSON API endpointy vrátia `401 Unauthorized`.
- **Rate limiting** na `/login` – po 5 zlých pokusoch v priebehu 5 minút sa prihlásenie zamkne na 5 minút (globálne pre appku, keďže appka má vždy len jedného legitímneho používateľa).
- Session cookie je kryptograficky podpísaná (`itsdangerous`) – akákoľvek manipulácia s cookie sa odmietne. Cookie sa navyše posiela iba cez HTTPS (`https_only`, konfigurovateľné cez `SESSION_HTTPS_ONLY` - viď sekcia `.env` vyššie).
- **CSRF ochrana** – všetky formuláre (POST/PUT/PATCH/DELETE) nesú skrytý token naviazaný na session (double-submit pattern); požiadavka bez platného tokenu sa odmietne s `403`.
- Nahrávané obrázky (logo, podpis, fotky zákaziek) sa overujú podľa **skutočného obsahu súboru** (cez Pillow), nielen podľa prípony – zabraňuje to nahratiu skrytého škodlivého súboru pod príponou `.png`. Zároveň sa kontroluje aj rozlíšenie (max. 40 megapixelov) ako ochrana proti "decompression bomb" útoku (malý súbor, ktorý sa pri dekódovaní rozvinie do enormnej veľkosti v pamäti).
- Obnova zo zálohy má rovnakú ochranu na úrovni ZIP archívu – pred rozbalením sa overí počet položiek aj ich nekomprimovaná veľkosť (jednotlivo aj spolu), nielen veľkosť nahraného súboru.
- SQLite má explicitne zapnuté vynucovanie `FOREIGN KEY` obmedzení (`PRAGMA foreign_keys=ON`) – bez tejto pragmy by ich SQLite defaultne ignoroval.
- **Audit log** (`/audit-log`, odkaz zo stránky Nastavenia) – zaznamenáva dôležité udalosti: vytvorenie/zmazanie/zmenu stavu faktúry a cenovej ponuky, dobropis, konverziu ponuky na faktúru, zmenu fakturačných údajov firmy, obnovu zo zálohy a prihlásenie/odhlásenie. Appka je jednopoužívateľská, takže záznam nesleduje "kto", len "čo a kedy" – pre spätnú dohľadateľnosť.
- **Kontrola integrity dát** (`/settings/integrity-check`, odkaz zo stránky Nastavenia) – porovná databázu (logo, podpis, fotky zákaziek) so súbormi v `uploads/`: nahlási chýbajúce súbory (DB odkazuje na niečo, čo na disku nie je) aj osirotené súbory (súbor na disku, na ktorý sa DB neodkazuje), s možnosťou osirotené súbory rovno zmazať.

---

## 🧭 Navigácia

| Cesta | Popis |
|---|---|
| `/` | Dashboard – financie (Na inkaso, Po splatnosti, tržby), termíny, posledné zákazky |
| `/zakaznici` | Zoznam zákazníkov (vyhľadávanie, pridanie, auto-doplnenie podľa IČO) |
| `/zakazky` | Zoznam zákaziek (filter podľa stavu/termínu) |
| `/kalendar` | Mesačný kalendár zákaziek podľa termínu realizácie |
| `/faktury` | Zoznam faktúr (vyhľadávanie, filter podľa stavu a obdobia) |
| `/ponuky` | Zoznam cenových ponúk |
| `/settings` | Fakturačné údaje firmy, DPH režim, logo/podpis, záloha/obnova databázy |

---

## 🧾 Fakturácia

1. **Nastavenia firmy** (`/settings`) – názov, IČO, DIČ, IČ DPH, adresa, IBAN, DPH režim (platca/neplatca). Appka udržiava presne jeden riadok fakturačných údajov – vynútené aj na úrovni databázy (`CHECK` constraint), nielen v kóde appky.
2. **DPH režim** – ak nie si platiteľ DPH, appka **nedovolí** účtovať DPH na faktúre (validácia na backende, nie len v UI) a na faktúre sa zobrazí zákonom vyžadovaný text namiesto rozpisu DPH.
3. **Prenesenie daňovej povinnosti** (tuzemské samozdanenie, §69 ods. 12) – voliteľné pri vytváraní faktúry, len ak sú platcami DPH obaja (dodávateľ aj odberateľ).
4. **Vytvorenie faktúry** – z detailu zákazníka, prípadne priamo zo zákazky alebo jedným klikom z akceptovanej cenovej ponuky (ostrá alebo zálohová/proforma).
5. **Číslovanie** – automatické, samostatné rady pre ostré faktúry (`RRRRPPP`, napr. `2026001`), zálohové faktúry (`ZFRRRRPPP`) a cenové ponuky (`CPRRRRPPP`).
6. **Stavy faktúry** – Návrh → Odoslaná → Uhradená/Stornovaná, s explicitne vymenovanými povolenými prechodmi (nedá sa napr. vrátiť odoslanú faktúru späť na návrh). "Po splatnosti" sa **nikdy neukladá** – počíta sa vždy za behu z dátumu splatnosti.
7. **Dátum skutočnej úhrady** – zaznamenáva sa automaticky pri prechode na "Uhradená", dá sa aj ručne opraviť. Tržby na dashboarde sa počítajú podľa tohto dátumu.
8. **Kopírovanie faktúry** – vytvorí nový návrh s rovnakými položkami a dnešným dátumom (napr. na opakovanú mesačnú fakturáciu).
9. **Vyhľadávanie a filtrovanie** – podľa čísla faktúry, mena zákazníka, poznámky, stavu aj obdobia vystavenia.
10. **PDF export** – faktúra aj dodací list (položky bez cien, s podpisovými riadkami), podpora slovenskej diakritiky.
11. **QR platobný kód** – PAY by square, vrátane konštantného (predvolene `0308`) a špecifického symbolu.
12. **Peppol XML export** – návrh/príprava dát vo formáte UBL 2.1 / Peppol BIS Billing 3.0 pre budúcu povinnú e-fakturáciu. Appka sama neposiela cez Peppol sieť – to vyžaduje registráciu cez certifikovaného poskytovateľa ("digitálneho poštára").

---

## 📋 Cenové ponuky

- Samostatný koncept od faktúry, vlastné číslovanie a stavy (Návrh → Odoslaná → Akceptovaná/Zamietnutá → Prevedená na faktúru).
- Dajú sa vytvoriť samostatne alebo naviazané na konkrétnu zákazku.
- Po akceptovaní: **jedným klikom** vygeneruješ ostrú alebo zálohovú faktúru s rovnakými položkami.
- PDF export ponuky aj dodacieho listu.

---

## 🔧 Zákazky

- Fotodokumentácia (pred/po) – ľubovoľný počet fotiek na zákazku, overenie skutočného typu obrázka. Fotky sa pri nahraní automaticky zmenšia (max. 1600px dlhšej strany) a opraví sa ich otočenie podľa EXIF údajov z mobilu.
- Evidencia nákladov (materiál, subdodávky) a výpočet reálneho čistého zisku (fakturovaná suma − náklady).
- Mesačný kalendár podľa termínu realizácie.

---

## 💾 Záloha a obnova databázy

V Nastaveniach (`/settings`):

- **Stiahnutie zálohy** – konzistentná kópia databázy cez natívne SQLite backup API (bezpečné aj počas behu appky).
- **Obnova zo zálohy** – validuje sa, že nahraný súbor je skutočne platná záloha tejto appky. Pred obnovou appka automaticky uloží bezpečnostnú kópiu aktuálneho stavu do `backups/` (negituje sa).

---

## 🏢 Registre a auto-doplnenie

Pri vytváraní zákazníka appka vie podľa zadaného IČO automaticky doplniť názov, adresu, DIČ aj IČ DPH (cez verejné API tretej strany, ktoré agreguje slovenské registre). Ak vyhľadávanie zlyhá alebo je nedostupné, appka sa vždy vráti k ručnému vyplneniu – nikdy to nič nezablokuje.

---

## ✅ Testy

```powershell
python -m pytest tests/ -q
```

Aktuálne **409 testov**, rozdelených podľa oblasti:

| Súbor | Pokrýva |
|---|---|
| `test_main.py` | CRUD zákazníkov a zákaziek, validácia |
| `test_auth.py`, `test_auth_security.py` | Prihlásenie, rate limiting, session bezpečnosť, systematická kontrola všetkých chránených routes |
| `test_csrf.py` | CSRF ochrana (double-submit token) – chýbajúci/nesprávny/cudzí token, správny tok |
| `test_audit_log.py` | Audit log – zápis pri vytvorení/zmazaní/zmene stavu faktúry a ponuky, zmene nastavení firmy, zobrazenie `/audit-log` |
| `test_integrity_check.py` | Kontrola integrity DB ↔ uploads – chýbajúce aj osirotené súbory, vyčistenie, stránka `/settings/integrity-check` |
| `test_invoices.py` | Fakturácia, DPH režim, stavy, PDF, Peppol XML, dátum úhrady, kopírovanie, vyhľadávanie/filter |
| `test_quotes.py` | Cenové ponuky, konverzia na faktúru, proforma |
| `test_jobs_extras.py` | Fotodokumentácia, náklady/zisk, kalendár |
| `test_uploads.py` | Nahrávanie loga/podpisu/fotiek zákaziek, overenie typu súboru, auto-resize a EXIF orientácia fotiek |
| `test_ico_lookup.py` | Auto-doplnenie podľa IČO (mockované externé API) |
| `test_backup.py` | Záloha a obnova databázy (izolované na dočasnom súbore), ochrana proti zip bomb, audit log záznam o obnove |

Testy bežia proti oddelenej in-memory SQLite databáze (alebo izolovanému dočasnému súboru pri zálohe/obnove) a nikdy nezasahujú do reálnych dát appky. `tests/conftest.py` pre testy vynucuje `SESSION_HTTPS_ONLY=false` (testovací klient beží cez obyčajné `http://`, nie `https://`).

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
│   ├── auth.py          # /login, /logout, rate limiting
│   ├── company.py       # /settings - fakturačné údaje, záloha/obnova DB, /audit-log, /settings/integrity-check
│   ├── customers.py     # CRUD zákazníkov, auto-doplnenie podľa IČO
│   ├── invoices.py      # CRUD faktúr, PDF, Peppol XML, kopírovanie, vyhľadávanie
│   ├── jobs.py           # CRUD zákaziek, fotky, náklady, kalendár
│   └── quotes.py        # CRUD cenových ponúk, konverzia na faktúru
│
├── static/
│   ├── fonts/            # DejaVu Sans (diakritika v PDF)
│   ├── logo-icon.png
│   └── style.css
│
├── templates/            # Jinja2 šablóny (jedna na stránku)
│
├── tests/                # pytest, viď sekcia Testy vyššie
│   └── conftest.py       # SESSION_HTTPS_ONLY=false pre testy (viď sekcia Testy)
│
├── uploads/              # logo, podpis, fotky zákaziek (negituje sa)
├── backups/              # automatické zálohy pred obnovou (negituje sa)
│
├── auth.py                 # hashovanie hesla, session, rate limiting
├── audit_log.py             # zápis do audit logu (história zmien)
├── backup_utils.py         # záloha/obnova cez SQLite backup API
├── csrf.py                  # CSRF ochrana (double-submit token)
├── database.py
├── delivery_note_pdf.py    # PDF dodacieho listu (z faktúry aj ponuky)
├── form_utils.py           # zdieľané parsovanie formulárov (faktúry aj ponuky)
├── ico_lookup.py           # auto-doplnenie firmy podľa IČO
├── integrity_check.py       # kontrola DB <-> uploads (chýbajúce/osirotené súbory)
├── invoice_pdf.py          # generovanie PDF faktúr
├── invoice_utils.py        # číslovanie, DPH výpočty, stavové prechody
├── main.py                 # dashboard, routing
├── models.py                # SQLAlchemy modely
├── peppol_xml.py            # export do Peppol BIS 3.0 XML formátu
├── qr_payment.py             # QR platobný kód (PAY by square)
├── quote_pdf.py               # generovanie PDF cenových ponúk
├── schemas.py                  # Pydantic modely (validácia)
├── templates_config.py         # zdieľaná Jinja2Templates inštancia
├── uploads_utils.py             # nahrávanie/overenie obrázkov
│
├── .env.example
├── .gitignore
├── alembic.ini
├── requirements.txt
└── README.md
```
