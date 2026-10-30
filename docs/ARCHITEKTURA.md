# Technický prehľad architektúry

Pre vývojára, ktorý appku preberá. Čítaj popri `README.md` (funkcie), `AUDIT.md` (bezpečnosť a stav) a `docs/KONFIGURACIA.md` (premenné).

## Čo to je

Webová appka na faktúry, ponuky, zákazníkov a zákazky pre živnostníkov. Server-renderované stránky (Jinja2), žiadny frontendový framework. **Viac kont na jednom serveri, každé s vlastnou SQLite databázou.** Jeden proces (uvicorn), jedna replika.

| Vrstva | Technológia |
|---|---|
| Web | FastAPI + Starlette, Jinja2 šablóny, vlastný JavaScript v `static/js/` (bez knižníc) |
| Dáta | SQLAlchemy 2, SQLite (WAL), migrácie Alembic (dva samostatné reťazce) |
| PDF | ReportLab (faktúry, ponuky, dodacie listy), QR platba PAY by square |
| Export | openpyxl (Excel), CSV, Peppol BIS 3.0 XML s vlastnou validáciou |
| Zálohy | SQLite backup API, ZIP, šifrovanie AES-GCM (`cryptography`), nahrávanie do S3/R2 (`boto3`) |
| Beh | Docker (neprivilegovaný používateľ), Railway alebo vlastný server (`docker-compose.yml` + Caddy) |

## Najdôležitejší princíp: konto = vlastná databáza

```
                       ┌────────────────────── accounts.db ──────────────────────┐
   prihlásenie ──────► │ accounts: username, slug, password_hash, email, is_active │
                       └───────────────────────────┬──────────────────────────────┘
                                                   │ slug (priečinok konta)
        data/accounts/<slug>/
            database.db        ← faktúry, zákazníci, zákazky, firma, audit_log (business dáta)
            uploads/           ← logo, podpis, fotky zákaziek
            backups/           ← ZIP zálohy konta
            backup-status.json ← stav záloh (číta monitoring)
```

- **`accounts.db`** drží len prihlasovacie údaje (model `Account`, `accounts_models.py`). Nikdy neobsahuje business dáta.
- **Každé konto má vlastný `database.db`** (modely v `models.py`). Izolácia dát je teda fyzická: kód nemá ako omylom siahnuť do dát iného konta.
- **Ktoré konto obsluhujem?** Určuje to `request.session["account_slug"]`. Závislosť `require_login_page` (`auth.py`) ho overí a nastaví do `ContextVar` (`tenancy.set_current_tenant`); `get_db` (`database.py`) z neho vyberie engine konta. Funkcie bez `get_db` (súbory, zálohy) čítajú ten istý `ContextVar` cez `tenancy.get_current_tenant()`. `require_login_page` je zámerne `async`, aby sa `ContextVar` preniesol do obsluhy požiadavky.
- **`tenancy.py`** = cesty, vytvorenie konta (`provision_account`), cache engine-ov, migrácie (`upgrade_account_database`).

## Životný cyklus požiadavky

1. Uvicorn prijme požiadavku (za proxy dôveruje `X-Forwarded-*` len podľa `FORWARDED_ALLOW_IPS`).
2. `SessionMiddleware`: podpísaná cookie so session (`account_slug`, `username`, `pwv`, CSRF token).
3. Middleware bezpečnostných hlavičiek (`main.py`): CSP (striktná pre skripty), `X-Frame-Options`, `Referrer-Policy`, pri HTTPS HSTS.
4. Router. Každý router má `dependencies=[Depends(verify_csrf)]` (CSRF pre všetky meniace metódy) a každá trasa vyžaduje prihlásenie cez `require_login_page` alebo `require_login_api`. Výnimky sú vymenované v `tests/test_route_policy.py` (`PUBLIC_ROUTES`) a test zlyhá, ak pribudne nechránená trasa.
5. `_session_is_current` pri každej požiadavke overí, že konto existuje, je zapnuté (`is_active`) a heslo sa od prihlásenia nezmenilo (odtlačok hashu `pwv`). Zmena hesla alebo vypnutie konta tak okamžite zneplatní všetky prihlásenia.
6. Handler pracuje s `db: Session = Depends(get_db)` (databáza prihláseného konta), zapíše zmeny (`db.commit()`), často aj záznam do audit logu (`audit_log.log_action`).
7. Odpoveď: šablóna (`templates_config.templates`), presmerovanie 303 po formulári alebo súbor (PDF, CSV, XLSX, ZIP).
8. Chyby: `HTTPException` sa pre prehliadač zobrazí ako stránka (`error.html`), neošetrená chyba vráti všeobecnú hlášku, zaloguje traceback a zvýši počítadlo pre monitoring.

## Moduly

