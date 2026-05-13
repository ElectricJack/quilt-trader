# Alpha Picks Scraper — Quilt Plugin Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `alpha-picks-scraper` as a Quilt scraper plugin, fix three upstream Quilt issues the rewrite surfaces, and install/verify the scraper inside `quilt-trader`.

**Architecture:** Two repos involved. (1) `quilt-trader` gets three small fixes to the `ScraperEngine` and manifest: drop a dead `output.filename` field, fix a `sdk.*` `ModuleNotFoundError` in scraper subprocesses, and add a `config` param to `run_scraper` so callers can pass per-instance configuration. (2) `alpha-picks-scraper` is wiped to its `.git/` dir and reborn as a clean Quilt package: `quilt.yaml` + thin `scraper.py` entry point + `lib/` modules ported verbatim from the old code + focused tests. Verification is a one-off Python runner script that invokes `ScraperEngine` against the installed package and confirms a CSV lands at `data/custom/alpha-picks-scraper.csv`.

**Tech Stack:** Python 3.11+ (quilt-trader) / 3.10+ (scraper), pandas, Playwright (Chromium), BeautifulSoup4, PyYAML, pytest

**Spec:** `docs/superpowers/specs/2026-05-13-alpha-picks-scraper-quilt-rewrite-design.md`

---

## File Map

### Phase 1 — Upstream Quilt fixes (`quilt-trader/`)

| File | Change | Responsibility |
|------|--------|----------------|
| `sdk/manifest.py` | Modify | Remove `output_format`/`output_filename` from dataclass + parser |
| `coordinator/services/scraper_engine.py` | Modify | Drop `output_filename` param; add `config` param; inject `quilt_root` into subprocess `sys.path` |
| `tests/sdk/test_manifest.py` | Modify | Update `test_load_valid_scraper`; remove assertions on `output_format`/`output_filename` |
| `tests/sdk/fixtures/valid_scraper.yaml` | Modify | Drop `output:` block |
| `tests/coordinator/test_scraper_engine.py` | Modify + add | Drop third arg from `run_scraper`; add `test_run_scraper_real_subprocess`; add `test_run_scraper_passes_config` |

### Phase 2 — Scraper rewrite (`alpha-picks-scraper/`)

| File | Change | Responsibility |
|------|--------|----------------|
| `pyproject.toml`, `.env.example`, `src/`, `scripts/`, `data/`, `logs/`, `tests/test_differ.py`, `tests/test_writer.py` | Delete | Remove all standalone-mode infrastructure |
| `lib/__init__.py` | Create | Package marker |
| `lib/cookies.py` | Create | Cookie loader (verbatim copy of old `src/alpha_picks_scraper/cookies.py`) |
| `lib/parser.py` | Create | HTML→picks parser (verbatim copy of old `src/alpha_picks_scraper/parser.py`) |
| `lib/fetch.py` | Create | Playwright fetch (verbatim copy of old `src/alpha_picks_scraper/scraper.py`, renamed) |
| `tests/__init__.py` | Create | Package marker |
| `tests/fixtures/picks_page.html` | Create | Move from old location |
| `tests/test_parser.py` | Create | Re-pointed at `lib.parser` |
| `tests/test_cookies.py` | Create | New focused tests for the cookie loader |
| `quilt.yaml` | Create | Quilt manifest |
| `scraper.py` | Create | `AlphaPicksScraper(QuiltScraper)` entry point |
| `requirements.txt` | Create | playwright, beautifulsoup4, pandas |
| `.gitignore` | Create | .venv/, __pycache__/, *.pyc, .pytest_cache/ |
| `README.md` | Replace | Quilt-package install/run instructions |

### Phase 3 — Install + verify (`quilt-trader/`)

| File | Change | Responsibility |
|------|--------|----------------|
| `.gitignore` | Modify | Add `packages/` |
| `scripts/run_scraper_once.py` | Create | Manual one-off scraper invocation script |
| `packages/alpha-picks-scraper/` | Created at install time | The cloned + venv'd scraper package |
| `data/secrets/alpha-picks-cookies.json` | Created at install time | Operator-supplied cookies (gitignored via existing `data/` rule) |

---

## Phase 1 — Upstream Quilt fixes

### Task 1: Drop `output.filename` and `output.format` from the manifest schema

**Files:**
- Modify: `sdk/manifest.py:23-37, 100-103`
- Modify: `tests/sdk/test_manifest.py:18-24`
- Modify: `tests/sdk/fixtures/valid_scraper.yaml`
- Modify: `coordinator/services/scraper_engine.py:29-56`
- Modify: `tests/coordinator/test_scraper_engine.py:39-60`

The current `output:` block parses into two fields (`output_format`, `output_filename`) that are passed to `ScraperEngine.run_scraper` and ignored — write path is always `{output_dir}/{name}.{fmt}` with `fmt` hardcoded as `csv` in the runner script. We remove both fields from the schema and from the engine signature in one task because they're tightly coupled.

- [ ] **Step 1: Update the failing manifest test first**

Edit `tests/sdk/test_manifest.py:18-24`. Replace `test_load_valid_scraper` so it no longer asserts on the removed fields:

```python
    def test_load_valid_scraper(self):
        manifest = QuiltManifest.from_file(FIXTURES / "valid_scraper.yaml")
        assert manifest.name == "alpha-picks-scraper"
        assert manifest.type == "scraper"
        assert manifest.schedule == "*/30 * * * *"
```

- [ ] **Step 2: Update the fixture**

Edit `tests/sdk/fixtures/valid_scraper.yaml` — replace entire contents with:

```yaml
name: alpha-picks-scraper
type: scraper
version: 1.0.0
description: Scrapes alpha stock picks
schedule: "*/30 * * * *"
```

Also update `tests/sdk/test_manifest.py:134-144` — the `test_scraper_missing_schedule_raises` test currently includes an `output:` block in its yaml that needs to be dropped to keep the fixture style consistent. Replace the yaml literal with:

```yaml
name: test
type: scraper
version: 1.0.0
```

- [ ] **Step 3: Run the manifest tests — expect failures from old dataclass fields**

Run: `.venv/bin/pytest tests/sdk/test_manifest.py -v`
Expected: PASS for `test_load_valid_scraper` (we updated it), but FAIL elsewhere if any other test references `output_format` / `output_filename`. Read the failures.

- [ ] **Step 4: Update `sdk/manifest.py` — remove `output_format` and `output_filename`**

Edit `sdk/manifest.py`. In the `QuiltManifest` dataclass (lines 23-37), remove these two lines:
```python
    output_format: str = ""
    output_filename: str = ""
```

In `_parse` (lines 88-103), remove:
```python
        output_data = data.get("output", {})
```
and remove the trailing keyword args from the return:
```python
            output_format=output_data.get("format", ""),
            output_filename=output_data.get("filename", ""),
```

The final return becomes:
```python
        return QuiltManifest(
            name=data["name"],
            type=data["type"],
            version=data.get("version", "0.0.0"),
            description=data.get("description", ""),
            entry_point=data.get("entry_point", ""),
            class_name=data.get("class_name", ""),
            requirements=requirements,
            config_parameters=config_parameters,
            custom_events=custom_events,
            schedule=data.get("schedule", ""),
        )
```

