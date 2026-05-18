from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

TRIGGER_REGEX = re.compile(r"^(bar:[a-z0-9]+|event|interval:\d+[smh])$")


class ManifestError(Exception):
    pass


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
    # Top-level `assets:` block (post-2026-05-18 manifest format). Each entry
    # is {broker, symbol, asset_class}. Replaces the legacy
    # `requirements.data_dependencies` for live-data subscription declaration.
    assets: list[dict] = field(default_factory=list)
    config_parameters: list[dict] = field(default_factory=list)
    custom_events: list[dict] = field(default_factory=list)
    schedule: str = ""
    jitter_seconds: Optional[int] = None
    trigger: str = "bar:1min"

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
        requirements = ManifestRequirements(
            asset_types=reqs_data.get("asset_types", []),
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
            for a in raw_assets:
                if not isinstance(a, dict):
                    continue
                symbol = a.get("symbol")
                if not symbol:
                    continue
                assets.append({
                    "symbol": symbol,
                    "asset_class": a.get("asset_class", "equities"),
                })

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
        )
