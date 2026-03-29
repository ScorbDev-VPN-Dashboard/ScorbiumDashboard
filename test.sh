clear && docker compose down
docker builder prune -f
sleep 5
docker compose build --no-cache app && docker compose up -d db app && docker compose exec app uv run alembic upgrade head