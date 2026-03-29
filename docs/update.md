
# Обновление

## Обновление до новой версии

```bash
cd /opt/vpn-dashboard

# 1. Получить обновления
git pull origin main

# 2. Пересобрать образ
docker compose -f docker-compose.prod.yml build app

# 3. Перезапустить приложение
docker compose -f docker-compose.prod.yml up -d app

# 4. Применить новые миграции (если есть)
docker compose -f docker-compose.prod.yml exec app uv run alembic upgrade head
```

> ⚠️ `.env` не перезаписывается — ваши настройки сохранятся.

## Проверка после обновления

```bash
# Статус контейнеров
docker compose -f docker-compose.prod.yml ps

# Логи
docker compose -f docker-compose.prod.yml logs app --tail=30
```

## Откат к предыдущей версии

```bash
# Посмотреть историю коммитов
git log --oneline -10

# Откатиться к конкретному коммиту
git checkout <commit_hash>

# Пересобрать
docker compose -f docker-compose.prod.yml build app
docker compose -f docker-compose.prod.yml up -d app
```

## Полезные команды

```bash
# Перезапустить только nginx
docker compose -f docker-compose.prod.yml restart nginx

# Перезапустить всё
docker compose -f docker-compose.prod.yml restart

# Остановить всё
docker compose -f docker-compose.prod.yml down

# Посмотреть логи в реальном времени
docker compose -f docker-compose.prod.yml logs -f app

# Войти в контейнер
docker compose -f docker-compose.prod.yml exec app bash

# Сбросить БД (ОСТОРОЖНО!)
docker compose -f docker-compose.prod.yml down -v
docker compose -f docker-compose.prod.yml up -d db app
docker compose -f docker-compose.prod.yml exec app uv run alembic upgrade head
```
