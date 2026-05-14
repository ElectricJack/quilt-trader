# Alpha Picks Scraper — Quilt Plugin Rewrite (Spec A)

**Date:** 2026-05-13
**Status:** Draft
**Repos:** `alpha-picks-scraper` (rewrite), `quilt-trader` (minor cross-repo fixes)

## 1. Overview

Rewrite the existing `alpha-picks-scraper` repository as a Quilt scraper plugin. The current standalone "cron on a Pi, write picks.json + state.json" implementation is fully replaced. The repository becomes a Quilt package: a `quilt.yaml` manifest plus a `QuiltScraper` subclass that returns a pandas DataFrame. Quilt's `ScraperEngine` persists the DataFrame as CSV; downstream algorithms read it via Quilt's `DataService`.

This spec also includes three small upstream changes to `quilt-trader` that the rewrite surfaces:
- Removing a dead-but-misleading manifest field (`output.filename`).
- Fixing a real bug that prevented any production scraper from importing the SDK in its subprocess.
- Adding an optional `config` parameter to `ScraperEngine.run_scraper` so callers can pass per-instance configuration to a scraper's `on_start`.

This is **Spec A** of a planned three-spec sequence:
- **Spec A** (this doc): rewrite the scraper, ship it deployable today with a simple cookie-file-on-disk auth model.
- **Spec B** (future): add a generic auth-flow capability to the Quilt SDK (declarative `auth:` block in manifests, companion CLI for browser-based login, encrypted credential storage in the coordinator, dashboard UI for re-auth).
- **Spec C** (future): migrate this scraper to use the Spec B auth contract.

### 1.1 Goals

- The `alpha-picks-scraper` repo becomes a self-contained Quilt scraper package: clone, `pip install`, `playwright install chromium`, place cookies file, run.
- Quilt's `ScraperEngine` can execute the package and produce `data/custom/alpha-picks-scraper.csv` with the current Alpha Picks portfolio.
- The output schema is stable, columnar, and consumable by downstream algorithms without diff-state baggage.
- The upstream Quilt fixes leave the SDK and `ScraperEngine` in a state where any future scraper can be developed without re-hitting the same two bugs.

### 1.2 Non-goals

- An API endpoint for registering scrapers in the coordinator DB. No `/api/scrapers/*` routes are added; the existing `Algorithm` table is not extended for scrapers.
- Coordinator-side automatic scheduling. The manifest's `schedule:` field is declared (required by the Quilt manifest parser) but the existing `SchedulerService` is not wired to `ScraperEngine` in this spec. Triggering is manual via a verification script.
- Auth/login UX. Cookies are produced by manually exporting from a logged-in browser session and placed at a path declared in the scraper config. The dashboard does not display scraper status, expiration, or re-auth controls. All of this is Spec B.
- A `quilt dev run-scraper` local-dev command. The existing `quilt dev run` only handles algorithms; extending it for scrapers is a nice-to-have left for later.
- Failure taxonomy. The scraper raises exception classes for distinct failure modes (`AuthExpiredError`, `PageLoadError`, `ParseError`, `CookieError`), but the coordinator treats them all as generic subprocess failures. Spec B will likely formalize this.

## 2. Scraper repository — new layout

The rewrite is a clean-slate replacement. Every file currently in `/home/jkern/dev/alpha-picks-scraper/` except `.git/` is deleted. The new layout is:

```
alpha-picks-scraper/
├── quilt.yaml
├── scraper.py                  # entry point — AlphaPicksScraper(QuiltScraper)
├── requirements.txt
├── .gitignore
├── README.md
├── lib/
│   ├── __init__.py
│   ├── cookies.py              # ported from old src/alpha_picks_scraper/cookies.py
│   ├── parser.py               # ported from old src/alpha_picks_scraper/parser.py
│   └── fetch.py                # ported from old src/alpha_picks_scraper/scraper.py (renamed)
└── tests/
    ├── __init__.py
    ├── fixtures/
    │   └── picks_page.html     # ported synthetic fixture
    ├── test_parser.py
    └── test_cookies.py
```

### 2.1 Files removed from the old repo

- `pyproject.toml` (no longer pip-installable as a standalone package).
- `src/alpha_picks_scraper/__init__.py`, `__main__.py`, `config.py`, `differ.py`, `logging_setup.py`, `writer.py`.
- `scripts/install_pi.sh`, `scripts/export_cookies.md`.
- `tests/test_differ.py`, `tests/test_writer.py`.
- `data/` and `logs/` directories (Quilt owns persistence and logging now).

### 2.2 `quilt.yaml`

