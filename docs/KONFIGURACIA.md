# Produkčná konfigurácia

Čo nastaviť podľa toho, ako kupujúci appku používa. Pred spustením (a po každej zmene premenných) spusti kontrolu:

```
python tools/check_config.py --profile managed --env-file .env
```

Namiesto `--env-file` sa čítajú premenné prostredia, takže na serveri stačí `python tools/check_config.py --profile managed`. Výstup je `OK`, `VAROVANIE` alebo `CHYBA`; pri chybe je návratový kód 1. Hodnoty tajných kľúčov sa nikdy nevypisujú.

## Tri spôsoby používania (profily)

| Profil | Kto zakladá kontá | Verejná registrácia | Typické použitie |
|---|---|---|---|
| `managed` | majiteľ servera (admin stránka `/admin/accounts` alebo `manage_accounts.py --create-account`) | **vypnutá** | prevádzkovateľ poskytuje appku viacerým živnostníkom a fakturuje im |
| `self-service` | každý používateľ si založí konto sám cez `/register` | **zapnutá so stropom** (`MAX_ACCOUNTS`) | otvorená služba |
| `single` | jedno konto, založené pri nasadení | **vypnutá** | appku používa jedna firma |

## Premenné podľa profilu

`P` = povinné, `O` = odporúčané, `-` = netreba.

| Premenná | managed | self-service | single | Poznámka |
|---|---|---|---|---|
| `APP_ENV` | `production` | `production` | `production` | nenastavené = production |
| `SECRET_KEY` | P | P | P | min. 32 znakov, náhodný (`python -c "import secrets; print(secrets.token_hex(32))"`) |
| `SESSION_HTTPS_ONLY` | `true` | `true` | `true` | predvolené `true` |
| `REGISTRATION_ENABLED` | `false` | `true` | `false` | |
| `MAX_ACCOUNTS` | - | P (napr. 50) | - | strop kont chráni disk a zálohy |
| `SERVER_ADMINS` | P | O | O | meno konta správcu, ktoré vidí „Správa kont“ |
| `MONITOR_TOKEN` | O | O | O | min. 32 znakov; `/health/details` pre UptimeRobot |
| `BACKUP_ENCRYPTION_KEY` | O | O | O | ak sa zálohy nahrávajú mimo servera; min. 20 znakov |
| `OFFSITE_BACKUP_*` | O | O | O | bucket, kľúče; bez nich zálohy ležia len na tom istom disku |
| `SMTP_HOST`, `SMTP_FROM`, `APP_BASE_URL` | voliteľné | O | voliteľné | obnova hesla e-mailom; `APP_BASE_URL` musí byť `https://` |
| `ENABLE_API_DOCS` | nenastavuj | nenastavuj | nenastavuj | vypisuje všetky trasy |

## Tajné kľúče pri odovzdaní

Kupujúci si všetky tajné hodnoty vygeneruje a uloží **sám**; tie z vývoja treba po odovzdaní vymeniť: `SECRET_KEY` (vymenou sa všetci odhlásia), `MONITOR_TOKEN`, `OFFSITE_BACKUP_*` (nový prístupový kľúč v bucketi) a `SMTP_PASSWORD`.

**Pozor na `BACKUP_ENCRYPTION_KEY`:** zálohy v bucketi sú šifrované týmto kľúčom. Po zmene kľúča sa staré zálohy dajú rozšifrovať **len starým kľúčom**, takže ho nevymieňaj, kým neexistujú nové zálohy a kým si starý kľúč neuložil na bezpečné miesto. Bez kľúča sa zálohy obnoviť nedajú.

## Ďalšie kontroly pred ostrou prevádzkou

1. V logu štartu (Railway: Deploy Logs) musí byť `Appka beží pod používateľom app`, `OK: /app/data je samostatný disk (Volume)` a riadok `Monitoring (/health/details): MONITOR_TOKEN nastavený`.
2. `/health/details?token=...` vracia `status: ok` a prázdne `problems`.
3. `/register` hlási vypnutú registráciu (profily `managed` a `single`).
4. Skúška obnovy: `python offsite_restore.py --verify` (pozri `docs/OBNOVA.md`).
5. Účtovné pravidlá (DPH, číslovanie, dobropisy) overiť s účtovníkom; testy nie sú daňové potvrdenie.
