# Technický audit (krok 10)

Rozsah: bezpečnosť, databázy, trvalé úložisko, testy, závislosti. Pôvodný stav k 19. 10. 2026 (v čase auditu 723 testov). Odvtedy pribudli dodatky nižšie; aktuálny počet testov je v README. Odporúčania v tomto zozname, ktoré sa medzitým vyriešili, sú označené ako vyriešené.

## Aktuálny stav (27. 10. 2026)

Súhrn pre nového vlastníka. Podrobnosti a história sú v dodatkoch nižšie.

| Oblasť | Stav |
|---|---|
| Testy | 1496 testov; 2 testy Docker entrypointu vyžadujú Linux a root (v CI bežia a nesmú sa preskočiť) |
| Závislosti | `pip-audit` pri poslednej kontrole bez známych zraniteľností (pozri dodatok o závislostiach); CI ho spúšťa |
| CSP | striktná pre skripty (žiadne inline skripty ani handlery), hlásenia na `/csp-report` |
| Izolácia kont | sweep všetkých trás s platnými formulármi, databáza a súbory cudzieho konta ostanú bajt po bajte rovnaké |
| Databáza | WAL, cudzie kľúče, triggery proti zmene vystavených faktúr a audit logu; ich prítomnosť kontroluje monitoring |
| Zálohy | denné, šifrované (AES-GCM), off-site do S3/R2 s overením SHA-256 a skúškou obnovy; `offsite_restore.py --verify` |
| Kontá | zakladá majiteľ servera (`/admin/accounts` alebo `manage_accounts.py --create-account`), verejná registrácia sa vypína `REGISTRATION_ENABLED=false` |
| Monitoring | `/health/details` (token) hlási zálohy, off-site, migrácie, triggery, disk, plánovač, beh ako root; upozornenia e-mailom cez externý nástroj (UptimeRobot) |
| Konfigurácia | `python tools/check_config.py --profile managed\|self-service\|single` skontroluje premenné prostredia (tajné kľúče, registrácia, správca, zálohy, e-mail); popis v `docs/KONFIGURACIA.md` |
| Architektúra | `docs/ARCHITEKTURA.md`: vrstvy, izolácia kont, životný cyklus požiadavky, migrácie, zálohy, testy, postupy „ako pridať…“ a známe limity pre nového vývojára. |
| Odovzdanie | `docs/ODOVZDANIE.md`: protokol s obsahom odovzdania, kontrolným zoznamom, úlohami predávajúceho, odovzdaním prístupov a tajných hodnôt a potvrdením. |
| Licencie | `LICENSE`, `THIRD_PARTY_NOTICES.md`, `third_party/licenses/`; čistý balík: `python tools/make_distribution.py` |

**Otvorené body pre kupujúceho (nerieši kód):**

1. **Účtovné pravidlá** (sadzby a režimy DPH, dobropisy, číslovanie) overiť s účtovníkom pred reálnym fakturovaním. Testy nie sú daňové potvrdenie.
2. **Čiastočné úhrady** sú podporované len pri bežných faktúrach (nie zálohová faktúra a dobropis, žiadne preplatky); pravidlá overiť s účtovníkom (napr. započítanie zálohy).
3. **Pôvod loga** (`static/logo-icon.png`, generované v Google Gemini: doplniť dátum, verziu služby a overiť podmienky použitia) a doplnenie mena držiteľa práv v `LICENSE` pred odovzdaním (pozri `THIRD_PARTY_NOTICES.md`).
4. **Jedna replika:** limity pokusov, zámok registrácie a plánovač sú v pamäti procesu; SQLite zvláda len jedného zapisovateľa. Pre viac replík treba zmeniť architektúru.
5. **Zámok objektov a verzovanie v bucketi** (ochrana záloh pred zmazaním) je nastavenie u poskytovateľa záloh, appka ho vynútiť nevie.
6. **Písma** sú od 28. 10. 2026 súčasťou balíka (`static/fonts/web/`, SIL OFL 1.1), žiadne spojenie na Google; nič na riešenie.
7. **Tajné kľúče** (`SECRET_KEY`, `BACKUP_ENCRYPTION_KEY`, `OFFSITE_BACKUP_*`, `MONITOR_TOKEN`) si kupujúci vygeneruje a uloží sám; po odovzdaní je vhodné ich všetky vymeniť.

