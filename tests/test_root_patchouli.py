"""遊戲根目錄 Patchouli 外部書（<game>/patchouli_books/<book>/<locale>/**.json）。

Vault Hunters 主指南（the_vault_main_guide，62 頁）即此形態：只有 en_us
樹、Patchouli 依遊戲語言載入對應 locale 資料夾並逐檔 fallback en_us。
掃描器先前只掃 jar 內 patchouli_books，整本書從未被翻譯。
"""
import json

from modpack_translator.pipeline.runner import process_target
from modpack_translator.pipeline.scanner import ModpackScanner


class _Dict:
    glossary = None

    def __init__(self, mapping):
        self.mapping = mapping

    def translate(self, text, cancel_check=None):
        return self.mapping.get(text.strip(), text)


class _Boom:
    glossary = None

    def translate(self, text, cancel_check=None):
        raise AssertionError("translator.translate must NOT be called on idempotent re-run")


_PAGE = {
    "sortnum": 1,
    "name": "Introduction",
    "icon": "create:brass_hand",
    "category": "patchouli:getting_started_category",
    "pages": [
        {"type": "patchouli:text", "text": "Vault Hunters is a progression based game."},
    ],
}


def _make_book(tmp_path):
    book = tmp_path / "patchouli_books" / "main_guide"
    (book / "en_us" / "entries").mkdir(parents=True)
    (book / "book.json").write_text(
        json.dumps({"name": "The Guide", "landing_text": "Welcome"}), encoding="utf-8"
    )
    (book / "en_us" / "entries" / "intro.json").write_text(
        json.dumps(_PAGE), encoding="utf-8"
    )
    return tmp_path


def _book_targets(root, include_translated=False):
    targets = ModpackScanner().scan(root, "zh_tw", include_translated=include_translated)
    return [
        t for t in targets
        if t.format == "patchouli_json" and t.output_mode == "in_place"
    ]


def test_scan_emits_root_book_pages(tmp_path):
    root = _make_book(tmp_path)
    targets = _book_targets(root)
    assert len(targets) == 1
    [t] = targets
    assert t.mod_id == "main_guide"
    assert t.source_file == root / "patchouli_books" / "main_guide" / "en_us" / "entries" / "intro.json"
    assert t.target_file == root / "patchouli_books" / "main_guide" / "zh_tw" / "entries" / "intro.json"
    assert t.existing_file is None
    # book.json 是跨語言共用檔，不得成為來源
    assert all("book.json" not in str(t.source_file) for t in targets)


def test_process_writes_translated_page_preserving_structure(tmp_path):
    root = _make_book(tmp_path)
    [t] = _book_targets(root)
    process_target(t, _Dict({
        "Introduction": "簡介",
        "Vault Hunters is a progression based game.": "Vault Hunters 是一款進度導向的遊戲。",
    }), {}, "zh_tw")
    out_file = root / "patchouli_books" / "main_guide" / "zh_tw" / "entries" / "intro.json"
    assert out_file.exists()
    out = json.loads(out_file.read_text(encoding="utf-8"))
    assert out["name"] == "簡介"
    assert out["pages"][0]["text"] == "Vault Hunters 是一款進度導向的遊戲。"
    # 結構欄位原樣保留
    assert out["icon"] == "create:brass_hand"
    assert out["category"] == "patchouli:getting_started_category"
    assert out["pages"][0]["type"] == "patchouli:text"
    assert out["sortnum"] == 1
    # 來源 en_us 頁不得被動到
    src = json.loads(
        (root / "patchouli_books" / "main_guide" / "en_us" / "entries" / "intro.json")
        .read_text(encoding="utf-8")
    )
    assert src == _PAGE


def test_translated_book_is_idempotent(tmp_path):
    root = _make_book(tmp_path)
    cache: dict[str, str] = {}
    [t] = _book_targets(root)
    process_target(t, _Dict({
        "Introduction": "簡介",
        "Vault Hunters is a progression based game.": "Vault Hunters 是一款進度導向的遊戲。",
    }), cache, "zh_tw")
    # 已全數翻譯 → 重掃無目標
    assert _book_targets(root) == []
    # 同 cache 重跑不送翻、檔案不變
    out_file = root / "patchouli_books" / "main_guide" / "zh_tw" / "entries" / "intro.json"
    before = out_file.read_bytes()
    process_target(t, _Boom(), cache, "zh_tw")
    assert out_file.read_bytes() == before


def test_partial_existing_page_only_fills_missing(tmp_path):
    root = _make_book(tmp_path)
    zh_page = dict(_PAGE, name="簡介")  # 標題已翻、內文仍英文
    zh_file = root / "patchouli_books" / "main_guide" / "zh_tw" / "entries" / "intro.json"
    zh_file.parent.mkdir(parents=True)
    zh_file.write_text(json.dumps(zh_page, ensure_ascii=False), encoding="utf-8")

    [t] = _book_targets(root)
    assert t.existing_file == zh_file
    # mapping 只含內文；若工具誤送已翻標題，"Introduction" 會退回英文
    process_target(t, _Dict({
        "Vault Hunters is a progression based game.": "Vault Hunters 是一款進度導向的遊戲。",
    }), {}, "zh_tw")
    out = json.loads(zh_file.read_text(encoding="utf-8"))
    assert out["name"] == "簡介"
    assert out["pages"][0]["text"] == "Vault Hunters 是一款進度導向的遊戲。"
