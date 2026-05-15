#!/bin/bash
cd /opt/border
git pull --rebase origin main 2>&1
docker compose build --no-cache 2>&1 | tail -5
docker compose up -d 2>&1