```yaml
name: alpha-picks-scraper
type: scraper
version: 1.0.0
description: Scrapes Seeking Alpha's "Alpha Picks" current portfolio.
entry_point: scraper.py
class_name: AlphaPicksScraper
schedule: "0 14 * * *"   # daily 14:00 UTC (Alpha Picks updates monthly; daily is plenty)
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

Note the absence of an `output:` block. After the upstream fix (§4.1), the canonical write path is `{output_dir}/{name}.{format}` and the manifest's old `output.format` field is no longer required (format is implied by `df.to_csv` for now). If/when Quilt supports multiple output formats, this section gets reintroduced; for Spec A it would be dead weight.

### 2.3 Entry-point class — `scraper.py`

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
        df = pd.DataFrame(picks, columns=[
            "symbol", "company", "date_picked",
            "return_pct", "sector", "rating", "holding_pct",
        ])
        return df
```

The class is a thin glue layer. All real logic stays in `lib/` modules where it's testable in isolation.

### 2.4 `lib/` modules

- `lib/cookies.py` — copied verbatim from `src/alpha_picks_scraper/cookies.py`. No code changes.
- `lib/parser.py` — copied verbatim from `src/alpha_picks_scraper/parser.py`. No code changes.
- `lib/fetch.py` — copied from `src/alpha_picks_scraper/scraper.py` (renamed so it doesn't collide with the package's entry-point `scraper.py`). No code changes other than the rename.

### 2.5 Output schema

One row per current pick. Column order is symbol-first to match downstream trading consumers' natural key.

| Column        | Type              | Notes                                                       |
|---------------|-------------------|-------------------------------------------------------------|
| `symbol`      | str               | Stock ticker, e.g. `TSLA`. Primary key.                     |
| `company`     | str               | e.g. `Tesla, Inc.`                                          |
| `date_picked` | str (`YYYY-MM-DD`)| Normalized by the parser.                                   |
| `return_pct`  | float             | Since picked, no `%` suffix.                                |
| `sector`      | str               | e.g. `Technology`                                           |
| `rating`      | str               | e.g. `Strong Buy`                                           |
| `holding_pct` | float             | Current portfolio weight, no `%` suffix.                    |

**No diff/change-tracking columns** (`change`, `changed_fields`) and **no history snapshots**. Each run is a full overwrite of `data/custom/alpha-picks-scraper.csv`. Downstream algorithms that care about additions/removals diff successive snapshots themselves.

### 2.6 `requirements.txt`

```
playwright>=1.42
beautifulsoup4>=4.12
pandas>=2.0
```

`playwright install chromium` is required after `pip install` to download the browser binary (~150 MB). This is documented in the README as a one-time post-install step. It is **not** part of `requirements.txt` because pip doesn't run arbitrary post-install commands.

### 2.7 Failure handling

Exceptions raised by `on_run()` propagate to Quilt's `ScraperEngine`, which captures stderr from the subprocess and reports a failed `ScraperResult`. The scraper does not retry, does not write a state file, and does not maintain a failure counter — those are framework concerns.

Exception classes raised:
- `CookieError` (from `lib/cookies.py`) — cookies file missing/malformed.
- `AuthExpiredError` (from `lib/fetch.py`) — login wall detected.
- `PageLoadError` (from `lib/fetch.py`) — navigation timeout, neither picks container nor login wall appeared.
- `ParseError` (from `lib/parser.py`) — the HTML didn't yield a parseable picks table.

The exception's class name and message appear in the captured stderr, which is enough to disambiguate failures when debugging. The coordinator does not need to import these classes.

### 2.8 Logging

Standard `logging` calls from inside `lib/fetch.py` (already present) write to stderr by default. Quilt's `ScraperEngine` captures stderr on failure; on success it's discarded. No `RotatingFileHandler`, no `logs/scraper.log`. If a per-run audit log becomes useful later, that's a Quilt-side feature (capture stdout/stderr unconditionally), not a scraper-side one.

