#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from PySide6.QtWidgets import QApplication
from modpack_translator.gui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Minecraft模組包翻譯器")
    app.setApplicationDisplayName("Minecraft模組包翻譯器v1.0.0")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
