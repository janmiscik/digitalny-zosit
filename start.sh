#!/bin/sh
# Štartovací skript kontajnera (Railway aj lokálny Docker).
# `set -e`: ak zlyhá akýkoľvek migračný krok, appka sa NESPUSTÍ -
# radšej neúspešný deploy (Railway ponechá predošlú verziu bežať),
# než appka nad čiastočne zmigrovanými databázami.
set -e

# 1. Master databáza kont (accounts.db)
alembic -c alembic_accounts.ini upgrade head

# 2. Business databázy VŠETKÝCH existujúcich kont. Nové konto dostane
#    migrácie automaticky pri registrácii, ale pri novej verzii appky by
#    existujúce kontá novú migráciu nedostali - tento krok to rieši.
#    Je idempotentný (kto je už na `head`, ten sa nezmení).
python manage_accounts.py --upgrade-all

# 3. Appka - Railway posiela port cez $PORT
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
