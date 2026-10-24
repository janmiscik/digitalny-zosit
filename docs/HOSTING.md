# Nasadenie mimo Railway (poistka proti výpadku)

Appka je obyčajný Docker kontajner s jedným priečinkom dát (`/app/data`). Dá sa spustiť na ľubovoľnom
serveri s Dockerom. Tento návod popisuje vlastný server (VPS) s automatickým HTTPS; rovnaký obraz
(`Dockerfile`) funguje aj na iných platformách, kde pripojíš trvalý disk na `/app/data`.

## Čo potrebuješ
- Server s Linuxom a Dockerom (aj `docker compose`), min. 1 GB RAM, verejná IP.
- Doménu (alebo subdoménu) s DNS záznamom A smerujúcim na IP servera.
- Zálohy mimo Railway (R2) už máš zapnuté: z nich appku obnovíš.

## 1. Stiahni zálohu z R2 (na svojom počítači)
Nastav premenné `OFFSITE_BACKUP_*` (rovnaké ako na Railway) a prípadne `BACKUP_ENCRYPTION_KEY`, potom:

```
python offsite_restore.py --list
python offsite_restore.py --download --account admin
```

Stiahne a rozšifruje NAJNOVŠIU zálohu konta do priečinka `obnova/` (názov `admin-<záloha>.zip`).
Opakuj pre každé konto. Priečinok `obnova/` obsahuje dáta zákazníkov, nikam ho nenahrávaj a po obnove
ho zmaž.

## 2. Spusti appku na serveri
```
git clone <tvoj repozitár> && cd digitalny-zosit
cp .env.example .env
```
V `.env` nastav minimálne:
- `SECRET_KEY`: nový náhodný reťazec (`python -c "import secrets; print(secrets.token_hex(32))"`),
- `APP_ENV=production`,
- `APP_BASE_URL=https://tvoja-domena` (ak používaš obnovu hesla e-mailom) a `SMTP_*`,
- `OFFSITE_BACKUP_*` a `BACKUP_ENCRYPTION_KEY` (rovnaké ako doteraz, aby sa zálohy ďalej ukladali do R2),
- `REGISTRATION_ENABLED=true` na čas obnovy (potom `false`).

Spusti:
```
DOMAIN=zosit.tvoja-domena.sk docker compose up -d --build
docker compose logs -f app
```
V logu hľadaj `[SPUSTENIE] Appka beží pod používateľom app` a `[ULOZISKE]`. Caddy si samo vybaví HTTPS
certifikát (porty 80 a 443 musia byť otvorené). Over `https://tvoja-domena/health`.

## 3. Obnov dáta
1. V appke sa **zaregistruj s rovnakým menom konta**, aké bolo na Railway.
2. Nastavenia → **Obnoviť zo zálohy** → nahraj `admin-<záloha>.zip`.
3. Skontroluj faktúry, zákazníkov a logo na PDF. Doklady vystavené po poslednej zálohe treba zapísať znova.
4. Spusti ručnú zálohu a over „Kópia mimo servera ✅".
5. Nastav `REGISTRATION_ENABLED=false`, ak appku používaš len ty (`docker compose up -d` po zmene `.env`).

## 4. Presmeruj doménu
Ak si používal doménu na Railway, zmeň DNS na IP nového servera. Prihlasovacie cookie sú viazané na doménu,
po zmene sa treba prihlásiť znova.

## Údržba
- **Aktualizácia:** `git pull && docker compose up -d --build` (migrácie sa spustia samy pri štarte).
- **Dáta** sú v Docker volume `zosit-data`. Zálohuj aj samotný server (snapshot u poskytovateľa), ale spoľahni sa hlavne na zálohy v R2.
- **Monitoring:** UptimeRobot na `https://tvoja-domena/health` a `/health/details?token=...` (`MONITOR_TOKEN`).
- **Proxy:** `docker-compose.yml` dôveruje hlavičke `X-Forwarded-For` len od Caddy v internej sieti (`172.30.0.0/24`), takže limit pokusov o prihlásenie funguje správne.
- **Firewall:** otvor len 22 (SSH), 80 a 443. Appka sama nie je vystavená priamo.

## Iné platformy
Obraz z `Dockerfile` sa dá nasadiť aj inde (Render, Fly.io, iný kontajnerový hosting), ak platforma ponúka
**trvalý disk pripojený na `/app/data`** a health check na `/health`. Nastav premenné prostredia ako vyššie
a `FORWARDED_ALLOW_IPS` podľa ich proxy (nenechávaj `*` mimo Railway, pozri `docs/PREVADZKA.md`). Konkrétne
kroky a ceny si over v dokumentácii danej platformy; tu ich zámerne neuvádzam, lebo sa menia.
