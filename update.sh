#!/usr/bin/env bash
# Обновление проекта на сервере
set -euo pipefail

GREEN='\033[0;32m'; CYAN='\033[0;36m'; RESET='\033[0m'

echo -e "${CYAN}[1/3] Получаю обновления из git...${RESET}"
git pull

echo -e "${CYAN}[2/3] Пересобираю и запускаю контейнеры...${RESET}"
docker compose -f docker-compose.prod.yml up -d --build --force-recreate

echo -e "${CYAN}[3/3] Применяю миграции БД...${RESET}"
sleep 5
docker compose -f docker-compose.prod.yml exec app uv run alembic upgrade head

echo -e "${GREEN}✅ Готово!${RESET}"
