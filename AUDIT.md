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
| Hlavičky | Žiadne bezpečnostné hlavičky | `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, HSTS pri HTTPS, praktická CSP pri HTML stránkach |
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
4. **CSP.** Zavedená je praktická CSP (cudzie domény zakázané, výnimka Google Fonts), overená v prehliadači na 16 stránkach. Striktná verzia bez `'unsafe-inline'` vyžaduje prepísať 23 šablón (inline skripty) a 57 inline handlerov do súborov v `static/`; zatiaľ sa to neoplatí, lebo Jinja escapuje výstup a `|safe` sa nikde nepoužíva.
5. **Zmena hesla** v Nastaveniach pribudla. **Obnova zabudnutého hesla cez e-mail** pribudla ako voliteľná (zapne sa nastavením `SMTP_*` a `APP_BASE_URL`); konto bez nastaveného e-mailu sa obnovuje manuálne cez `manage_accounts.py`. Zmena aj obnova hesla odhlásia ostatné zariadenia (pozri dodatok o platnosti prihlásení).
6. **WAL režim SQLite** by zlepšil súbeh čítania a zápisu, ale mení správanie záloh a obnovy (súbory `-wal`/`-shm`), preto som ho nezapínal bez samostatného testu na Railway.
7. V koreni projektu leží osamotený `invoice_detail.html` (starý duplikát `templates/invoice_detail.html`); appka ho nepoužíva, dá sa zmazať.

## Dodatok: nálezy z externého posúdenia

| Bod | Výsledok |
|---|---|
| Obnova po strate úložiska | Postup je otestovaný end-to-end (`test_disaster_recovery.py`) a popísaný v `docs/OBNOVA.md`. Pri tom sa našli a opravili dve chyby: záloha zo staršej verzie appky sa po obnove nemigrovala (appka by padala na chýbajúcich stĺpcoch) a poškodené logo zhodilo tvorbu PDF. Obnova teraz po nahraní migruje schému a pri zlyhaní vráti pôvodný stav. Zálohy ležia na tom istom Volume, preto pribudlo voliteľné automatické nahrávanie mimo servera (`offsite_backup.py`, S3-kompatibilné: Backblaze B2, R2, AWS S3) s kontrolou veľkosti a retenciou. **Je vypnuté, kým nenastavíš `OFFSITE_BACKUP_*` v Railway** – dovtedy appka v Nastaveniach upozorňuje, že zálohy sú len na tom istom disku. |
| Účtovná správnosť | **Chyba opravená:** DPH sa zaokrúhľovalo po riadkoch, nie po sadzbe. Pri veľa malých položkách vychádzalo o centy inak než v rekapitulácii a než vyžaduje Peppol pravidlo BR-CO-17 (napr. 3 × 0,10 € pri 23 %: 0,06 € namiesto 0,07 €). Teraz sa DPH počíta zo súčtu základov danej sadzby. Pridaná kontrola, že dobropisy spolu nepresiahnu pôvodnú faktúru. Číslovanie (RRRRPPP, DP…, ZF…, CP…) je po rokoch a typoch oddelené a pri kolízii sa bezpečne opakuje. **Nie je to daňové poradenstvo** – správnosť pre konkrétneho používateľa (režim DPH, prenesenie daňovej povinnosti, zahraniční odberatelia) má potvrdiť účtovník. |
| Reprodukovateľnosť testov | `pay-by-square` je v `requirements.txt` (0.2.0); chýbal len v prostredí bez inštalácie závislostí. Pridané GitHub Actions s čistou inštaláciou a `pip-audit`. Testy prešli aj v novom virtuálnom prostredí s pripnutými verziami. |
| Migrácie a súbežnosť | Testy: dáta z každej z 19 revízií sa po migrácii na head zachovajú (integrity + foreign key check), `--upgrade-all` a štart appky pokračujú po zlyhaní jedného konta, 6 súbežných zapisovačov počas nepretržitých záloh bez „database is locked“ a bez straty riadkov. SQLite migrácie nie sú atomické (alembic: „non-transactional DDL“), preto pred migráciou vzniká poistná záloha a migrácie majú ochrany proti opakovaniu. |

## Dodatok: číslovanie dokladov a obnova hesla

- **Číslovanie:** ďalšie číslo sa počítalo z textového zoradenia, takže po 999. doklade za rok by sa vracalo stále to isté číslo (`2026999` je textovo väčšie než `20261000`). Teraz sa porovnáva číselne. Kontrola integrity (Nastavenia → Kontrola integrity) novo hlási **medzery v číselných radoch** (faktúry, zálohové faktúry, dobropisy, ponuky), najčastejšie po zmazaní návrhu. Faktúry majú byť číslované súvisle, preto vystavený doklad radšej stornuj než zmaž a dôvod medzery si zapíš.
- **Obnova hesla:** odkaz je podpísaný (`itsdangerous`), platí 1 hodinu a viaže sa na odtlačok súčasného hashu hesla, takže po použití alebo zmene hesla zaniká. Adresa v e-maile sa skladá z `APP_BASE_URL`, nie z hlavičky Host. E-mail sa odosiela na pozadí (rovnaký čas odpovede pre existujúce aj neexistujúce meno). Nastavenie e-mailu konta vyžaduje súčasné heslo, aby ukradnutá session nemohla prevziať účet. Upozornenie: token je v URL a objaví sa v prístupových logoch servera.

## Dodatok: platnosť prihlásení

Prihlásenie je podpísaná cookie. Nová kontrola pri každej požiadavke porovná odtlačok hashu hesla uložený v session s kontom v `accounts.db`. Zmena alebo obnova hesla preto odhlási všetky ostatné zariadenia (zariadenie, z ktorého sa heslo mení, ostáva prihlásené) a session zmazaného konta prestane platiť. Dôsledok pri nasadení: každý je raz odhlásený, lebo staré cookies odtlačok nemajú. Pri prvom prihlásení konta so starým hashom hesla (automatický prehash) sa ostatné zariadenia odhlásia tiež, ide o jednorazový jav.

## Dodatok: overenie off-site zálohy podľa obsahu

Kontrola veľkosti objektu v bucketi nestačí (dve rovnako veľké súbory môžu mať rôzne dáta). Po každom nahratí sa záloha z bucketu znova stiahne, porovná sa jej SHA-256 s lokálnym súborom (hash sa ukladá aj ako metadáta objektu) a stiahnutá kópia prejde plným testom obnovy (`verify_backup_zip`). Až potom sa zálohy v bucketi orežú podľa retencie, takže neoverená záloha nikdy nevytlačí overenú. Výsledok je v Nastaveniach pri „Kópia mimo servera" a pri zlyhaní appka zobrazí upozornenie. Cena je jedno stiahnutie zálohy pri každom zálohovaní (zálohy majú rádovo MB).

## Dodatok: WAL a upratanie

SQLite engine kont aj `accounts.db` teraz používa `journal_mode=WAL` a `synchronous=NORMAL` (spolu s existujúcim `busy_timeout` 15 s). Režim sa do súboru zapíše pri prvom otvorení a je trvalý. Zálohy aj obnova idú cez SQLite backup API, takže s WAL fungujú a kompletná testovacia sada (vrátane obnovy po strate úložiska) prešla bez zmeny. Zmazaný bol osamotený `invoice_detail.html` v koreni projektu (staršia, neúplná kópia `templates/invoice_detail.html`: bez CSRF polí a pripomienok platby).

## Dodatok: izolácia kont a monitoring

- **Izolácia kont:** `tests/test_account_isolation.py` vytvorí dve skutočné kontá a z konta B skúsi každú trasu appky s ID alebo názvom súboru, ktoré patria kontu A (faktúry, ponuky, zákazky, náklady, fotky, logo, stiahnutie záloh, exporty, obnova), vrátane path traversal. Test overuje, že nič z A neuniklo, A zostalo nezmenené a B nič nevzniklo. Zoznam trás sa zbiera z aplikácie automaticky, takže nová trasa s ID sa do testu dostane sama (poistka počtu trás zabráni nepovšimnutému zúženiu). Overené mutáciou: keď `get_db` vždy otvorí databázu konta A, test padne.
- **Monitoring:** `/health/details` (chránený `MONITOR_TOKEN`) hlási zastarané alebo zlyhané zálohy, off-site kópiu a nezmigrované databázy kont pre externý nástroj (503 = poplach). `/health` ostáva jednoduchý, aby Railway kvôli záloham neblokoval nasadenie.

## Dodatok: odolnosť obnovy, číslovanie a obnova hesla

- **Pád procesu počas obnovy:** žurnál obnovy + automatický návrat z poistnej zálohy pri štarte (pred migráciami), testované zabitím procesu v troch bodoch výmeny. Neúspešný návrat hlási monitoring (`restore`).
- **CI:** čistá inštalácia z `requirements-dev.txt` na Pythone 3.12 (rovnaký ako Docker a GitHub Actions) aj 3.13, `pip check` bez chýb, celá sada prechádza.
- **Súbežné číslovanie:** serializácia ukladania dokladu po kontách + UNIQUE s opakovaním, test s 20 vláknami.
- **Medzery v číslovaní** sú upozornenie s možnosťou preverenia (dôvod v audit logu), nie chyba integrity.
- **Obnova hesla:** len cez https `APP_BASE_URL`; atomická jednorazovosť odkazu (podmienený UPDATE) aj pri súbežných požiadavkách; testované chyby SMTP (autentifikácia, spojenie, odmietnutý príjemca, timeout, TLS); zmena hesla odhlási ostatné zariadenia.
- **Externé zálohy:** zámok objektov/verzie v bucketi neznemožnia úspešnú zálohu (chyba mazania starých = upozornenie); odporúčané nastavenie v `docs/PREVADZKA.md`.
- **Prevádzka:** `docs/PREVADZKA.md` popisuje poplachy `/health/details` a postup pri každom z nich. Poznámka: skutočné zapnutie zámku objektov a verzovania v bucketi je nastavenie u poskytovateľa a nedá sa vynútiť z appky.

## Dodatok: závislosti, limity a SECRET_KEY

- **Závislosti:** `pip-audit` (teraz nad `requirements-dev.txt`, ktorý zahŕňa aj produkčné) našiel zraniteľné verzie `pypdf` 5.9.0 (dev), `Pillow` 12.3.0 (produkcia, 35 upozornení) a `certifi` 2026.7.22. Aktualizované na `pypdf` 6.21.2, `Pillow` 13.0.0, `certifi` 2026.10.19, celá sada prechádza a `pip-audit` hlási „No known vulnerabilities found". CI kontroluje aj vývojové závislosti. Pillow je produkčná knižnica (spracovanie nahratých obrázkov), preto táto aktualizácia stojí za nasadenie aj bez ohľadu na ostatné zmeny.
- **Limity obnovy hesla:** `_reset_requests` sa nečistil; teraz ho čistí `_cleanup()` aj samotná žiadosť o obnovu.
- **SECRET_KEY:** v produkcii sa odmietne chýbajúci, krátky (< 32), predvolený/ukážkový (`change-me`, `secret`, `test-secret-key`...) alebo nenáhodný kľúč. `.env.example` už neobsahuje `change-me`. Ak je kľúč v Railway náhodný reťazec z `token_hex(32)` alebo `token_urlsafe(48)`, nič sa nemení; slabý kľúč by znamenal, že appka nenaštartuje.
