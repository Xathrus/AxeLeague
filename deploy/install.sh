#!/usr/bin/env bash
# Install the Abilene Axe League app inside an Ubuntu LXC.
# Usage:
#   ./install.sh https://github.com/YOURUSER/axe-league.git
# or, if you've already copied the project to /opt/axeleague, just:
#   ./install.sh
set -euo pipefail

REPO_URL="${1:-}"
APP_DIR=/opt/axeleague

apt-get update
apt-get install -y python3 python3-venv python3-pip git

if [ -n "$REPO_URL" ]; then
    if [ -d "$APP_DIR/.git" ]; then
        git -C "$APP_DIR" pull
    else
        git clone "$REPO_URL" "$APP_DIR"
    fi
fi

if [ ! -f "$APP_DIR/app.py" ]; then
    echo "ERROR: $APP_DIR/app.py not found. Clone the repo or copy the files there first." >&2
    exit 1
fi

cd "$APP_DIR"
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

mkdir -p "$APP_DIR/data"

cp "$APP_DIR/deploy/axeleague.service" /etc/systemd/system/axeleague.service
systemctl daemon-reload
systemctl enable --now axeleague

echo
echo "Done. The app should now be reachable at: http://$(hostname -I | awk '{print $1}')/"
