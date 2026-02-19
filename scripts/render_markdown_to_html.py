#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

import markdown


STYLE = """
@page {
  size: A4;
  margin: 16mm 14mm 22mm 14mm;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: "Segoe UI", Arial, sans-serif;
  color: #1f2933;
  background: #eef3f8;
  line-height: 1.5;
}
.page {
  background: #ffffff;
  border: 1px solid #d6dde7;
  border-radius: 12px;
  padding: 20px 22px;
}
.cover {
  background: linear-gradient(135deg, #005eb8 0%, #2f7bc6 100%);
  color: #ffffff;
  border-radius: 12px;
  padding: 18px 20px;
  margin-bottom: 16px;
}
.cover .meta {
  display: grid;
  grid-template-columns: 210px 1fr;
  gap: 4px 12px;
  margin-top: 12px;
  font-size: 14px;
}
.cover-logo {
  max-height: 92px;
  width: auto;
  margin-bottom: 14px;
  background: #ffffff;
  border-radius: 8px;
  padding: 8px 10px;
}
h1, h2, h3, h4 {
  color: #0f2f4d;
  margin-top: 1.0em;
  margin-bottom: 0.4em;
  page-break-after: avoid;
  break-after: avoid-page;
}
h1 { font-size: 30px; margin-top: 0; }
h2 {
  font-size: 22px;
  border-bottom: 2px solid #e2e8f0;
  padding-bottom: 4px;
}
h3 { font-size: 18px; }
h4 { font-size: 16px; }
p, li { font-size: 13.5px; }
ul, ol { padding-left: 22px; }
h3 + ul, h3 + ol, h4 + ul, h4 + ol {
  page-break-inside: avoid;
  break-inside: avoid-page;
}
ul, ol, li, pre, table, blockquote, img {
  page-break-inside: avoid;
  break-inside: avoid-page;
}
p {
  orphans: 3;
  widows: 3;
}
code {
  font-family: Consolas, "Courier New", monospace;
  background: #f3f7fb;
  border: 1px solid #d9e4ef;
  border-radius: 4px;
  padding: 1px 5px;
  font-size: 12px;
}
pre {
  background: #0f172a;
  color: #f8fafc;
  border-radius: 8px;
  padding: 10px 12px;
  overflow: auto;
  border: 1px solid #1e293b;
}
pre code {
  background: transparent;
  border: none;
  color: inherit;
  padding: 0;
}
table {
  border-collapse: collapse;
  width: 100%;
  margin: 8px 0 12px 0;
  font-size: 12.5px;
}
th, td {
  border: 1px solid #d6dde7;
  padding: 6px 8px;
  text-align: left;
  vertical-align: top;
}
th {
  background: #f2f6fb;
  color: #0f2f4d;
}
img {
  max-width: 100%;
  height: auto;
  border: 1px solid #d6dde7;
  border-radius: 8px;
  margin: 8px 0 14px 0;
}
blockquote {
  border-left: 4px solid #9fb7d1;
  background: #f4f8fc;
  margin: 8px 0;
  padding: 8px 12px;
}
.note {
  border: 1px solid #d6dde7;
  background: #f7fbff;
  border-radius: 8px;
  padding: 10px 12px;
  margin: 10px 0 12px 0;
}
.page-break {
  page-break-before: always;
  break-before: page;
  height: 0;
}
"""


def parse_meta(md_text: str) -> dict[str, str]:
    meta = {"version": "-", "author": "-", "date": "-", "software": "-"}

    patterns = {
        "version": r"\*\*Dokumentversion:\*\*\s*(.+)",
        "author": r"\*\*Autor:\*\*\s*(.+)",
        "date": r"\*\*Datum:\*\*\s*(.+)",
        "software": r"\*\*Softwarestand:\*\*\s*(.+)",
    }

    for key, pattern in patterns.items():
        m = re.search(pattern, md_text)
        if m:
            meta[key] = m.group(1).strip()
    return meta


def build_html(title: str, md_text: str, logo_src: str) -> str:
    meta = parse_meta(md_text)
    body = markdown.markdown(
        md_text,
        extensions=["extra", "tables", "fenced_code", "sane_lists", "toc"],
        output_format="html5",
    )

    safe_title = html.escape(title)
    safe_logo = html.escape(logo_src)
    safe_version = html.escape(meta["version"])
    safe_author = html.escape(meta["author"])
    safe_date = html.escape(meta["date"])
    safe_software = html.escape(meta["software"])

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>{safe_title}</title>
  <style>{STYLE}</style>
</head>
<body>
  <div class="page">
    <section class="cover">
      <img class="cover-logo" src="{safe_logo}" alt="Videojet Logo"/>
      <h1>{safe_title}</h1>
      <div class="meta">
        <div><b>Dokumentversion</b></div><div>{safe_version}</div>
        <div><b>Softwarestand</b></div><div>{safe_software}</div>
        <div><b>Autor</b></div><div>{safe_author}</div>
        <div><b>Datum</b></div><div>{safe_date}</div>
      </div>
    </section>
    {body}
  </div>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Render Markdown to styled HTML.")
    parser.add_argument("--input", required=True, help="Input markdown file")
    parser.add_argument("--output", required=True, help="Output html file")
    parser.add_argument("--title", required=True, help="Document title")
    parser.add_argument(
        "--logo",
        default="../mas004_rpi_databridge/assets/videojet-logo.jpg",
        help="Logo path relative to output html",
    )
    args = parser.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)
    md_text = in_path.read_text(encoding="utf-8")

    html_text = build_html(args.title, md_text, args.logo)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_text, encoding="utf-8")


if __name__ == "__main__":
    main()