- [ ] **Step 5: Run manifest tests — all green**

Run: `.venv/bin/pytest tests/sdk/test_manifest.py -v`
Expected: all PASS.

- [ ] **Step 6: Update `ScraperEngine.run_scraper` signature**

Edit `coordinator/services/scraper_engine.py:29`. Change the signature:
```python
    def run_scraper(self, name: str, output_format: str) -> ScraperResult:
```
(remove the `output_filename: str` parameter)

- [ ] **Step 7: Update scraper-engine tests**

Edit `tests/coordinator/test_scraper_engine.py`. Two call sites need updating:

Line 48 — replace:
```python
    result = engine.run_scraper("alpha-picks-scraper", "csv", "alpha-picks-scraper.csv")
```
with:
```python
    result = engine.run_scraper("alpha-picks-scraper", "csv")
```

Line 58 — same change:
```python
    result = engine.run_scraper("alpha-picks-scraper", "csv")
```

- [ ] **Step 8: Run scraper-engine tests**

Run: `.venv/bin/pytest tests/coordinator/test_scraper_engine.py -v`
Expected: all PASS (still using mocked subprocess).

- [ ] **Step 9: Run the full test suite to confirm no other regressions**

Run: `.venv/bin/pytest -q`
Expected: all PASS.

- [ ] **Step 10: Commit**

```bash
git add sdk/manifest.py tests/sdk/test_manifest.py tests/sdk/fixtures/valid_scraper.yaml coordinator/services/scraper_engine.py tests/coordinator/test_scraper_engine.py
git commit -m "$(cat <<'EOF'
refactor(sdk): drop dead output.filename/format from scraper manifest

ScraperEngine.run_scraper ignored the output_filename param; the write
path is always {output_dir}/{name}.{fmt}. Both DataService.load_custom_data
and the /api/data/custom/{name} route assume the same convention.
Honoring the field on write alone would break lookup; honoring it
everywhere is multi-file scope creep. Delete the field to remove the
misleading documentation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Add `config` parameter to `ScraperEngine.run_scraper`

**Files:**
- Modify: `coordinator/services/scraper_engine.py:29-56`
- Modify: `tests/coordinator/test_scraper_engine.py` (add a test)

The current runner script hardcodes `scraper.on_start({})`. This task makes config injectable so callers (eventually the scheduler; immediately the verification script) can pass per-instance config dicts to scrapers.

- [ ] **Step 1: Add the failing test**

Append to `tests/coordinator/test_scraper_engine.py`:

```python
@patch("coordinator.services.scraper_engine.subprocess")
def test_run_scraper_passes_config_to_subprocess(mock_subprocess, packages_dir, output_dir):
    mock_subprocess.run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    engine = ScraperEngine(packages_dir=packages_dir, output_dir=output_dir)
    engine.run_scraper("alpha-picks-scraper", "csv", config={"cookies_file": "/tmp/c.json", "headless": True})
    args, kwargs = mock_subprocess.run.call_args
    runner_script = args[0][2]
    assert '"cookies_file": "/tmp/c.json"' in runner_script
    assert '"headless": true' in runner_script
    assert "json.loads(" in runner_script
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/coordinator/test_scraper_engine.py::test_run_scraper_passes_config_to_subprocess -v`
Expected: FAIL — `run_scraper()` got an unexpected keyword argument 'config'.

- [ ] **Step 3: Update `run_scraper` and the runner script**

Edit `coordinator/services/scraper_engine.py`. Add `import json` at the top, alongside the other imports. Update the signature and runner_script:

```python
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Optional
import yaml

logger = logging.getLogger(__name__)

@dataclass
class ScraperResult:
    success: bool
    output_path: Optional[str] = None
    error: Optional[str] = None

class ScraperEngine:
    def __init__(self, packages_dir: str, output_dir: str) -> None:
        self._packages_dir = packages_dir
        self._output_dir = output_dir

    def parse_manifest(self, name: str) -> dict:
        manifest_path = os.path.join(self._packages_dir, name, "quilt.yaml")
        with open(manifest_path) as f:
            return yaml.safe_load(f)

    def output_path(self, name: str, fmt: str) -> str:
        return os.path.join(self._output_dir, f"{name}.{fmt}")

    def run_scraper(self, name: str, output_format: str, config: Optional[dict] = None) -> ScraperResult:
        pkg_dir = os.path.join(self._packages_dir, name)
        venv_python = os.path.join(pkg_dir, ".venv", "bin", "python")
        python = venv_python if os.path.exists(venv_python) else "python"
        out_path = self.output_path(name, output_format)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        config_json = json.dumps(config or {})
        runner_script = (
            f"import sys, json; sys.path.insert(0, '{pkg_dir}'); "
            f"import yaml; "
            f"manifest = yaml.safe_load(open('{pkg_dir}/quilt.yaml')); "
            f"entry = manifest.get('entry_point', 'scraper.py'); "
            f"class_name = manifest.get('class_name', 'Scraper'); "
            f"import importlib.util; "
            f"spec = importlib.util.spec_from_file_location('mod', '{pkg_dir}/' + entry); "
            f"mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); "
            f"scraper = getattr(mod, class_name)(); "
            f"scraper.on_start(json.loads({config_json!r})); "
            f"df = scraper.on_run(); "
            f"df.to_csv('{out_path}', index=False); "
            f"scraper.on_stop(); "
        )
        result = subprocess.run(
            [python, "-c", runner_script], capture_output=True, text=True, cwd=pkg_dir,
        )
        if result.returncode == 0:
            return ScraperResult(success=True, output_path=out_path)
        else:
            return ScraperResult(success=False, error=result.stderr)
```

The `{config_json!r}` is the key trick — `repr()` produces a properly-escaped Python string literal containing the JSON, which is then `json.loads`'d inside the subprocess. No nested-quote hell.

- [ ] **Step 4: Run the new test to verify it passes**

Run: `.venv/bin/pytest tests/coordinator/test_scraper_engine.py::test_run_scraper_passes_config_to_subprocess -v`
Expected: PASS.

- [ ] **Step 5: Run all scraper-engine tests**

Run: `.venv/bin/pytest tests/coordinator/test_scraper_engine.py -v`
Expected: all PASS (existing tests don't pass `config`, get the default `None` → empty dict, behave unchanged).

- [ ] **Step 6: Commit**

```bash
git add coordinator/services/scraper_engine.py tests/coordinator/test_scraper_engine.py
git commit -m "$(cat <<'EOF'
feat(scraper_engine): accept config dict and pass to scraper.on_start

Previously the runner script hardcoded on_start({}), so scrapers
could never receive per-instance configuration. Add an optional
config parameter to run_scraper; embed it into the subprocess script
via json.dumps + repr (safe against quote-escape edge cases).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Fix SDK import in scraper subprocess

**Files:**
- Modify: `coordinator/services/scraper_engine.py`
- Modify: `tests/coordinator/test_scraper_engine.py` (add a non-mocked test)

