
#!/usr/bin/env bash
#
# setup.sh -- Deploy asymptote-wiki on asymptote.ualberta.ca
#
# Run as root (or with sudo). Assumes:
#   - An 'asymptote' system user exists (from AWA / http-server setup)
#   - asy is installed (find its path with: which asy)
#   - nginx is running with a server block for asymptote.ualberta.ca
#
set -euo pipefail

APP_DIR=/opt/asywiki
DATA_DIR=/var/lib/asywiki
SERVICE_USER=asymptote
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Detect asy location
ASY_PATH=$(which asy 2>/dev/null || echo "/usr/local/bin/asy")
echo "Using asy: $ASY_PATH"

echo "=== asymptote-wiki setup ==="

# 1. Create directories
echo "[1/6] Creating directories..."
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$APP_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$DATA_DIR"

# 2. Copy application files
echo "[2/6] Copying application files to $APP_DIR..."
rsync -a --exclude='venv' --exclude='__pycache__' --exclude='data' \
    --exclude='.git' "$REPO_DIR/" "$APP_DIR/"

# 3. Create virtualenv and install dependencies
echo "[3/6] Setting up Python virtualenv..."
sudo -u "$SERVICE_USER" bash -c "
    cd $APP_DIR
    python3 -m venv venv
    venv/bin/pip install --quiet --upgrade pip
    venv/bin/pip install --quiet -r requirements.txt
"

# 4. Install systemd service (override ASY_BINARY if not default)
echo "[4/6] Installing systemd service..."
cp "$REPO_DIR/deploy/asywiki.service" /etc/systemd/system/asywiki.service
if [ "$ASY_PATH" != "/usr/local/bin/asy" ]; then
    sed -i "s|Environment=ASY_BINARY=.*|Environment=ASY_BINARY=$ASY_PATH|" \
        /etc/systemd/system/asywiki.service
    echo "  (set ASY_BINARY=$ASY_PATH)"
fi
systemctl daemon-reload
systemctl enable asywiki.service
systemctl restart asywiki.service
sleep 2
systemctl status asywiki.service --no-pager

# 5. nginx configuration
echo "[5/6] nginx configuration..."
echo ""
echo "  ADD THIS to your nginx server block for asymptote.ualberta.ca:"
echo ""
echo "    location /wiki/ {"
echo "        proxy_pass http://127.0.0.1:8080/;"
echo "        proxy_set_header Host \$host;"
echo "        proxy_set_header X-Real-IP \$remote_addr;"
echo "        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;"
echo "        proxy_set_header X-Forwarded-Proto \$scheme;"
echo "        client_max_body_size 5m;"
echo "        proxy_read_timeout 60s;"
echo "    }"
echo ""
echo "  Then run:  nginx -t && systemctl reload nginx"
echo ""

# 6. Verify
echo "[6/6] Verifying..."
sleep 1
if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/ | grep -q "200"; then
    echo "  OK: Wiki is responding on 127.0.0.1:8080"
else
    echo "  WARNING: Wiki did not respond. Check: journalctl -u asywiki -n 20"
fi

echo ""
echo "=== Done ==="
echo "  Wiki URL (after nginx reload): https://asymptote.ualberta.ca/wiki/"
echo "  Data directory: $DATA_DIR"
echo "  Logs: journalctl -u asywiki -f"
echo ""
echo "  Useful commands:"
echo "    systemctl status asywiki"
echo "    systemctl restart asywiki"
echo "    journalctl -u asywiki -n 50"

