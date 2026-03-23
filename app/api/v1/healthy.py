from fastapi import APIRouter, HTTPException
from app.core.config import config
router = APIRouter()

@router.get("/healthy")
async def healthy():
    return {"status": "ok"}

@router.get("/config")
async def get_config():
    return {
        "config": "value",
        "telegram_config": {
            "bot_token": config.telegram.telegram_bot_token.get_secret_value(),
            "admin_ids": config.telegram.telegram_admin_ids,
            "type_protocol": config.telegram.telegram_type_protocol,
        }
    }