#!/usr/bin/env bash
# install.sh — one-shot setup for the LaTeX PDF Editor
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== LaTeX PDF Editor — Setup ==="

# ── Python dependencies ───────────────────────────────────────────────────────
echo ""
echo "[1/2] Installing Python dependencies…"
pip3 install --upgrade pip --quiet
pip3 install PyQt5 PyMuPDF

echo ""
echo "[2/2] Checking for pdflatex…"
if command -v pdflatex &>/dev/null || [ -f /Library/TeX/texbin/pdflatex ]; then
    echo "      ✓ pdflatex found"
else
    echo "      ✗ pdflatex NOT found."
    echo "        Install MacTeX from https://tug.org/mactex/ (free, ~4 GB)"
    echo "        or BasicTeX from https://tug.org/mactex/morepackages.html (~100 MB)"
    echo ""
    echo "        Without pdflatex the LaTeX rendering feature will not work,"
    echo "        but PDF viewing, drawing, and erasing still work fine."
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Run the app with:"
echo "    python3 main.py"
echo "or:"
echo "    python3 main.py path/to/your.pdf"
