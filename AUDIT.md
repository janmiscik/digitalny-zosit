# Technický audit (krok 10)

Rozsah: bezpečnosť, databázy, trvalé úložisko, testy, závislosti. Stav k 19. 10. 2026, 723 testov prechádza.

## Opravené

| Oblasť | Nález | Oprava |
|---|---|---|
| Závislosti | Pillow 12.1.1 a pypdf 5.9.0 mali známe zraniteľnosti (`pip-audit`); do produkčného obrazu sa inštalovali aj pytest, pypdf a pod. | Pillow 12.3.0; pypdf už nie je v produkčnom obraze; testovacie balíky presunuté do `requirements-dev.txt`. `pip-audit`: žiadne známe zraniteľnosti. |
| Heslá | PBKDF2 len 200 000 iterácií, bez verzie formátu | 600 000 iterácií, formát `pbkdf2_sha256$iter$soľ$hash`; staré hashe fungujú a pri prihlásení sa prehashujú; poškodený hash nikdy nespadne ani nespustí extrémny výpočet |
| Prihlásenie | Limit len podľa IP (za proxy sa dá obchádzať) | Pridaný limit podľa mena konta (20 pokusov / 15 min) |
| Registrácia | Neobmedzené zakladanie kont (každé = databáza + drahé hashovanie) | Max. 5 kont / hodinu / IP |
| Session | Pri prihlásení sa starý obsah session (aj CSRF token) preniesol | Session sa pri prihlásení vymení (session fixation) |
| Hlavičky | Žiadne bezpečnostné hlavičky | `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, HSTS pri HTTPS |
| Informácie | `/docs`, `/redoc`, `/openapi.json` boli verejné a vypisovali všetky trasy | Vypnuté; zapne ich `ENABLE_API_DOCS=true` |
| SQLite | Bez `busy_timeout`: súbeh požiadavky a plánovača záloh mohol skončiť „database is locked“ | Timeout 15 s pre databázu kont aj pre databázy kont-firiem |
| Testy | Limit registrácií by sa v testoch míňal naprieč súbormi | `conftest.py` vynuluje počítadlá pred každým testom |

## Skontrolované a v poriadku

- Multi-tenancy: databáza sa vyberá podľa konta zo session; súbory kont sú v oddelených priečinkoch.
- CSRF na všetkých stavových požiadavkách; nahrávanie fotiek ide cez multipart.
- Nahrávanie: obsah sa overuje cez Pillow, limit 2 MB sa kontroluje priebežne, limit rozlíšenia proti decompression bomb, názvy súborov z whitelistu, kontrola cesty proti path traversal.
- Obnova zo zálohy: limity počtu a veľkosti položiek ZIP-u, `PRAGMA integrity_check`.
- Žiadne SQL zostavované z užívateľského vstupu (f-stringy v SQL len s názvami tabuliek z `sqlite_master`).
- Názvy súborov v `Content-Disposition` pochádzajú zo servera (číslo faktúry, dátumy), nie z používateľského vstupu.
- Export: texty začínajúce `=`, `+`, `-`, `@` sa neutralizujú.
- Chyby 500 nezobrazujú detaily výnimiek.

## Odporúčania (nerobil som ich)

1. **Záloha mimo Railway.** Zálohy ležia na tom istom Volume ako dáta. Pri strate Volume zmiznú spolu s dátami. Pravidelne si sťahuj zálohu v Nastaveniach, alebo doplň odosielanie do S3/Backblaze.
2. **Overenie Volume.** V Railway logu hľadaj riadky `[ULOZISKE]`: musí tam byť „OK: /app/data je samostatný disk (Volume)“. Ak je tam VAROVANIE, dáta sa po redeployi stratia.
3. **Jedna replika.** Limity pokusov a plánovač záloh sú v pamäti procesu; pri škálovaní na viac replík by sa nezdieľali (a SQLite na Volume by aj tak nezvládla viac zapisovateľov). Nechaj 1 repliku.
4. **CSP.** Šablóny majú inline skripty a štýly; striktná Content-Security-Policy by ich rozbila. Vyžadovalo by to presun skriptov do `static/` alebo nonce.
5. **Zmena hesla a obnova zabudnutého hesla** v appke nie sú; bez e-mailového odosielania sa dá riešiť len manuálne cez `manage_accounts.py`.
6. **WAL režim SQLite** by zlepšil súbeh čítania a zápisu, ale mení správanie záloh a obnovy (súbory `-wal`/`-shm`), preto som ho nezapínal bez samostatného testu na Railway.
7. V koreni projektu leží osamotený `invoice_detail.html` (starý duplikát `templates/invoice_detail.html`); appka ho nepoužíva, dá sa zmazať.

## Dodatok: nálezy z externého posúdenia

| Bod | Výsledok |
|---|---|
| Obnova po strate úložiska | Postup je otestovaný end-to-end (`test_disaster_recovery.py`) a popísaný v `docs/OBNOVA.md`. Pri tom sa našli a opravili dve chyby: záloha zo staršej verzie appky sa po obnove nemigrovala (appka by padala na chýbajúcich stĺpcoch) a poškodené logo zhodilo tvorbu PDF. Obnova teraz po nahraní migruje schému a pri zlyhaní vráti pôvodný stav. **Zostáva riziko, že zálohy ležia na tom istom Volume** – treba ich sťahovať mimo Railway alebo doplniť automatický export (S3/Backblaze). |
| Účtovná správnosť | **Chyba opravená:** DPH sa zaokrúhľovalo po riadkoch, nie po sadzbe. Pri veľa malých položkách vychádzalo o centy inak než v rekapitulácii a než vyžaduje Peppol pravidlo BR-CO-17 (napr. 3 × 0,10 € pri 23 %: 0,06 € namiesto 0,07 €). Teraz sa DPH počíta zo súčtu základov danej sadzby. Pridaná kontrola, že dobropisy spolu nepresiahnu pôvodnú faktúru. Číslovanie (RRRRPPP, DP…, ZF…, CP…) je po rokoch a typoch oddelené a pri kolízii sa bezpečne opakuje. **Nie je to daňové poradenstvo** – správnosť pre konkrétneho používateľa (režim DPH, prenesenie daňovej povinnosti, zahraniční odberatelia) má potvrdiť účtovník. |
| Reprodukovateľnosť testov | `pay-by-square` je v `requirements.txt` (0.2.0); chýbal len v prostredí bez inštalácie závislostí. Pridané GitHub Actions s čistou inštaláciou a `pip-audit`. Testy prešli aj v novom virtuálnom prostredí s pripnutými verziami. |
| Migrácie a súbežnosť | Testy: dáta z každej z 19 revízií sa po migrácii na head zachovajú (integrity + foreign key check), `--upgrade-all` a štart appky pokračujú po zlyhaní jedného konta, 6 súbežných zapisovačov počas nepretržitých záloh bez „database is locked“ a bez straty riadkov. SQLite migrácie nie sú atomické (alembic: „non-transactional DDL“), preto pred migráciou vzniká poistná záloha a migrácie majú ochrany proti opakovaniu. |
