"""自訂用語編輯器：使用者級 custom_glossary.json 的表格編輯介面。
底層就是一份可手動編輯的 JSON（Path.home()/.modpack_translator/），
表格只是它的編輯介面。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from modpack_translator.pipeline.glossary import (
    default_custom_glossary_path,
    load_custom_terms,
    save_custom_terms,
)


class CustomGlossaryDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("自訂用語")
        self.resize(560, 440)

        vbox = QVBoxLayout(self)
        hint = QLabel(
            "英文詞 → 繁中譯名，優先序最高（可覆蓋官方用語庫與模組名譯名）。\n"
            "譯名留空 ＝ 保留英文（停用該詞條，可用來壓掉不想要的預建譯名）。\n"
            "詞彙對應請放這裡；題材/語氣描述請放「翻譯語境」。"
        )
        hint.setWordWrap(True)
        vbox.addWidget(hint)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["英文原文", "繁中譯名"])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        vbox.addWidget(self.table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("新增列")
        add_btn.clicked.connect(lambda: self._append_row())
        del_btn = QPushButton("刪除選取列")
        del_btn.clicked.connect(self._delete_selected_rows)
        save_btn = QPushButton("儲存")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        btn_row.addWidget(save_btn)
        btn_row.addWidget(cancel_btn)
        vbox.addLayout(btn_row)

        for en, zh in load_custom_terms(default_custom_glossary_path()).items():
            self._append_row(en, zh)

    def _append_row(self, en: str = "", zh: str = "") -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(en))
        self.table.setItem(row, 1, QTableWidgetItem(zh))

    def _delete_selected_rows(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)

    def _save(self) -> None:
        terms: dict[str, str] = {}
        for row in range(self.table.rowCount()):
            en_item = self.table.item(row, 0)
            zh_item = self.table.item(row, 1)
            en = (en_item.text() if en_item else "").strip()
            zh = (zh_item.text() if zh_item else "").strip()
            if en:
                terms[en] = zh
        save_custom_terms(default_custom_glossary_path(), terms)
        self.accept()
