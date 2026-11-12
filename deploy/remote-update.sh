#!/usr/bin/env bash
# Pull main and restart the API and UI. Called by GitHub Actions over SSH.
set -euo pipefail

cd /opt/enterprise-rag
export GIT_SSH_COMMAND="ssh -i /home/ubuntu/.ssh/github_deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes"
git fetch origin main
git reset --hard origin/main
./venv/bin/pip install --no-cache-dir -r requirements-prod.txt
sudo systemctl restart enterprise-rag-api enterprise-rag-ui

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
