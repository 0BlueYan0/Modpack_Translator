"""資源包掃描：zip 包重用 jar 流程、資料夾包/config 執行期包走 in_place。

DawnCraft 案例：DawnCraft_Resources.zip 以 en_us.json 新增 quest_giver 公會
任務、paraglider 女神像對話等鍵（mod jar 內沒有）,不掃資源包整批顯示英文;
config/dcclasses/ 是帶 pack.mcmeta 的執行期包,職業選擇 GUI 文字在其中。
"""
import json
import zipfile

from modpack_translator.pipeline.patcher import backup_pack_sources
from modpack_translator.pipeline.runner import process_target
from modpack_translator.pipeline.scanner import ModpackScanner

EN = {"entity.quest_giver.quest_villager.shepherd": "Guildmaster Shepherd"}
ZH = {"entity.quest_giver.quest_villager.shepherd": "公會牧羊人"}


class _Dict:
    glossary = None

    def __init__(self, mapping):
        self.mapping = mapping

    def translate(self, text, cancel_check=None):
        return self.mapping.get(text.strip(), text)


def _make_root(tmp_path):
    (tmp_path / "mods").mkdir()
    return tmp_path


def _make_zip_pack(root, name="DawnCraft_Resources.zip", lang=None, extra=None):
    rp_dir = root / "resourcepacks"
    rp_dir.mkdir(exist_ok=True)
    zip_path = rp_dir / name
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("pack.mcmeta", json.dumps({"pack": {"pack_format": 8, "description": "x"}}))
        zf.writestr("assets/quest_giver/lang/en_us.json", json.dumps(lang or EN))
        for path, payload in (extra or {}).items():
            zf.writestr(path, payload)
    return zip_path


# ── zip 資源包 ───────────────────────────────────────────────────────

def test_zip_pack_lang_detected(tmp_path):
    root = _make_root(tmp_path)
    zip_path = _make_zip_pack(root)
    targets = ModpackScanner().scan(root, "zh_tw")
    assert len(targets) == 1
    t = targets[0]
    assert t.format == "json_lang"
    assert t.output_mode == "jar_inject"
    assert t.source_file == zip_path
    assert t.target_path_in_jar == "assets/quest_giver/lang/zh_tw.json"


def test_zip_pack_fully_translated_skipped(tmp_path):
    root = _make_root(tmp_path)
    _make_zip_pack(root, extra={"assets/quest_giver/lang/zh_tw.json": json.dumps(ZH)})
    assert ModpackScanner().scan(root, "zh_tw") == []


def test_zip_pack_process_injects_translation(tmp_path):
    root = _make_root(tmp_path)
    zip_path = _make_zip_pack(root)
    [t] = ModpackScanner().scan(root, "zh_tw")
    process_target(t, _Dict({"Guildmaster Shepherd": "公會牧羊人"}), {}, "zh_tw")
    with zipfile.ZipFile(zip_path) as zf:
        written = json.loads(zf.read("assets/quest_giver/lang/zh_tw.json").decode("utf-8"))
    assert written == ZH
    assert ModpackScanner().scan(root, "zh_tw") == []  # 冪等


def test_zip_pack_data_patchouli_detected(tmp_path):
    """資源包/datapack zip 內的 data 側 Patchouli 書也走同一條 jar 流程。"""
    root = _make_root(tmp_path)
    page = json.dumps({"name": "Wings", "pages": [{"type": "patchouli:text", "text": "A butterfly guide."}]})
    _make_zip_pack(root, extra={"data/lilwings/patchouli_books/great_butter_book/en_us/entries/wings.json": page})
    targets = ModpackScanner().scan(root, "zh_tw")
    formats = {t.format for t in targets}
    assert "patchouli_json" in formats


# ── 資料夾包與 config 執行期包 ───────────────────────────────────────

def _make_folder_pack(base, lang=None):
    lang_dir = base / "assets" / "dawncraft" / "lang"
    lang_dir.mkdir(parents=True)
    (base / "pack.mcmeta").write_text(json.dumps({"pack": {"pack_format": 8}}), encoding="utf-8")
    (lang_dir / "en_us.json").write_text(json.dumps(lang or {"class.dawncraft.ronin.desc": "A wandering swordsman."}), encoding="utf-8")
    return lang_dir


