"""Render SOLUTION.md to SOLUTION.pdf (A4) via headless Microsoft Edge / Chrome.

Usage (from project root):  python scripts/make_pdf.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
SRC, HTML, PDF = ROOT / "SOLUTION.md", ROOT / "scripts" / "_solution.html", ROOT / "SOLUTION.pdf"

CSS = """
@page { size: A4; margin: 14mm 15mm; }
body { font-family: 'Segoe UI', Calibri, Arial, sans-serif; font-size: 9.6pt; line-height: 1.38; color: #14213a; }
h1 { font-size: 15.5pt; color: #0b1d3a; margin: 0 0 1px; }
h1 + p { margin: 0 0 5px; color: #44526b; }
h2 { font-size: 11pt; color: #1a3666; margin: 7px 0 2px; padding-bottom: 1px; border-bottom: 1px solid #d5ddec; }
p { margin: 2px 0 3px; }
ul { margin: 2px 0 3px; padding-left: 15px; }
li { margin: 0 0 1px; }
table { border-collapse: collapse; width: 100%; margin: 3px 0 4px; font-size: 9pt; }
th, td { border: 1px solid #d5ddec; padding: 2px 5px; text-align: left; vertical-align: top; }
th { background: #eef3fb; }
code { font-family: Consolas, monospace; font-size: 8.8pt; background: #f1f4f9; padding: 0 2px; }
em { color: #44526b; }
"""


def browser() -> str:
    for p in [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              shutil.which("msedge") or "", shutil.which("chrome") or "", shutil.which("chromium") or ""]:
        if p and Path(p).exists():
            return p
    sys.exit("Edge/Chrome not found")


if __name__ == "__main__":
    body = markdown.markdown(SRC.read_text(encoding="utf-8"), extensions=["tables"])
    HTML.write_text(f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head>"
                    f"<body>{body}</body></html>", encoding="utf-8")
    subprocess.run([browser(), "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={PDF}", HTML.as_uri()], check=True, capture_output=True, timeout=120)
    HTML.unlink()
    from pypdf import PdfReader
    print(f"{PDF.name}: {len(PdfReader(str(PDF)).pages)} page(s)")