`from sdk.scraper import QuiltScraper` fails inside the scraper's subprocess because the package venv has no idea where to find `sdk`. Detect the quilt-trader root and append it to `sys.path` in the runner script.

- [ ] **Step 1: Write the failing real-subprocess test**

Append to `tests/coordinator/test_scraper_engine.py`:

```python
import sys as _sys
import venv as _venv

@pytest.mark.slow
def test_run_scraper_real_subprocess_can_import_sdk(tmp_path):
    pkg = tmp_path / "packages" / "stub-scraper"
    pkg.mkdir(parents=True)
    (pkg / "quilt.yaml").write_text(
        "name: stub-scraper\ntype: scraper\nschedule: '*/30 * * * *'\n"
    )
    (pkg / "scraper.py").write_text(
        "from sdk.scraper import QuiltScraper\n"
        "import pandas as pd\n"
        "class Scraper(QuiltScraper):\n"
        "    def on_run(self):\n"
        "        return pd.DataFrame({'a': [1, 2]})\n"
    )
    venv_dir = pkg / ".venv"
    _venv.create(str(venv_dir), with_pip=True)
    pip = venv_dir / "bin" / "pip"
    import subprocess as _sub
    r = _sub.run([str(pip), "install", "--quiet", "pandas", "pyyaml"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    out_dir = tmp_path / "custom"
    out_dir.mkdir()
    engine = ScraperEngine(packages_dir=str(tmp_path / "packages"), output_dir=str(out_dir))
    result = engine.run_scraper("stub-scraper", "csv")
    assert result.success is True, f"stderr was: {result.error}"
    assert (out_dir / "stub-scraper.csv").exists()
    csv = (out_dir / "stub-scraper.csv").read_text()
    assert "1\n2" in csv
```

Note: `@pytest.mark.slow` — this test creates a real venv (~5–20 seconds). Acceptable for CI runs but operators may want to skip it locally with `-m "not slow"`. The marker doesn't need to be registered in `pyproject.toml` for the test to run; pytest will emit a warning but execute it.

- [ ] **Step 2: Run the new test to verify it fails**

Run: `.venv/bin/pytest tests/coordinator/test_scraper_engine.py::test_run_scraper_real_subprocess_can_import_sdk -v`
Expected: FAIL — the assertion `result.success is True` fails with stderr containing `ModuleNotFoundError: No module named 'sdk'`.

- [ ] **Step 3: Add quilt-root detection and inject into runner script**

Edit `coordinator/services/scraper_engine.py`. Change `ScraperEngine` to:

```python
class ScraperEngine:
    def __init__(self, packages_dir: str, output_dir: str, quilt_root: Optional[str] = None) -> None:
        self._packages_dir = packages_dir
        self._output_dir = output_dir
        if quilt_root is None:
            quilt_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self._quilt_root = quilt_root
```

Update the runner_script's first line in `run_scraper` from:
```python
            f"import sys, json; sys.path.insert(0, '{pkg_dir}'); "
```
to:
```python
            f"import sys, json; sys.path.insert(0, '{pkg_dir}'); sys.path.append('{self._quilt_root}'); "
```

Note the order: `pkg_dir` at index 0 takes precedence on name collisions; `quilt_root` appended so `sdk.*` resolves as a fallback.

- [ ] **Step 4: Run the new test to verify it passes**

Run: `.venv/bin/pytest tests/coordinator/test_scraper_engine.py::test_run_scraper_real_subprocess_can_import_sdk -v`
Expected: PASS (may take 10-30 seconds for venv creation + pip install).

- [ ] **Step 5: Run all scraper-engine tests**

Run: `.venv/bin/pytest tests/coordinator/test_scraper_engine.py -v`
Expected: all PASS.

- [ ] **Step 6: Run the full test suite**

Run: `.venv/bin/pytest -q -m "not slow"` (fast pass)
then `.venv/bin/pytest -q tests/coordinator/test_scraper_engine.py` (incl. slow)
Expected: all PASS in both runs.

- [ ] **Step 7: Commit**

```bash
git add coordinator/services/scraper_engine.py tests/coordinator/test_scraper_engine.py
git commit -m "$(cat <<'EOF'
fix(scraper_engine): inject quilt root into subprocess sys.path

The scraper runs in the package's own venv, which doesn't have the
quilt-trader source tree on sys.path. Any scraper writing the obvious
'from sdk.scraper import QuiltScraper' would have hit ModuleNotFoundError
in production. The bug was masked by tests that mocked subprocess.

Detect the quilt-trader root from __file__, append it to the runner
script's sys.path after the package dir (so package modules still win
on name collision). Add a real-subprocess integration test that creates
a venv and verifies the import works end-to-end.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 2 — Scraper repository rewrite

Phase 2 work happens in `/home/jkern/dev/alpha-picks-scraper/`. The repo's master branch currently has no commits — all files are untracked. The rewrite produces a single initial commit.

### Task 4: Wipe the old standalone-mode files

**Files:**
- Delete: everything except `.git/` and `tests/fixtures/picks_page.html` (the fixture we want to preserve for Task 5)

- [ ] **Step 1: Verify we're in the right place and there are no commits to lose**

Run from `/home/jkern/dev/alpha-picks-scraper`:
```bash
git -C /home/jkern/dev/alpha-picks-scraper log --oneline 2>&1 | head -5
```
Expected: `fatal: your current branch 'master' does not have any commits yet`. If you see actual commits, STOP and confirm with the user before proceeding.

- [ ] **Step 2: Move the fixture to a temporary location**

```bash
mv /home/jkern/dev/alpha-picks-scraper/tests/fixtures/picks_page.html /tmp/picks_page.html.keep
```

- [ ] **Step 3: Delete all repo contents except `.git/`**

```bash
cd /home/jkern/dev/alpha-picks-scraper
find . -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} +
```

Verify with `ls -la`. Expected: only `.git/` and `..` and `.` remain.

- [ ] **Step 4: Restore the fixture into the new tests/fixtures/ path**

```bash
mkdir -p /home/jkern/dev/alpha-picks-scraper/tests/fixtures
mv /tmp/picks_page.html.keep /home/jkern/dev/alpha-picks-scraper/tests/fixtures/picks_page.html
```

No commit yet — Task 5 will create the initial commit with everything together.

---

### Task 5: Create `lib/` modules and tests

**Files:**
- Create: `lib/__init__.py` (empty)
- Create: `lib/cookies.py`
- Create: `lib/parser.py`
- Create: `lib/fetch.py`
- Create: `tests/__init__.py` (empty)
- Create: `tests/test_parser.py`
- Create: `tests/test_cookies.py`
- (Already in place from Task 4): `tests/fixtures/picks_page.html`

These modules are verbatim ports from the (now-deleted) old `src/alpha_picks_scraper/` package. Since they had passing tests at the time of deletion, this task is a code-transplant rather than fresh TDD — but we do verify with pytest before committing.

- [ ] **Step 1: Create `lib/__init__.py`**

```bash
touch /home/jkern/dev/alpha-picks-scraper/lib/__init__.py
```

(File intentionally empty — just a package marker.)

- [ ] **Step 2: Create `lib/cookies.py`**

Write `/home/jkern/dev/alpha-picks-scraper/lib/cookies.py`:

```python
from __future__ import annotations

