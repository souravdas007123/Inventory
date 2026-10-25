#!/usr/bin/env bash
# One command to update the live site. Run it as the site user, from anywhere:
#     ~/htdocs/medsync.socialflylive.com/deploy.sh
# It pulls the latest code, installs packages, updates the database of the main shop and of every shop, collects static
# files, restarts the app and checks that it answers. It stops at the first problem and says what failed.
set -euo pipefail

cd "$(dirname "$0")"
PY=./venv/bin/python
SERVICE="${MEDSYNC_SERVICE:-medsync}"

step() { printf '\n==> %s\n' "$1"; }

step "Current version"
git rev-parse --short HEAD

step "Getting the latest code"
git pull --ff-only

step "Installing packages"
./venv/bin/pip install -q -r requirements.txt

step "Checking the settings"
$PY manage.py check

step "Updating the database (main shop and every shop)"
if grep -qiE '^MULTI_SHOP=(1|true|yes|on)' .env 2>/dev/null; then
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
    exit 0
fi

step "Checking that the site answers"
sleep 3
HOST="$(grep -E '^SHOP_BASE_DOMAIN=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' || true)"
if [ -n "$HOST" ]; then
    CODE="$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $HOST" -H 'X-Forwarded-Proto: https' http://127.0.0.1:8091/healthz/ || true)"
    if [ "$CODE" = "200" ]; then echo "OK: the site answers (200)."; else echo "WARNING: the health check gave '$CODE'. Look at:  journalctl -u $SERVICE -n 50"; fi
else
    echo "Skipped the health check (SHOP_BASE_DOMAIN is not set in .env)."
fi
echo; echo "Done. Now running version: $(git rev-parse --short HEAD)"
