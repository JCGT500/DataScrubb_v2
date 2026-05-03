"""Convert README.md and LOGIC.md to single self-contained HTML files for sharing.

Outputs:
- docs/DataScrubb_Docs.html — README rendered.
- docs/DataScrubb_Logic.html — LOGIC.md rendered.

Both open in any browser, no server needed, emailable as single attachments.

Usage:
    .venv/Scripts/python.exe scripts/build_docs_html.py
"""

from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs"
SOURCES = [
    (ROOT / "README.md", OUT_DIR / "DataScrubb_Docs.html", "DataScrubb — Documentation"),
    (ROOT / "LOGIC.md", OUT_DIR / "DataScrubb_Logic.html", "DataScrubb — Metric Logic Reference"),
]

CSS = """
:root {
  --fg: #1f2937;
  --muted: #6b7280;
  --bg: #ffffff;
  --bg-alt: #f9fafb;
  --border: #e5e7eb;
  --accent: #2563eb;
  --code-bg: #f3f4f6;
  --code-fg: #111827;
}
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  font-size: 16px;
  line-height: 1.6;
  color: var(--fg);
  background: var(--bg);
  margin: 0;
  padding: 0;
}
.wrap {
  max-width: 980px;
  margin: 0 auto;
  padding: 48px 32px 96px;
}
h1, h2, h3, h4 { line-height: 1.25; margin-top: 2em; margin-bottom: 0.5em; font-weight: 600; }
h1 { font-size: 2.0em; border-bottom: 2px solid var(--border); padding-bottom: 0.3em; margin-top: 0; }
h2 { font-size: 1.5em; border-bottom: 1px solid var(--border); padding-bottom: 0.3em; }
h3 { font-size: 1.2em; }
h4 { font-size: 1.05em; color: var(--muted); }
p { margin: 0.75em 0; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
ul, ol { padding-left: 1.5em; }
li { margin: 0.25em 0; }
code, pre {
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Monaco, "Cascadia Mono", "Roboto Mono", monospace;
  font-size: 0.92em;
}
code {
  background: var(--code-bg);
  color: var(--code-fg);
  padding: 0.1em 0.35em;
  border-radius: 4px;
}
pre {
  background: var(--code-bg);
  color: var(--code-fg);
  padding: 14px 16px;
  border-radius: 8px;
  overflow-x: auto;
  border: 1px solid var(--border);
}
pre code { background: transparent; padding: 0; font-size: 0.88em; line-height: 1.5; }
table {
  border-collapse: collapse;
  width: 100%;
  margin: 1em 0;
  font-size: 0.93em;
}
th, td {
  text-align: left;
  padding: 8px 12px;
  border: 1px solid var(--border);
  vertical-align: top;
}
th { background: var(--bg-alt); font-weight: 600; }
tr:nth-child(2n) td { background: var(--bg-alt); }
blockquote {
  border-left: 4px solid var(--accent);
  background: var(--bg-alt);
  padding: 8px 14px;
  color: var(--muted);
  margin: 1em 0;
  border-radius: 0 6px 6px 0;
}
hr { border: 0; border-top: 1px solid var(--border); margin: 2em 0; }
.toc {
  background: var(--bg-alt);
  border: 1px solid var(--border);
  padding: 14px 22px;
  border-radius: 8px;
  margin: 1em 0 2em;
}
.toc ol { margin: 0; }
.banner {
  font-size: 0.85em;
  color: var(--muted);
  border: 1px dashed var(--border);
  padding: 8px 14px;
  border-radius: 6px;
  margin-bottom: 24px;
  background: var(--bg-alt);
}
@media print {
  .wrap { max-width: 100%; padding: 0; }
  pre, code { font-size: 0.78em; }
  h1 { page-break-before: auto; }
  h2 { page-break-before: avoid; page-break-after: avoid; }
  table { page-break-inside: avoid; }
}
"""

EXTENSIONS = ["fenced_code", "tables", "toc", "sane_lists", "attr_list"]


def _render_one(src: Path, out: Path, title: str) -> None:
    md_text = src.read_text(encoding="utf-8")
    body = markdown.markdown(md_text, extensions=EXTENSIONS, output_format="html5")
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{title}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>{CSS}</style>
</head>
<body>
  <div class="wrap">
    <div class="banner">
      Single-file documentation — generated from <code>{src.name}</code>.
      Save or email this file; it's fully self-contained.
    </div>
    {body}
  </div>
</body>
</html>
"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    size_kb = out.stat().st_size / 1024
    print(f"Wrote {out} ({size_kb:.1f} KB)")


def main() -> None:
    for src, out, title in SOURCES:
        if not src.exists():
            print(f"Skipping (missing): {src}")
            continue
        _render_one(src, out, title)


if __name__ == "__main__":
    main()
