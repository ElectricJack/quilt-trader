from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from zoneinfo import available_timezones

import yaml

TRIGGER_REGEX = re.compile(r"^(bar:[a-z0-9]+|event|interval:\d+[smh])$")

# Canonical asset_type values. Mirrors coordinator AssetType enum — kept
# inline since the SDK can't import the coordinator.
_VALID_ASSET_TYPES = frozenset({"equities", "options", "crypto", "index"})


class ManifestError(Exception):
    pass


def _default_market_timezone(asset_types: list[str]) -> str:
    """Return the most-restrictive default market timezone for a set of asset types.

    - Equities or options (alone or mixed with crypto) → America/New_York
    - Crypto only → UTC
    - Other / unknown → UTC fallback
    """
    types = set(asset_types or [])
    if types & {"equities", "options"}:
        return "America/New_York"
    if types == {"crypto"}:
        return "UTC"
    return "UTC"


def _validate_asset_type_list(values: list[str], field_name: str) -> list[str]:
    bad = [v for v in values if v not in _VALID_ASSET_TYPES]
    if bad:
        raise ManifestError(
            f"invalid asset_type values in {field_name}: {bad}; "
            f"must be one of {sorted(_VALID_ASSET_TYPES)}"
        )
    return values


@dataclass
class ManifestRequirements:
    asset_types: list[str] = field(default_factory=list)
    options_level: Optional[int] = None
    account_features: list[str] = field(default_factory=list)
    brokers: Optional[list[str]] = None
    data_dependencies: list[dict] = field(default_factory=list)


