#!/usr/bin/env bash
# Pull main, rebuild Docker images, and restart the Compose stack.
# Called by GitHub Actions over SSM.
set -euo pipefail

cd /opt/enterprise-rag
export GIT_SSH_COMMAND="ssh -i /home/ubuntu/.ssh/github_deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes"
git fetch origin main
git reset --hard origin/main

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo systemctl enable --now docker
fi

# Ensure legacy host services are not binding ports 80/8501/8502.
sudo systemctl disable --now enterprise-rag-api enterprise-rag-ui enterprise-rag-evals nginx 2>/dev/null || true

mkdir -p processed_data
chmod 600 .env 2>/dev/null || true

sudo docker compose build
sudo docker compose up -d --remove-orphans
sudo docker image prune -f >/dev/null 2>&1 || true

for _ in $(seq 1 60); do
  if curl --fail --silent http://127.0.0.1/api/health >/dev/null; then
    echo
    sudo docker compose ps
    exit 0
  fi
  sleep 5
done

sudo docker compose ps || true
sudo docker compose logs --tail=80 api nginx || true
exit 1
