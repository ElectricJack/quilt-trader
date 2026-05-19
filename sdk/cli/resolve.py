"""Shared name/short-ID → UUID resolution helpers for CLI commands."""
from __future__ import annotations

from sdk.cli.output import fail


def _short_id(uuid_str: str, length: int = 8) -> str:
    return uuid_str[:length] if uuid_str else "—"


async def resolve_id(
    client,
    name_or_id: str,
    endpoint: str,
    name_field: str = "name",
    label: str = "item",
) -> str:
    """Resolve a name, short ID prefix, or full UUID to the actual ID.

    Fetches *endpoint*, then:
    1. Exact case-insensitive match on *name_field*.
    2. Prefix match on "id".
    Raises SystemExit(2) on no-match or ambiguous prefix.
    """
    try:
        items = await client.get(endpoint)
    finally:
        await client.aclose()

    # Exact name match (case-insensitive)
    for item in items:
        if item.get(name_field, "").lower() == name_or_id.lower():
            return item["id"]

    # ID prefix match
    matches = [item for item in items if item["id"].startswith(name_or_id)]
    if len(matches) == 1:
        return matches[0]["id"]
    if len(matches) > 1:
        fail(2, f"Ambiguous ID prefix '{name_or_id}' — matches {len(matches)} {label}s. Use a longer prefix or the {label} name.")
    fail(2, f"No {label} found matching '{name_or_id}'")


async def resolve_deployment_id(client, name_or_id: str) -> str:
    """Resolve a deployment by algorithm name, short ID prefix, or full UUID.

    When resolving by algorithm name:
    - If exactly one match, return it.
    - If multiple, prefer the running one.
    - If multiple are running, error and ask the user to use the ID.
    """
    try:
        deployments = await client.get("/api/deployments")
    finally:
        await client.aclose()

    # Exact name match on algorithm_name (case-insensitive)
    name_matches = [
        d for d in deployments
        if d.get("algorithm_name", "").lower() == name_or_id.lower()
    ]
    if len(name_matches) == 1:
        return name_matches[0]["id"]
    if len(name_matches) > 1:
        running = [d for d in name_matches if d.get("status") == "running"]
        if len(running) == 1:
            return running[0]["id"]
        if len(running) > 1:
            fail(
                2,
                f"Multiple running deployments for algorithm '{name_or_id}'. "
                "Use a deployment ID or short ID prefix to be specific."
            )
        # Multiple matches, none running — take most recent (last in list)
        return name_matches[-1]["id"]

    # ID prefix match
    matches = [d for d in deployments if d["id"].startswith(name_or_id)]
    if len(matches) == 1:
        return matches[0]["id"]
    if len(matches) > 1:
        fail(2, f"Ambiguous ID prefix '{name_or_id}' — matches {len(matches)} deployments. Use a longer prefix.")
    fail(2, f"No deployment found matching '{name_or_id}'")
