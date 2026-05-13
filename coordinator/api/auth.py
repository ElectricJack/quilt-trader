import hashlib
import hmac
import logging
import os
import secrets
from typing import Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

EXEMPT_PATHS = {"/api/health", "/ws/dashboard", "/ws/worker"}
EXEMPT_PREFIXES = ("/docs", "/openapi.json", "/redoc")


def generate_api_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def verify_token(provided: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_token(provided), stored_hash)


class APIAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token_hash: Optional[str] = None) -> None:
        super().__init__(app)
        self._token_hash = token_hash

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if self._token_hash is None:
            return await call_next(request)

        path = request.url.path

        if path in EXEMPT_PATHS:
            return await call_next(request)
        for prefix in EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)
        if not path.startswith("/api/"):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            if verify_token(token, self._token_hash):
                return await call_next(request)

        api_key = request.query_params.get("api_key", "")
        if api_key and verify_token(api_key, self._token_hash):
            return await call_next(request)

        return JSONResponse(status_code=401, content={"detail": "Invalid or missing API token"})
