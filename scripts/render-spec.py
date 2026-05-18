#!/usr/bin/env python3
"""Render a markdown file to HTML and open it in the default browser.

Usage:
    python scripts/render-spec.py <path-to-markdown>
    python scripts/render-spec.py            # renders most recent docs/superpowers/specs/*.md
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown


CSS = """
:root {
    --bg: #0d1117;
    --fg: #c9d1d9;
    --muted: #8b949e;
    --accent: #58a6ff;
    --border: #30363d;
    --code-bg: #161b22;
    --hr: #21262d;
}
* { box-sizing: border-box; }
body {
    margin: 0;
    background: var(--bg);
    color: var(--fg);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    font-size: 15px;
    line-height: 1.6;
}
main {
    max-width: 880px;
    margin: 0 auto;
    padding: 48px 32px 96px;
}
h1, h2, h3, h4 {
    color: #f0f6fc;
    border-bottom: 1px solid var(--hr);
    padding-bottom: 6px;
    margin-top: 32px;
}
h1 { font-size: 2em; }
h2 { font-size: 1.5em; }
h3 { font-size: 1.2em; border-bottom: none; }
h4 { font-size: 1.05em; border-bottom: none; color: var(--muted); }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
code {
    background: var(--code-bg);
    padding: 2px 5px;
    border-radius: 4px;
    font-family: "SF Mono", Menlo, Consolas, monospace;
    font-size: 0.88em;
}
pre {
    background: var(--code-bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 14px 16px;
    overflow-x: auto;
}
pre code { background: none; padding: 0; }
table {
    border-collapse: collapse;
    margin: 12px 0;
    width: 100%;
}
th, td {
    border: 1px solid var(--border);
    padding: 8px 12px;
    text-align: left;
}
th { background: #161b22; }
tr:nth-child(even) td { background: #0f141b; }
blockquote {
    border-left: 3px solid var(--border);
    color: var(--muted);
    margin: 16px 0;
    padding: 4px 16px;
}
hr {
    border: none;
    border-top: 1px solid var(--hr);
    margin: 32px 0;
}
ul, ol { padding-left: 28px; }
li { margin: 4px 0; }
.title-meta {
    color: var(--muted);
    font-size: 13px;
    margin-bottom: 24px;
}
"""

HTML_SHELL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<main>
<div class="title-meta">{path}</div>
{body}
</main>
</body>
</html>
"""


def find_latest_spec() -> Path:
    repo_root = Path(__file__).resolve().parent.parent
    specs_dir = repo_root / "docs" / "superpowers" / "specs"
    candidates = list(specs_dir.glob("*.md"))
    if not candidates:
        sys.exit(f"no spec files found in {specs_dir}")
    # Sort by modification time so the newest *actually-edited* file wins
    # when multiple specs share a date prefix.
    return max(candidates, key=lambda p: p.stat().st_mtime)


def render(md_path: Path) -> Path:
    text = md_path.read_text(encoding="utf-8")
    body = markdown.markdown(
        text,
        extensions=["fenced_code", "tables", "toc", "sane_lists", "codehilite"],
        extension_configs={"codehilite": {"noclasses": True, "pygments_style": "monokai"}},
    )
    html = HTML_SHELL.format(
        title=md_path.stem,
        css=CSS,
        path=str(md_path),
        body=body,
    )
    out = Path(tempfile.gettempdir()) / f"{md_path.stem}.html"
    out.write_text(html, encoding="utf-8")
    return out


def open_in_browser(html_path: Path) -> None:
    for cmd in (["xdg-open"], ["wslview"], ["cmd.exe", "/c", "start", ""]):
        try:
            subprocess.run([*cmd, str(html_path)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    print(f"could not launch browser; open manually: file://{html_path}")


def main() -> None:
    if len(sys.argv) > 1:
        md_path = Path(sys.argv[1]).resolve()
        if not md_path.exists():
            sys.exit(f"not found: {md_path}")
    else:
        md_path = find_latest_spec()
    html_path = render(md_path)
    print(f"rendered: {html_path}")
    open_in_browser(html_path)


if __name__ == "__main__":
    main()
