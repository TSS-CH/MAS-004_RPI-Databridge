#!/usr/bin/env bash
set -e

sudo apt-get update
sudo apt-get install -y python3-venv python3-pip sqlite3

sudo mkdir -p /etc/mas004_rpi_databridge /var/lib/mas004_rpi_databridge

python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e .

if [ ! -f /etc/mas004_rpi_databridge/config.json ]; then
  sudo cp scripts/default_config.json /etc/mas004_rpi_databridge/config.json
fi

sudo cp systemd/mas004-rpi-databridge.service /etc/systemd/system/mas004-rpi-databridge.service
sudo systemctl daemon-reload
sudo systemctl enable --now mas004-rpi-databridge.service

echo "Web UI: http://192.168.1.100:8080"
echo "Logs: journalctl -u mas004-rpi-databridge.service -f"
