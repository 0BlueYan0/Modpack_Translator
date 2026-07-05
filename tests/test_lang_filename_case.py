"""大小寫語言檔名回歸：mod 出貨大寫 zh_TW.json 時,工具必須把譯文寫進
遊戲讀得到的正規小寫 zh_tw.json,並重用既有(大寫)譯文避免重花付費 API。

背景:Minecraft 語言碼為小寫 zh_tw,且 jar 內查找(ZipFile.getEntry)區分
大小寫,只找 assets/<ns>/lang/zh_tw.json。mod 若只出貨大寫 zh_TW.json,
遊戲整包 fallback 成英文(oritech 實例)。
"""
from __future__ import annotations

import json
import zipfile

from modpack_translator.pipeline.runner import process_target
from modpack_translator.pipeline.scanner import ModpackScanner


class _FixedTranslator:
    """對任何輸入回傳固定譯文的假 translator。"""

    glossary = None

    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[str] = []

    def translate(self, text: str, cancel_check=None) -> str:
        self.calls.append(text)
        return self.reply


def _make_jar(path, files: dict[str, dict]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, payload in files.items():
            zf.writestr(name, json.dumps(payload, ensure_ascii=False))


# ── jar json_lang:寫入目標一律小寫 ──────────────────────────────────

def test_jar_write_target_is_lowercase_when_mod_ships_uppercase(tmp_path):
    jar = tmp_path / "oritech-1.0.jar"
    _make_jar(jar, {
        "assets/oritech/lang/en_us.json": {"greeting": "Hello world", "farewell": "Goodbye friend"},
        "assets/oritech/lang/zh_TW.json": {"greeting": "哈囉世界"},  # farewell 缺 → 需翻譯
    })
    targets = ModpackScanner()._scan_jar(jar, "zh_tw", None)
    assert len(targets) == 1
    assert targets[0].target_path_in_jar == "assets/oritech/lang/zh_tw.json"


def test_process_target_writes_full_lowercase_file_seeded_from_uppercase(tmp_path):
    jar = tmp_path / "oritech-1.0.jar"
    _make_jar(jar, {
        "assets/oritech/lang/en_us.json": {"greeting": "Hello world", "farewell": "Goodbye friend"},
        "assets/oritech/lang/zh_TW.json": {"greeting": "哈囉世界"},
    })
    [target] = ModpackScanner()._scan_jar(jar, "zh_tw", None)
    process_target(target, _FixedTranslator("再見朋友"), {}, "zh_tw")

    with zipfile.ZipFile(jar) as zf:
        names = set(zf.namelist())
        assert "assets/oritech/lang/zh_tw.json" in names  # 遊戲讀得到的小寫檔
        out = json.loads(zf.read("assets/oritech/lang/zh_tw.json").decode("utf-8-sig"))
    assert out["greeting"] == "哈囉世界"   # 重用既有大寫譯文
    assert out["farewell"] == "再見朋友"   # 新翻譯


# ── 完整大寫譯檔:即使無 diff 也要遷移成小寫 ─────────────────────────

def test_complete_uppercase_still_emits_migration_target(tmp_path):
    jar = tmp_path / "oritech-1.0.jar"
    _make_jar(jar, {
        "assets/oritech/lang/en_us.json": {"greeting": "Hello world"},
        "assets/oritech/lang/zh_TW.json": {"greeting": "哈囉世界"},  # 已完整,diff 為空
    })
    targets = ModpackScanner()._scan_jar(jar, "zh_tw", None)
    assert len(targets) == 1
    assert targets[0].target_path_in_jar == "assets/oritech/lang/zh_tw.json"


def test_complete_uppercase_migration_creates_lowercase_without_llm(tmp_path):
    jar = tmp_path / "oritech-1.0.jar"
    _make_jar(jar, {
        "assets/oritech/lang/en_us.json": {"greeting": "Hello world"},
        "assets/oritech/lang/zh_TW.json": {"greeting": "哈囉世界"},
    })
    [target] = ModpackScanner()._scan_jar(jar, "zh_tw", None)
    tr = _FixedTranslator("不應被呼叫")
    process_target(target, tr, {}, "zh_tw")

    with zipfile.ZipFile(jar) as zf:
        out = json.loads(zf.read("assets/oritech/lang/zh_tw.json").decode("utf-8-sig"))
    assert out["greeting"] == "哈囉世界"
    assert tr.calls == []  # 純遷移,零 API


# ── 既有小寫檔:行為不變(不得回歸) ─────────────────────────────────

def test_existing_lowercase_target_unchanged(tmp_path):
    jar = tmp_path / "mod-1.0.jar"
    _make_jar(jar, {
        "assets/mod/lang/en_us.json": {"greeting": "Hello world", "farewell": "Goodbye friend"},
        "assets/mod/lang/zh_tw.json": {"greeting": "哈囉世界"},
    })
    targets = ModpackScanner()._scan_jar(jar, "zh_tw", None)
    assert len(targets) == 1
    assert targets[0].target_path_in_jar == "assets/mod/lang/zh_tw.json"


# ── jar patchouli:寫入語系目錄一律小寫 ──────────────────────────────

def test_patchouli_write_target_locale_is_lowercase(tmp_path):
    jar = tmp_path / "guidebook-1.0.jar"
    en = "assets/mod/patchouli_books/book/en_us/entries/basics.json"
    up = "assets/mod/patchouli_books/book/zh_TW/entries/basics.json"
    page = {"name": "Machine basics", "pages": [{"type": "text", "text": "Long guide text about the crushing machine here"}]}
    _make_jar(jar, {en: page, up: page})  # 大寫語系存在但仍是英文 → 需翻譯
    targets = ModpackScanner()._scan_jar(jar, "zh_tw", None)
    patchouli = [t for t in targets if t.format == "patchouli_json"]
    assert len(patchouli) == 1
    assert patchouli[0].target_path_in_jar == "assets/mod/patchouli_books/book/zh_tw/entries/basics.json"


# ── 本地 flat lang 檔(KubeJS 等):寫入目標一律小寫 ─────────────────

def test_kubejs_local_write_target_is_lowercase(tmp_path):
    lang = tmp_path / "kubejs" / "assets" / "mymod" / "lang"
    lang.mkdir(parents=True)
    (lang / "en_us.json").write_text(
        json.dumps({"greeting": "Hello world", "farewell": "Goodbye friend"}), encoding="utf-8")
    (lang / "zh_TW.json").write_text(
        json.dumps({"greeting": "哈囉世界"}, ensure_ascii=False), encoding="utf-8")
    targets = ModpackScanner()._scan_kubejs(tmp_path, "zh_tw", None)
    assert len(targets) == 1
    assert targets[0].target_file is not None
    assert targets[0].target_file.name == "zh_tw.json"
