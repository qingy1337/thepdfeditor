"""
engine.py — LaTeX compilation engine.

Uses the system pdflatex to compile a LaTeX snippet inside a standalone
document, then converts the resulting PDF page to a transparent PNG via
PyMuPDF (no external pdftoppm / ghostscript required).
"""

import os
import shutil
import subprocess
import tempfile
import hashlib

import fitz  # PyMuPDF


# ── Locate pdflatex ──────────────────────────────────────────────────────────

def _find_pdflatex() -> str:
    candidates = [
        shutil.which("pdflatex"),
        "/Library/TeX/texbin/pdflatex",
        "/usr/local/texlive/2024/bin/universal-darwin/pdflatex",
        "/usr/local/texlive/2023/bin/universal-darwin/pdflatex",
        "/usr/local/texlive/2022/bin/universal-darwin/pdflatex",
        "/usr/texbin/pdflatex",
        "/usr/local/bin/pdflatex",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return "pdflatex"  # last-resort; will fail gracefully if missing


PDFLATEX = _find_pdflatex()

# Persistent cache directory so re-renders within a session are instant.
CACHE_DIR = os.path.join(tempfile.gettempdir(), "latex_pdf_editor_cache")
os.makedirs(CACHE_DIR, exist_ok=True)
TEMPLATE_VERSION = "border-v1"


# ── Engine class ─────────────────────────────────────────────────────────────

class LaTeXEngine:
    """Compile LaTeX snippets to PNG images."""

    DEFAULT_PACKAGES = [
        "amsmath",
        "amssymb",
        "amsfonts",
        "braket",
        "mathtools",
        "xcolor",
        "graphicx",
        "varwidth",   # allows \\ and paragraphs in standalone mode
    ]

    def __init__(self):
        self.packages: list[str] = list(self.DEFAULT_PACKAGES)

    # ── Public API ────────────────────────────────────────────────────────────

    def render(
        self,
        latex_code: str,
        font_size: int = 12,
        dpi: int = 600,
    ) -> tuple[str | None, str | None, str | None]:
        """
        Compile *latex_code* and return ``(png_path, pdf_path, None)`` on success
        or ``(None, None, error_message)`` on failure.

        The PNG has a transparent background (alpha channel) so it can
        be composited directly onto the canvas.  The PDF is also cached for
        lossless vector embedding during export.
        """
        cache_key = hashlib.md5(
            f"{TEMPLATE_VERSION}|{latex_code}|{','.join(self.packages)}|{font_size}|{dpi}".encode()
        ).hexdigest()
        png_path = os.path.join(CACHE_DIR, f"{cache_key}.png")
        pdf_path = os.path.join(CACHE_DIR, f"{cache_key}.pdf")
        if os.path.exists(png_path) and os.path.exists(pdf_path):
            return png_path, pdf_path, None

        tmpdir = tempfile.mkdtemp(prefix="latex_editor_")
        try:
            # Build full .tex document.
            # Wrap the user's content in a varwidth environment so that:
            #   • \\ creates a real line break (bare standalone body doesn't allow it)
            #   • blank lines produce paragraph breaks
            #   • display math and custom environments still work unchanged
            # varwidth auto-shrinks to the minimum required width (unlike minipage),
            # so simple one-liners stay compact.
            # \pagecolor{none} (requires xcolor, already in DEFAULT_PACKAGES) makes
            # the PDF page background transparent so show_pdf_page overlays cleanly.
            pkg_block = "\n".join(f"\\usepackage{{{p}}}" for p in self.packages)
            body = (
                f"\\begin{{varwidth}}{{16cm}}\n"
                f"{latex_code}\n"
                f"\\end{{varwidth}}"
            )
            tex = (
                f"\\documentclass[{font_size}pt,border=2pt]{{standalone}}\n"
                f"{pkg_block}\n"
                "\\begin{document}\n"
                f"{body}\n"
                "\\end{document}\n"
            )
            tex_file = os.path.join(tmpdir, "snippet.tex")
            with open(tex_file, "w", encoding="utf-8") as f:
                f.write(tex)

            # Environment: make sure TeX binaries are on PATH
            env = os.environ.copy()
            extra_paths = "/Library/TeX/texbin:/usr/local/texlive/2024/bin/universal-darwin"
            env["PATH"] = extra_paths + ":" + env.get("PATH", "")

            proc = subprocess.run(
                [PDFLATEX, "-interaction=nonstopmode", "-halt-on-error", "snippet.tex"],
                cwd=tmpdir,
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )

            pdf_file = os.path.join(tmpdir, "snippet.pdf")
            if not os.path.exists(pdf_file):
                err = self._parse_log(tmpdir, proc.stdout + proc.stderr)
                return None, None, err

            # Cache the compiled PDF for vector export
            shutil.copy2(pdf_file, pdf_path)

            # Convert first page → transparent PNG via PyMuPDF
            doc = fitz.open(pdf_file)
            page = doc[0]
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat, alpha=True)
            pix.save(png_path)
            doc.close()

            return png_path, pdf_path, None

        except subprocess.TimeoutExpired:
            return None, None, "LaTeX compilation timed out (30 s).\nCheck for infinite loops or missing packages."
        except Exception as exc:
            return None, None, str(exc)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ── Package management ────────────────────────────────────────────────────

    def add_package(self, pkg: str) -> bool:
        pkg = pkg.strip()
        if pkg and pkg not in self.packages:
            self.packages.append(pkg)
            self.clear_cache()
            return True
        return False

    def remove_package(self, pkg: str) -> bool:
        if pkg in self.packages:
            self.packages.remove(pkg)
            self.clear_cache()
            return True
        return False

    def clear_cache(self):
        for fname in os.listdir(CACHE_DIR):
            try:
                os.remove(os.path.join(CACHE_DIR, fname))
            except OSError:
                pass

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_log(tmpdir: str, fallback: str) -> str:
        log_file = os.path.join(tmpdir, "snippet.log")
        if os.path.exists(log_file):
            with open(log_file, "r", errors="replace") as f:
                lines = f.readlines()
            errors = [
                l.rstrip()
                for l in lines
                if l.startswith("!") or (l.startswith("l.") and "error" in l.lower())
            ]
            if errors:
                return "\n".join(errors[:8])
        return (fallback or "Unknown LaTeX error")[:800]
