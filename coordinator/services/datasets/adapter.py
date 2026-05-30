from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Awaitable, Callable, Any
from coordinator.services.datasets.registry import DatasetSpec


PageCallback   = Callable[[int, int], Awaitable[None]]                  # (page_idx, cumulative_rows)
StatusCallback = Callable[[str], Awaitable[None]]
RowsCallback   = Callable[[list[dict], int], Awaitable[None]]           # (rows, page_idx)


class AdapterAuthError(Exception):
    """Raised when an adapter's credentials are rejected (e.g. HTTP 401)."""


class DatasetAdapter(ABC):
    provider: str

    @abstractmethod
    async def fetch_dataset(
        self,
        spec: DatasetSpec,
        params: dict,
        *,
        on_page: PageCallback | None = None,
        on_status: StatusCallback | None = None,
        on_rows: RowsCallback | None = None,
    ) -> list[dict]:
        ...