import json
from pathlib import Path


class CookieError(Exception):
    """Raised when cookies.json is missing or malformed."""


REQUIRED_FIELDS = {"name", "value", "domain"}


def load_cookies(path: Path) -> list[dict]:
    if not path.exists():
        raise CookieError(f"cookies file not found: {path}")
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise CookieError(f"cookies file is not valid JSON: {e}") from e

    if not isinstance(raw, list) or not raw:
        raise CookieError("cookies file must be a non-empty JSON array")

    normalized: list[dict] = []
    for i, c in enumerate(raw):
        if not isinstance(c, dict):
            raise CookieError(f"cookie #{i} is not an object")
        missing = REQUIRED_FIELDS - c.keys()
        if missing:
            raise CookieError(f"cookie #{i} missing fields: {sorted(missing)}")
        normalized.append(_to_playwright_cookie(c))
    return normalized


def _to_playwright_cookie(c: dict) -> dict:
    """Accept cookies from common browser exporters and shape them for Playwright."""
    out: dict = {
        "name": c["name"],
        "value": c["value"],
        "domain": c["domain"],
        "path": c.get("path", "/"),
    }
    if "expirationDate" in c:
        out["expires"] = int(c["expirationDate"])
    elif "expires" in c:
        out["expires"] = int(c["expires"])
    if "httpOnly" in c:
        out["httpOnly"] = bool(c["httpOnly"])
    if "secure" in c:
        out["secure"] = bool(c["secure"])
    same_site = c.get("sameSite")
    if isinstance(same_site, str):
        s = same_site.lower()
        mapping = {"no_restriction": "None", "unspecified": "Lax", "lax": "Lax", "strict": "Strict", "none": "None"}
        if s in mapping:
            out["sameSite"] = mapping[s]
    return out
```

- [ ] **Step 3: Create `lib/parser.py`**

Write `/home/jkern/dev/alpha-picks-scraper/lib/parser.py`:

```python
from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup, Tag


class ParseError(Exception):
    """Raised when the picks page cannot be parsed into a valid list of picks."""


HEADER_ALIASES: dict[str, str] = {
    "company": "company",
    "name": "company",
    "symbol": "symbol",
    "ticker": "symbol",
    "date picked": "date_picked",
    "pick date": "date_picked",
    "picked": "date_picked",
    "return": "return_pct",
    "return %": "return_pct",
    "total return": "return_pct",
    "sector": "sector",
    "rating": "rating",
    "holding": "holding_pct",
    "holding %": "holding_pct",
    "weight": "holding_pct",
    "weight %": "holding_pct",
    "portfolio %": "holding_pct",
}

REQUIRED_FIELDS = ("company", "symbol", "date_picked", "return_pct", "sector", "rating", "holding_pct")


