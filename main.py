"""
main.py — Entry point for the LaTeX PDF Editor.

Usage
-----
    python main.py [path/to/file.pdf]
"""

import os
import sys

# Ensure sibling modules can be imported when run directly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# On macOS, Qt5's built-in HiDPI flags conflict with the OS's own Retina
# handling and cause bus errors.  Leave them unset — macOS does the right
# thing automatically.
from PyQt5.QtWidgets import QApplication

from window import EditorWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("LaTeX PDF Editor")
    app.setStyle("Fusion")

    window = EditorWindow()
    window.show()

    # If a PDF path was passed on the command line, open it immediately
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        window.open_pdf_path(sys.argv[1])

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
