#!/bin/sh
# Vstupný bod kontajnera: appka NEbeží ako root.
#
# Railway Volume sa pripája s vlastníkom root, preto kontajner štartuje ako
# root len na krátky čas: opraví vlastníka /app/data (iba ak niečo nepatrí
# používateľovi `app`) a hneď potom spustí start.sh pod neprivilegovaným
# používateľom `app` (setpriv). Ak by sa zápis pod `app` nepodaril (napr.
# platforma zakáže chown), appka radšej beží ako root s hlasným varovaním,
# než aby sa deploy rozbil; s REQUIRE_NON_ROOT=true sa v takom prípade nespustí.
#
# Premenné (hlavne pre testy): APP_USER (predvolené app), APP_DATA_DIR
# (predvolené /app/data).
set -e

APP_USER="${APP_USER:-app}"
DATA_DIR="${APP_DATA_DIR:-/app/data}"
APP_GROUP="$(id -gn "$APP_USER" 2>/dev/null || echo "$APP_USER")"

say() { echo "[SPUSTENIE] $*"; }

if [ "$(id -u)" != "0" ]; then
    # Kontajner už beží pod neprivilegovaným používateľom (docker run --user ...).
    exec "$@"
fi

mkdir -p "$DATA_DIR"

# Vlastník sa opravuje len ak niečo vo Volume nepatrí používateľovi appky
# (prvý štart po prechode z root, nový Volume). Inak sa nič nemení, štart je rýchly.
if [ -n "$(find "$DATA_DIR" -not -user "$APP_USER" -print -quit 2>/dev/null)" ]; then
    say "Opravujem vlastníka $DATA_DIR na $APP_USER (jednorazovo)."
    chown -R "$APP_USER:$APP_GROUP" "$DATA_DIR" \
        || say "VAROVANIE: chown $DATA_DIR zlyhal."
fi

as_app() { setpriv --reuid="$APP_USER" --regid="$APP_GROUP" --init-groups "$@"; }

if as_app sh -c 'probe="$1/.write-test-$$"; : > "$probe" && rm -f "$probe"' sh "$DATA_DIR" 2>/dev/null; then
    say "Appka beží pod používateľom $APP_USER (nie root)."
    exec setpriv --reuid="$APP_USER" --regid="$APP_GROUP" --init-groups "$@"
fi

if [ "${REQUIRE_NON_ROOT:-false}" = "true" ]; then
    say "CHYBA: $APP_USER nemôže zapisovať do $DATA_DIR a REQUIRE_NON_ROOT=true - nespúšťam."
    exit 1
fi

say "VAROVANIE: $APP_USER nemôže zapisovať do $DATA_DIR - appka beží ako root. Skontroluj práva Volume."
exec "$@"
