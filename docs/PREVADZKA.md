# Prevádzka: monitoring, poplachy a čo robiť

## Čo sa sleduje a kde to vidno

| Čo | Kde | Poplach |
|----|-----|---------|
| Appka žije | `/health` (Railway healthcheck) | 503 pri nedostupnej databáze kont |
| Zálohy, off-site kópia, prerušená obnova, migrácie | `/health/details` (token `MONITOR_TOKEN`) | 503 + zoznam problémov |
| Stav záloh pre používateľa | Nastavenia → Záloha (upozornenia) | červené varovanie |
| Integrita dát | Nastavenia → Kontrola integrity | chýbajúce/osirotené súbory = chyba, medzery v číslovaní = upozornenie |

### Zapnutie externého monitoringu (UptimeRobot a pod.)
1. V Railway nastav `MONITOR_TOKEN` na dlhý náhodný reťazec.
2. V UptimeRobote vytvor monitor HTTP(s) na `https://<appka>/health/details?token=<MONITOR_TOKEN>`
   (alebo s hlavičkou `Authorization: Bearer <MONITOR_TOKEN>`), interval 5 – 15 min.
3. Stav 503 = poplach (e-mail príde sám). Telo odpovede hovorí, čo je zle:

| `kind` | Význam | Čo robiť |
|--------|--------|----------|
| `backup` | Záloha chýba, je staršia než 2 × interval + 1 h, alebo posledný pokus zlyhal | Pozri Railway logy (`Záloha konta ... zlyhala`), voľné miesto na Volume, spusti Nastavenia → Vytvoriť zálohu |
| `offsite` | Kópia mimo servera chýba, zlyhala (aj pri nezhode SHA-256 alebo neprejdenom teste obnovy) alebo je stará | Skontroluj `OFFSITE_BACKUP_*` a prístup do bucketu, potom vytvor zálohu znova |
| `restore` | Prerušená obnova, ktorú sa pri štarte nepodarilo vrátiť | Pozri nižšie, treba ručný zásah |
| `migration` | Databáza konta nie je na poslednej migrácii | `python manage_accounts.py --upgrade-all` (alebo reštart; migráciu robí aj `start.sh`) |

`/health` zámerne nezávisí od záloh, aby Railway kvôli zastaranej zálohe nezastavil nasadenie.

## Prerušená obnova zo zálohy (pád procesu)
Obnova mení databázu a súbory v dvoch krokoch. Pred prvou zmenou sa zapíše žurnál
`restore-journal.json` do priečinka konta (ukazuje na poistnú zálohu spred obnovy) a po úspešnom
konci sa zmaže. Ak proces zomrie uprostred (OOM, reštart kontajnera), pri ďalšom štarte sa
automaticky vráti databáza aj súbory z poistnej zálohy a do histórie zmien sa zapíše
`backup.restore_interrupted`. Robí to `start.sh` (pred migráciami) aj appka sama pri štarte.

Ak automatický návrat zlyhá, žurnál ostane a monitoring hlási `restore`. Ručne:
1. Pozri log (`Vrátenie prerušenej obnovy konta ... zlyhalo`), zistíš prečo (chýba poistná záloha, plný disk).
2. Poistná záloha je v `accounts/<konto>/backups/` (názov je v `restore-journal.json`).
3. Nahraj ju v Nastaveniach → Obnoviť zo zálohy, alebo spusti `python manage_accounts.py --recover-restores`
   po oprave príčiny. Žurnál sa zmaže až po úspešnom návrate.

## Ochrana externých záloh (bucket)
Zálohy obsahujú dáta zákazníkov a sú posledná poistka pri strate Volume. Odporúčané nastavenie:

1. **Bucket súkromný.** Žiadny verejný prístup, žiadna verejná `r2.dev` adresa ani verejný doménový alias.
2. **Obmedzený kľúč.** Token len na tento bucket s právom Object Read & Write (nie Admin, nie celý účet).
   Pri podozrení na únik premenných kľúč zruš a vydaj nový (Cloudflare → R2 → Manage API Tokens → Roll).
3. **Ochrana pred zmazaním.** Zapni to, čo poskytovateľ ponúka: verzovanie objektov a/alebo zámok/retenciu
   objektov (S3 Object Lock, Backblaze B2 Object Lock, R2 bucket lock/retenčné pravidlá). Appka s tým počíta:
   ak bucket mazanie starých záloh zakáže, záloha ostane úspešná a v Nastaveniach sa zobrazí upozornenie
   „staré zálohy sa v úložisku nepodarilo zmazať". Staré zálohy potom čistí pravidlo životného cyklu bucketu.
4. **Pravidelný test obnovy.** Appka po KAŽDOM nahratí zálohu z bucketu stiahne, porovná SHA-256 a spustí
   plný test obnovy (rozbalenie, integrita databázy, počty riadkov, súbory). Zlyhanie sa hlási v Nastaveniach
   aj cez monitoring. Raz za štvrťrok navyše nacvič skutočnú obnovu podľa `docs/OBNOVA.md`.
5. **Kľúč nikde mimo Railway.** Neposielaj ho e-mailom ani do chatu.

## Štartovacia poistka a poradie
`start.sh`: migrácia `accounts.db` → návrat prerušených obnov → poistná záloha kont s čakajúcou migráciou →
migrácie kont → štart appky. Návrat prerušenej obnovy je pred migráciami zámerne, aby sa migrovala
konzistentná databáza.
