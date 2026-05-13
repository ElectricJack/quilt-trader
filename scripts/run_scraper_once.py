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
        # Persistent Chromium user-data-dir established via
        # `scripts/setup_profile.py` in the scraper repo. Required for the
        # scrape to bypass PerimeterX — exported cookies alone won't work.
        "profile_dir": str(Path.home() / ".cache" / "alpha-picks-profile"),
        # Headed mode required: PerimeterX blocks headless even with a
        # trusted profile. WSLg provides the display locally; xvfb on Pi.
        "headless": False,
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
