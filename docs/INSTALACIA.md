# Čistá inštalácia

Postup od prázdneho počítača po bežiacu appku. Overené skutočným prechodom: rozbalenie distribučného balíka, nové virtuálne prostredie, inštalácia závislostí, migrácie, štart, založenie konta, prihlásenie. Na Linuxe s Pythonom 3.13; CI testuje Python 3.12 (ten používa aj Docker obraz).

## Požiadavky

- Python **3.12 alebo novší** (`python --version`)
- Git nie je potrebný, stačí rozbalený balík
- Pre ostrú prevádzku: server s HTTPS (Railway, alebo vlastný server cez `docker-compose.yml`, pozri `docs/HOSTING.md`)

## 1. Rozbaľ a nainštaluj

Windows (PowerShell):

```powershell
Expand-Archive digitalny-zosit-RRRR-MM-DD.zip .
cd digitalny-zosit
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Linux / macOS:

```bash
unzip digitalny-zosit-RRRR-MM-DD.zip && cd digitalny-zosit
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Ak PowerShell odmietne aktivovať prostredie, spusti raz `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`. Kontrola integrity závislostí: `pip check` (má vypísať „No broken requirements found“).

## 2. Nastav `.env`

Otvor `.env` a nastav minimálne:

```
SECRET_KEY=<výsledok príkazu nižšie>
APP_ENV=development
SESSION_HTTPS_ONLY=false
```

Kľúč vygeneruješ takto: `python -c "import secrets; print(secrets.token_hex(32))"`.

`SESSION_HTTPS_ONLY=false` je potrebné **len pri lokálnom behu cez `http://localhost`**. S predvolenou hodnotou `true` prehliadač cookie cez http neuloží a prihlásenie skončí chybou 403. Na ostrom serveri s HTTPS ho nechaj `true` (a `APP_ENV=production`).

Ďalšie premenné a ich význam: README (sekcia „Priprav `.env` súbor“) a `docs/KONFIGURACIA.md`.

## 3. Inicializuj databázu kont

```
alembic -c alembic_accounts.ini upgrade head
```

Bez tohto kroku appka naštartuje s hláškou „Databáza kont nie je inicializovaná“. Databáze jednotlivých kont sa vytvárajú a migrujú samy pri založení konta.

## 4. Spusti

```
uvicorn main:app
```

Appka beží na `http://127.0.0.1:8000`. Kontrola: `http://127.0.0.1:8000/health` vráti `{"status":"ok"}`.

## 5. Založ prvé konto

Podľa spôsobu používania (pozri `docs/KONFIGURACIA.md`):

- **Cez web:** otvor `/register` (kým je `REGISTRATION_ENABLED` zapnuté, čo je predvolené).
- **Z príkazového riadku:** `python manage_accounts.py --create-account MENO` (opýta sa na heslo). Funguje aj pri vypnutej registrácii.

Správu ďalších kont (zoznam, nové heslo, vypnutie konta) robí správca cez Nastavenia → Správa kont. Zobrazí sa kontu, ktorého meno je v `SERVER_ADMINS`.

Potom sa prihlás a vyplň **Nastavenia** (fakturačné údaje firmy, IBAN, logo). Bez nich sa faktúry nedajú vystaviť.

## 6. Spusti testy (voliteľné)

```
pip install -r requirements-dev.txt
python -m pytest -q
```

Na Windowse pridaj `--basetemp=.pytest_tmp`, inak pytest môže pri upratovaní dočasného priečinka skončiť chybou prístupu. Dva testy Docker entrypointu sa mimo Linuxu s rootom preskočia a to je v poriadku.

## 7. Ostrá prevádzka

1. Vyber hosting: **Railway** (postup v README, sekcia „Nasadenie na Railway“) alebo **vlastný server** (`docs/HOSTING.md`, Docker Compose s Caddy a automatickým HTTPS).
2. Nastav premenné podľa profilu v `docs/KONFIGURACIA.md` a over ich: `python tools/check_config.py --profile managed`.
3. Zapni zálohy mimo servera (`OFFSITE_BACKUP_*`, `BACKUP_ENCRYPTION_KEY`) a vyskúšaj obnovu: `docs/OBNOVA.md`, `python offsite_restore.py --verify`.
4. Nastav monitoring (`MONITOR_TOKEN`, UptimeRobot na `/health/details?token=...`): `docs/PREVADZKA.md`.

## Riešenie problémov

| Príznak | Príčina a oprava |
|---|---|
| Prihlásenie skončí `403 Forbidden` (lokálne cez http) | `SESSION_HTTPS_ONLY=true` pri behu cez `http://`; nastav `false` |
| `SECRET_KEY nie je nastavený` alebo `je slabý` | Vygeneruj kľúč príkazom z kroku 2; v produkcii sa slabý kľúč odmietne |
| `Databáza kont nie je inicializovaná` | Spusti krok 3 |
| `Neplatná hodnota APP_ENV / REGISTRATION_ENABLED / MAX_ACCOUNTS` | Preklep v premennej; appka radšej nenaštartuje, než by sa spustila v zlom režime |
| `Address already in use` | Port 8000 používa iný program: `uvicorn main:app --port 8001` |
| Po aktualizácii kódu chýba stĺpec alebo tabuľka | `python manage_accounts.py --upgrade-all` (na Docker/Railway to robí `start.sh` sám) |
