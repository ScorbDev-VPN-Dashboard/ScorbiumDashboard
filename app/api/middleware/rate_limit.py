"""
Simple in-memory rate limiter for the FastAPI panel.
Limits requests per IP using a sliding window counter.
No external dependencies needed.
"""
import time
from collections import defaultdict, deque
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# ── Config ────────────────────────────────────────────────────────────────────
PANEL_WINDOW = 60     
PANEL_MAX_REQUESTS = 120 
API_WINDOW = 60
API_MAX_REQUESTS = 60 
BLOCK_DURATION = 120


_WHITELIST_PREFIXES = ("/docs", "/redoc", "/openapi")


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app) -> None:
        super().__init__(app)

        self._panel_hits: dict[str, deque] = defaultdict(deque)
        self._api_hits: dict[str, deque] = defaultdict(deque)
        self._blocked: dict[str, float] = {}  # ip -> unblock_time

    def _get_ip(self, request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _is_rate_limited(self, ip: str, path: str) -> bool:
        now = time.monotonic()

        if ip in self._blocked:
            if now < self._blocked[ip]:
                return True
            else:
                del self._blocked[ip]

        is_api = path.startswith("/api/")
        window = API_WINDOW if is_api else PANEL_WINDOW
        max_req = API_MAX_REQUESTS if is_api else PANEL_MAX_REQUESTS
        hits = self._api_hits[ip] if is_api else self._panel_hits[ip]

        # Slide window
        cutoff = now - window
        while hits and hits[0] < cutoff:
            hits.popleft()

        hits.append(now)

        if len(hits) > max_req:
            self._blocked[ip] = now + BLOCK_DURATION
            return True

        return False

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        if any(path.startswith(p) for p in _WHITELIST_PREFIXES):
            return await call_next(request)

        ip = self._get_ip(request)

        if self._is_rate_limited(ip, path):
            if path.startswith("/api/"):
                return JSONResponse(
                    {"detail": "Too many requests. Try again later."},
                    status_code=429,
                    headers={"Retry-After": str(BLOCK_DURATION)},
                )
            return Response(
                content=(
                    "<html><body style='background:#080812;color:#ef4444;"
                    "font-family:sans-serif;text-align:center;padding:4rem'>"
                    "<h2>⏳ Too Many Requests</h2>"
                    "<p>Подождите немного и попробуйте снова.</p>"
                    "</body></html>"
                ),
                status_code=429,
                media_type="text/html",
                headers={"Retry-After": str(BLOCK_DURATION)},
            )

        return await call_next(request)