| Oblasť | Súbory |
|---|---|
| Vstup a nastavenie | `main.py` (app, middleware, health, lifespan), `security_config.py` (`APP_ENV`, `SECRET_KEY`), `templates_config.py` |
| Prihlásenie a kontá | `auth.py` (hashe hesiel, session, limity pokusov), `routers/auth.py` (login, registrácia, obnova hesla), `account_service.py` (zakladanie kont, heslo, vypnutie), `routers/admin.py` (správa kont), `manage_accounts.py` (CLI), `mailer.py` (SMTP), `csrf.py` |
| Biznis | `routers/customers.py`, `jobs.py`, `invoices.py`, `quotes.py`, `reminders.py`, `export.py`, `company.py` (nastavenia, zálohy, audit, integrita) |
| Výpočty a doklady | `invoice_utils.py` (číslovanie, DPH, prechody stavov), `form_utils.py`, `profit_utils.py`, `dashboard_utils.py`, `reminder_utils.py`, `customer_history.py`, `search_utils.py` |
| Dokumenty | `invoice_pdf.py`, `quote_pdf.py`, `delivery_note_pdf.py`, `qr_payment.py`, `peppol_xml.py`, `peppol_validation.py`, `export_utils.py` |
| Dáta | `models.py`, `schemas.py`, `validators.py` (IČO, DIČ, IBAN), `db_guards.py` (triggery), `database.py`, `tenancy.py`, `accounts_db.py`, `uploads_utils.py` |
| Zálohy a prevádzka | `backup_utils.py` (ZIP, obnova), `auto_backup.py` (plánovač, test obnovy, retencia), `backup_crypto.py`, `offsite_backup.py`, `offsite_restore.py`, `decrypt_backup.py`, `monitoring.py`, `integrity_check.py`, `diagnose_storage.py`, `log_filters.py` |
| Nástroje | `tools/make_distribution.py`, `tools/generate_notices.py`, `tools/check_config.py` |

## Doménový model (business databáza konta)

`Customer` (+ `CustomerNote`) → `Job` (+ `JobPhoto`, `JobCost`) → `Invoice` (+ `InvoiceItem`, `InvoicePayment`) a `Quote` (+ `QuoteItem`); `Company` je jediný riadok s fakturačnými údajmi (singleton, `id = 1`); `AuditLog` je história zmien.

- **Doklady v jednej tabuľke:** `Invoice` pokrýva faktúru, zálohovú faktúru (`is_proforma`) aj dobropis (`is_credit_note`, `original_invoice_id`). Ponuka sa konvertuje na faktúru (`quote_id`).
- **Stavy faktúry:** Návrh → Odoslaná → Uhradená, kedykoľvek Stornovaná (konečný stav). Povolené prechody sú v `invoice_utils.ALLOWED_INVOICE_STATUS_TRANSITIONS`. Čiastočné úhrady sú v samostatnej tabuľke `InvoicePayment` (faktúra sa nemení): zostatok = suma − súčet úhrad (`invoice_utils.invoice_balance`), zostatok 0 prepne faktúru na Uhradená. Len bežné faktúry.
- **Číslovanie:** `next_invoice_number` a súrodenci počítajú číselne podľa roka; ukladanie je serializované po kontách a `UNIQUE` s opakovaním pri kolízii.
- **Súčty:** všetky idú cez jednu funkciu v `invoice_utils.py` (DPH sa zaokrúhľuje zo súčtu základov danej sadzby, nie po riadkoch, kvôli Peppol BR-CO-17). PDF, export, dashboard aj Peppol ju zdieľajú.
- **Ochrana vystavených faktúr:** na troch vrstvách. Aplikačná logika (prechody stavov), ORM hook v `models.py` a **databázové triggery** v `db_guards.py` (vystavenú faktúru nemožno zmazať, vrátiť do návrhu ani meniť jej položky; `audit_log` je append-only). Triggery sa inštalujú migráciou aj pri `create_all` a ich prítomnosť kontroluje monitoring.

## Migrácie

Dva samostatné reťazce Alembic:

| Reťazec | Konfigurácia | Čo migruje | Kedy beží |
|---|---|---|---|
| `alembic_accounts/` | `alembic_accounts.ini` | `accounts.db` | `start.sh` pri štarte; pri lokálnom `uvicorn main:app` ju v `lifespan` doplní appka sama (`accounts_db.upgrade_accounts_database`, s kópiou súboru); ručne `alembic -c alembic_accounts.ini upgrade head` |
| `alembic/` | `alembic.ini` | `database.db` **každého konta** | pri založení konta; `manage_accounts.py --upgrade-all` (štart kontajnera) a pri štarte appky (`AUTO_MIGRATE_ACCOUNTS`) |

Pravidlá: appka nikdy nevolá `create_all()` (schému spravuje len Alembic). Migrácie sú idempotentné. Pred migráciou kont s čakajúcou zmenou sa spraví poistná záloha (`--kind pre-upgrade`). Alembic nie je bezpečný pre súbežný beh vo vláknach, preto všetky migrácie v procese idú cez `tenancy._migration_lock`. Pri novej migrácii pridaj test, že dáta zo starej revízie prežijú (`tests/test_migrations_concurrency.py`).

## Štart kontajnera (`start.sh`)

