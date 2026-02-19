#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


def find_chromium_executable() -> str | None:
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    if not base.exists():
        return None
    candidates = sorted(base.glob("chromium-*"), reverse=True)
    for c in candidates:
        exe = c / "chrome-win" / "chrome.exe"
        if exe.exists():
            return str(exe)
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Render local HTML to PDF with Playwright footer page numbers.")
    parser.add_argument("--input", required=True, help="Input HTML path")
    parser.add_argument("--output", required=True, help="Output PDF path")
    parser.add_argument("--timeout-ms", type=int, default=2500, help="Wait timeout before PDF")
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    url = input_path.as_uri()

    footer = """
<div style="width:100%;font-size:10px;color:#5f6b7a;padding:0 14mm;text-align:center;">
  Seite <span class="pageNumber"></span> / <span class="totalPages"></span>
</div>
"""

    with sync_playwright() as p:
        exe = find_chromium_executable()
        launch_kwargs = {"headless": True}
        if exe:
            launch_kwargs["executable_path"] = exe

        browser = p.chromium.launch(**launch_kwargs)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="networkidle")
            page.wait_for_timeout(args.timeout_ms)
            page.pdf(
                path=str(output_path),
                format="A4",
                print_background=True,
                margin={"top": "16mm", "right": "14mm", "bottom": "18mm", "left": "14mm"},
                display_header_footer=True,
                header_template="<div></div>",
                footer_template=footer,
            )
        finally:
            browser.close()


if __name__ == "__main__":
    main()
