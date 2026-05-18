---
description: Render a markdown spec to HTML and open in browser
argument-hint: "[path-to-md]  (omit to render the most recent docs/superpowers/specs/*.md)"
---

Render the markdown file to a styled HTML page and open it in the user's default browser.

Run:

```
python3 scripts/render-spec.py $ARGUMENTS
```

If `$ARGUMENTS` is empty, the script defaults to the most recent file in `docs/superpowers/specs/`. After running, report the path it rendered (the script prints `rendered: <path>` to stdout) and confirm the browser was launched. Do not read, summarize, or quote the spec contents — the user is reading it in the browser.
