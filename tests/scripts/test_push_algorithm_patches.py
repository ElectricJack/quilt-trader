"""Unit test for the upstream PR rollout script (Phase 3 of algorithm hardening)."""
import subprocess
import sys
from pathlib import Path
import pytest


SCRIPT = Path(__file__).parents[2] / "scripts" / "push_algorithm_patches.py"


@pytest.fixture
def fake_repos(tmp_path):
    """Build a fake /tmp/quilt-algos/-equivalent directory with 3 repos."""
    root = tmp_path / "quilt-algos"
    for name in ("repo-clean", "repo-in-scope", "repo-out-of-scope"):
        d = root / name
        d.mkdir(parents=True)
        (d / "quilt.yaml").write_text("name: " + name + "\n")
        (d / "algorithm.py").write_text("# stub\n")
    return root


def _run(args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True,
    )


def test_dry_run_invokes_no_side_effects(fake_repos):
    """--dry-run prints intended actions but never invokes gh or git push."""
    result = _run(["--repos-root", str(fake_repos), "--dry-run"])
    # Script should run without errors
    assert result.returncode == 0, result.stderr
    # Output should mention each repo
    assert "repo-clean" in result.stdout
    assert "DRY-RUN" in result.stdout or "dry" in result.stdout.lower()


def test_only_filter_processes_single_repo(fake_repos):
    """--only filters to a single repo."""
    result = _run(["--repos-root", str(fake_repos), "--only", "repo-clean", "--dry-run"])
    assert result.returncode == 0, result.stderr
    assert "repo-clean" in result.stdout
    # Other repos should not be mentioned in output
    assert "repo-in-scope" not in result.stdout
    assert "repo-out-of-scope" not in result.stdout


def test_missing_repos_root_exits_nonzero(tmp_path):
    """If --repos-root doesn't exist, script exits with an error."""
    bogus = tmp_path / "does-not-exist"
    result = _run(["--repos-root", str(bogus), "--dry-run"])
    assert result.returncode != 0
    assert "not found" in result.stderr.lower() or "does not exist" in result.stderr.lower()
