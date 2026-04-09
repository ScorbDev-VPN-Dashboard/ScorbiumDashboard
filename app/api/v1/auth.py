from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from app.core.config import config
from app.utils.security import create_access_token

router = APIRouter()


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/login", response_model=TokenResponse, summary="Admin login")
async def login(form: OAuth2PasswordRequestForm = Depends()) -> TokenResponse:
    expected_username = config.web.web_superadmin_username
    expected_password = config.web.web_superadmin_password.get_secret_value()

    if form.username != expected_username or form.password != expected_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(subject=form.username)
    return TokenResponse(access_token=token)
