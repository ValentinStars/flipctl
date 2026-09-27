#!/bin/bash
# ==============================================================================
# One-Click Setup Script for FlipCTL on Raspberry Pi Zero W
# Waveshare 1.44inch LCD HAT (ST7735 128x128)
# Zero Compilation Required: Pure Python + Native Hardware Acceleration
# ==============================================================================
set -e

echo "=== [1/4] Checking environment ==="
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (e.g. sudo bash install.sh)"
  exit 1
fi

echo "=== [2/4] Installing dependencies (No compilation needed) ==="
apt-get update
apt-get install -y --no-install-recommends \
  python3 \
  python3-pip \
  python3-pil \
  python3-spidev \
  python3-rpi.gpio \
  raspi-config

echo "=== [3/4] Enabling SPI hardware interface ==="
# Enable SPI using raspi-config non-interactively
raspi-config nonint do_spi 0 || true

# Add spi-bcm2835 to /etc/modules if not present
if ! grep -q "spi-bcm2835" /etc/modules 2>/dev/null; then
  echo "spi-bcm2835" >> /etc/modules
fi

echo "=== [4/4] Setting up systemd service ==="
SERVICE_DIR="/etc/systemd/system"
SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/waveshare_st7735.py"
chmod +x "$SCRIPT_PATH"

cat << EOF > "$SERVICE_DIR/flipctl-st7735.service"
[Unit]
Description=FlipCTL Waveshare 1.44inch LCD HAT Driver
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$(cd "$(dirname "$0")" && pwd)
ExecStart=/usr/bin/python3 $SCRIPT_PATH --host 127.0.0.1 --port 8899 --fps 20
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable flipctl-st7735.service

echo ""
echo "=============================================================================="
echo " Setup complete! Zero compilation performed."
echo " To start the display driver right now:"
echo "   sudo systemctl start flipctl-st7735"
echo ""
echo " Note: If your FlipCTL daemon runs on another machine (e.g. your laptop),"
echo " edit /etc/systemd/system/flipctl-st7735.service and change '--host 127.0.0.1'"
echo " to your laptop's WiFi IP address (e.g. '--host 192.168.1.50')!"
echo "=============================================================================="
