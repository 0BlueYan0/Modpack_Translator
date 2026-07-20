"""伺服器同步：把「伺服器端才需要」的已翻檔從客戶端複製到伺服器實例。

背景：翻譯器對整個客戶端實例翻譯。物品名/GUI 等走 translate key 由客戶端
語言檔解析（伺服器不需要）；但任務資料、資料包字面文字是伺服器載入後同步
給客戶端顯示——連專用伺服器時必須讓伺服器那份也翻好才生效。本模組依輸出
格式挑出伺服器端檔，單向複製到伺服器實例（只增不減、覆蓋前備份）。
"""
from __future__ import annotations

# 伺服器端格式（唯一真相）。vh_config_json 經反編譯確認為客戶端載入，不列入。
SERVER_SIDE_FORMATS: frozenset[str] = frozenset({
    "ftbq_snbt",
    "ftbq_inline_snbt",
    "heracles_snbt",
    "heracles_inline_snbt",
    "bq_lang",
    "datapack_json",
})


def is_server_side(fmt: str) -> bool:
    return fmt in SERVER_SIDE_FORMATS