def test_folder_pack_in_resourcepacks_detected(tmp_path):
    root = _make_root(tmp_path)
    lang_dir = _make_folder_pack(root / "resourcepacks" / "SomePack")
    targets = ModpackScanner().scan(root, "zh_tw")
    assert len(targets) == 1
    t = targets[0]
    assert t.format == "pack_json_lang"
    assert t.output_mode == "in_place"
    assert t.mod_id == "dawncraft"
    assert t.target_file == lang_dir / "zh_tw.json"


def test_config_runtime_pack_detected(tmp_path):
    root = _make_root(tmp_path)
    lang_dir = _make_folder_pack(root / "config" / "dcclasses")
    targets = ModpackScanner().scan(root, "zh_tw")
    assert len(targets) == 1
    assert targets[0].format == "pack_json_lang"
    assert targets[0].target_file == lang_dir / "zh_tw.json"


def test_config_dir_without_mcmeta_ignored(tmp_path):
    root = _make_root(tmp_path)
    lang_dir = root / "config" / "notapack" / "assets" / "x" / "lang"
    lang_dir.mkdir(parents=True)
    (lang_dir / "en_us.json").write_text(json.dumps(EN), encoding="utf-8")
    assert ModpackScanner().scan(root, "zh_tw") == []


def test_folder_pack_process_writes_zh_file(tmp_path):
    root = _make_root(tmp_path)
    lang_dir = _make_folder_pack(root / "config" / "dcclasses")
    [t] = ModpackScanner().scan(root, "zh_tw")
    process_target(t, _Dict({"A wandering swordsman.": "浪跡天涯的劍士。"}), {}, "zh_tw")
    written = json.loads((lang_dir / "zh_tw.json").read_text(encoding="utf-8"))
    assert written == {"class.dawncraft.ronin.desc": "浪跡天涯的劍士。"}
    assert ModpackScanner().scan(root, "zh_tw") == []  # 冪等


def test_folder_pack_existing_translation_diffed(tmp_path):
    root = _make_root(tmp_path)
    lang_dir = _make_folder_pack(
        root / "resourcepacks" / "SomePack",
        lang={"a.key": "First line", "b.key": "Second line"},
    )
    (lang_dir / "zh_tw.json").write_text(json.dumps({"a.key": "第一行"}), encoding="utf-8")
    [t] = ModpackScanner().scan(root, "zh_tw")
    assert t.existing_file == lang_dir / "zh_tw.json"


# ── 原版覆蓋包的語言中繼鍵不送翻 ──────────────────────────────────────

def test_language_metadata_keys_are_copy_only():
    from modpack_translator.pipeline.preprocessor import classify_translation_entry
    assert classify_translation_entry("language.name", "English") == "copy"
    assert classify_translation_entry("language.region", "United States") == "copy"
    assert classify_translation_entry("language.code", "en_us") != "translate"


# ── 備份 ─────────────────────────────────────────────────────────────

def test_backup_pack_sources_zip_and_lang_dir(tmp_path):
    root = _make_root(tmp_path)
    zip_path = _make_zip_pack(root)
    _make_folder_pack(root / "config" / "dcclasses")
    targets = ModpackScanner().scan(root, "zh_tw")

    count = backup_pack_sources(root, targets)
    assert count == 2
    assert (root / "packs_bak" / "resourcepacks" / zip_path.name).is_file()
    assert (root / "packs_bak" / "config" / "dcclasses" / "assets" / "dawncraft" / "lang" / "en_us.json").is_file()

    # 已備份者不重複
    assert backup_pack_sources(root, targets) == 0


def test_backup_pack_sources_skips_mod_jars(tmp_path):
    root = _make_root(tmp_path)
    jar = root / "mods" / "somemod.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/somemod/lang/en_us.json", json.dumps(EN))
    targets = ModpackScanner().scan(root, "zh_tw")
    assert backup_pack_sources(root, targets) == 0
