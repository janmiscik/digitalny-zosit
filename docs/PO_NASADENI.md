# Kontrola po nasadení

Postup po každom deployi na Railway (alebo inom hostingu), hlavne po zmene verzie. Trvá asi 10 minút. Čo robiť pri poplachu: `docs/PREVADZKA.md`; premenné: `docs/KONFIGURACIA.md`.

## A. Logy nasadenia (Railway → Deployments → View logs)

Hľadaj tieto riadky (použi vyhľadávanie v logu):

| Hľadaj | Správne | Zle a čo robiť |
|---|---|---|
| `[ULOZISKE]` | `OK: /app/data je samostatný disk (Volume)` | `VAROVANIE`: Volume nie je pripojený na `/app/data`, dáta sa stratia pri redeployi. Pripoj Volume a nasadenie zopakuj |
| `[SPUSTENIE]` | `Appka beží pod používateľom app` | `VAROVANIE ... beží ako root`: skontroluj práva Volume (`docs/PREVADZKA.md`) |
| `Beh appky:` | `používateľ=app ... prostredie=production` | `prostredie=development` alebo root: oprav `APP_ENV` / práva |
| `Monitoring (/health/details)` | `MONITOR_TOKEN nastavený (dĺžka ...)` a odtlačok sa zhoduje s tým, ktorý máš | `NIE JE nastavený`: doplň `MONITOR_TOKEN` v službe appky a redeployni |
| `Running upgrade` | migrácie prebehli bez `Error` (po verzii s čiastočnými úhradami pribudne tabuľka `invoice_payments`) | `Error` alebo `Traceback`: nasadenie nechaj, nič nemaž, pozri `docs/OBNOVA.md` (poistná záloha `pre-upgrade` je vytvorená pred migráciou) |
| `Traceback`, `ERROR` | žiadne | pošli riadky vývojárovi |

## B. Stav a monitoring

1. Otvor `https://<appka>/health`: musí vrátiť `{"status": "ok"}`.
2. Otvor `https://<appka>/health/details?token=<MONITOR_TOKEN>`: `"status": "ok"`, v `runtime` je `"user": "app"` a prázdny zoznam `problems`. Hneď po prvom štarte môže chvíľu hlásiť `backup` (kým prebehne prvá záloha); po niekoľkých minútach musí zmiznúť.
3. Bez tokenu musí `/health/details` vrátiť 404 a so zlým tokenom 401.
4. V UptimeRobote musia byť dva monitory: `/health` a `/health/details?token=...`; oba zelené.

## C. Funkčná skúška v prehliadači (konto správcu)

1. Prihlás sa. Nastavenia obsahujú kartu „Správa kont servera"; `/admin/accounts` funguje.
2. Strana sa zobrazuje s písmami appky (Space Grotesk, Inter) a v prehliadači (F12 → Network) nie sú žiadne požiadavky na `google` ani iné cudzie servery.
3. Vytvor testovaciu faktúru (Návrh → Odoslaná). Zapíš čiastočnú úhradu: faktúra ostane Odoslaná a ukáže „zostáva X €"; po doplatení prejde na Uhradená. Potom ju stornuj s dôvodom, nech ostane čistý prehľad (vystavenú faktúru zmazať nemožno).
4. Stiahni PDF faktúry (diakritika, QR kód) a Peppol XML, ak ho používaš.
5. Nastavenia → Zálohovať teraz: stránka hlási „Záloha bola vytvorená a overená".
6. Voliteľne: zákazník podľa IČO (načítanie z ORSF môže zlyhať, je to v poriadku, appka ho nevyžaduje).

## D. Hlásenia CSP

V logu hľadaj `[CSP]`. Žiadny riadok = všetko v poriadku. Ak sa objavia, pozri „blokované" a „stránka": zvyčajne ide o rozšírenie prehliadača (`chrome-extension:`), čo ignoruj. Ak ide o vlastnú adresu appky, pošli riadok vývojárovi.

## E. Zálohy mimo servera (raz po zmene nastavení a pred odovzdaním)

1. V R2 bucket `digitalny-zosit-zalohy` musia byť nové objekty po poslednej zálohe (šifrované, prípona `.enc`, ak je `BACKUP_ENCRYPTION_KEY`).
2. Lokálne: `python offsite_restore.py --verify` (premenné `OFFSITE_BACKUP_*` a `BACKUP_ENCRYPTION_KEY` nastav v termináli); musí skončiť bez chyby. Postup: `docs/OBNOVA.md`.

## F. Ak sa niečo pokazí po nasadení

1. Railway → Deployments → predchádzajúci úspešný deploy → Redeploy (vráti kód).
2. Dáta (Volume) sa pri vrátení kódu nemenia. Ak nová verzia už zmigrovala databázy, starší kód ich môže odmietnuť; vtedy obnov poistnú zálohu `pre-upgrade` podľa `docs/OBNOVA.md`.
3. Poznač si čas, text chyby z logu a ktorý bod tohto zoznamu zlyhal.