1. `diagnose_storage.py`: do logu `[ULOZISKE]` (je `/app/data` samostatný disk?).
2. `alembic -c alembic_accounts.ini upgrade head`.
3. `manage_accounts.py --recover-restores`: vráti konto po prerušenej obnove.
4. Poistná záloha kont s čakajúcou migráciou.
5. `manage_accounts.py --upgrade-all`: migrácia všetkých kont.
6. `uvicorn main:app`. V `lifespan` (`main.py`): kontrola `accounts.db`, návrat po prerušenej obnove, migrácie, riadok o monitoringu v logu a štart plánovača záloh.

`docker-entrypoint.sh` beží ako root len na opravu vlastníka Volume, potom pustí `start.sh` pod používateľom `app`.

## Zálohy a obnova

- **Plánovač** (`auto_backup.BackupScheduler`, vlákno v procese): raz za `BACKUP_INTERVAL_HOURS` zálohuje každé konto cez SQLite backup API (konzistentné aj počas zápisov) do ZIP s databázou a `uploads/`.
- **Test obnovy po každej zálohe** (`verify_backup_zip`): integrita DB, počty riadkov, existencia príloh, čitateľnosť cez modely.
- **Off-site** (`offsite_backup.py`): voliteľné šifrovanie (`backup_crypto.py`, formát `DZENC1`, AES-GCM po blokoch), nahratie do S3/R2, stiahnutie späť, porovnanie SHA-256 a test obnovy na stiahnutej (rozšifrovanej) kópii; až potom retencia.
- **Obnova** (`backup_utils.restore_from_upload`): staging, žurnál obnovy, poistná záloha, migrácia na aktuálnu schému, atomická výmena; pri páde sa pri štarte stav vráti. Šifrovaná záloha sa rozpozná podľa obsahu.
- **Mimo appky:** `offsite_restore.py` (zoznam, stiahnutie, `--verify`) a `decrypt_backup.py`. Postupy: `docs/OBNOVA.md`, poplachy: `docs/PREVADZKA.md`.

## Bezpečnostné vrstvy

Session cookie (podpísaná, `SameSite=Lax`, HTTPS-only v produkcii), CSRF na každej meniacej trase, hashe hesiel PBKDF2 (600 000 iterácií) s tichým prehashovaním, limity pokusov (IP aj meno), politika hesiel, validácia nahrávaných obrázkov (Pillow), striktná CSP, izolácia kont, audit log, triggery, `APP_ENV` s vynútením silného `SECRET_KEY`. Podrobnosti a nálezy: `AUDIT.md`.

## Monitoring

`/health` (verejný, len „žije“) a `/health/details` (token): zálohy, off-site, nedokončená obnova, migrácie, ochranné triggery, chyby 500 za hodinu, miesto na disku, plánovač, beh ako root. Stav 503 = poplach pre UptimeRobot. Počítadlo chýb sa ukladá do `server-errors.log` vo Volume.

## Testy

`tests/` (pytest, ~1500 testov, `conftest.py` izoluje databázy, vypína zálohy a nuluje limity). Dôležité „poistky“, ktoré zlyhajú pri nedbalej zmene: `test_route_policy.py` (každá trasa chránená, každá meniaca trasa má CSRF), `test_account_isolation.py` (konto B nemôže nič z konta A), `test_csp_inline_budget.py` (žiadne inline skripty), `test_db_guards_migration.py` (triggery v starých databázach), `test_distribution.py` (čistý balík a licencie). Zoznam všetkých: README, sekcia Testy. CI: `.github/workflows/tests.yml` (inštalácia z čista, `pip-audit`, Docker build).

## Ako pridať…

| Úloha | Postup |
|---|---|
| Novú stránku | Trasa v routeri (`require_login_page`, `get_db`), šablóna v `templates/` s `<script src=…app.js>` bez inline skriptov; skript stránky do `static/js/pages/` a `DZ.register({...})`; test trás sa upraví sám (nová trasa musí byť chránená) |
| Nové pole v databáze | Zmena v `models.py` + migrácia v `alembic/versions/` (idempotentná) + test, že stará revízia prežije; pre `Account` ide o `alembic_accounts/` |
| Novú premennú prostredia | Použi ju v kóde, doplň `.env.example`, README a `tools/check_config.py` (ak ovplyvňuje produkciu) |
| Nový typ dokladu | `invoice_utils.py` (číslovanie, stavy), súčty cez jednu spoločnú funkciu, PDF a export, test súm (`test_totals_property.py`) |
| Novú akciu v audit logu | `log_action(db, "oblast.akcia", entity_type=…, detail=…)`; nikdy nezapisuj heslá ani osobné údaje |

## Známe limity

- **Jedna replika.** Limity pokusov, zámok registrácie, počítadlo chýb a plánovač sú v pamäti procesu; SQLite zvláda jedného zapisovateľa. Škálovanie na viac replík vyžaduje zmenu architektúry (zdieľaná databáza, externý plánovač a limity).
- **Čiastočné úhrady** sú len pri bežných faktúrach (bez preplatkov, zálohová faktúra a dobropis ich nemajú).
- **IČO lookup** ide cez tretiu stranu (ORSF, bez SLA), appka kvôli nemu nikdy nepadá.
- **Ochrana proti priamemu zásahu do súboru databázy** (triggery sa dajú zrušiť) a zámok objektov v bucketi sú nastavenia prevádzky, nie kód.
