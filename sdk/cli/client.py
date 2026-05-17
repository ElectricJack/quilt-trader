"""HTTP client wrapper for CLI commands.

Maps HTTP outcomes to CLI exit codes via the CLIError exception:
- ConnectError / Timeout → CLIError(code=3, "coordinator unreachable at <url>")
- 4xx → CLIError(code=2, <detail>)
- 5xx → CLIError(code=4, <detail>)
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class CLIError(Exception):
    def __init__(self, code: int, message: str,
                 error_code: Optional[str] = None,
                 status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.code = code
        self.error_code = error_code
        self.status_code = status_code


class CoordinatorClient:
    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._http.aclose()

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    async def _check(self, resp: httpx.Response) -> dict:
        if 200 <= resp.status_code < 300:
            if resp.content and resp.headers.get("content-type", "").startswith("application/json"):
                return resp.json()
            return {}
        try:
            body = resp.json()
            detail = body.get("detail") if isinstance(body, dict) else str(body)
        except Exception:
            detail = resp.text or f"HTTP {resp.status_code}"
        if resp.status_code in (400, 401, 403, 404, 409, 422):
            raise CLIError(code=2, message=str(detail), status_code=resp.status_code)
        if 500 <= resp.status_code < 600:
            raise CLIError(code=4, message=str(detail), status_code=resp.status_code)
        raise CLIError(code=1, message=f"HTTP {resp.status_code}: {detail}",
                       status_code=resp.status_code)

    async def get(self, path: str, params: Optional[dict] = None) -> dict:
        try:
            resp = await self._http.get(self._url(path), params=params)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise CLIError(code=3, message=f"coordinator unreachable at {self.base_url}",
                           error_code="COORD_UNREACHABLE")
        return await self._check(resp)

    async def post(self, path: str, json: Optional[Any] = None,
                   params: Optional[dict] = None) -> dict:
        try:
            resp = await self._http.post(self._url(path), json=json, params=params)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise CLIError(code=3, message=f"coordinator unreachable at {self.base_url}",
                           error_code="COORD_UNREACHABLE")
        return await self._check(resp)

    async def patch(self, path: str, json: Optional[Any] = None) -> dict:
        try:
            resp = await self._http.patch(self._url(path), json=json)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise CLIError(code=3, message=f"coordinator unreachable at {self.base_url}",
                           error_code="COORD_UNREACHABLE")
        return await self._check(resp)

    async def delete(self, path: str) -> dict:
        try:
            resp = await self._http.delete(self._url(path))
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise CLIError(code=3, message=f"coordinator unreachable at {self.base_url}",
                           error_code="COORD_UNREACHABLE")
        return await self._check(resp)
