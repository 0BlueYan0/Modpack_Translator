#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from modpack_translator.gui.main_window import MainWindow

_PROJECT_ROOT = Path(__file__).parent
_APP_ICON_PATH = _PROJECT_ROOT / "assets" / "icon" / "app_icon.png"


def _set_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "koudesuk.modpacktranslator.1.2.0"
        )
    except Exception:
        pass


def main():
    _set_windows_app_id()
    app = QApplication(sys.argv)
    # Fusion 是跨平台一致、且最配合 QSS / colorScheme 的基礎樣式
    app.setStyle("Fusion")
    app.setApplicationName("Minecraft模組包翻譯器")
    app.setApplicationDisplayName("Minecraft模組包翻譯器v1.3.0")
    if _APP_ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(_APP_ICON_PATH)))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
