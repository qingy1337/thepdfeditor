"""
pkgdialog.py — LaTeX package management dialog.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QVBoxLayout,
)


class PackageDialog(QDialog):
    """Dialog to add / remove LaTeX packages used during compilation."""

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Manage LaTeX Packages")
        self.setMinimumWidth(420)
        self.setMinimumHeight(360)
        self._build_ui()
        self._refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        info = QLabel(
            "Packages listed here are included in every LaTeX compilation.\n"
            "Add a package name (without \\usepackage{}) and press Add."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#444; font-size:11px;")
        layout.addWidget(info)

        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget { font-family: Menlo, monospace; font-size: 12px; }"
        )
        self._list.setAlternatingRowColors(True)
        layout.addWidget(self._list)

        # Add row
        add_row = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setPlaceholderText("e.g. pgfplots, tikz, siunitx …")
        self._input.returnPressed.connect(self._add)
        add_row.addWidget(self._input)

        add_btn = QPushButton("Add")
        add_btn.setFixedWidth(70)
        add_btn.setStyleSheet(
            "QPushButton{background:#0078d4;color:white;border:none;"
            "border-radius:3px;padding:4px 10px;}"
            "QPushButton:hover{background:#005fa3;}"
        )
        add_btn.clicked.connect(self._add)
        add_row.addWidget(add_btn)
        layout.addLayout(add_row)

        # Remove button
        rm_btn = QPushButton("Remove Selected Package")
        rm_btn.setStyleSheet(
            "QPushButton{background:#c0392b;color:white;border:none;"
            "border-radius:3px;padding:5px;}"
            "QPushButton:hover{background:#a93226;}"
        )
        rm_btn.clicked.connect(self._remove)
        layout.addWidget(rm_btn)

        # Reset button
        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.setStyleSheet(
            "QPushButton{background:#7f8c8d;color:white;border:none;"
            "border-radius:3px;padding:5px;}"
            "QPushButton:hover{background:#636e72;}"
        )
        reset_btn.clicked.connect(self._reset)
        layout.addWidget(reset_btn)

        # Close
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(self.accept)
        layout.addWidget(btns)

    def _refresh(self):
        self._list.clear()
        for pkg in self.engine.packages:
            item = QListWidgetItem(pkg)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._list.addItem(item)

    def _add(self):
        raw = self._input.text().strip()
        if not raw:
            return
        # Allow comma-separated entry
        pkgs = [p.strip() for p in raw.split(",") if p.strip()]
        for pkg in pkgs:
            self.engine.add_package(pkg)
        self._input.clear()
        self._refresh()

    def _remove(self):
        item = self._list.currentItem()
        if item is None:
            QMessageBox.information(self, "Remove Package", "Select a package first.")
            return
        pkg = item.text()
        self.engine.remove_package(pkg)
        self._refresh()

    def _reset(self):
        reply = QMessageBox.question(
            self,
            "Reset Packages",
            "Reset to the default package list?\nThis clears the render cache.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            from engine import LaTeXEngine
            self.engine.packages = list(LaTeXEngine.DEFAULT_PACKAGES)
            self.engine.clear_cache()
            self._refresh()