### 2.9 `.gitignore`

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
```

No `cookies.json` entry needed because cookies live outside the package directory now.

### 2.10 README

Rewritten to describe the package as a Quilt plugin: what it produces, how to install it inside a Quilt deployment, how to obtain and place the cookies file, how to run the tests. The old "cron on a Pi" instructions are removed entirely.

## 3. Tests

### 3.1 Kept

- `tests/test_parser.py` — covers header aliasing, missing-column detection, percent/date coercion. Most fragile component (depends on Seeking Alpha's HTML layout).
- `tests/test_cookies.py` — covers the various cookie-export formats (Chrome's `expirationDate`, Firefox's `expires`, the `sameSite` string mapping, malformed JSON, missing required fields). The existing repo doesn't have this as a dedicated file; it's added here.

### 3.2 Deleted

- `tests/test_differ.py` — no differ.
- `tests/test_writer.py` — no writer.

### 3.3 Not added

- No test for `AlphaPicksScraper.on_run()` itself. It's three already-tested function calls wrapped in a DataFrame constructor; the only thing a test could exercise is the mock setup. Adding one would test the mock rather than the code.

### 3.4 How tests run

`python -m pytest tests/` from the package root. `lib/` is importable because pytest adds the rootdir to `sys.path`. No `pyproject.toml` and no `conftest.py` are needed.

## 4. Upstream Quilt fixes (`quilt-trader/`)

### 4.1 Remove the dead `output.filename` manifest field

The field is parsed into `QuiltManifest.output_filename` and passed as the third arg to `ScraperEngine.run_scraper(name, output_format, output_filename)`, but `run_scraper` ignores it — the write path is always `{output_dir}/{name}.{fmt}`. Two downstream consumers (`DataService.load_custom_data` and the `/api/data/custom/{source_name}` route) also assume the `{name}.{fmt}` convention. Honoring the field on the write side without overhauling lookup would create a worse bug than the cosmetic one we have. Honoring it everywhere is multi-file scope creep. Deleting it is the smallest honest fix.

Changes:
- `sdk/manifest.py` — remove `output_filename` from the `QuiltManifest` dataclass; remove the `output_data = data.get("output", {})` / `output_format` / `output_filename` parsing from `_parse`. The `output:` block in manifests is now ignored entirely. (Scrapers may continue to declare it for documentation purposes if desired, but it has no runtime effect.)
- `coordinator/services/scraper_engine.py` — remove `output_filename` param from `run_scraper`; the signature becomes `run_scraper(self, name: str, output_format: str) -> ScraperResult`.
- `tests/sdk/fixtures/valid_scraper.yaml` — remove the `output:` block (or leave it for documentation; either is fine since it's now ignored).
- `tests/coordinator/test_scraper_engine.py` — drop the third argument from the two `run_scraper(...)` calls.
- `tests/sdk/test_manifest.py` — remove any assertions on `output_filename` if present (verify by running tests).

### 4.2 Fix the SDK import in the subprocess

`ScraperEngine.run_scraper` builds a runner script and executes it via `subprocess.run([python, "-c", runner_script], ..., cwd=pkg_dir)`. The Python it invokes is the **package's own venv** (`pkg_dir/.venv/bin/python`). The runner script inserts `pkg_dir` into `sys.path` but **not** the quilt-trader root, so `from sdk.scraper import QuiltScraper` (which every real scraper will write) fails with `ModuleNotFoundError`. The existing `test_run_scraper` mocks `subprocess` entirely, so this bug has never been observed.

Changes:
- `coordinator/services/scraper_engine.py`:
  - `ScraperEngine.__init__` learns to detect the quilt-trader root by climbing up from `__file__` (three `os.path.dirname` levels). The constructor also accepts an optional `quilt_root: Optional[str] = None` parameter for explicit override (used in tests and unusual deploy layouts).
  - The `runner_script` template is amended:
    ```python
    f"import sys; sys.path.insert(0, '{pkg_dir}'); sys.path.append('{self._quilt_root}'); "
    ```
    `pkg_dir` stays at index 0 so package modules take precedence on name collisions; `quilt_root` appended so `sdk.*` resolves as a fallback.
- `tests/coordinator/test_scraper_engine.py` — add a new test, e.g. `test_run_scraper_real_subprocess`, that does **not** mock `subprocess`. The test:
  1. Creates a temp package dir with a minimal `quilt.yaml` and a `scraper.py` containing `from sdk.scraper import QuiltScraper; class S(QuiltScraper): def on_run(self): import pandas as pd; return pd.DataFrame({"a":[1]})`.
  2. Creates a real venv inside the package dir, installs `pandas` and `pyyaml` into it.
  3. Instantiates `ScraperEngine` pointing at it and calls `run_scraper`.
  4. Asserts `result.success is True` and that the output CSV exists.
  This is the test that would have caught the original bug. (Acceptable for it to be marked `@pytest.mark.slow` if the venv setup is too slow for the default test run.)

### 4.3 Add an optional `config` parameter to `run_scraper`

The current `run_scraper` calls `scraper.on_start({})` — always an empty dict. This means scrapers can never receive non-default configuration, making the manifest's `config.parameters` defaults the only effective values. To honor cookie-file overrides (and any future per-instance config), `run_scraper` needs a way for callers to pass config in.

Changes:
- `coordinator/services/scraper_engine.py`:
  - `run_scraper` signature becomes `run_scraper(self, name: str, output_format: str, config: Optional[dict] = None) -> ScraperResult`.
  - The config dict is embedded into the runner script using `json.dumps(config or {})` (not `repr`) — JSON is a strict subset of Python literals for the types we accept (str/int/float/bool/None/list/dict), is unambiguous, and avoids exec-injection edge cases with strings containing quotes. The runner script gains an `import json` at the top and changes `scraper.on_start({})` to `scraper.on_start(json.loads({json_config_literal!r}))`.
- Test: extend `test_run_scraper` (or add a new test) that passes a non-empty config dict and asserts (via a fixture scraper that records the dict it received) that `on_start` saw the same dict back. Combine with §4.2's non-mocked subprocess test for end-to-end coverage.

## 5. Install plan (`quilt-trader/packages/alpha-picks-scraper/`)

Manual sequence; no CLI automation in this spec.

1. **Create packages dir:** `mkdir -p /home/jkern/dev/quilt-trader/packages`
2. **Clone the rewritten scraper:** `git clone /home/jkern/dev/alpha-picks-scraper /home/jkern/dev/quilt-trader/packages/alpha-picks-scraper` (local clone for first install; remote URL once the repo is pushed).
3. **Create the package venv:** `python -m venv /home/jkern/dev/quilt-trader/packages/alpha-picks-scraper/.venv`
4. **Install Python deps:** `/home/jkern/dev/quilt-trader/packages/alpha-picks-scraper/.venv/bin/pip install -r /home/jkern/dev/quilt-trader/packages/alpha-picks-scraper/requirements.txt`
5. **Install chromium for Playwright:** `/home/jkern/dev/quilt-trader/packages/alpha-picks-scraper/.venv/bin/playwright install chromium` (~150 MB download, one-time per venv)
6. **Place cookies file.** For this install, use `/home/jkern/dev/quilt-trader/data/secrets/alpha-picks-cookies.json` (writable without root, lives alongside other Quilt data, gitignored). The operator obtains the file by exporting cookies from a logged-in Seeking Alpha browser session using a standard cookie-export browser extension (e.g., Cookie-Editor → "Export → JSON"). Export should be **filtered to the `.seekingalpha.com` domain** — exporting all cookies from the browser produces a multi-MB file containing unrelated tracking cookies that the scraper doesn't need (and that would unnecessarily expose other site sessions). The `cookies_file` config parameter is overridden at run time to point at this path (see §6).
7. **Add `packages/` and `data/secrets/` to `quilt-trader/.gitignore`** if not already present, so packages and secrets aren't accidentally committed.
8. **Validate the manifest:** from quilt-trader root, `python -m sdk.cli.main dev validate --path packages/alpha-picks-scraper` — should print `PASS: alpha-picks-scraper (scraper) v1.0.0 is valid`.

## 6. Verification

Add `scripts/run_scraper_once.py` to quilt-trader:

```python
"""One-off scraper runner — manual smoke test for Spec A.

Replaced by proper scheduler/API wiring in a future spec.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coordinator.services.scraper_engine import ScraperEngine

def main(name: str = "alpha-picks-scraper"):
    engine = ScraperEngine(
        packages_dir=str(ROOT / "packages"),
        output_dir=str(ROOT / "data" / "custom"),
    )
    result = engine.run_scraper(name, "csv")
    if result.success:
        print(f"OK: wrote {result.output_path}")
        return 0
    print(f"FAIL: {result.error}", file=sys.stderr)
    return 1

if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
```

Manual override of `cookies_file` is needed because step 6 in §5 puts cookies somewhere other than the manifest default. The `config` parameter added to `run_scraper` in §4.3 makes this possible. Extend the verification script:

```python
result = engine.run_scraper(name, "csv", config={
    "cookies_file": str(ROOT / "data" / "secrets" / "alpha-picks-cookies.json"),
})
```

### 6.1 Acceptance criteria

- `python scripts/run_scraper_once.py` exits 0 and prints `OK: wrote .../data/custom/alpha-picks-scraper.csv`.
- The CSV exists, has the 7 expected columns in the documented order, and at least one row.
- Re-running the script overwrites the CSV cleanly (no duplicate rows, no permission errors).
- `python -m pytest` in both `quilt-trader/` and `packages/alpha-picks-scraper/` passes (existing tests + the new ones from §3 and §4.2).
- `quilt dev validate --path packages/alpha-picks-scraper` prints `PASS`.

## 7. Open items deferred to Spec B+

- Coordinator-side trigger: the manifest's `schedule:` is declarative only; no scheduler wiring is added.
- Auth flow: cookie expiration is currently a manual re-export problem. Spec B introduces a generic SDK auth contract, a companion CLI for browser-based login, encrypted credential storage, and dashboard re-auth UI.
- API/database for scrapers: no `Scraper` table, no `/api/scrapers/*` routes. Spec B may introduce these.
- Quilt's `DataService` and `/api/data/custom/{source_name}` route still use the `{name}.{fmt}` convention; if a future scraper needs a custom filename, lookup needs to be made manifest-aware.
- A `quilt dev run-scraper` local-dev command remains a nice-to-have.