@dataclass
class QuiltManifest:
    name: str
    type: str
    version: str
    description: str = ""
    entry_point: str = ""
    class_name: str = ""
    requirements: ManifestRequirements = field(default_factory=ManifestRequirements)
    # Top-level `assets:` block. Each entry is {symbol, asset_class}. The
    # deployment's account decides which broker handles routing; `broker:`
    # in a manifest is parsed for back-compat but silently stripped.
    assets: list[dict] = field(default_factory=list)
    config_parameters: list[dict] = field(default_factory=list)
    custom_events: list[dict] = field(default_factory=list)
    schedule: str = ""
    jitter_seconds: Optional[int] = None
    trigger: str = "bar:1min"
    data: list[dict] = field(default_factory=list)
    market_timezone: str = "UTC"

    @staticmethod
    def from_file(path: Path) -> QuiltManifest:
        if not path.exists():
            raise FileNotFoundError(f"Manifest not found: {path}")
        with open(path) as f:
            data = yaml.safe_load(f)
        return QuiltManifest._parse(data)

    @staticmethod
    def from_string(yaml_str: str) -> QuiltManifest:
        data = yaml.safe_load(yaml_str)
        return QuiltManifest._parse(data)

    @staticmethod
    def _parse(data: dict) -> QuiltManifest:
        if not data.get("name"):
            raise ManifestError("Manifest must have a 'name' field")

        pkg_type = data.get("type", "")
        if pkg_type not in ("algorithm", "scraper"):
            raise ManifestError(f"Manifest 'type' must be 'algorithm' or 'scraper', got '{pkg_type}'")

        if pkg_type == "algorithm":
            if not data.get("entry_point"):
                raise ManifestError("Algorithm manifest must have an 'entry_point' field")
            if not data.get("class_name"):
                raise ManifestError("Algorithm manifest must have a 'class_name' field")
            reqs_data = data.get("requirements", {})
            if not reqs_data.get("asset_types"):
                raise ManifestError("Algorithm manifest must specify requirements.asset_types")

        if pkg_type == "scraper":
            if not data.get("schedule"):
                raise ManifestError("Scraper manifest must have a 'schedule' field")

        reqs_data = data.get("requirements", {})
        asset_types = _validate_asset_type_list(
            reqs_data.get("asset_types", []), "requirements.asset_types",
        )
        requirements = ManifestRequirements(
            asset_types=asset_types,
            options_level=reqs_data.get("options_level"),
            account_features=reqs_data.get("account_features", []),
            brokers=reqs_data.get("brokers"),
            data_dependencies=reqs_data.get("data_dependencies", []),
        )

        config_data = data.get("config", {})
        config_parameters = config_data.get("parameters", [])

        notifications_data = data.get("notifications", {})
        custom_events = notifications_data.get("custom_events", [])

        jitter_raw = data.get("jitter_seconds")
        jitter_seconds: Optional[int] = None
        if jitter_raw is not None:
            try:
                jitter_seconds = int(jitter_raw)
            except (TypeError, ValueError):
                raise ManifestError(
                    f"jitter_seconds must be an integer, got {jitter_raw!r}"
                )
            if jitter_seconds < 0:
                raise ManifestError(
                    f"jitter_seconds must be non-negative, got {jitter_seconds}"
                )

        trigger = data.get("trigger", "bar:1min")
        if not TRIGGER_REGEX.match(trigger):
            raise ManifestError(
                f"trigger must match {TRIGGER_REGEX.pattern!r}, got {trigger!r}"
            )

        # Validate history_bars on each data_dependency entry
        for dep in (reqs_data.get("data_dependencies") or []):
            if not isinstance(dep, dict):
                continue
            hb = dep.get("history_bars")
            if hb is None:
                continue
            if not isinstance(hb, int) or hb <= 0:
                raise ManifestError(
                    f"data_dependencies entry history_bars must be a positive integer, got {hb!r}"
                )

        # Parse top-level `assets:` block. Only symbol + asset_class are kept;
        # broker (and any other legacy fields) are stripped — the deployment's
        # account decides routing. Entries without a symbol are dropped silently.
        raw_assets = data.get("assets") or []
        assets: list[dict] = []
        if isinstance(raw_assets, list):
            # Lazy import to avoid sdk → coordinator dependency at module load
            from coordinator.services.asset_services.registry import get_default_registry
            registry = get_default_registry()
            for a in raw_assets:
                if not isinstance(a, dict):
                    continue
                symbol = a.get("symbol")
                if not symbol:
                    continue
                asset_class = a.get("asset_class", "equities")
                if asset_class not in _VALID_ASSET_TYPES:
                    raise ManifestError(
                        f"invalid asset_class {asset_class!r} for symbol {symbol!r}; "
                        f"must be one of {sorted(_VALID_ASSET_TYPES)}"
                    )

                # Gate 1: symbol must be canonical for SOME asset class
                try:
                    registry.validate(symbol)
                except ValueError as e:
                    raise ManifestError(f"asset {symbol!r}: {e}")

                # Gate 2: symbol's natural classification must match declared asset_class
                inferred = registry.classify(symbol).value
                if inferred != asset_class:
                    raise ManifestError(
                        f"asset {symbol!r} is declared as asset_class={asset_class!r} "
                        f"but its canonical form classifies as {inferred!r}. "
                        f"Either fix the symbol or change asset_class."
                    )

                entry = {
                    "symbol": symbol,
                    "asset_class": asset_class,
                }
                if a.get("timeframe"):
                    entry["timeframe"] = a["timeframe"]
                if a.get("source"):
                    entry["source"] = a["source"]
                assets.append(entry)

        raw_data = data.get("data") or []
        data_deps: list[dict] = []
        valid_data_types = {"scraper", "csv", "json", "parquet"}
        if isinstance(raw_data, list):
            for d in raw_data:
                if not isinstance(d, dict):
                    continue
                source = d.get("source")
                if not source:
                    continue
                dtype = d.get("type", "csv")
                if dtype not in valid_data_types:
                    raise ManifestError(
                        f"data entry type must be one of {valid_data_types}, got {dtype!r}"
                    )
                data_deps.append({"source": source, "type": dtype})

        # Parse market_timezone — explicit field with smart default per asset_types
        explicit_tz = data.get("market_timezone")
        if explicit_tz is not None:
            if not isinstance(explicit_tz, str) or explicit_tz not in available_timezones():
                raise ManifestError(
                    f"invalid market_timezone {explicit_tz!r}; "
                    f"must be a valid IANA timezone name (e.g. America/New_York)"
                )
            market_timezone = explicit_tz
        else:
            market_timezone = _default_market_timezone(asset_types)

        return QuiltManifest(
            name=data["name"],
            type=data["type"],
            version=data.get("version", "0.0.0"),
            description=data.get("description", ""),
            entry_point=data.get("entry_point", ""),
            class_name=data.get("class_name", ""),
            requirements=requirements,
            assets=assets,
            config_parameters=config_parameters,
            custom_events=custom_events,
            schedule=data.get("schedule", ""),
            jitter_seconds=jitter_seconds,
            trigger=trigger,
            data=data_deps,
            market_timezone=market_timezone,
        )
