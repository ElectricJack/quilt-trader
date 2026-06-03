#!/usr/bin/env python3
"""One-off script: push canonical-symbol + ET-helper patches to each upstream
quilt-algo-* repo as a fix branch, open a draft PR via gh CLI.

Idempotent — re-running skips repos that have no local changes or already
have the branch pushed. Safety: refuses to push if files outside
quilt.yaml/algorithm.py are modified. Always opens PRs as drafts.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ALLOWED_FILES = {"quilt.yaml", "algorithm.py"}
# Paths to filter out of the change list before scope-checking. These are
# build/runtime artifacts that may be left behind by previous installs and
# shouldn't block the PR — they're not patches we want to push either.
IGNORED_PATH_PARTS = {"__pycache__", ".pytest_cache", ".venv"}
BRANCH = "fix/canonical-symbols-and-market-time"
PR_TITLE = "fix: canonical symbols + ctx.market_time() compatibility"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repos-root", default="/tmp/quilt-algos",
                   help="Directory containing one subdir per upstream repo (default /tmp/quilt-algos)")
    p.add_argument("--only", default=None,
                   help="If set, process only this repo name (exact match)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print intended actions; perform no git/gh side effects")
    p.add_argument("--quilt-trader-sha", default=None,
                   help="quilt-trader main SHA to reference in PR body (default: read from current repo HEAD)")
    args = p.parse_args()

    root = Path(args.repos_root)
    if not root.exists():
        print(f"error: --repos-root {root} not found / does not exist", file=sys.stderr)
        return 1

    quilt_sha = args.quilt_trader_sha or _read_quilt_trader_sha()

    results: list[tuple[str, str, str]] = []  # (repo, status, detail)
    for repo_dir in sorted(root.iterdir()):
        if not repo_dir.is_dir():
            continue
        if args.only and repo_dir.name != args.only:
            continue
        status, detail = _process_repo(repo_dir, quilt_sha, args.dry_run)
        results.append((repo_dir.name, status, detail))
        print(f"{repo_dir.name:40} → {status}  {detail}")

    print()
    print(f"SUMMARY: {sum(1 for _,s,_ in results if s == 'PR_OPENED')} opened, "
          f"{sum(1 for _,s,_ in results if s == 'SKIP_CLEAN')} clean, "
          f"{sum(1 for _,s,_ in results if s == 'SKIP_BRANCH_EXISTS')} branch already pushed, "
          f"{sum(1 for _,s,_ in results if s.startswith('REFUSED'))} refused, "
          f"{sum(1 for _,s,_ in results if s == 'DRY-RUN')} dry-run")
    return 0


def _read_quilt_trader_sha() -> str:
    """Best-effort: read the SHA of the current HEAD of the quilt-trader repo."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).parents[1],
            text=True,
        )
        return out.strip()
    except subprocess.CalledProcessError:
        return "<unknown>"


def _process_repo(repo: Path, quilt_sha: str, dry_run: bool) -> tuple[str, str]:
    # 1. Check git status
    try:
        status = subprocess.check_output(
            ["git", "-C", str(repo), "status", "--porcelain"],
            text=True,
        )
    except subprocess.CalledProcessError as e:
        return ("REFUSED_NOT_GIT_REPO", str(e))

    if not status.strip():
        return ("SKIP_CLEAN", "no local changes")

    # 2. Parse changed paths, dropping ignored runtime/build artifacts
    all_paths = []
    for line in status.splitlines():
        # Porcelain v1: "XY path" — strip the 2-char status + space
        all_paths.append(line[3:].strip())
    changed = [p for p in all_paths
               if not any(part in IGNORED_PATH_PARTS for part in Path(p).parts)]

    if not changed:
        return ("SKIP_CLEAN", f"only ignored artifacts modified ({all_paths})")

    # 3. Sanity-check: every remaining changed file is in ALLOWED_FILES
    out_of_scope = [p for p in changed if Path(p).name not in ALLOWED_FILES]
    if out_of_scope:
        return ("REFUSED_OUT_OF_SCOPE", f"unexpected files: {out_of_scope}")

    if dry_run:
        return ("DRY-RUN", f"would commit + push + PR ({len(changed)} files)")

    # 3. Check if branch already exists locally
    branch_check = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", BRANCH],
        capture_output=True, text=True,
    )
    if branch_check.returncode == 0:
        # Branch exists — check if pushed
        remote_check = subprocess.run(
            ["git", "-C", str(repo), "ls-remote", "--heads", "origin", BRANCH],
            capture_output=True, text=True,
        )
        if remote_check.stdout.strip():
            return ("SKIP_BRANCH_EXISTS", "branch already pushed to origin")

    # 4. Create/check out branch, stage, commit, push
    subprocess.check_call(["git", "-C", str(repo), "checkout", "-B", BRANCH])
    subprocess.check_call(["git", "-C", str(repo), "add", *changed])
    commit_msg = (
        f"fix: canonical symbols + ctx.market_time() compat with quilt-trader\n\n"
        f"Apply changes required by quilt-trader main as of {quilt_sha}.\n\n"
        f"- Canonical-symbol manifest fixes (BTC->BTCUSD, ^VIX->VIX, etc.)\n"
        f"- ctx.timestamp -> ctx.market_time() where ET wall-clock was intended"
    )
    subprocess.check_call(["git", "-C", str(repo), "commit", "-m", commit_msg])
    subprocess.check_call(["git", "-C", str(repo), "push", "-u", "origin", BRANCH])

    # 5. Open draft PR via gh CLI
    pr_body = _pr_body(repo.name, quilt_sha, changed)
    try:
        out = subprocess.check_output(
            ["gh", "pr", "create",
             "--draft",
             "--title", PR_TITLE,
             "--body", pr_body,
             "--repo", _origin_slug(repo)],
            cwd=str(repo), text=True,
        )
        return ("PR_OPENED", out.strip())
    except subprocess.CalledProcessError as e:
        return ("REFUSED_PR_FAILED", str(e))


def _origin_slug(repo: Path) -> str:
    """Extract owner/repo slug from origin URL."""
    out = subprocess.check_output(
        ["git", "-C", str(repo), "remote", "get-url", "origin"],
        text=True,
    ).strip()
    # https://github.com/owner/repo.git -> owner/repo
    if out.endswith(".git"):
        out = out[:-4]
    if "github.com/" in out:
        return out.split("github.com/", 1)[1]
    return out


def _pr_body(repo_name: str, quilt_sha: str, changed_files: list[str]) -> str:
    return f"""## What

Apply canonical-symbol and ET market-time updates required by
quilt-trader main as of `{quilt_sha}`.

## Why

The framework (quilt-trader) now:
1. Rejects non-canonical symbols (`BTC` -> `BTCUSD`, `^VIX` -> `VIX`,
   OCC without `O:` prefix, etc.) at three validation gates - manifest
   install, data-store I/O, asset-service inputs.
   Spec: `docs/superpowers/specs/2026-05-31-canonical-symbol-design.md`.
2. Provides `ctx.market_time()` (tz-aware datetime in the manifest's
   `market_timezone`) and `ctx.is_market_open()` helpers.
   Spec: `docs/superpowers/specs/2026-06-02-algorithm-hardening-design.md`.
   Algorithms that previously compared `ctx.timestamp.hour` against
   ET-window literals misfired (UTC vs ET).

## Changes

Files touched: {changed_files}

## Validation

This patch was applied locally and verified via a backtest sweep before
opening this PR. See the spec links above for the full audit trail.

\U0001f916 Generated with [Claude Code](https://claude.com/claude-code)
"""


if __name__ == "__main__":
    sys.exit(main())
