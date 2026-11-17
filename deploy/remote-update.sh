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
sudo systemctl restart enterprise-rag-api enterprise-rag-ui enterprise-rag-evals

if [[ -f deploy/nginx/enterprise-rag.conf ]]; then
  sudo cp deploy/nginx/enterprise-rag.conf /etc/nginx/sites-available/enterprise-rag
  sudo ln -sf /etc/nginx/sites-available/enterprise-rag /etc/nginx/sites-enabled/enterprise-rag
  sudo rm -f /etc/nginx/sites-enabled/default
  sudo nginx -t
  sudo systemctl reload nginx
fi

for _ in $(seq 1 36); do
  if curl --fail --silent http://127.0.0.1:8000/health; then
    echo
    exit 0
  fi
  sleep 5
done

sudo systemctl status enterprise-rag-api --no-pager || true
sudo journalctl -u enterprise-rag-api -n 80 --no-pager || true
exit 1
