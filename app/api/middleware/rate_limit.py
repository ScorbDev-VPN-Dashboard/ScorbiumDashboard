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
LOGIN_WINDOW = 300    # 5 минут
LOGIN_MAX_ATTEMPTS = 10  # макс 10 попыток логина за 5 мин
BLOCK_DURATION = 300  # 5 минут блокировки


_WHITELIST_PREFIXES = ("/docs", "/redoc", "/openapi")


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app) -> None:
        super().__init__(app)
        self._panel_hits: dict[str, deque] = defaultdict(deque)
        self._api_hits: dict[str, deque] = defaultdict(deque)
        self._login_hits: dict[str, deque] = defaultdict(deque)
        self._blocked: dict[str, float] = {}

    def _get_ip(self, request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _check(self, hits: deque, window: int, max_req: int, ip: str) -> bool:
        now = time.monotonic()
        cutoff = now - window
        while hits and hits[0] < cutoff:
            hits.popleft()
        hits.append(now)
        if len(hits) > max_req:
            self._blocked[ip] = now + BLOCK_DURATION
            return True
        return False

    def _is_rate_limited(self, ip: str, path: str, method: str) -> bool:
        now = time.monotonic()
        if ip in self._blocked:
            if now < self._blocked[ip]:
                return True
            del self._blocked[ip]

        # Strict limit on login endpoint
        if path in ("/panel/api/login", "/panel/login") and method == "POST":
            return self._check(self._login_hits[ip], LOGIN_WINDOW, LOGIN_MAX_ATTEMPTS, ip)

        is_api = path.startswith("/api/")
        if is_api:
            return self._check(self._api_hits[ip], API_WINDOW, API_MAX_REQUESTS, ip)
        return self._check(self._panel_hits[ip], PANEL_WINDOW, PANEL_MAX_REQUESTS, ip)

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if any(path.startswith(p) for p in _WHITELIST_PREFIXES):
            return await call_next(request)

        ip = self._get_ip(request)
        if self._is_rate_limited(ip, path, request.method):
            if path.startswith("/api/") or request.headers.get("accept","").startswith("application/json"):
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