## Opravené

| Oblasť | Nález | Oprava |
|---|---|---|
| Závislosti | Pillow 12.1.1 a pypdf 5.9.0 mali známe zraniteľnosti (`pip-audit`); do produkčného obrazu sa inštalovali aj pytest, pypdf a pod. | Pillow 12.3.0 (neskôr 13.0.0, pozri dodatok o závislostiach); pypdf už nie je v produkčnom obraze; testovacie balíky presunuté do `requirements-dev.txt`. `pip-audit`: žiadne známe zraniteľnosti. |
| Heslá | PBKDF2 len 200 000 iterácií, bez verzie formátu | 600 000 iterácií, formát `pbkdf2_sha256$iter$soľ$hash`; staré hashe fungujú a pri prihlásení sa prehashujú; poškodený hash nikdy nespadne ani nespustí extrémny výpočet |
| Prihlásenie | Limit len podľa IP (za proxy sa dá obchádzať) | Pridaný limit podľa mena konta (20 pokusov / 15 min) |
| Registrácia | Neobmedzené zakladanie kont (každé = databáza + drahé hashovanie) | Max. 5 kont / hodinu / IP |
| Session | Pri prihlásení sa starý obsah session (aj CSRF token) preniesol | Session sa pri prihlásení vymení (session fixation) |
| Hlavičky | Žiadne bezpečnostné hlavičky | `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, HSTS pri HTTPS, CSP pri HTML stránkach (od 26. 10. 2026 striktná pre skripty, pozri „CSP po etapách“) |
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

1. **Záloha mimo Railway** (vyriešené: pribudlo voliteľné nahrávanie do S3/R2 s kontrolou SHA-256, pozri dodatky). Pôvodný nález: zálohy ležia na tom istom Volume ako dáta. Pri strate Volume zmiznú spolu s dátami. Pravidelne si sťahuj zálohu v Nastaveniach, alebo doplň odosielanie do S3/Backblaze.
2. **Overenie Volume.** V Railway logu hľadaj riadky `[ULOZISKE]`: musí tam byť „OK: /app/data je samostatný disk (Volume)“. Ak je tam VAROVANIE, dáta sa po redeployi stratia.
3. **Jedna replika.** Limity pokusov a plánovač záloh sú v pamäti procesu; pri škálovaní na viac replík by sa nezdieľali (a SQLite na Volume by aj tak nezvládla viac zapisovateľov). Nechaj 1 repliku.
4. **CSP** (vyriešené: skripty a handlery sú v `static/js/`, vynucuje sa `script-src 'self'` a `script-src-attr 'none'`, pozri „CSP po etapách“). Pôvodný nález: praktická CSP s `'unsafe-inline'`, lebo šablóny mali 23 inline skriptov a 57 inline handlerov. Inline štýly ostávajú povolené (nižšie riziko).
5. **Zmena hesla** v Nastaveniach pribudla. **Obnova zabudnutého hesla cez e-mail** pribudla ako voliteľná (zapne sa nastavením `SMTP_*` a `APP_BASE_URL`); konto bez nastaveného e-mailu sa obnovuje manuálne cez `manage_accounts.py`. Zmena aj obnova hesla odhlásia ostatné zariadenia (pozri dodatok o platnosti prihlásení).
6. **WAL režim SQLite** (vyriešené: zapnutý, pozri Dodatok: WAL a upratanie). Pôvodný nález: by zlepšil súbeh čítania a zápisu, ale mení správanie záloh a obnovy (súbory `-wal`/`-shm`), preto som ho nezapínal bez samostatného testu na Railway.
7. Osamotený `invoice_detail.html` (vyriešené: zmazaný, pozri Dodatok: WAL a upratanie). Pôvodný nález: v koreni projektu ležal (starý duplikát `templates/invoice_detail.html`); appka ho nepoužíva, dá sa zmazať.

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

## Dodatok: kontajner, proxy a token obnovy hesla

| Oblasť | Nález | Oprava |
|---|---|---|
| Docker | Appka bežala ako `root`. | Obraz má používateľa `app` (uid 10001). `docker-entrypoint.sh` štartuje ako root len na opravu vlastníka Railway Volume (`/app/data`, jednorazovo, len ak niečo nepatrí `app`), overí zápis pod `app` a spustí `start.sh` cez `setpriv` pod `app`. Ak platforma `chown` zakáže, appka radšej beží ako root s varovaním; `REQUIRE_NON_ROOT=true` v takom prípade štart odmietne. Bytekód je skompilovaný pri zostavení. **Overené len skriptom a testami, nie na skutočnom Railway Volume** (v tomto prostredí nie je Docker démon); po nasadení skontroluj v logu riadok `[SPUSTENIE] Appka beží pod používateľom app`. |
| Proxy | `FORWARDED_ALLOW_IPS=*` dôveruje hlavičkám `X-Forwarded-For` z ľubovoľného zdroja. | Na Railway ostáva (appka je dostupná len cez ich proxy; inak by všetci zdieľali jeden limit pokusov). `start.sh` pri `*` mimo Railway vypíše varovanie; pre iný hosting nastav premennú na IP/CIDR svojej proxy. |
| Docker build | `.dockerignore` nevylúčil zálohy, nahrané súbory ani lokálne tajné súbory. | Doplnené `.env.*`, `*.pem`, `*.key`, `backups/`, `uploads/`, `accounts/`, `*.db` (aj `-wal`/`-shm`), `*.zip`, žurnál obnovy, `.github/`, logy; test overuje, že nič potrebné pre beh appky nie je vylúčené. |
| Obnova hesla | Token je v URL, takže sa mohol dostať do prístupových logov a cache. | Odpovede na `/reset-password/*` a `/forgot-password` majú `Cache-Control: no-store`, `Pragma: no-cache` a `Referrer-Policy: no-referrer`; filter logov (`log_filters.py`) nahradí token v `uvicorn.access` textom `[skryte]`. Logy samotnej platformy (reverse proxy) appka ovplyvniť nevie, preto je token jednorazový a platí 1 hodinu. |
| Účtovníctvo | Testy DPH, dobropisov a číslovania treba zachovať a pravidlá overiť. | Testy ostávajú súčasťou sady (`test_accounting.py`, `test_numbering.py`, `test_concurrent_numbering.py`, `test_invoices.py`). **Overenie pravidiel s účtovníkom je úloha pre človeka**, nie pre kód; pred reálnym účtovným nasadením ho treba urobiť. |
| Dokumentácia | `AUDIT.md` uvádzal 723 testov a staré odporúčania o WAL a `invoice_detail.html`. | Hlavička upravená, vyriešené odporúčania označené. |


## Dodatok: APP_ENV a audit stopa (23. 10. 2026)

- **Produkčný režim** určuje `APP_ENV` (nenastavené = `production`), nie `SESSION_HTTPS_ONLY`. V produkcii sa vždy vynucuje silný `SECRET_KEY` a zapnuté HTTPS cookie; `SESSION_HTTPS_ONLY=false` už kontrolu kľúča neobíde. Lokálne: `APP_ENV=development`. Na Railway nastav `APP_ENV=production`.
- **Audit stopa:** storno faktúry vyžaduje dôvod, zmena dátumu úhrady sa loguje, `/audit-log` má filter podľa typu, hľadanie a stránkovanie. Tabuľka `audit_log` má triggery proti UPDATE/DELETE. Nejde o nemenný archív ani náhradu zákonných povinností: kto má priamy prístup k súboru databázy, vie triggery zrušiť.
- **Nerobí sa bez Railway:** reálna obnova zo zálohy z R2 a test non-root na skutočnom Volume (obe sa odvtedy overili, pozri nižšie) a ochrana proti zmazaniu v R2 (Object Lock) sú prevádzkové kroky, nie kód. Šifrovanie záloh pred nahratím je už implementované (`BACKUP_ENCRYPTION_KEY`).


## Dodatok: päť auditov (24. 10. 2026)

1. **Preskočené testy:** dva testy Docker entrypointu vyžadujú root a na GitHub Actions by sa potichu preskočili. Workflow ich teraz spúšťa cez sudo a zlyhá, ak sa preskočia; pribudla aj kontrola, že Docker obraz beží pod používateľom `app`.
2. **Bezpečnosť:** test prejde všetky trasy a vyžaduje prihlásenie (okrem 12 verejných, ktoré sú vymenované v teste) a CSRF pri každej meniacej trase. Pridané: `REGISTRATION_ENABLED` a `MAX_ACCOUNTS` (verejná registrácia mohla plniť disk), odmietnutie bežných hesiel a hesla rovnakého ako meno. Upload, izolácia kont, session a CSP sa nemenili. (CSP je od etapy 3 striktná pre skripty.)
3. **Fakturácia:** všetky súčty (PDF, export, dashboard, pripomienky, Peppol) idú cez jednu funkciu; zálohové faktúry sa do tržieb nezapočítavajú. Pridané náhodné testy invariantov (DPH po sadzbách, dobropis je presný opak). Čiastočné úhrady appka nepozná (faktúra je buď uhradená, alebo nie).
4. **Databáza:** cudzie kľúče sú zapnuté, zápisy serializované, migrácie bežia pri štarte. Pridané: vystavenú faktúru nemožno zmazať ani cez cascade (ochrana na úrovni modelu). Appka nemá žiadnu trasu na mazanie zákazníkov ani zákaziek.
5. **Prevádzka:** `/health/details` hlási aj veľa chýb 500 za hodinu, málo miesta na disku a nebežiaci plánovač záloh. Chyby sa logujú s tracebackom (Railway Deploy Logs). Notifikácie e-mailom appka neposiela; na upozornenia slúži externý monitoring (UptimeRobot).

## Dodatok: ďalšie postrehy (24. 10. 2026)

- **Strop kont:** kontrola a vloženie konta idú pod jedným zámkom; neplatné `MAX_ACCOUNTS` alebo `REGISTRATION_ENABLED` appku nespustia (nepovažujú sa za „bez limitu"). Zámok platí pre jeden proces; pri viacerých replikách by sa kontrola musela presunúť do databázy.
- **Vystavené faktúry:** okrem ochrany v modeli aj databázové triggery (platia aj pri priamom SQL a hromadných operáciách): nemožno zmazať ani vrátiť do návrhu vystavenú faktúru, ani meniť jej položky. Kto má priamy prístup k súboru databázy, ich vie zrušiť.
- **Počítadlo chýb** sa ukladá aj do súboru vo Volume (prežije reštart). Pre viac replík alebo upozornenia by bolo treba externé logovanie.
- **Testy:** skutočné HTTP požiadavky na citlivé trasy, hraničné prípady súm (nulové a malé množstvá, zľavy, zmiešané sadzby, polcent), rozšírený zoznam bežných hesiel. Sadzby DPH a režim treba overiť s účtovníkom; testovacie sadzby nie sú daňové potvrdenie.
- **Hygiena archívu:** `.pytest_tmp/` a `server-errors.log` sú v `.gitignore` aj `.dockerignore`; archív pri odovzdaní nesmie obsahovať `.env`, databázy ani zálohy.

- **Mazanie fotiek:** súbor sa maže až po úspešnom commite (pri rollbacku záznam aj súbor ostanú); trasa na zmazanie fotky už súbor nemaže vopred.
- **Chybové hlásenia pri štarte** (slabý `SECRET_KEY`, neplatné `APP_ENV`, `MAX_ACCOUNTS`) nikdy neobsahujú hodnotu tajného kľúča; test to overuje.

- **Súbežné migrácie (nájdené v CI):** Alembic nie je bezpečný pre súbežný beh vo vláknach jedného procesu. Dve súčasné registrácie (alebo registrácia počas štartovej migrácie) mohli nechať konto s polovičnou schémou. Všetky migrácie v procese teraz bežia jedna po druhej (`tenancy._migration_lock`); test s pôvodným chovaním zlyháva, s opravou prechádza.

- **Poistka proti výpadku hostingu:** `docker-compose.yml` + `Caddyfile` (HTTPS) a `offsite_restore.py` (stiahnutie a rozšifrovanie zálohy z bucketu) umožňujú spustiť appku inde a obnoviť dáta (`docs/HOSTING.md`). Postup je overený testami konfigurácie a CLI; samotné nasadenie na cudzom serveri som nespúšťal.

- **Obnova šifrovanej zálohy v appke:** `.zip.enc` sa dá nahrať priamo v Nastaveniach; rozpozná sa podľa obsahu, dešifruje sa mimo hlavného vlákna, pri zlom hesle sa dáta nezmenia a heslo sa nezobrazuje (testy v `tests/test_backup.py`).

- **Audit stopa, dokončenie:** loguje sa aj úprava konceptu faktúry, vytvorenie zákazníka a zmena zákazníka (len názvy zmenených polí, nie osobné údaje). Detail faktúry má sekciu „História zmien" s jej udalosťami (storno s dôvodom, úhrada, úprava) a odkaz na celý prehľad; prehľad má filter „Zákazníci".

## CSP po etapách
1. **Etapa 1 (hotová):** report-only režim a `/csp-report`; inventúra: 28 inline `<script>` blokov, 59 inline handlerov.
2. **Etapa 2 (hotová):** všetky skripty a handlery sú v `static/js/` (`app.js` + `pages/*.js`), obsluha cez `data-action`, `data-change-action`, `data-auto-submit`, `data-confirm`. Šablóny majú 0 inline skriptov a 0 handlerov; stráži to `test_csp_inline_budget.py`.
3. **Etapa 3 (hotová):** vynucovaná politika má `script-src 'self'` a `script-src-attr 'none'` a `report-uri /csp-report`. Overené v reálnom prehliadači (Chromium): téma, formuláre zákazníka a zákazky, pridanie a odobratie riadkov faktúry bez porušenia CSP. Inline štýly (`style-src 'unsafe-inline'`) ostávajú povolené - nižšie riziko. Po nasadení sleduj v logu riadky `[CSP]`.

## Kontrola priorít 1-5 (po CSP)
1. **Izolácia kont:** nový sweep posiela z konta B platné formuláre na všetky trasy s ID konta A aj trasy bez ID a kontroluje, že databáza, súbory a zálohy konta A ostali bajt po bajte rovnaké a audit log A sa nezmenil. Kontrolný test overuje, že rovnaký payload majiteľ prijme (sweep teda nenarazí len na 422).
2. **Triggery v existujúcich databázach:** test vyrobí databázu na revízii pred triggermi s dátami, zmigruje ju a overí všetkých 6 triggerov, ich účinok, zachované dáta, opakovateľnosť a obnovu starej zálohy. Nové: `db_guards.missing_triggers` a monitoring `guards` hlási chýbajúci alebo oslabený trigger.
3. **Obnova zálohy:** 8 druhov chybného vstupu (zlé heslo, poškodený a skrátený `.enc`, odpad, skrátený zip, pokazená DB, cesta mimo adresára, prázdny súbor) nezmení živú DB ani uploads a nezanechá súbory; úspešná šifrovaná obnova dáta nahradí a vytvorí poistnú kópiu.
4. **Súbežná registrácia:** nájdená a opravená chyba - súbežná registrácia rovnakého mena končila 500 (kontrola mena bola mimo zámku). Testy: 12 súbežných registrácií nad limitom 3 dajú presne 3 úplné kontá bez sirotských priečinkov a bez 5xx.
5. **Monitoring:** `/health/details` hlási aj ochranné triggery a beh ako root v produkcii a vracia `runtime` (používateľ, prostredie).

## Dodatok: správa kont a overenie na ostrej prevádzke (26. 10. 2026)

- **Správa kont majiteľom servera:** `/admin/accounts` (len kontá v `SERVER_ADMINS`, ostatným 404): zoznam, založenie konta, nové heslo (zneplatní staré prihlásenia), vypnutie a zapnutie konta (dáta a zálohy ostávajú). Zakladanie používa jednu logiku (`account_service.py`) aj pre CLI a verejnú registráciu pod rovnakým zámkom. Pri testovaní sa našla a opravila chyba: súbežná registrácia rovnakého mena končila chybou 500.
- **Overené na Railway:** štartový log potvrdzuje beh pod používateľom `app` (nie root), samostatný Volume, migrácie všetkých kont vrátane novej databázy kont; `/health/details` vracia `status: ok`, 3 kontá, `runtime.user: app`.
- **Overené mimo kódu:** šifrovaná záloha v R2 sa dá stiahnuť a rozšifrovať; `offsite_restore.py --verify` prešiel (OK) pre všetky kontá v bucketi.
- **Diagnostika monitoringu:** štartový log vypíše, či je `MONITOR_TOKEN` nastavený, jeho dĺžku a odtlačok SHA-256 (token sa nikdy nevypíše), aby sa dala porovnať hodnota na serveri s tou, ktorú zadáva nástroj na monitoring.

