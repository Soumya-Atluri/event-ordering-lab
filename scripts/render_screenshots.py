#!/usr/bin/env python3
"""Run each scenario and save a terminal-style PNG of its output to docs/screenshots/.

Requires: pip install playwright && playwright install chromium
"""
import html
import pathlib
import re
import subprocess
import sys

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
COLORS = {
    "APPLY": "#7ee787", "RELEASE": "#56d4dd", "BUFFER": "#e3b341",
    "DUP": "#d2a8ff", "ALERT": "#ff7b72", "REJECT": "#ff7b72",
    "WRITE": "#7ee787", "LOCK": "#79c0ff", "PAUSE": "#e3b341",
    "RESUME": "#e3b341", "RECV": "#8b949e",
}


def colorize(line: str) -> str:
    esc = html.escape(line)
    m = re.match(r"(\[t=\d+\]) (\w+)(\s+)(.*)", esc)
    if m:
        c = COLORS.get(m.group(2), "#c9d1d9")
        return (f'<span style="color:#6e7681">{m.group(1)}</span> '
                f'<span style="color:{c};font-weight:600">{m.group(2)}</span>'
                f'{m.group(3)}{m.group(4)}')
    esc = esc.replace("[PASS]", '<span style="color:#7ee787">[PASS]</span>')
    esc = esc.replace("[FAIL]", '<span style="color:#ff7b72">[FAIL]</span>')
    if esc.startswith("  SCENARIO") or esc.startswith("SUMMARY"):
        esc = f'<span style="color:#f0f6fc;font-weight:700">{esc}</span>'
    return esc


def page(cmd: str, text: str) -> str:
    body = "\n".join(colorize(line) for line in text.rstrip().splitlines())
    return f"""<html><body style="margin:0;background:#0d1117">
<div style="font:13px/1.45 'DejaVu Sans Mono',monospace;color:#c9d1d9;padding:18px 22px;
white-space:pre;display:inline-block;min-width:900px">
<span style="color:#7ee787">$</span> {html.escape(cmd)}
{body}</div></body></html>"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    runs = ["ordering", "missing", "stale", "chaos"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        pg = browser.new_page(device_scale_factor=2, viewport={"width": 1000, "height": 400})
        for name in runs:
            cmd = f"python3 main.py {name}"
            text = subprocess.run([sys.executable, "main.py", name], cwd=ROOT,
                                  capture_output=True, text=True).stdout
            pg.set_content(page(cmd, text))
            pg.locator("div").first.screenshot(path=str(OUT / f"{name}.png"))
            print("wrote", OUT / f"{name}.png")
        browser.close()


if __name__ == "__main__":
    main()
