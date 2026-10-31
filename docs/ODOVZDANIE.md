# Odovzdávací protokol

Jedna strana pre predávajúceho a kupujúceho. Vyplňa sa pri odovzdaní; vľavo je, čo sa odovzdáva, vpravo kto a čo ešte musí urobiť. Dokument nie je zmluva (tú rieši zmluva o prevode práv alebo licencii, pozri `LICENSE`).

Odovzdaná verzia: ____________ (dátum balíka, napr. `digitalny-zosit-RRRR-MM-DD.zip`)
Kontrolný súčet SHA-256: ____________________________________________

## 1. Čo sa odovzdáva

| Položka | Kde | Poznámka |
|---|---|---|
| Zdrojový kód (čistý balík) | `python tools/make_distribution.py` | bez databáz, `.env`, záloh a cache; vytvorí `.sha256` |
| Dokumentácia | `README.md`, `AUDIT.md`, `docs/` | architektúra: `docs/ARCHITEKTURA.md`; inštalácia: `docs/INSTALACIA.md` |
| Licencie a pôvod | `LICENSE`, `THIRD_PARTY_NOTICES.md`, `third_party/licenses/` | písma sú priložené (SIL OFL), logo pozri bod 3 |
| Testy | `tests/` (spustenie: `python -m pytest -q`) | všetky prechádzajú; na Windowse sa 2 testy Docker entrypointu preskočia (vyžadujú Linux a root, v CI bežia) |
| Repozitár a nasadenie | GitHub `janmiscik/digitalny-zosit`, Railway projekt | prístup sa prevádza oddelene (bod 4) |
| Dáta zákazníkov | **neodovzdávajú sa** | produkčné databázy a zálohy sú dáta prevádzkovateľa; prevod len osobitnou dohodou a podľa GDPR |

## 2. Overenie pred odovzdaním (predávajúci)

Každý riadok sa odškrtne po vlastnom spustení, nie z pamäte.

- [ ] `python -m pytest -q` prejde bez chýb na čistej inštalácii z `docs/INSTALACIA.md`.
- [ ] `python tools/make_distribution.py` skončí bez nálezu tajných údajov.
- [ ] `python tools/check_config.py --profile managed` (alebo profil kupujúceho) nemá `CHYBA`.
- [ ] V logu nasadenia je `[ULOZISKE] OK` a `Beh appky: používateľ=app` (nie root).
- [ ] `/health/details` s tokenom vracia `"status": "ok"` (zálohy, off-site, migrácie, triggery).
- [ ] Zálohy: test obnovy `python offsite_restore.py --verify` prešiel (`docs/OBNOVA.md`).

## 3. Čo musí doplniť predávajúci (nerieši kód)

| Úloha | Stav |
|---|---|
| Nápis „Created by ©JanMi 2026“ z päty aplikácie je odstránený; autorstvo ostáva v `LICENSE` a v README (časť Autor). Logo je vlastné dielo autora a ostáva | hotovo |
| Po zmene súborov v bode vyššie znova vygenerovať `python tools/generate_notices.py` a balík | ☐ |

Mimo kódu (rieši zmluva a web, nie appka), odporúčané dohodnúť v kúpnej zmluve:

- Predávajúci smie projekt uvádzať vo svojom portfóliu (výslovné právo v zmluve).
- Ak sa na `janmi.sk` ponechá odkaz na projekt, zmluva určí, ako sa po predaji uvádza.
- Zachovať informáciu o pôvodnom autorovi v README a dokumentácii (už je).

## 4. Odovzdanie prístupov a tajných hodnôt (predávajúci → kupujúci)

Odovzdáva sa bezpečným kanálom, nie e-mailom s heslami v texte. Po prevzatí kupujúci hodnoty **vymení** a stará strana ich zruší.

| Prístup | Čo urobiť |
|---|---|
| GitHub repozitár | prenos vlastníctva alebo pridanie kupujúceho ako vlastníka; zrušiť osobné tokeny predávajúceho |
| Railway projekt | prenos projektu alebo nový projekt kupujúceho s nasadením z jeho repozitára; Volume sa neprenáša bez exportu dát |
| Cloudflare R2 bucket so zálohami | nový bucket a nové kľúče kupujúceho; staré kľúče zrušiť (`docs/HOSTING.md`) |
| `SECRET_KEY`, `MONITOR_TOKEN`, `OFFSITE_BACKUP_*` | kupujúci vygeneruje nové (`docs/KONFIGURACIA.md`, časť Tajné kľúče pri odovzdaní) |
| `BACKUP_ENCRYPTION_KEY` | **pozor:** zálohy šifrované starým kľúčom sa bez neho nedajú obnoviť. Buď sa kľúč odovzdá a uschová, alebo sa po zmene kľúča urobí nová záloha a stará sa archivuje s označením kľúča |
| Konto správcu v appke | kupujúci si cez `manage_accounts.py --create-account` založí vlastného správcu a nastaví `SERVER_ADMINS`; heslá kont vývoja zmeniť alebo konto vypnúť |
| UptimeRobot (alebo iný monitoring) | kupujúci vytvorí vlastný monitor (`docs/PREVADZKA.md`) |

## 5. Známe obmedzenia (kupujúci berie na vedomie)

Úplný zoznam: `AUDIT.md`, časť „Otvorené body pre kupujúceho", a `docs/ARCHITEKTURA.md`, časť „Známe limity".

- Účtovné pravidlá (DPH, dobropisy, číslovanie, čiastočné úhrady) treba overiť s účtovníkom pred reálnym fakturovaním.
- Jedna replika appky a SQLite; škálovanie na viac replík vyžaduje zmenu architektúry.
- Zámok objektov a verzovanie v bucketi so zálohami sa nastavuje u poskytovateľa, nie v appke.
- IČO lookup beží cez tretiu stranu (ORSF) bez SLA; appka ho nevyžaduje.

## 6. Potvrdenie

| | Predávajúci | Kupujúci |
|---|---|---|
| Meno | | |
| Dátum | | |
| Podpis | | |
