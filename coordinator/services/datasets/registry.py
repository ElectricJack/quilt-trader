from dataclasses import dataclass, field
from enum import StrEnum
from datetime import timedelta


class Pagination(StrEnum):
    SINGLE = "single"
    PAGE = "page"
    DATE_RANGE = "date_range"


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    provider: str
    endpoint_path: str
    event_date_column: str
    knowledge_date_column: str | None
    symbol_keyed: bool
    id_columns: tuple[str, ...]
    columns: dict[str, str] = field(default_factory=dict)
    pagination: Pagination = Pagination.PAGE
    page_size: int = 100
    date_chunk_days: int = 365
    knowledge_date_lag: timedelta = timedelta(0)
    free_tier: bool = True


_REGISTRY: dict[str, DatasetSpec] = {}


def register(spec: DatasetSpec) -> None:
    if spec.name in _REGISTRY:
        raise ValueError(f"duplicate dataset: {spec.name}")
    _REGISTRY[spec.name] = spec


def get(name: str) -> DatasetSpec:
    return _REGISTRY[name]


def list_all() -> list[DatasetSpec]:
    return list(_REGISTRY.values())


def clear_registry() -> None:
    """Test helper. Do not call from production code."""
    _REGISTRY.clear()
