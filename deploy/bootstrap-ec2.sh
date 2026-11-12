#!/usr/bin/env bash
# One-time setup for a fresh Ubuntu t3.micro. Run on the instance:
#   REPO_URL=https://github.com/<you>/<repo>.git bash deploy/bootstrap-ec2.sh
set -euo pipefail

APP_DIR=/opt/enterprise-rag
REPO_URL="${REPO_URL:?Set REPO_URL to the GitHub clone URL}"

sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip git

if ! swapon --show | grep -q .; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

if [[ ! -d "${APP_DIR}/.git" ]]; then
  sudo mkdir -p "${APP_DIR}"
  sudo chown "$USER:$USER" "${APP_DIR}"
  git clone "${REPO_URL}" "${APP_DIR}"
fi

cd "${APP_DIR}"
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install --no-cache-dir -r requirements-prod.txt

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Write the real values into ${APP_DIR}/.env before starting the services."
fi
chmod 600 .env

sudo cp deploy/systemd/enterprise-rag-api.service /etc/systemd/system/
sudo cp deploy/systemd/enterprise-rag-ui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable enterprise-rag-api enterprise-rag-ui
sudo systemctl restart enterprise-rag-api enterprise-rag-ui
