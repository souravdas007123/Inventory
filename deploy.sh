#!/usr/bin/env bash
# One command to update the live site. Run it as the site user, from anywhere:
#     ~/htdocs/medsync.socialflylive.com/deploy.sh
# Many-shop setup: add --maintenance (or --maintenance=30, minutes) to show every shop the "we are updating" page during the update.
# Maintenance is switched off again only after the site answers. If something fails it stays on and the script says how to switch it off
# (it also ends by itself when its minutes run out). Plan it earlier from the platform panel (Maintenance) so shops get a notice first.
# It pulls the latest code, installs packages, updates the database of the main shop and of every shop, collects static
# files, restarts the app and checks that it answers. It stops at the first problem and says what failed.
set -Eeuo pipefail

cd "$(dirname "$0")"
PY=./venv/bin/python
SERVICE="${MEDSYNC_SERVICE:-medsync}"

step() { printf '\n==> %s\n' "$1"; }

WANT_MAINT=0
MAINT_MIN=20
for arg in "$@"; do
    case "$arg" in
        --maintenance) WANT_MAINT=1 ;;
        --maintenance=*) WANT_MAINT=1; MAINT_MIN="${arg#*=}" ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "Unknown option: $arg  (use --maintenance or --maintenance=MINUTES)"; exit 2 ;;
    esac
done

MULTI=0
if grep -qiE '^MULTI_SHOP=(1|true|yes|on)' .env 2>/dev/null; then MULTI=1; fi
MAINT_ON=0

still_on() {
    echo
    echo "!! MAINTENANCE IS STILL ON, so shops still see the update page."
    echo "   When the site is fine, switch it off:  $PY manage.py maintenance off"
    echo "   (It also ends by itself when its minutes run out.)"
}
on_error() {
    code=$?
    if [ "$MAINT_ON" = 1 ]; then still_on; fi
    exit "$code"
}
trap on_error ERR

# Everything below is inside one function, so bash has read it all before the first step runs. That matters: "git pull"
# below can replace this very file, and bash must not read half of an old and half of a new script.
run_update() {
step "Current version"
git rev-parse --short HEAD

step "Getting the latest code"
git pull --ff-only

if [ "$WANT_MAINT" = 1 ]; then
    if [ "$MULTI" = 1 ]; then
        step "Switching maintenance mode ON ($MAINT_MIN minutes)"
        if $PY manage.py maintenance on --minutes "$MAINT_MIN" --message "We are updating MedSync."; then
            MAINT_ON=1
        else
            echo "Could not switch maintenance on (an older version?). Continuing without it."
        fi
    else
        echo "Maintenance mode is only for the many-shop setup. Skipped."
    fi
fi

step "Installing packages"
./venv/bin/pip install -q -r requirements.txt

step "Checking the settings"
$PY manage.py check

step "Updating the database (main shop and every shop)"
if [ "$MULTI" = 1 ]; then
    $PY manage.py migrate_schemas
else
    $PY manage.py migrate
fi

step "Collecting static files"
$PY manage.py collectstatic --noinput | tail -1

step "Restarting the app"
if sudo -n systemctl restart "$SERVICE" 2>/dev/null; then
    echo "Restarted $SERVICE."
else
    echo "This user may not restart the service. As root, run:  systemctl restart $SERVICE"
    echo "(To allow it for this user, see the 'One command update' section of the README.)"
    if [ "$MAINT_ON" = 1 ]; then still_on; fi
    exit 0
fi

step "Checking that the site answers"
sleep 3
HOST="$(grep -E '^SHOP_BASE_DOMAIN=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' || true)"
if [ -n "$HOST" ]; then
    CODE="$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $HOST" -H 'X-Forwarded-Proto: https' http://127.0.0.1:8091/healthz/ || true)"
    if [ "$CODE" = "200" ]; then
        echo "OK: the site answers (200)."
        if [ "$MAINT_ON" = 1 ]; then
            step "Switching maintenance mode OFF"
            $PY manage.py maintenance off && MAINT_ON=0
        fi
    else
        echo "WARNING: the health check gave '$CODE'. Look at:  journalctl -u $SERVICE -n 50"
        if [ "$MAINT_ON" = 1 ]; then still_on; fi
    fi
else
    echo "Skipped the health check (SHOP_BASE_DOMAIN is not set in .env)."
    if [ "$MAINT_ON" = 1 ]; then still_on; fi
fi
echo; echo "Done. Now running version: $(git rev-parse --short HEAD)"
}
run_update
exit 0
