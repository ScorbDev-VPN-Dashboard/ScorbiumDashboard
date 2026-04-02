clear && docker compose down
docker builder prune -f
sleep 5
docker compose build --no-cache app && docker compose up -d db app && docker compose exec app uv run alembic upgrade head


#Пересобрать и перезапустить: docker compose -f docker-compose.prod.yml up -d --build

#Применить миграцию БД (добавляет колонку language в таблицу users): docker compose -f docker-compose.prod.yml exec app alembic upgrade head


