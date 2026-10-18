#!/bin/sh
# Štartovací skript kontajnera (Railway aj lokálny Docker).
# `set -e`: ak zlyhá akýkoľvek migračný krok, appka sa NESPUSTÍ -
# radšej neúspešný deploy (Railway ponechá predošlú verziu bežať),
# než appka nad čiastočne zmigrovanými databázami.
set -e

# 0. Diagnostika úložiska (len výpis do logu, nič nemení)
python diagnose_storage.py || true

# 1. Master databáza kont (accounts.db)
alembic -c alembic_accounts.ini upgrade head

# 1b. Poistná záloha kont, ktorým čaká migrácia schémy (ak by migrácia
#     pokazila dáta, je z čoho obnoviť). Zlyhanie zálohy nezastaví štart -
#     varovanie je v logu (a appka zálohuje aj sama, viď auto_backup.py).
python manage_accounts.py --backup-all --kind pre-upgrade --only-if-migration-pending \
    || echo "VAROVANIE: poistná záloha pred migráciou zlyhala - pozri výpis vyššie."

# 2. Business databázy VŠETKÝCH existujúcich kont. Nové konto dostane
#    migrácie automaticky pri registrácii, ale pri novej verzii appky by
#    existujúce kontá novú migráciu nedostali - tento krok to rieši.
#    Je idempotentný (kto je už na `head`, ten sa nezmení).
python manage_accounts.py --upgrade-all

# 3. Appka - Railway posiela port cez $PORT
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