def parse_picks(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    table = _find_picks_table(soup)
    if table is None:
        raise ParseError("could not find a picks table containing all required columns")

    headers = _extract_headers(table)
    column_to_field: dict[int, str] = {}
    for i, h in enumerate(headers):
        key = HEADER_ALIASES.get(h.lower().strip())
        if key:
            column_to_field[i] = key

    missing = set(REQUIRED_FIELDS) - set(column_to_field.values())
    if missing:
        raise ParseError(f"picks table is missing required columns: {sorted(missing)}")

    rows = _extract_body_rows(table)
    if not rows:
        raise ParseError("picks table has no body rows")

    picks: list[dict] = []
    for row_idx, cells in enumerate(rows):
        record: dict = {}
        for col_idx, field in column_to_field.items():
            if col_idx >= len(cells):
                raise ParseError(f"row {row_idx} is missing column {col_idx} ({field})")
            value = cells[col_idx].strip()
            if not value:
                raise ParseError(f"row {row_idx} field {field!r} is empty")
            record[field] = _coerce(field, value, row_idx)
        picks.append(record)

    if not picks:
        raise ParseError("no picks parsed from table")
    return picks


def _find_picks_table(soup: BeautifulSoup) -> Tag | None:
    for table in soup.find_all("table"):
        headers = _extract_headers(table)
        normalized = {h.lower().strip() for h in headers}
        mapped = {HEADER_ALIASES[k] for k in normalized if k in HEADER_ALIASES}
        if set(REQUIRED_FIELDS).issubset(mapped):
            return table
    return None


def _extract_headers(table: Tag) -> list[str]:
    thead = table.find("thead")
    header_row = None
    if thead:
        header_row = thead.find("tr")
    if header_row is None:
        header_row = table.find("tr")
    if header_row is None:
        return []
    return [c.get_text(strip=True) for c in header_row.find_all(["th", "td"])]


def _extract_body_rows(table: Tag) -> list[list[str]]:
    tbody = table.find("tbody") or table
    out: list[list[str]] = []
    seen_header = False
    for tr in tbody.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        if tbody is table and not seen_header and tr.find("th") and not tr.find("td"):
            seen_header = True
            continue
        out.append([c.get_text(" ", strip=True) for c in cells])
    return out


_PCT_RE = re.compile(r"-?\d+(\.\d+)?")


def _coerce(field: str, value: str, row_idx: int):
    if field in ("return_pct", "holding_pct"):
        cleaned = value.replace("+", "").replace("%", "").replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError as e:
            raise ParseError(f"row {row_idx} field {field!r} not a number: {value!r}") from e
    if field == "date_picked":
        return _coerce_date(value, row_idx)
    return value


_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y", "%d %b %Y")


def _coerce_date(value: str, row_idx: int) -> str:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ParseError(f"row {row_idx} could not parse date: {value!r}")
```

- [ ] **Step 4: Create `lib/fetch.py`**

Write `/home/jkern/dev/alpha-picks-scraper/lib/fetch.py`:

```python
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

log = logging.getLogger(__name__)


class AuthExpiredError(Exception):
    """The site rendered a login form — our cookies are no good."""


class PageLoadError(Exception):
    """Neither the picks container nor a login form appeared — page failed to load."""


PICKS_READY_SELECTORS = (
    "table[data-test-id='alpha-picks-table']",
    "table:has(thead th:has-text('Symbol'))",
    "[data-test-id='current-picks']",
)

LOGIN_WALL_SELECTORS = (
    "form[action*='login']",
    "input[type='password']",
    "[data-test-id='sign-in']",
    "text=/Sign in/i",
)


@contextmanager
def launch_browser(headless: bool = True) -> Iterator[Browser]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            yield browser
        finally:
            browser.close()


def fetch_picks_html(
    *,
    browser: Browser,
    cookies: list[dict],
    url: str,
    timeout_ms: int = 30000,
) -> str:
    context: BrowserContext = browser.new_context()
    try:
        context.add_cookies(cookies)
        page: Page = context.new_page()
        log.info("navigating to %s", url)
        try:
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        except PlaywrightTimeoutError as e:
            raise PageLoadError(f"navigation timeout: {e}") from e

        log.info("verifying login state")
        outcome = _wait_for_picks_or_login(page, timeout_ms=timeout_ms)
        if outcome == "login":
            raise AuthExpiredError("login wall detected — cookies likely expired")
        if outcome == "timeout":
            raise PageLoadError("neither picks container nor login wall appeared in time")

        log.info("extracting page content")
        return page.content()
    finally:
        context.close()


def _wait_for_picks_or_login(page: Page, timeout_ms: int) -> str:
    """Race the picks container against the login wall. Returns 'picks', 'login', or 'timeout'."""
    deadline_ms = timeout_ms
    poll_interval = 250
    elapsed = 0
    while elapsed < deadline_ms:
        for sel in PICKS_READY_SELECTORS:
            try:
                if page.locator(sel).first.is_visible(timeout=poll_interval):
                    return "picks"
            except PlaywrightTimeoutError:
                pass
            except Exception:
                pass
        for sel in LOGIN_WALL_SELECTORS:
            try:
                if page.locator(sel).first.is_visible(timeout=poll_interval):
                    return "login"
            except PlaywrightTimeoutError:
                pass
            except Exception:
                pass
        page.wait_for_timeout(poll_interval)
        elapsed += poll_interval * (len(PICKS_READY_SELECTORS) + len(LOGIN_WALL_SELECTORS) + 1)
    return "timeout"
```

- [ ] **Step 5: Create `tests/__init__.py`**

```bash
touch /home/jkern/dev/alpha-picks-scraper/tests/__init__.py
```

- [ ] **Step 6: Create `tests/test_parser.py`**

Write `/home/jkern/dev/alpha-picks-scraper/tests/test_parser.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from lib.parser import parse_picks, ParseError


FIXTURE = Path(__file__).parent / "fixtures" / "picks_page.html"


def test_parse_fixture_returns_three_picks():
    html = FIXTURE.read_text()
    picks = parse_picks(html)
    assert len(picks) == 3


def test_parsed_pick_has_canonical_fields_and_types():
    html = FIXTURE.read_text()
    picks = parse_picks(html)
    p = picks[0]
    assert p["company"] == "Example Corp"
    assert p["symbol"] == "EXMP"
    assert p["date_picked"] == "2025-11-04"
    assert p["return_pct"] == pytest.approx(12.34)
    assert p["sector"] == "Technology"
    assert p["rating"] == "Strong Buy"
    assert p["holding_pct"] == pytest.approx(4.5)


def test_negative_and_no_sign_percentages_parse():
    html = FIXTURE.read_text()
    picks = parse_picks(html)
    by_sym = {p["symbol"]: p for p in picks}
    assert by_sym["ACME"]["return_pct"] == pytest.approx(-3.21)
    assert by_sym["GLBX"]["return_pct"] == pytest.approx(45.0)
    assert by_sym["GLBX"]["holding_pct"] == pytest.approx(6.25)


def test_missing_table_raises():
    with pytest.raises(ParseError):
        parse_picks("<html><body><p>No picks here.</p></body></html>")


def test_empty_table_raises():
    html = "<table><thead><tr><th>Company</th><th>Symbol</th><th>Date Picked</th><th>Return</th><th>Sector</th><th>Rating</th><th>Holding %</th></tr></thead><tbody></tbody></table>"
    with pytest.raises(ParseError):
        parse_picks(html)


def test_row_with_missing_field_raises():
    html = """
    <table>
      <thead><tr><th>Company</th><th>Symbol</th><th>Date Picked</th><th>Return</th><th>Sector</th><th>Rating</th><th>Holding %</th></tr></thead>
      <tbody><tr><td>X</td><td>XYZ</td><td>2025-01-01</td><td>1%</td><td></td><td>Buy</td><td>1%</td></tr></tbody>
    </table>
    """
    with pytest.raises(ParseError):
        parse_picks(html)
```

(Same as the old test file but with `from lib.parser import ...` instead of `from alpha_picks_scraper.parser import ...`.)

- [ ] **Step 7: Create `tests/test_cookies.py`**

Write `/home/jkern/dev/alpha-picks-scraper/tests/test_cookies.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.cookies import load_cookies, CookieError


def write_cookies(tmp_path: Path, data) -> Path:
    p = tmp_path / "cookies.json"
    p.write_text(json.dumps(data))
    return p


def test_load_minimal_cookies(tmp_path):
    path = write_cookies(tmp_path, [{"name": "sa", "value": "abc", "domain": ".seekingalpha.com"}])
    cookies = load_cookies(path)
    assert cookies == [{"name": "sa", "value": "abc", "domain": ".seekingalpha.com", "path": "/"}]


def test_chrome_expiration_date_translated(tmp_path):
    path = write_cookies(tmp_path, [{
        "name": "x", "value": "y", "domain": ".s.com", "expirationDate": 1735689600.5,
    }])
    cookies = load_cookies(path)
    assert cookies[0]["expires"] == 1735689600


def test_firefox_expires_translated(tmp_path):
    path = write_cookies(tmp_path, [{
        "name": "x", "value": "y", "domain": ".s.com", "expires": 1735689600,
    }])
    cookies = load_cookies(path)
    assert cookies[0]["expires"] == 1735689600


def test_same_site_no_restriction_maps_to_none(tmp_path):
    path = write_cookies(tmp_path, [{
        "name": "x", "value": "y", "domain": ".s.com", "sameSite": "no_restriction",
    }])
    cookies = load_cookies(path)
    assert cookies[0]["sameSite"] == "None"


def test_same_site_unspecified_maps_to_lax(tmp_path):
    path = write_cookies(tmp_path, [{
        "name": "x", "value": "y", "domain": ".s.com", "sameSite": "unspecified",
    }])
    cookies = load_cookies(path)
    assert cookies[0]["sameSite"] == "Lax"


def test_http_only_and_secure_preserved(tmp_path):
    path = write_cookies(tmp_path, [{
        "name": "x", "value": "y", "domain": ".s.com",
        "httpOnly": True, "secure": True,
    }])
    cookies = load_cookies(path)
    assert cookies[0]["httpOnly"] is True
    assert cookies[0]["secure"] is True


def test_missing_file_raises(tmp_path):
    with pytest.raises(CookieError, match="not found"):
        load_cookies(tmp_path / "no_such_file.json")


def test_malformed_json_raises(tmp_path):
    p = tmp_path / "cookies.json"
    p.write_text("{not valid json")
    with pytest.raises(CookieError, match="valid JSON"):
        load_cookies(p)


def test_empty_array_raises(tmp_path):
    path = write_cookies(tmp_path, [])
    with pytest.raises(CookieError, match="non-empty"):
        load_cookies(path)


def test_non_array_raises(tmp_path):
    p = tmp_path / "cookies.json"
    p.write_text('{"name": "x"}')
    with pytest.raises(CookieError, match="non-empty JSON array"):
        load_cookies(p)


def test_missing_required_field_raises(tmp_path):
    path = write_cookies(tmp_path, [{"name": "x", "value": "y"}])  # no domain
    with pytest.raises(CookieError, match="missing fields"):
        load_cookies(path)


def test_non_object_cookie_raises(tmp_path):
    path = write_cookies(tmp_path, ["not an object"])
    with pytest.raises(CookieError, match="not an object"):
        load_cookies(path)
```

- [ ] **Step 8: Install test deps and run pytest**

Create a throwaway venv just to run the tests (the real package venv comes in Phase 3 install):

```bash
cd /home/jkern/dev/alpha-picks-scraper
python -m venv .venv_test
.venv_test/bin/pip install --quiet beautifulsoup4 pytest
.venv_test/bin/python -m pytest tests/ -v
```

Expected: 18 tests pass (6 parser + 12 cookies).

If any fail, debug and fix before continuing.

- [ ] **Step 9: Clean up the test venv**

```bash
rm -rf /home/jkern/dev/alpha-picks-scraper/.venv_test
```

(No commit yet — Task 7 will commit Phase 2 as a single initial commit.)

---

### Task 6: Create the Quilt-facing files (manifest, entry point, requirements, gitignore)

**Files:**
- Create: `quilt.yaml`
- Create: `scraper.py`
- Create: `requirements.txt`
- Create: `.gitignore`

- [ ] **Step 1: Create `quilt.yaml`**

Write `/home/jkern/dev/alpha-picks-scraper/quilt.yaml`:

```yaml
name: alpha-picks-scraper
type: scraper
version: 1.0.0
description: Scrapes Seeking Alpha's "Alpha Picks" current portfolio.
entry_point: scraper.py
class_name: AlphaPicksScraper
schedule: "0 14 * * *"
config:
  parameters:
    - name: cookies_file
      type: string
      default: /etc/quilt/alpha-picks-cookies.json
      description: Absolute path to exported Seeking Alpha cookies JSON. Must exist on the coordinator host.
    - name: picks_url
      type: string
      default: https://seekingalpha.com/alpha-picks/picks/current
    - name: timeout_ms
      type: int
      default: 30000
    - name: headless
      type: bool
      default: true
```

- [ ] **Step 2: Create `scraper.py` (Quilt entry point)**

Write `/home/jkern/dev/alpha-picks-scraper/scraper.py`:

```python
from __future__ import annotations

from pathlib import Path

import pandas as pd

from sdk.scraper import QuiltScraper

from lib.cookies import load_cookies
from lib.fetch import launch_browser, fetch_picks_html
from lib.parser import parse_picks


class AlphaPicksScraper(QuiltScraper):
    def on_start(self, config: dict) -> None:
        self._cookies_file = Path(config.get("cookies_file", "/etc/quilt/alpha-picks-cookies.json"))
        self._picks_url = config.get("picks_url", "https://seekingalpha.com/alpha-picks/picks/current")
        self._timeout_ms = int(config.get("timeout_ms", 30000))
        self._headless = bool(config.get("headless", True))

    def on_run(self) -> pd.DataFrame:
        cookies = load_cookies(self._cookies_file)
        with launch_browser(headless=self._headless) as browser:
            html = fetch_picks_html(
                browser=browser,
                cookies=cookies,
                url=self._picks_url,
                timeout_ms=self._timeout_ms,
            )
        picks = parse_picks(html)
        return pd.DataFrame(picks, columns=[
            "symbol", "company", "date_picked",
            "return_pct", "sector", "rating", "holding_pct",
        ])
```

- [ ] **Step 3: Create `requirements.txt`**

Write `/home/jkern/dev/alpha-picks-scraper/requirements.txt`:

```
playwright>=1.42
beautifulsoup4>=4.12
pandas>=2.0
```

- [ ] **Step 4: Create `.gitignore`**

Write `/home/jkern/dev/alpha-picks-scraper/.gitignore`:

```
.venv/
.venv_test/
__pycache__/
*.pyc
.pytest_cache/
```

- [ ] **Step 5: Verify the entry-point module can be imported as a module**

A real import requires `sdk.scraper`, which lives in the quilt-trader repo — so we can't import `scraper.py` standalone. But we can syntax-check it and import the `lib/` modules in isolation:

```bash
cd /home/jkern/dev/alpha-picks-scraper
python -c "import ast; ast.parse(open('scraper.py').read()); print('scraper.py parses OK')"
python -c "import sys; sys.path.insert(0, '.'); from lib import cookies, parser, fetch; print('lib modules import OK')"
```

The second command requires the test venv from Task 5, OR a venv with beautifulsoup4 + playwright. Skip the lib import check if neither is set up — the full import is verified end-to-end in Phase 3.

Expected outputs:
- `scraper.py parses OK`
- `lib modules import OK` (if deps available)

(No commit yet — Task 7 will commit everything together.)

---

### Task 7: Write README and create initial commit

**Files:**
- Create: `README.md`
- Initial commit on `master`

- [ ] **Step 1: Write the README**

Write `/home/jkern/dev/alpha-picks-scraper/README.md`:

````markdown
# alpha-picks-scraper

A [Quilt](https://github.com/ElectricJack/quilt-trader) scraper plugin that scrapes Seeking Alpha's "Alpha Picks" current portfolio and exposes it as a CSV consumable by Quilt trading algorithms.

## What it produces

A single CSV at `data/custom/alpha-picks-scraper.csv` inside your Quilt deployment, one row per current pick:

| Column        | Type    | Example          |
|---------------|---------|------------------|
| `symbol`      | str     | `TSLA`           |
| `company`     | str     | `Tesla, Inc.`    |
| `date_picked` | str     | `2025-11-04`     |
| `return_pct`  | float   | `12.34`          |
| `sector`      | str     | `Technology`     |
| `rating`      | str     | `Strong Buy`     |
| `holding_pct` | float   | `4.5`            |

Each run is a full overwrite. The previous CSV is replaced atomically by Quilt's scraper engine. No diff state, no history snapshots — downstream algorithms diff successive snapshots themselves if they care.

## Install inside Quilt

From the root of your `quilt-trader` checkout:

```bash
mkdir -p packages
git clone <this repo url> packages/alpha-picks-scraper
python -m venv packages/alpha-picks-scraper/.venv
packages/alpha-picks-scraper/.venv/bin/pip install -r packages/alpha-picks-scraper/requirements.txt
packages/alpha-picks-scraper/.venv/bin/playwright install chromium
```

The last command downloads ~150 MB of Chromium binary into the venv; it's a one-time setup cost per venv.

## Provide cookies

You need a logged-in Seeking Alpha session, exported as a JSON cookies file.

1. In Chrome or Firefox, install a cookie-export extension (e.g. "Cookie-Editor").
2. Sign in to Seeking Alpha as normal.
3. Open the extension, **filter to the `.seekingalpha.com` domain**, choose "Export → JSON", and save the file.
4. Place it on the coordinator host. Default path the scraper expects is `/etc/quilt/alpha-picks-cookies.json`. To use a different path, override `cookies_file` in the scraper's config at run time.

Cookies expire periodically. When the scraper's next run fails with an `AuthExpiredError`, repeat steps 2-4.

## Configure

Override defaults via the `config` dict when invoking the scraper through Quilt:

| Param          | Default                                                       | Notes                                  |
|----------------|---------------------------------------------------------------|----------------------------------------|
| `cookies_file` | `/etc/quilt/alpha-picks-cookies.json`                         | Absolute path on the coordinator host. |
| `picks_url`    | `https://seekingalpha.com/alpha-picks/picks/current`          |                                        |
| `timeout_ms`   | `30000`                                                       | Page load timeout.                     |
| `headless`     | `true`                                                        | Set false for debugging.               |

## Schedule

The manifest declares `schedule: "0 14 * * *"` (daily 14:00 UTC). Alpha Picks updates monthly, so any time of day works. As of this writing, Quilt's coordinator does not automatically schedule scrapers; trigger manually via the verification script in your Quilt repo (`scripts/run_scraper_once.py`) until scheduler wiring lands.

## Test

```bash
python -m venv .venv_test
.venv_test/bin/pip install beautifulsoup4 pytest
.venv_test/bin/python -m pytest tests/
```

Tests are offline — no Playwright invocation, no network. The parser fixture (`tests/fixtures/picks_page.html`) is a synthetic approximation of the real Alpha Picks layout; replace it with a real captured page the first time you scrape live to harden parser regression coverage.

## Troubleshooting

| Error class          | Cause                                                                  | Fix                                                    |
|----------------------|------------------------------------------------------------------------|--------------------------------------------------------|
| `CookieError`        | Cookies file missing, malformed JSON, or empty.                        | Re-export per "Provide cookies" above.                 |
| `AuthExpiredError`   | Cookies expired — Seeking Alpha showed a login wall.                   | Re-export per "Provide cookies" above.                 |
| `PageLoadError`      | Page didn't load in `timeout_ms`. Network slow or Seeking Alpha down.  | Check connectivity; increase `timeout_ms`.             |
| `ParseError`         | Seeking Alpha's table layout changed.                                  | Capture the current page, update the fixture, fix the parser's header aliases. |
````

- [ ] **Step 2: Re-run tests one more time as a final check**

```bash
cd /home/jkern/dev/alpha-picks-scraper
python -m venv .venv_test
.venv_test/bin/pip install --quiet beautifulsoup4 pytest
.venv_test/bin/python -m pytest tests/ -v
rm -rf .venv_test
```

Expected: 18 tests pass.

- [ ] **Step 3: Verify final tree**

```bash
cd /home/jkern/dev/alpha-picks-scraper
ls -la
```

Expected files at root: `.git/`, `.gitignore`, `README.md`, `lib/`, `quilt.yaml`, `requirements.txt`, `scraper.py`, `tests/`.

Run `find . -type f -not -path './.git/*'` to confirm the full set:
```
./scraper.py
./quilt.yaml
./requirements.txt
./.gitignore
./README.md
./lib/__init__.py
./lib/cookies.py
./lib/parser.py
./lib/fetch.py
./tests/__init__.py
./tests/test_parser.py
./tests/test_cookies.py
./tests/fixtures/picks_page.html
```

- [ ] **Step 4: Stage and commit**

```bash
cd /home/jkern/dev/alpha-picks-scraper
git add .gitignore README.md quilt.yaml scraper.py requirements.txt lib/ tests/
git commit -m "$(cat <<'EOF'
feat: rewrite as a Quilt scraper plugin

The previous standalone cron-on-a-Pi implementation is fully replaced.
The repo is now a Quilt package: quilt.yaml manifest + AlphaPicksScraper
entry point + lib/ modules (cookies loader, HTML parser, Playwright fetch).
Output is a pandas DataFrame written by Quilt's ScraperEngine to
data/custom/alpha-picks-scraper.csv. Diff/state tracking, file-based
logging, and the standalone CLI are gone — Quilt owns those concerns now.

Cookies authentication remains manual for this release: operator places
an exported cookies.json at the path declared by the cookies_file config
parameter. A generic auth flow (declarative auth: block + companion CLI
for browser-based login + dashboard re-auth UI) is planned for Spec B.

See: quilt-trader docs/superpowers/specs/2026-05-13-alpha-picks-scraper-quilt-rewrite-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git log --oneline
```

Expected: single commit `feat: rewrite as a Quilt scraper plugin`.

---

## Phase 3 — Install and verify

### Task 8: Add `packages/` to quilt-trader gitignore + scaffold

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Confirm `packages/` is not already gitignored**

Run from `/home/jkern/dev/quilt-trader`:
```bash
grep -n '^packages/' .gitignore
```
Expected: no output (not yet ignored).

- [ ] **Step 2: Add `packages/` to gitignore**

Append `packages/` as a new line to `/home/jkern/dev/quilt-trader/.gitignore`. The file's existing contents already cover `data/` and `.venv/`, so secrets and venvs are safe.

After edit, `.gitignore` should contain (key lines):
```
__pycache__/
*.pyc
*.pyo
.venv/
*.egg-info/
dist/
build/
data/
*.db
.pytest_cache/
dashboard/node_modules/
dashboard/dist/
packages/
```

- [ ] **Step 3: Commit the gitignore change**

```bash
cd /home/jkern/dev/quilt-trader
git add .gitignore
git commit -m "$(cat <<'EOF'
chore: gitignore packages/ for locally-installed Quilt packages

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Install the scraper package inside quilt-trader

This task is mostly shell commands; no test code. It produces installed artifacts in `packages/alpha-picks-scraper/` and (with the operator's help) a cookies file in `data/secrets/`.

**Files:**
- Created at install: `packages/alpha-picks-scraper/` (entire directory tree via git clone)
- Created at install: `packages/alpha-picks-scraper/.venv/` (Python virtual environment)
- Created at install: `data/secrets/alpha-picks-cookies.json` (operator-supplied)

- [ ] **Step 1: Clone the rewritten scraper into packages/**

```bash
cd /home/jkern/dev/quilt-trader
mkdir -p packages
git clone /home/jkern/dev/alpha-picks-scraper packages/alpha-picks-scraper
ls packages/alpha-picks-scraper
```

Expected: the file listing from Task 7 Step 3.

- [ ] **Step 2: Create the package venv**

```bash
python -m venv /home/jkern/dev/quilt-trader/packages/alpha-picks-scraper/.venv
```

- [ ] **Step 3: Install Python dependencies**

```bash
/home/jkern/dev/quilt-trader/packages/alpha-picks-scraper/.venv/bin/pip install -r /home/jkern/dev/quilt-trader/packages/alpha-picks-scraper/requirements.txt
```

Expected: pip installs playwright, beautifulsoup4, pandas (and transitive deps). Takes ~30-60 seconds.

- [ ] **Step 4: Install Chromium for Playwright**

```bash
/home/jkern/dev/quilt-trader/packages/alpha-picks-scraper/.venv/bin/playwright install chromium
```

Expected: Downloads ~150 MB of browser binaries into `~/.cache/ms-playwright/chromium-*`. Takes 1-3 minutes depending on connection.

⚠ **Pause point:** if running this as part of an automated execution, ask the user for confirmation before kicking off the 150 MB download.

- [ ] **Step 5: Confirm cookies file location with the operator**

The operator needs to export Seeking Alpha cookies and place them at `/home/jkern/dev/quilt-trader/data/secrets/alpha-picks-cookies.json`. This is a manual step requiring the operator's authenticated browser session.

Procedure (the operator does this on their own machine):
1. Install a cookie-export browser extension (e.g. "Cookie-Editor").
2. Sign into Seeking Alpha at `https://seekingalpha.com`.
3. Open the extension while on a Seeking Alpha page, filter to the `.seekingalpha.com` domain, "Export → JSON", save the file.
4. Transfer to the coordinator host (`scp` from laptop to Pi if running headless).
5. Place at exactly `/home/jkern/dev/quilt-trader/data/secrets/alpha-picks-cookies.json`.

Make the secrets directory (the file goes inside it):
```bash
mkdir -p /home/jkern/dev/quilt-trader/data/secrets
```

⚠ **Pause point:** confirm the file exists before continuing:
```bash
ls -la /home/jkern/dev/quilt-trader/data/secrets/alpha-picks-cookies.json
```
If it doesn't exist, stop and wait for the operator.

- [ ] **Step 6: Validate the manifest**

Use the `QuiltManifest` parser directly — this avoids depending on the `quilt` CLI script being registered in `.venv/bin/` (which requires a fresh `pip install -e .` and is brittle):

```bash
cd /home/jkern/dev/quilt-trader
.venv/bin/python -c "from sdk.manifest import QuiltManifest; m = QuiltManifest.from_file('packages/alpha-picks-scraper/quilt.yaml'); print(f'PASS: {m.name} ({m.type}) v{m.version}')"
```

Expected: `PASS: alpha-picks-scraper (scraper) v1.0.0`.

If you get `ModuleNotFoundError: No module named 'sdk'`, the quilt-trader package isn't installed in the venv. Run `.venv/bin/pip install -e .` from the quilt-trader root and retry.

If FAIL with `ManifestError`: the manifest is malformed. Re-read `quilt.yaml` against `sdk/manifest.py`'s `_parse` rules.

---

### Task 10: Add the verification script and run end-to-end

**Files:**
- Create: `scripts/run_scraper_once.py`

- [ ] **Step 1: Create the runner script**

Write `/home/jkern/dev/quilt-trader/scripts/run_scraper_once.py`:

```python
"""One-off scraper runner — manual smoke test for Spec A.

Spec A does not wire scrapers to Quilt's scheduler or expose an API
endpoint to trigger them. This script invokes ScraperEngine directly
so an operator can verify an installed scraper produces output.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coordinator.services.scraper_engine import ScraperEngine


DEFAULT_CONFIG = {
    "alpha-picks-scraper": {
        "cookies_file": str(ROOT / "data" / "secrets" / "alpha-picks-cookies.json"),
    },
}


def main(name: str = "alpha-picks-scraper") -> int:
    engine = ScraperEngine(
        packages_dir=str(ROOT / "packages"),
        output_dir=str(ROOT / "data" / "custom"),
    )
    config = DEFAULT_CONFIG.get(name)
    result = engine.run_scraper(name, "csv", config=config)
    if result.success:
        print(f"OK: wrote {result.output_path}")
        return 0
    print(f"FAIL: {result.error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
```

- [ ] **Step 2: Make the data/custom directory**

```bash
mkdir -p /home/jkern/dev/quilt-trader/data/custom
```

- [ ] **Step 3: Run the scraper once**

```bash
cd /home/jkern/dev/quilt-trader
python scripts/run_scraper_once.py
```

Expected: `OK: wrote /home/jkern/dev/quilt-trader/data/custom/alpha-picks-scraper.csv` after Chromium navigates to Seeking Alpha (5-15 seconds in headless mode).

If FAIL with `CookieError` → cookies file missing or malformed (re-do Task 9 Step 5).
If FAIL with `AuthExpiredError` → cookies expired or wrong site session (re-export from a fresh login).
If FAIL with `PageLoadError` → network/timeout issue, increase `timeout_ms` in the config dict.
If FAIL with `ParseError` → Seeking Alpha's layout changed; capture the current HTML and update parser/fixture.
If FAIL with `ModuleNotFoundError: No module named 'sdk'` → Task 3 didn't land properly; re-verify `coordinator/services/scraper_engine.py` includes the `sys.path.append` for quilt_root.

- [ ] **Step 4: Inspect the output CSV**

```bash
head -3 /home/jkern/dev/quilt-trader/data/custom/alpha-picks-scraper.csv
wc -l /home/jkern/dev/quilt-trader/data/custom/alpha-picks-scraper.csv
```

Expected:
- Header line: `symbol,company,date_picked,return_pct,sector,rating,holding_pct`
- At least one data row
- Line count ≥ 2 (header + 1 pick), typically around 10-13 (header + current picks count, which varies).

- [ ] **Step 5: Run a second time to verify clean overwrite**

```bash
cd /home/jkern/dev/quilt-trader
python scripts/run_scraper_once.py
wc -l data/custom/alpha-picks-scraper.csv
```

Expected: same exit code, same line count (within ±1 if a pick was added/removed between runs).

- [ ] **Step 6: Commit the runner script**

```bash
cd /home/jkern/dev/quilt-trader
git add scripts/run_scraper_once.py
git commit -m "$(cat <<'EOF'
feat(scripts): add manual one-off scraper runner

Spec A does not wire scrapers into the SchedulerService or expose an
API endpoint to trigger them. This script lets operators verify an
installed scraper produces output by invoking ScraperEngine directly.
Replaced by proper scheduler/API wiring in a future spec.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 7: Verify Spec A acceptance criteria**

Walk through §6.1 of the spec and check each:

- ✅ `python scripts/run_scraper_once.py` exits 0 and prints `OK: wrote .../data/custom/alpha-picks-scraper.csv`
- ✅ The CSV exists, has the 7 expected columns in the documented order, at least one row
- ✅ Re-running overwrites cleanly (no duplicate rows, no permission errors)
- ✅ `python -m pytest` passes in both repos:
  ```bash
  cd /home/jkern/dev/quilt-trader && .venv/bin/python -m pytest -q
  cd /home/jkern/dev/alpha-picks-scraper && python -m venv .venv_test && .venv_test/bin/pip install -q beautifulsoup4 pytest && .venv_test/bin/python -m pytest tests/ -q && rm -rf .venv_test
  ```
- ✅ Manifest validates: `.venv/bin/python -c "from sdk.manifest import QuiltManifest; QuiltManifest.from_file('packages/alpha-picks-scraper/quilt.yaml'); print('PASS')"` prints `PASS`

If all five check, Spec A is complete.

---

## Done state

After all ten tasks:
- `quilt-trader` has three new commits (one per upstream fix, plus one each for the gitignore and runner script).
- `alpha-picks-scraper` has one initial commit replacing the standalone implementation with a Quilt plugin.
- `packages/alpha-picks-scraper/` is installed with a working venv and Chromium browser.
- `data/secrets/alpha-picks-cookies.json` holds operator-supplied Seeking Alpha cookies.
- `data/custom/alpha-picks-scraper.csv` is being produced on demand by `scripts/run_scraper_once.py`.

Next session (Spec B): generalize cookie capture into a declarative SDK auth contract + companion CLI + dashboard re-auth UI.
