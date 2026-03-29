from fastapi import APIRouter

router = APIRouter()


@router.get("/", summary="Health check")
async def healthy() -> dict:
    return {"status": "ok"}
