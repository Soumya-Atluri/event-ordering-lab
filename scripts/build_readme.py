#!/usr/bin/env python3
"""Regenerate README.md from scripts/README.template.md using real program output.

Runs each scenario and the unit tests, saves the raw logs to docs/, and pastes
the captured output into the README so the evidence always matches the code.
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def run(*args: str) -> str:
    r = subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True)
    return (r.stdout + r.stderr).strip()


def main() -> None:
    blocks = {name: f"$ python3 main.py {name}\n" + run("main.py", name).strip()
              for name in ("ordering", "missing", "stale", "chaos")}
    tests = run("-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v")
    blocks["tests"] = "$ python3 -m unittest discover -s tests -t . -v\n" + tests

    (ROOT / "docs" / "sample-output.txt").write_text(run("main.py") + "\n")
    (ROOT / "docs" / "test-output.txt").write_text(tests + "\n")

    text = (ROOT / "scripts" / "README.template.md").read_text()
    for key, value in blocks.items():
        text = text.replace("{{" + key + "}}", value)
    (ROOT / "README.md").write_text(text)
    print("README.md regenerated")


if __name__ == "__main__":
    main()
