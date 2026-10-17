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

- **`SECRET_KEY`** – náhodný reťazec pre podpis prihlasovacej session (povinné, appka bez neho nenaštartuje):

  ```powershell
  python -c "import secrets; print(secrets.token_hex(32))"
  ```

- **`SESSION_HTTPS_ONLY`** – či sa prihlasovacia cookie posiela iba cez HTTPS (predvolené `true`). Nechaj `true` v produkcii (Railway a pod.). Iba ak appku spúšťaš lokálne cez obyčajné `http://localhost`, nastav na `false` - inak sa neprihlásiš (prehliadač cookie s príznakom `Secure` cez obyčajné http vôbec neuloží).

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

1. **Premenné služby (Variables):** `SECRET_KEY` (povinné) a `SESSION_HTTPS_ONLY=true`. `ACCOUNTS_DATABASE_URL` a `ACCOUNTS_DATA_DIR` sú v Dockerfile už nastavené na `/app/data/...`, netreba ich meniť.
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

- **Kontá a registrácia** (`/register`, `/login`) – každý používateľ má vlastné konto (`Account` v `accounts.db`: meno, hash hesla, `slug` priečinka), heslo min. 8 znakov hashované cez PBKDF2, session-based prihlásenie (cookie). Dáta kont sú fyzicky oddelené – appka pri obsluhe jedného konta nikdy neotvorí súbor iného (viď `tenancy.py`).
- HTML stránky vyžadujú prihlásenie – neprihláseného používateľa presmerujú na `/login`. JSON API endpointy vrátia `401 Unauthorized`.
- **Rate limiting** na `/login` – po 5 zlých pokusoch v priebehu 5 minút sa prihlásenie zamkne na 5 minút (kľúčované podľa IP adresy klienta, aby útočník na jedno konto nezamkol všetkým ostatným).
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

- **Stiahnutie zálohy** – konzistentná kópia databázy cez natívne SQLite backup API (bezpečné aj počas behu appky); pred zabalením sa databáza overí cez `PRAGMA integrity_check` – poškodená databáza sa nezálohuje.
- **Obnova zo zálohy** – validuje sa, že nahraný súbor je skutočne platná záloha tejto appky, vrátane `PRAGMA integrity_check` nad `database.db` v zálohe. Pred obnovou appka automaticky uloží bezpečnostnú kópiu aktuálneho stavu do `backups/` (negituje sa) – názov súboru (`pred-obnovou-RRRRMMDD-HHMMSS.zip`) je odolný voči kolízii: ak by v tej istej sekunde prebehli dve obnovy, druhá bezpečnostná záloha dostane príponu (`-2`, `-3`...) namiesto toho, aby ticho prepísala prvú.

---

## 🏢 Registre a auto-doplnenie

Pri vytváraní zákazníka appka vie podľa zadaného IČO automaticky doplniť názov, adresu, DIČ aj IČ DPH (cez verejné API tretej strany, ktoré agreguje slovenské registre). Ak vyhľadávanie zlyhá alebo je nedostupné, appka sa vždy vráti k ručnému vyplneniu – nikdy to nič nezablokuje.

---

## ✅ Testy

```powershell
python -m pytest tests/ -q
```

Aktuálne **588 testov**, rozdelených podľa oblasti:

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
| `test_quotes.py` | Cenové ponuky, konverzia na faktúru, proforma |
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
│   └── conftest.py       # SESSION_HTTPS_ONLY=false pre testy (viď sekcia Testy)
│
├── alembic_accounts/     # migrácie master databázy kont (accounts.db)
│
├── data/                 # accounts.db + dáta kont (uploads, backups) - negituje sa; na Railway = Volume
│
├── auth.py                 # hashovanie hesla, session, rate limiting
├── audit_log.py             # zápis do audit logu (história zmien)
├── backup_utils.py         # záloha/obnova cez SQLite backup API
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
├── requirements.txt
└── README.md
```
