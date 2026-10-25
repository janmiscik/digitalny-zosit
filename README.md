# 📒 Digitálny zošit

Webová aplikácia na fakturáciu, cenové ponuky a evidenciu zákazníkov/zákaziek pre remeselníkov a živnostníkov.

Aplikácia je vytvorená v Pythone pomocou FastAPI, SQLAlchemy, Jinja2 a SQLite. Je **multi-tenant**: každý živnostník/remeselník si cez `/register` vytvorí vlastné konto a má vlastnú, fyzicky oddelenú databázu a súbory (jedna sada fakturačných údajov firmy na konto).

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

- **`SECRET_KEY`** – náhodný reťazec pre podpis prihlasovacej session a odkazov na obnovu hesla (povinné, appka bez neho nenaštartuje; min. 32 znakov, v produkcii sa odmietne aj predvolená či ukážková hodnota ako `change-me` alebo nenáhodný kľúč – lokálne pri `APP_ENV=development` sa slabý kľúč iba zaloguje):

  ```powershell
  python -c "import secrets; print(secrets.token_hex(32))"
  ```

- **`APP_ENV`** – `production` (predvolené, aj keď premennú nenastavíš) alebo `development`. V produkcii appka vždy vyžaduje silný `SECRET_KEY` a zapnuté `SESSION_HTTPS_ONLY`, bez ohľadu na iné nastavenia. Lokálne vývoj: `APP_ENV=development`. Neplatná hodnota (preklep) appku nespustí.

- **`REGISTRATION_ENABLED`** – `false` vypne verejnú registráciu nových kont (predvolene zapnutá). Ak appku používaš len ty, po vytvorení svojho konta ju vypni. **`MAX_ACCOUNTS`** – strop počtu kont na serveri (0 alebo nenastavené = bez limitu).

- **`MONITOR_MAX_ERRORS_PER_HOUR`** – po koľkých neošetrených chybách servera (HTTP 500) za hodinu `/health/details` ohlási problém (predvolene 5). Počet chýb sa ukladá aj do súboru `server-errors.log` vo Volume, takže prežije reštart (appka beží v jednej replike). Monitoring ďalej hlási málo miesta na disku (pod 200 MB alebo 10 %) a nebežiaci plánovač záloh.

- **`SESSION_HTTPS_ONLY`** – či sa prihlasovacia cookie posiela iba cez HTTPS (predvolené `true`). Nechaj `true` v produkcii (Railway a pod.). Iba ak appku spúšťaš lokálne cez obyčajné `http://localhost`, nastav `APP_ENV=development` a `false` - v produkcii `false` appku nespustí. Inak sa neprihlásiš (prehliadač cookie s príznakom `Secure` cez obyčajné http vôbec neuloží).

- **`ACCOUNTS_DATABASE_URL`** – master databáza kont (predvolené `sqlite:///./data/accounts.db`).

- **`ACCOUNTS_DATA_DIR`** – priečinok s dátami jednotlivých kont (predvolené `./data/accounts`).

Appka už **nepoužíva** `ADMIN_USERNAME` ani `ADMIN_PASSWORD_HASH` - používatelia sa registrujú cez `/register` a ich prihlasovacie údaje sú v `accounts.db`.

### 3. Spusti databázové migrácie

Appka má dva oddelené reťazce migrácií:

```powershell
# 1. master databáza kont (accounts.db) - povinné pred prvým spustením
alembic -c alembic_accounts.ini upgrade head

# 2. business databázy VŠETKÝCH existujúcich kont (po každej aktualizácii appky)
python manage_accounts.py --upgrade-all
```

Nové konto dostane všetky business migrácie automaticky pri registrácii (`tenancy.provision_account`). **Existujúce kontá však novú migráciu pri novej verzii appky automaticky nedostanú** - preto sa `manage_accounts.py --upgrade-all` musí spustiť po každom nasadení s novou migráciou v `alembic/versions/` (v Dockeri/Railway to robí `start.sh` pri každom štarte, viď nižšie). Príkaz je bezpečné púšťať opakovane. `python manage_accounts.py --list` vypíše registrované kontá.

Appka **nespúšťa `create_all()` automaticky** - schéma sa spravuje výhradne cez Alembic. Ak databáza kont nie je zmigrovaná, appka pri štarte zlyhá s jasnou hláškou.

### 4. Spusti aplikáciu

```powershell
uvicorn main:app --reload
```

Aplikácia beží na `http://127.0.0.1:8000`, pri prvom vstupe ťa presmeruje na `/login`. Prvé konto vytvoríš na `/register`.

---

## 🚂 Nasadenie na Railway

Appka sa nasadzuje cez `Dockerfile` (`railway.json` ho vyberie a nastaví healthcheck).

1. **Premenné služby (Variables):** `SECRET_KEY` (povinné), `APP_ENV=production` a `SESSION_HTTPS_ONLY=true`. `ACCOUNTS_DATABASE_URL` a `ACCOUNTS_DATA_DIR` sú v Dockerfile už nastavené na `/app/data/...`, netreba ich meniť.
2. **Volume:** v službe pridaj Volume s **Mount path `/app/data`**. Appka tam ukladá všetko trvalé:

   ```text
   /app/data/accounts.db                      master databáza kont
   /app/data/accounts/<konto>/database.db     business dáta konta
   /app/data/accounts/<konto>/uploads/        logo, podpis, fotky zákaziek
   /app/data/accounts/<konto>/backups/        zálohy
   ```

   Bez pripojeného Volume sa **všetky dáta stratia pri každom redeployi**. (Instrukcia `VOLUME` v Dockerfile sa zámerne nepoužíva - Railway ju nepodporuje.)
3. **Port:** Railway nastavuje premennú `PORT` a appka počúva práve na nej (`start.sh` -> `uvicorn ... --port "${PORT:-8000}"`). Port nezadávaj ručne.
4. **Štart kontajnera** (`start.sh`) pri každom nasadení: migrácia `accounts.db` -> `manage_accounts.py --upgrade-all` (migrácia všetkých existujúcich kont) -> spustenie appky. Ak zlyhá migrácia, appka sa nespustí a Railway ponechá predošlú verziu.
5. **Healthcheck:** `GET /health` (bez prihlásenia) vráti `200 {"status":"ok"}`, ak appka beží a číta databázu kont; inak `503`. Nastavený v `railway.json`.
6. **Jedna replika:** SQLite súbory na Volume nepodporujú viac súbežných inštancií - nechaj 1 repliku.

