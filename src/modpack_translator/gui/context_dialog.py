"""翻譯語境編輯器：每包 extra_prompt 的編輯介面。
存於 <模組包>/.modpack_translator/context.json，換包自動切換。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from modpack_translator.pipeline.pack_context import load_pack_context


class ContextDialog(QDialog):
    def __init__(self, game_root: Path, parent=None):
        super().__init__(parent)
        self._game_root = game_root
        self.setWindowTitle("翻譯語境")
        self.resize(520, 360)

        vbox = QVBoxLayout(self)
        hint = QLabel(
            "描述這個模組包的題材、語氣、受眾（例：「寶可夢主題整合包，"
            "任務文字口語輕鬆，玩家多為熟悉寶可夢的老玩家」）。\n"
            "會插入翻譯提示詞，只影響這個包。\n"
            "詞彙對應（X 譯為 Y）請改用「自訂用語」——那邊有強制一致與省費機制。"
        )
        hint.setWordWrap(True)
        vbox.addWidget(hint)

        self.edit = QPlainTextEdit()
        self.edit.setPlainText(load_pack_context(game_root).extra_prompt)
        vbox.addWidget(self.edit)

        btn_row = QHBoxLayout()
        save_btn = QPushButton("儲存")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(save_btn)
        btn_row.addWidget(cancel_btn)
        vbox.addLayout(btn_row)

    def _save(self) -> None:
        # 重新載入再改 extra_prompt：不覆蓋期間累積的 learned_terms
        ctx = load_pack_context(self._game_root)
        ctx.extra_prompt = self.edit.toPlainText().strip()
        ctx.save()
        self.accept()
