#!/usr/bin/env bash
# Pull main and restart API, chat UI, evals UI, and reload nginx.
# Called by GitHub Actions over SSM.
set -euo pipefail

cd /opt/enterprise-rag
export GIT_SSH_COMMAND="ssh -i /home/ubuntu/.ssh/github_deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes"
git fetch origin main
git reset --hard origin/main
./venv/bin/pip install --no-cache-dir -r requirements-prod.txt

sudo cp deploy/systemd/enterprise-rag-api.service /etc/systemd/system/
sudo cp deploy/systemd/enterprise-rag-ui.service /etc/systemd/system/
sudo cp deploy/systemd/enterprise-rag-evals.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable enterprise-rag-api enterprise-rag-ui enterprise-rag-evals
sudo systemctl restart enterprise-rag-api enterprise-rag-ui enterprise-rag-evals

if [[ -f deploy/nginx/enterprise-rag.conf ]]; then
  if ! command -v nginx >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y nginx
  fi
  sudo cp deploy/nginx/enterprise-rag.conf /etc/nginx/sites-available/enterprise-rag
  sudo ln -sf /etc/nginx/sites-available/enterprise-rag /etc/nginx/sites-enabled/enterprise-rag
  sudo rm -f /etc/nginx/sites-enabled/default
  sudo nginx -t
  sudo systemctl enable nginx
  sudo systemctl restart nginx
fi

for _ in $(seq 1 36); do
  if curl --fail --silent http://127.0.0.1:8000/health \
    && curl --fail --silent -o /dev/null http://127.0.0.1/api/health; then
    echo
    exit 0
  fi
  sleep 5
done

sudo systemctl status enterprise-rag-api nginx --no-pager || true
sudo journalctl -u enterprise-rag-api -u nginx -n 80 --no-pager || true
exit 1