---

## 🔐 Prihlásenie a bezpečnosť

- **Kontá a registrácia** (`/register`, `/login`) – každý používateľ má vlastné konto (`Account` v `accounts.db`: meno, hash hesla, `slug` priečinka), heslo min. 8 znakov hashované cez PBKDF2-SHA256 (600 000 iterácií; staré hashe sa pri prihlásení potichu prehashujú), session-based prihlásenie (cookie). Dáta kont sú fyzicky oddelené – appka pri obsluhe jedného konta nikdy neotvorí súbor iného (viď `tenancy.py`).
- HTML stránky vyžadujú prihlásenie – neprihláseného používateľa presmerujú na `/login`. JSON API endpointy vrátia `401 Unauthorized`.
- **Rate limiting** – `/login`: po 5 zlých pokusoch z jednej IP za 5 minút sa prihlásenie zamkne na 5 minút; popri tom limit podľa mena konta (20 zlých pokusov za 15 min), aby sa heslo nedalo skúšať donekonečna ani pri zmene IP. `/register`: max. 5 nových kont za hodinu z jednej IP.
- **Zmena hesla** (Nastavenia → Zmena hesla): vyžaduje súčasné heslo, nové aspoň 8 znakov a iné ako súčasné; zlé súčasné heslo sa počíta do limitu pokusov; zmena sa zapíše do histórie zmien. Prihlásenia na iných zariadeniach ostávajú platné.
- **Obnova zabudnutého hesla cez e-mail** (voliteľná, vypnutá, kým nie je nastavený SMTP): používateľ si v Nastaveniach zadá e-mail konta (vyžaduje súčasné heslo), na prihlasovacej stránke sa potom ukáže „Zabudnuté heslo?". Odkaz je podpísaný, platí 1 hodinu a funguje len raz (po zmene hesla prestane platiť). Odpoveď je rovnaká pre existujúce aj neexistujúce konto a e-mail sa odosiela na pozadí, takže sa z odpovede ani z jej času nedá zistiť, ktoré mená existujú. Žiadosti sú limitované na 5 za hodinu z jednej IP. Zapne sa premennými `SMTP_HOST`, `SMTP_PORT` (587 = STARTTLS, 465 = SSL), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` a `APP_BASE_URL` (verejná adresa appky, z nej sa skladá odkaz; hlavička Host sa zámerne nepoužíva). Konto bez e-mailu sa týmto spôsobom obnoviť nedá (ostáva `manage_accounts.py`).
- **Zmena alebo obnova hesla odhlási ostatné zariadenia** (session nesie odtlačok hashu hesla, ktorý sa pri každej požiadavke overuje voči `accounts.db`; zmazané konto prestane platiť okamžite).
- **SQLite v režime WAL** (`journal_mode=WAL`, `synchronous=NORMAL`): čitatelia neblokujú zapisovateľa, čím ubudne "database is locked" pri súbežných požiadavkách. Pri databáze pribudnú súbory `database.db-wal` a `-shm`; zálohy a obnova používajú SQLite backup API, takže s tým počítajú. Súbor `.db` nikdy nekopíruj ručne bez týchto dvoch súborov.
- **Monitoring záloh a migrácií** (`/health/details`, vypnuté, kým nenastavíš `MONITOR_TOKEN`): vráti 200 `{"status": "ok"}` alebo 503 `{"status": "degraded", "problems": [...]}`, ak niektoré konto nemá čerstvú zálohu (staršiu než 2 × interval + 1 h), posledný pokus o zálohu zlyhal, kópia mimo servera chýba/zlyhala/je stará alebo databáza konta nie je na poslednej migrácii. Problémy obsahujú len konto, druh a pevný popis (žiadne cesty ani texty chýb). Token sa posiela v hlavičke `Authorization: Bearer <token>` (alebo `?token=` pre nástroje bez hlavičiek). Obyčajný `/health` zostáva len kontrola "appka žije", aby Railway kvôli zastaranej zálohe nezastavil nasadenie. V UptimeRobote: monitor typu HTTP(s) na `https://<appka>/health/details?token=<MONITOR_TOKEN>` (503 = poplach, e-mail príde sám).
- **Prerušená obnova zo zálohy** (pád procesu uprostred výmeny databázy a súborov): pred prvou zmenou sa zapíše žurnál a pri ďalšom štarte (`start.sh` aj appka) sa konto automaticky vráti z poistnej zálohy. Ak sa to nepodarí, monitoring hlási `restore`. Viď `docs/PREVADZKA.md`.
- **Súbežné číslovanie dokladov:** uloženie dokladu s číslom je serializované po kontách a poistkou ostáva UNIQUE obmedzenie s opakovaním, takže 20 súbežných požiadaviek dostane jedinečné čísla.
- **Medzery v číslovaní** sú upozornenie, nie chyba integrity: v Kontrole integrity ich možno preveriť s dôvodom („zmazaný návrh"), dôvod ostáva dohľadateľný a zapísaný v histórii zmien.
- **Obnova hesla vyžaduje https:** `APP_BASE_URL` musí byť `https://` (okrem localhost), inak je funkcia vypnutá. Odkaz je jednorazový aj pri súbežnom odoslaní a zmena hesla odhlási ostatné zariadenia.
- **Ochrana externých záloh:** súkromný bucket, kľúč len na bucket, zámok/verzie objektov; ak bucket zakáže mazanie starých záloh, záloha ostane úspešná a zobrazí sa upozornenie. Viď `docs/PREVADZKA.md`.
- **Kontrola SECRET_KEY pri štarte** (`security_config.py`): v produkcii (HTTPS-only cookie) sa appka so slabým, krátkym (< 32 znakov), predvoleným alebo nenáhodným kľúčom nespustí a vypíše, ako vygenerovať nový. Pri zmene kľúča sú všetci raz odhlásení.
- **Limity obnovy hesla sa čistia:** staré záznamy žiadostí (okno 1 h) sa pravidelne zahadzujú aj pri samotnej žiadosti o obnovu, pamäť teda nerastie pri množstve rôznych IP.
- **Závislosti:** `pip-audit` v CI kontroluje aj vývojové závislosti (`requirements-dev.txt`), nielen produkčné.
- **Kontajner beží pod neprivilegovaným používateľom** (`app`): `docker-entrypoint.sh` pri štarte opraví vlastníka Volume `/app/data` a zníži oprávnenia. `REQUIRE_NON_ROOT=true` odmietne štart, ak sa to nepodarí (inak appka s varovaním pobeží ako root, aby sa deploy nerozbil). Po nasadení hľadaj v logu `[SPUSTENIE] Appka beží pod používateľom app`.
- **Dôvera proxy:** `FORWARDED_ALLOW_IPS=*` (predvolené v obraze) je určené pre Railway, kde je appka dostupná len cez ich proxy. Pri inom hostingu nastav na IP/CIDR svojej proxy; `start.sh` pri `*` mimo Railway vypíše varovanie.
- **Token obnovy hesla v logoch:** `log_filters.py` ho z prístupových logov appky odstráni, stránky obnovy majú `Cache-Control: no-store` a `Referrer-Policy: no-referrer`.
- **Session** sa pri prihlásení vymení (ochrana proti session fixation), takže starý CSRF token po prihlásení neplatí.
- **Bezpečnostné hlavičky** na každej odpovedi: `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy` a pri HTTPS aj `Strict-Transport-Security`. K HTML stránkam sa posiela aj **Content-Security-Policy**: skripty, štýly, obrázky, formuláre a spojenia len z vlastnej domény (výnimka: písma Google Fonts), žiadne `<object>`, žiadne vloženie do cudzieho rámu. Povolené je `'unsafe-inline'`, lebo šablóny majú inline skripty a handlery; striktná CSP by vyžadovala presun do `static/`. PDF a JSON odpovede CSP nenesú.
- **Interaktívna dokumentácia API** (`/docs`, `/redoc`, `/openapi.json`) je v produkcii vypnutá; na vývoj ju zapne `ENABLE_API_DOCS=true`.
- **SQLite `busy_timeout`** 15 s – súbeh požiadavky a automatickej zálohy nekončí chybou „database is locked“.
- Session cookie je kryptograficky podpísaná (`itsdangerous`) – akákoľvek manipulácia s cookie sa odmietne. Cookie sa navyše posiela iba cez HTTPS (`https_only`, konfigurovateľné cez `SESSION_HTTPS_ONLY` - viď sekcia `.env` vyššie).
- **CSRF ochrana** – všetky formuláre (POST/PUT/PATCH/DELETE) nesú skrytý token naviazaný na session (double-submit pattern); požiadavka bez platného tokenu sa odmietne s `403`.
- Nahrávané obrázky (logo, podpis, fotky zákaziek) sa overujú podľa **skutočného obsahu súboru** (cez Pillow), nielen podľa prípony – zabraňuje to nahratiu skrytého škodlivého súboru pod príponou `.png`. Zároveň sa kontroluje aj rozlíšenie (max. 40 megapixelov) ako ochrana proti "decompression bomb" útoku (malý súbor, ktorý sa pri dekódovaní rozvinie do enormnej veľkosti v pamäti).
- Obnova zo zálohy má rovnakú ochranu na úrovni ZIP archívu – pred rozbalením sa overí počet položiek aj ich nekomprimovaná veľkosť (jednotlivo aj spolu), nielen veľkosť nahraného súboru.
- SQLite má explicitne zapnuté vynucovanie `FOREIGN KEY` obmedzení (`PRAGMA foreign_keys=ON`) – bez tejto pragmy by ich SQLite defaultne ignoroval.
- **Audit log** (`/audit-log`, odkaz zo stránky Nastavenia) – zaznamenáva dôležité udalosti: vytvorenie/zmazanie/zmenu stavu faktúry a cenovej ponuky, dobropis, konverziu ponuky na faktúru, zmenu fakturačných údajov firmy, obnovu zo zálohy a prihlásenie/odhlásenie. Audit log je súčasťou databázy každého konta, takže záznam nesleduje "kto", len "čo a kedy" – pre spätnú dohľadateľnosť.
- **Kontrola integrity dát** (`/settings/integrity-check`, odkaz zo stránky Nastavenia) – porovná databázu (logo, podpis, fotky zákaziek) so súbormi v `uploads/`: nahlási chýbajúce súbory (DB odkazuje na niečo, čo na disku nie je) aj osirotené súbory (súbor na disku, na ktorý sa DB neodkazuje), s možnosťou osirotené súbory rovno zmazať.
- **Validácia e-mailu, IBAN, IČO, DIČ a IČ DPH** (`validators.py`) – pri zadávaní zákazníka aj vo fakturačných údajoch firmy. E-mail sa overí formátom a normalizuje na malé písmená; IBAN sa overí kontrolným súčtom (mod-97, ISO 13616, funguje pre IBAN akejkoľvek krajiny) aj presnou dĺžkou pre bežné krajiny; slovenské IČO (8 číslic) sa overí kontrolnou číslicou (modulo 11); DIČ a IČ DPH sa overujú len formátom (počet číslic, resp. `SK` + 10 číslic), bez kontrolného súčtu, keďže pre slovenské DIČ žiadny verejne zdokumentovaný nie je. Hodnoty, ktoré nevyzerajú na slovenský formát (zahraniční zákazníci), appka neodmieta – validácia sa cez ne jednoducho preskočí. Rovnaké validátory (v "best-effort" režime, ktorý nikdy nezlyhá na historicky uložených dátach) sa používajú aj pri generovaní Peppol XML a QR platby.
- **Validácia vygenerovaného Peppol XML** (`peppol_validation.py`) – po vygenerovaní skontroluje vybrané pravidlá EN16931/Peppol BIS Billing 3.0.21 (máj 2026, povinná od 17.8.2026): povinné polia (BR-01 až BR-11, BR-16, BR-21 až BR-26), aritmetickú konzistenciu súčtov (BR-CO-10/13/14/15/17), elektronickú adresu predávajúceho aj odberateľa (PEPPOL-EN16931-R010/R020, BR-62/BR-63, BR-CL-25) a osobitné pravidlá pre prenesenie daňovej povinnosti a nulovú sadzbu DPH (BR-AE-01/02/05/08/09/10, BR-Z-01/02/05/08/09/10). **Nie je to náhrada oficiálnej validácie** (plná Schematron/XSD sada ani úplný EAS číselník nie sú appke k dispozícii) – slúži hlavne ako regresná poistka v testoch a ako nezáväzné upozornenie (hlavička `X-Peppol-Validation-Issues`, záznam do audit logu, a zoznam nálezov priamo na detaile faktúry), export nikdy neblokuje.
- **Robustnosť voči neočakávaným chybám** – niekoľko miest, kde by tichá alebo príliš široká chyba mohla spôsobiť stratu dát alebo zavádzajúci stav: číslovanie dokladov sa pri súbežnej kolízii čísla (`IntegrityError`) bezpečne opakuje a doklad sa nestratí; zmena veľkosti nahranej fotky rozlišuje poškodený obrázok (zaloguje sa s `logger.exception` a použije sa pôvodný súbor) od skutočnej programátorskej chyby (tá sa nezachytáva potichu); záloha aj obnova databázy overujú `PRAGMA integrity_check`; bezpečnostná záloha pred obnovou má názov odolný voči kolízii pri dvoch obnovách v tej istej sekunde; nahrávanie súboru (obrázky aj obnova zálohy) prekročenie veľkostného limitu zistí PRIEBEŽNE (`uploads_utils.read_upload_with_limit`), nie až po tom, čo appka celý (aj niekoľko-GB) súbor stiahne do pamäte.
- **Logovanie a neošetrené (500) chyby** (`main.py`) – appka má nastavené štruktúrované logovanie (`logging.basicConfig`, úroveň nastaviteľná cez `LOG_LEVEL`) a globálny handler pre neošetrené výnimky: používateľovi sa ukáže všeobecná chybová stránka v štýle appky (nie holé "Internal Server Error" ani detail výnimky, ktorý by mohol prezradiť interné detaily), zatiaľ čo do logu ide plný text chyby aj traceback.

---

## 🔎 Vyhľadávanie

Hľadanie v zákazníkoch, zákazkách a faktúrach funguje **bez ohľadu na diakritiku a veľkosť písmen** (`simon` nájde `Šimon`) a hľadá sa **každé zadané slovo samostatne** (`novak kotol` nájde zákazníka Novák s položkou o kotle, poradie slov nezáleží). SQLite `LIKE` porovnáva bez ohľadu na veľkosť písmen len ASCII, preto sa hľadanie robí v Pythone (`search_utils.py`) nad záznamami už zúženými filtrom stavu/obdobia - pre objem dát jedného remeselníka je to dostatočne rýchle.

Zoznamy faktúr a zákaziek sa dajú zúžiť na jedného zákazníka cez `?customer_id=<id>` a odkazy filtrov zachovávajú všetky ostatné aktívne filtre.

---

## 📱 Mobil

Appka je určená aj na prácu priamo u zákazníka z telefónu (overené na šírkach 390 a 360 px, žiadna stránka nepresahuje do šírky):

- **Horná lišta** na dashboarde: logo hore, menu pod ním ako posuvný rad (s náznakom pokračovania na okraji).
- **Položky faktúry, ponuky a dobropisu** sa na mobile zobrazujú ako **kartička na položku** (popis, množstvo, MJ, cena, DPH) namiesto širokej tabuľky; tlačidlo `×` aj polia majú plochu aspoň 44 px. Rovnako sa v detaile faktúry/ponuky zobrazujú položky len na čítanie.
- **Formuláre** majú polia pod sebou na celú šírku; písmo polí je 16 px, aby iOS pri písaní nezväčšoval stránku.
- **Filtre a tlačidlá** majú dostatočne veľkú plochu na ťuknutie.

Mobilné pravidlá sú na konci `static/style.css` (sekcia „MOBIL - OVLÁDANIE PRSTAMI“). Bunky tabuliek položiek preto nesú atribút `data-label` - ak pridáš nový stĺpec, doplň ho aj tam.

---

## 🧭 Navigácia

| Cesta | Popis |
|---|---|
| `/` | Dashboard – pás „Vyžaduje pozornosť“, financie (Na inkaso, Po splatnosti, tržby vs. minulý mesiac), graf príjmov za 6 mesiacov, faktúry po splatnosti, termíny, posledné zákazky |
| `/zakaznici` | Zoznam zákazníkov (vyhľadávanie podľa mena, telefónu, e-mailu, mesta, IČO; pridanie, auto-doplnenie podľa IČO) |
| `/zakazky` | Zoznam zákaziek (hľadanie podľa názvu, popisu, zákazníka; filter podľa stavu, termínu a zákazníka `?customer_id=`) |
| `/kalendar` | Mesačný kalendár zákaziek podľa termínu realizácie |
| `/faktury` | Zoznam faktúr (hľadanie podľa čísla, zákazníka, IČO, položky, poznámky; filter podľa stavu, obdobia a zákazníka `?customer_id=`) |
| `/ponuky` | Zoznam cenových ponúk |
| `/settings` | Fakturačné údaje firmy, DPH režim, logo/podpis, záloha/obnova databázy |
| `/register` | Registrácia nového konta |
| `/health` | Healthcheck pre Railway (verejný, bez prihlásenia) |

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
12. **Peppol XML export** – návrh/príprava dát vo formáte UBL 2.1 / Peppol BIS Billing 3.0.21 pre budúcu povinnú e-fakturáciu. Appka sama neposiela cez Peppol sieť – to vyžaduje registráciu cez certifikovaného poskytovateľa ("digitálneho poštára"). Firma aj každý zákazník majú samostatné polia **Peppol schéma ID** (kód ISO 6523/EAS, napr. `9950` pre SK:VAT) a **Peppol Endpoint ID** (samotná hodnota elektronickej adresy v tejto schéme – pri `9950` je to IČ DPH, nie IČO) – obe sa musia vyplniť spolu. Zákazník má aj vlastný **kód krajiny** (`country_code`, predvolene `SK`), ktorý appka posiela ako krajinu odberateľa namiesto natvrdo predpokladaného Slovenska.

---

## 📋 Cenové ponuky

- Samostatný koncept od faktúry, vlastné číslovanie a stavy (Návrh → Odoslaná → Akceptovaná/Zamietnutá → Prevedená na faktúru).
- Dajú sa vytvoriť samostatne alebo naviazané na konkrétnu zákazku.
- **Jedným klikom** vygeneruješ ostrú alebo zálohovú faktúru s rovnakými položkami – z odoslanej aj akceptovanej ponuky (odoslaná sa pritom automaticky označí ako akceptovaná). Tlačidlo je priamo v zozname ponúk aj v detaile ponuky; opätovné kliknutie druhú faktúru nevytvorí.
- Ponuka v detaile ukazuje faktúry, ktoré z nej vznikli, a faktúra odkazuje späť na svoju ponuku.
- PDF export ponuky aj dodacieho listu.

## 🔔 Pripomienky platieb

- Stránka **`/pripomienky`** (odkaz z dashboardu v „Vyžaduje pozornosť“): odoslané neuhradené faktúry **po splatnosti** (najdlhšie omeškané hore) a **čoskoro splatné** (najbližšie 3 dni). Návrhy, zálohové faktúry a dobropisy sa nepripomínajú.
- Stupeň podľa omeškania: **Pripomienka** (1–13 dní), **Druhá upomienka** (14–29), **Posledná výzva** (30+) – s tomu zodpovedajúcou tónou textu.
- Jedným klikom sa pripraví hotový text (predmet, suma, IBAN, variabilný symbol, názov firmy) – dá sa upraviť, otvoriť v e-mailovej aplikácii (`mailto:`) alebo skopírovať (napr. na SMS). Appka sama **nič neodosiela**.
- Po odoslaní klikneš „Označiť ako pripomenuté“ – eviduje sa počet pripomienok a dátum poslednej (aj v histórii zmien), vidno ich v zozname aj na faktúre.

## 👤 História zákazníka

Detail zákazníka (`/customers/<id>`) ukazuje všetko na jednej stránke:

- **Prehľad:** počet zákaziek (a otvorených), tržba bez DPH, zisk zo zákaziek s maržou a priemerná doba, za ktorú zákazník platí.
- **Poznámky:** záznamy s časom (dohoda po telefóne, kľúče od vchodu, preferovaný kontakt...) – pribúdajú, nič sa neprepisuje; dajú sa zmazať.
- **Časová os:** vystavené a uhradené faktúry, ponuky, poznámky a nahraté fotky (zlúčené po zákazke a dni), najnovšie hore.
- **Zákazky, ponuky, faktúry** zákazníka a **fotky zo všetkých jeho zákaziek** (pred/po) v jednej galérii.

---

## 📤 Export pre účtovníka

Stránka `/export` (odkaz na prehľade) pripraví podklady za zvolené obdobie – tento/minulý mesiac, štvrťrok, rok alebo vlastný rozsah:

- **Excel pre účtovníka** (`.xlsx`): hárky *Súhrn*, *Faktúry*, *Rekapitulácia DPH* (po sadzbách) a *Náklady*; čísla a dátumy sú skutočné hodnoty, nie text.
- **CSV faktúr / nákladov** pre slovenský Excel (UTF-8 s BOM, oddeľovač `;`, desatinná čiarka).
- **ZIP s PDF** všetkých faktúr v období.
- Zahŕňajú sa odoslané a uhradené faktúry podľa dátumu vystavenia; dobropisy sú záporné a odkazujú na pôvodnú faktúru, stornované sú v zozname, ale mimo súčtov. Zálohové faktúry sa dajú pridať prepínačom.
- Texty začínajúce `=`, `+`, `-`, `@` sa neutralizujú (ochrana pred vložením vzorca do Excelu). Každé stiahnutie sa zapíše do histórie zmien.

---

## 🔧 Zákazky

- Fotodokumentácia (pred/po) – ľubovoľný počet fotiek na zákazku, overenie skutočného typu obrázka. Fotky sa pri nahraní automaticky zmenšia (max. 1600px dlhšej strany) a opraví sa ich otočenie podľa EXIF údajov z mobilu.
- **Zisk zo zákazky** (karta „Náklady a zisk“ v úprave zákazky aj riadok v zozname zákaziek):
  - **Tržba bez DPH** z vystavených faktúr zákazky (odoslané a uhradené; návrhy, stornované a zálohové sa nepočítajú, dobropis tržbu znižuje). Zvlášť sa ukazuje, čo je ešte neuhradené.
  - **Náklady podľa druhu:** materiál, práca, iné (doprava, subdodávka...). Pri práci zadáš **hodiny a sadzbu** – suma sa vypočíta; hodiny sa sčítavajú.
  - **Zisk a marža** (% z tržby), rozdelenie nákladov v pruhu. Pri strate sa zvýrazní.
  - Náklady zadávaj bez DPH. Staré náklady (zadané pred zavedením druhov) majú druh „Iné“.
- Mesačný kalendár podľa termínu realizácie.

---

## 💾 Záloha a obnova databázy

Postup po úplnej strate úložiska je v [docs/OBNOVA.md](docs/OBNOVA.md). GitHub Actions (`.github/workflows/tests.yml`) spúšťa testy na čistej inštalácii závislostí.


V Nastaveniach (`/settings`):

- **Stiahnutie zálohy** – konzistentná kópia databázy cez natívne SQLite backup API (bezpečné aj počas behu appky); pred zabalením sa databáza overí cez `PRAGMA integrity_check` – poškodená databáza sa nezálohuje.
- **Obnova zo zálohy** – validuje sa, že nahraný súbor je skutočne platná záloha tejto appky, vrátane `PRAGMA integrity_check` nad `database.db` v zálohe. Pred obnovou appka automaticky uloží bezpečnostnú kópiu aktuálneho stavu do `backups/` (negituje sa) – názov súboru (`pred-obnovou-RRRRMMDD-HHMMSS.zip`) je odolný voči kolízii: ak by v tej istej sekunde prebehli dve obnovy, druhá bezpečnostná záloha dostane príponu (`-2`, `-3`...) namiesto toho, aby ticho prepísala prvú.

### Automatické zálohy a test obnovy

Appka zálohuje **sama** (vlákno na pozadí, `auto_backup.py`) – každé konto približne raz za 24 hodín. Záloha je ZIP s `database.db` + `uploads/` v `data/accounts/<konto>/backups/`. Každá záloha sa **hneď overí testom obnovy**: rozbalí sa do dočasného priečinka a skontroluje sa CRC všetkých súborov, `PRAGMA integrity_check`, počty záznamov (zákazníci, faktúry, zákazky...) voči stavu pri zálohovaní, prítomnosť všetkých súborov, na ktoré odkazuje databáza (logo, podpis, fotky), a či z nej appka vie čítať cez svoje modely. Záloha, ktorá testom neprejde, sa zmaže a chyba sa ukáže v Nastaveniach.

Druhy záloh a koľko posledných sa drží: `auto` (7), `rucne` (10), `pred-aktualizaciou` (3), `pred-obnovou` (5). Pri zlyhaní sa pokus zopakuje najskôr o hodinu. Pred zápisom sa kontroluje voľné miesto na disku.

Pri každom štarte (`start.sh`) sa kontám, ktorým čaká migrácia schémy, spraví **poistná záloha** `pred-aktualizaciou-*` ešte pred migráciou (zlyhanie zálohy štart nezastaví, len vypíše varovanie).

| Premenná | Predvolene | Význam |
|---|---|---|
| `BACKUP_ENABLED` | `true` | `false` vypne automatické zálohy |
| `BACKUP_INTERVAL_HOURS` | `24` | ako často zálohovať |
| `BACKUP_KEEP` | `7` | koľko automatických záloh držať |
| `BACKUP_CHECK_MINUTES` | `30` | ako často plánovač kontroluje, či je záloha na rade |
| `BACKUP_MIN_FREE_MB` | `200` | minimálna rezerva voľného miesta |
| `AUTO_MIGRATE_ACCOUNTS` | `true` | pri štarte appky doplní kontám chýbajúce migrácie schémy (s poistnou zálohou); `false` vypne |

CLI (Railway shell alebo lokálne):

```bash
python manage_accounts.py --backup-all                 # záloha všetkých kont
python manage_accounts.py --verify-all                 # test obnovy najnovšej zálohy každého konta
```

> ⚠️ **Zálohy na tom istom Volume nechránia pred stratou celého Volume.** Chránia pred chybou v appke, omylom zmazanými dátami a pokazenou migráciou. Preto si občas stiahni zálohu aj mimo Railway (Nastavenia → Stiahnuť) a ak to tvoj plán ponúka, zapni aj Railway Volume Backups.

### Záloha mimo servera (odporúčané)

Zálohy ležia na tom istom Volume ako dáta, takže pri jeho strate zmiznú spolu s nimi. Preto vie appka každú vytvorenú a overenú zálohu nahrať aj do **súkromného** S3-kompatibilného úložiska (Backblaze B2, Cloudflare R2, AWS S3, MinIO). Predvolene je vypnuté; zapne sa nastavením premenných v Railway → Variables:

| Premenná | Význam |
|---|---|
| `OFFSITE_BACKUP_BUCKET` | názov bucketu (povinné) |
| `OFFSITE_BACKUP_ACCESS_KEY_ID` | prístupový kľúč (povinné) |
| `OFFSITE_BACKUP_SECRET_ACCESS_KEY` | tajný kľúč (povinné) |
| `OFFSITE_BACKUP_ENDPOINT` | URL S3 API, napr. `https://s3.eu-central-003.backblazeb2.com` (pri AWS S3 prázdne) |
| `OFFSITE_BACKUP_REGION` | región (predvolené `auto`) |
| `OFFSITE_BACKUP_PREFIX` | predpona kľúčov (predvolené `digitalny-zosit`) |
| `OFFSITE_BACKUP_KEEP` | koľko záloh na konto držať v bucketi (predvolené 30) |
| `BACKUP_ENCRYPTION_KEY` | voliteľné heslo (min. 20 znakov): zálohy sa pred nahratím zašifrujú (AES-256-GCM) a v bucketi majú príponu `.enc`. **Ulož ho mimo Railway** (správca hesiel) - bez neho sa zálohy nedajú obnoviť. Obnova: súbor `.zip.enc` sa dá nahrať priamo v Nastavenia → Obnoviť zo zálohy (s heslom); mimo appky `python decrypt_backup.py zaloha.zip.enc zaloha.zip`. Pri zlom hesle sa nenahrá nič (nikdy nie nešifrovaná záloha). |

Súbory sa ukladajú ako `<predpona>/<konto>/<názov zálohy>.zip`. Po nahratí appka overí veľkosť súboru v bucketi a poreže staré zálohy nad limit (len v priečinku daného konta). Chyba nahrávania nezhodí vytvorenie zálohy – zobrazí sa ako upozornenie v Nastaveniach. Bucket nastav ako **súkromný** (zálohy obsahujú dáta zákazníkov) a pre appku vytvor kľúč len s prístupom k tomuto bucketu.

### Obnova prístupu, keď zmizne zoznam kont

Ak sa stratí alebo prepíše `data/accounts.db` (prihlásenie hlási zlé meno/heslo), dáta samotných kont v `data/accounts/<konto>/` ostávajú. Pripoj ich k novému menu a heslu:

```bash
python manage_accounts.py --list-orphans                       # ktoré priečinky nemajú konto
python manage_accounts.py --reattach SLUG --username MENO      # opýta sa na nové heslo
```

> ⚠️ Priečinok `data/` a súbory `*.db` nikdy neprepisuj rozbalením ZIP-u cez projekt – patria k tvojim dátam, nie ku kódu.

---

## 🏢 Registre a auto-doplnenie

Pri vytváraní zákazníka appka vie podľa zadaného IČO automaticky doplniť názov, adresu, DIČ aj IČ DPH (cez verejné API tretej strany, ktoré agreguje slovenské registre). Ak vyhľadávanie zlyhá alebo je nedostupné, appka sa vždy vráti k ručnému vyplneniu – nikdy to nič nezablokuje.

---

## ✅ Testy

```powershell
pip install -r requirements-dev.txt   # raz, pytest sa do produkčného obrazu neinštaluje
python -m pytest tests/ -q
```

Aktuálne **1393 testov**, rozdelených podľa oblasti:

| Súbor | Pokrýva |
|---|---|
| `test_main.py` | CRUD zákazníkov a zákaziek, validácia |
| `test_auth.py`, `test_auth_security.py` | Prihlásenie, rate limiting, session bezpečnosť, systematická kontrola všetkých chránených routes |
| `test_csrf.py` | CSRF ochrana (double-submit token) – chýbajúci/nesprávny/cudzí token, správny tok |
| `test_audit_log.py` | Audit log – zápis pri vytvorení/zmazaní/zmene stavu faktúry a ponuky, zmene nastavení firmy, zobrazenie `/audit-log` |
| `test_integrity_check.py` | Kontrola integrity DB ↔ uploads – chýbajúce aj osirotené súbory, vyčistenie, stránka `/settings/integrity-check` |
| `test_validators.py` | Validácia e-mailu, IBAN (mod-97), IČO (mod-11), DIČ, IČ DPH – formát, kontrolné súčty, normalizácia |
| `test_peppol_validation.py` | Validácia vygenerovaného Peppol XML – povinné polia, súčty, elektronická adresa (schéma + Endpoint ID), prenesenie daňovej povinnosti, nulová sadzba DPH – správne aj úmyselne pokazené scenáre |
| `test_peppol_scheme_validator.py` | `validators.validate_peppol_scheme_id()` – formát, zhoda schémy s krajinou (napr. že `9946` nie je slovenská schéma) |
| `test_invoices.py` | Fakturácia, DPH režim, stavy, PDF, Peppol XML, country_code/Endpoint ID zákazníka, nálezy validácie na detaile faktúry, dátum úhrady, kopírovanie, vyhľadávanie/filter |
| `test_quotes.py` | Cenové ponuky, konverzia na faktúru (aj z odoslanej), ochrana pred dvojitým kliknutím, odkazy ponuka ↔ faktúra, rýchle tlačidlo v zozname, proforma |
| `test_jobs_extras.py` | Fotodokumentácia, náklady/zisk (vrátane odmietnutia NaN/Infinity v sume nákladu), kalendár |
| `test_uploads.py` | Nahrávanie loga/podpisu/fotiek zákaziek, overenie typu súboru, auto-resize a EXIF orientácia fotiek |
| `test_uploads_resize.py` | Zúžený `except` pri zmene veľkosti fotky – poškodený obrázok vs. skutočná programátorská chyba, logovanie s traceback (`logger.exception`) |
| `test_number_retry.py` | Opakovanie uloženia dokladu pri kolízii čísla (`IntegrityError`) – doklad sa po `rollback()` znova pridá do session aj s položkami |
| `test_ico_lookup.py` | Auto-doplnenie podľa IČO (mockované externé API) |
| `test_backup.py` | Záloha a obnova databázy (izolované na dočasnom súbore), ochrana proti zip bomb, audit log záznam o obnove, kolízia názvu bezpečnostnej zálohy |
| `test_backup_integrity.py` | `PRAGMA integrity_check` pri tvorbe zálohy aj pri validácii obnovy – poškodená databáza sa odmietne |
| `test_upload_limit.py` | `uploads_utils.read_upload_with_limit()` – priebežné (nie až na konci) presadzovanie veľkostného limitu pri nahrávaní súboru |
| `test_dashboard.py` | Dashboard – graf tržieb po mesiacoch (okno 6 mesiacov, prelom roka, dobropisy), zoznam faktúr po splatnosti, pás „Vyžaduje pozornosť“ |
| `test_search.py` | Vyhľadávanie bez ohľadu na diakritiku a veľkosť písmen (faktúry, zákazky, zákazníci), viac slov naraz, filter podľa zákazníka, správne zakódované odkazy filtrov |
| `test_auto_backup.py` | Automatické zálohy: vytvorenie + test obnovy (poškodený ZIP, chýbajúci súbor, nesedí počet záznamov), retencia, bezpečné názvy a stiahnutie, plánovač, kontrola miesta, poistná záloha pred migráciou, trasy v Nastaveniach |
| `test_reminders.py` | Pripomienky platieb: výber faktúr, stupne podľa omeškania, text správy a `mailto`, evidencia pripomenutí, ochrana pred nesprávnym stavom faktúry |
| `test_profit.py` | Zisk zo zákazky: tržba bez DPH, dobropis/zálohové/návrhy, náklady podľa druhu, hodiny × sadzba, marža a strata, validácia, zobrazenie na kartách |
| `test_customer_history.py` | História zákazníka: súhrn (tržba bez DPH, zisk, doba platby), poznámky (pridanie, validácia, mazanie, izolácia, escapovanie), časová os, galéria fotiek |
| `test_password_reset.py` | Obnova hesla cez e-mail: zapnutie podľa SMTP, nastavenie e-mailu, celý priebeh, rovnaká odpoveď pre neexistujúce konto, jednorazový/expirovaný/cudzí token, limit žiadostí, audit |
| `test_numbering.py` | Číslovanie dokladov: číselné poradie aj nad 999, nezávislé rady, hľadanie medzier v rade |
| `test_wal.py` | SQLite v režime WAL (journal_mode, synchronous, cudzie kľúče), čitateľ nie je blokovaný otvorenou zápisovou transakciou |
| `test_deploy_hardening.py` | Docker: neprivilegovaný používateľ, entrypoint (oprava Volume, odmietnutie štartu), `.dockerignore`; proxy varovanie; token obnovy hesla (hlavičky, filter logov) |
| `test_secret_key.py` | Kontrola SECRET_KEY: chýbajúci, krátky, predvolený, nenáhodný; skutočný štart appky v produkcii a lokálne |
| `test_concurrent_numbering.py` | 20 súbežných požiadaviek na vytvorenie faktúr/ponúk dostane jedinečné čísla bez chyby |
| `test_gap_warnings.py` | Medzery v číslovaní ako upozornenie: preverenie s dôvodom, audit, izolácia kont |
| `test_account_isolation.py` | Izolácia kont: dve skutočné kontá, konto B skúša KAŽDÚ trasu s ID a názvami súborov konta A (faktúry, ponuky, zákazky, fotky, logo, zálohy, exporty), path traversal, obnova cudzej zálohy; A ostáva nezmenené |
| `test_monitoring.py` | `/health/details`: token, zastaraná/chýbajúca záloha, zlyhanie, off-site, migrácie, žiadny únik textov chýb |
| `test_session_invalidation.py` | Odhlásenie ostatných zariadení po zmene hesla, zmazané konto, cookie bez odtlačku |
| `test_change_password.py` | Zmena hesla: úspech, nové prihlásenie, zlé súčasné heslo, validácia, limit pokusov, CSRF, vyžaduje prihlásenie, izolácia kont, audit log |
| `test_hosting_and_offsite_restore.py` | `docker-compose.yml` (production, dôvera proxy len v internej sieti), Caddyfile, CLI `offsite_restore.py`: zoznam, stiahnutie a rozšifrovanie zálohy, zlé heslo, neprepisovanie, nebezpečné názvy |
| `test_route_policy.py` | Každá trasa vyžaduje prihlásenie (okrem zoznamu verejných) a každá meniaca trasa má CSRF; vypnutie/strop registrácie; slabé heslá |
| `test_totals_property.py` | Náhodné kontroly súm faktúry (DPH po sadzbách, zaokrúhľovanie, dobropis = presný opak) |
| `test_issued_invoice_protection.py` | Vystavenú faktúru nemožno zmazať ani cez cascade |
| `test_backup_crypto.py` | Šifrovanie záloh: rozšifrovanie, zlé heslo, upravený/odrezaný/prehodený súbor, test obnovy nad rozšifrovanou kópiou, nič sa nenahrá pri zlom hesle, CLI `decrypt_backup.py` |
| `test_offsite_backup.py` | Nahrávanie záloh mimo servera: zapnutie podľa premenných, kľúče/predpona, kontrola veľkosti, SHA-256 a stiahnutie späť, test obnovy stiahnutej kópie, retencia po kontách, chyba siete nezhodí lokálnu zálohu, upozornenia v Nastaveniach |
| `test_accounting.py` | Zaokrúhľovanie DPH po sadzbách (nie po riadkoch), rekapitulácia, súlad s Peppol BR-CO-17 |
| `test_disaster_recovery.py` | Úplná strata úložiska a obnova zo stiahnutej zálohy, poškodené zálohy, záloha zo staršej verzie, rollback pri zlyhaní migrácie, poškodené logo v PDF |
| `test_migrations_concurrency.py` | Migrácie nad dátami z každej revízie, zlyhanie jedného konta, súbežné zápisy počas zálohy |
| `test_hardening.py` | Audit: hash hesiel (formát, starý hash, poškodené hashe), limity (IP, meno, registrácia, čistenie), obnova session, bezpečnostné hlavičky, vypnuté /docs, busy_timeout |
| `test_export.py` | Export: obdobia (hranice roka, priestupný rok), výber dokladov, CSV formát, štruktúra Excelu, DPH rekapitulácia, ochrana pred vzorcami, ZIP s PDF, validácia, audit |
| `test_tenant_context.py` | Regresia: fotky zákaziek sa servujú po skutočnom prihlásení (aktuálne konto v kontexte požiadavky), cudzie konto ich nevidí |
| `test_mobile.py` | Mobilné zobrazenie - viewport meta v každej stránke, popisky buniek tabuliek položiek (pre kartičkové rozloženie), kľúčové mobilné CSS pravidlá |
| `test_deploy.py` | `/health` (OK aj 503), štart na `$PORT`, poradie migrácií v `start.sh`, Dockerfile bez `VOLUME` s dátami v `/app/data`, `manage_accounts --upgrade-all` na existujúcom konte |
| `test_error_handling.py` | Globálny handler neošetrených (500) chýb – používateľovi sa ukáže všeobecná hláška (nie detail výnimky), zaloguje sa traceback, JSON aj HTML vetva |

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
│   └── conftest.py       # SESSION_HTTPS_ONLY=false pre testy, nulovanie limitov (viď sekcia Testy)
│
├── alembic_accounts/     # migrácie master databázy kont (accounts.db)
│
├── data/                 # accounts.db + dáta kont (uploads, backups) - negituje sa; na Railway = Volume
│
├── auth.py                 # hashovanie hesla, session, rate limiting
├── audit_log.py             # zápis do audit logu (história zmien)
├── backup_utils.py         # záloha/obnova cez SQLite backup API
├── export_utils.py         # export pre účtovníka: obdobia, CSV, Excel
├── customer_history.py     # história zákazníka: súhrn, časová os, galéria fotiek
├── profit_utils.py         # zisk zo zákazky: tržba, náklady podľa druhu, marža
├── reminder_utils.py       # pripomienky platieb: stupne, text správy, mailto
├── auto_backup.py          # automatické zálohy, test obnovy, retencia, plánovač
├── csrf.py                  # CSRF ochrana (double-submit token)
├── dashboard_utils.py       # graf tržieb po mesiacoch, faktúry po splatnosti pre dashboard
├── database.py
├── delivery_note_pdf.py    # PDF dodacieho listu (z faktúry aj ponuky)
├── form_utils.py           # zdieľané parsovanie formulárov (faktúry aj ponuky) – vrátane job_id
├── ico_lookup.py           # auto-doplnenie firmy podľa IČO
├── integrity_check.py       # kontrola DB <-> uploads (chýbajúce/osirotené súbory)
├── invoice_pdf.py          # generovanie PDF faktúr
├── invoice_utils.py        # číslovanie (vrátane bezpečného opakovania pri kolízii), DPH výpočty, stavové prechody
├── main.py                 # dashboard, routing
├── models.py                # SQLAlchemy modely
├── peppol_xml.py            # export do Peppol BIS 3.0.21 XML formátu
├── peppol_validation.py      # kontrola vygenerovaného Peppol XML (BR-01.., BR-CO-10.., BR-AE-.., BR-Z-..)
├── search_utils.py          # hľadanie bez diakritiky a veľkosti písmen (fold, matches_search)
├── qr_payment.py             # QR platobný kód (PAY by square)
├── quote_pdf.py               # generovanie PDF cenových ponúk
├── schemas.py                  # Pydantic modely (validácia)
├── templates_config.py         # zdieľaná Jinja2Templates inštancia
├── uploads_utils.py             # nahrávanie/overenie obrázkov
├── validators.py                # validácia e-mailu, IBAN, IČO, DIČ, IČ DPH
│
├── .env.example
├── .gitignore
├── accounts_db.py          # master databáza kont
├── accounts_models.py      # model Account
├── manage_accounts.py      # --upgrade-all (migrácia všetkých kont), --list
├── tenancy.py              # cesty, provisioning a engine jednotlivých kont
├── start.sh                # štart kontajnera: migrácie + uvicorn na $PORT
├── Dockerfile
├── railway.json            # Railway: Dockerfile + healthcheck /health
├── alembic.ini
├── alembic_accounts.ini
├── requirements.txt         # produkčné závislosti (Docker obraz)
├── requirements-dev.txt     # + pytest, pypdf, httpx (na testy)
└── README.md
```


## Nasadenie mimo Railway

Appka sa dá spustiť aj na vlastnom serveri (`docker-compose.yml` + Caddy s automatickým HTTPS) a dáta obnoviť
zo zálohy v R2 (`python offsite_restore.py --download --account <konto>`). Celý postup: `docs/HOSTING.md`.
