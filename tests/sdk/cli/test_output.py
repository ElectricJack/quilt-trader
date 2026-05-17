import json
import pytest
from sdk.cli.output import print_json, print_table, print_status, fail


def test_print_json_emits_pretty(capsys):
    print_json({"a": 1, "b": [2, 3]})
    out = capsys.readouterr().out
    assert json.loads(out) == {"a": 1, "b": [2, 3]}


def test_print_table_emits_headers_and_rows(capsys):
    print_table([
        {"id": "abc", "name": "alice"},
        {"id": "def", "name": "bob"},
    ], columns=["id", "name"])
    out = capsys.readouterr().out
    assert "abc" in out
    assert "alice" in out
    assert "bob" in out


def test_print_table_handles_empty_rows(capsys):
    print_table([], columns=["id"])
    out = capsys.readouterr().out
    assert "no rows" in out.lower() or "0 rows" in out.lower()


def test_print_status_goes_to_stderr(capsys):
    print_status("starting...")
    cap = capsys.readouterr()
    assert "starting..." in cap.err
    assert "starting..." not in cap.out


def test_fail_writes_stderr_and_raises_systemexit(capsys):
    with pytest.raises(SystemExit) as exc:
        fail(3, "coordinator unreachable")
    assert exc.value.code == 3
    cap = capsys.readouterr()
    assert "coordinator unreachable" in cap.err
