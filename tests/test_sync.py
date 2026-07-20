from pathlib import Path

from modpack_translator.pipeline import sync
from modpack_translator.pipeline.scanner import TranslationTarget


def test_server_side_formats_membership():
    assert sync.is_server_side("ftbq_snbt")
    assert sync.is_server_side("ftbq_inline_snbt")
    assert sync.is_server_side("heracles_snbt")
    assert sync.is_server_side("heracles_inline_snbt")
    assert sync.is_server_side("bq_lang")
    assert sync.is_server_side("datapack_json")


def test_client_side_formats_excluded():
    for fmt in (
        "json_lang", "legacy_lang", "pack_json_lang", "pack_legacy_lang",
        "patchouli_json", "oracle_mdx", "oracle_meta", "guideme_md",
        "citadel_book_txt", "rct_names", "kubejs_json", "vh_config_json",
    ):
        assert not sync.is_server_side(fmt), fmt


def test_manifest_path(tmp_path):
    assert sync.manifest_path(tmp_path) == tmp_path / ".modpack_translator" / "sync_manifest.json"


def test_load_manifest_missing_returns_empty(tmp_path):
    assert sync.load_manifest(tmp_path) == []


def test_merge_and_load_roundtrip(tmp_path):
    sync.merge_manifest(tmp_path, [
        sync.ManifestEntry("config/ftbquests/quests/a.snbt", "ftbq_inline_snbt"),
    ])
    got = sync.load_manifest(tmp_path)
    assert got == [sync.ManifestEntry("config/ftbquests/quests/a.snbt", "ftbq_inline_snbt")]


def test_merge_is_union_dedup_by_rel_path(tmp_path):
    sync.merge_manifest(tmp_path, [sync.ManifestEntry("a.snbt", "ftbq_snbt")])
    sync.merge_manifest(tmp_path, [
        sync.ManifestEntry("a.snbt", "ftbq_snbt"),          # 重複 → 去重
        sync.ManifestEntry("b.json", "datapack_json"),      # 新增
    ])
    got = {e.rel_path for e in sync.load_manifest(tmp_path)}
    assert got == {"a.snbt", "b.json"}


def test_load_manifest_corrupt_returns_empty(tmp_path):
    p = sync.manifest_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ not json", encoding="utf-8")
    assert sync.load_manifest(tmp_path) == []


def _mk_target(fmt, target_file):
    return TranslationTarget(
        source_file=target_file, path_in_jar=None, mod_id="x",
        format=fmt, output_mode="in_place", target_file=target_file,
    )


def test_build_manifest_keeps_only_server_side(tmp_path):
    root = tmp_path
    server_file = root / "config" / "ftbquests" / "quests" / "a.snbt"
    client_file = root / "kubejs" / "assets" / "ns" / "lang" / "zh_tw.json"
    targets = [
        _mk_target("ftbq_inline_snbt", server_file),
        _mk_target("kubejs_json", client_file),        # 客戶端 → 濾除
        _mk_target("vh_config_json", root / "config" / "the_vault" / "x.json"),  # 客戶端 → 濾除
    ]
    entries = sync.build_manifest_from_targets(targets, root)
    assert entries == [sync.ManifestEntry("config/ftbquests/quests/a.snbt", "ftbq_inline_snbt")]


def test_build_manifest_skips_target_outside_root(tmp_path):
    outside = tmp_path.parent / "elsewhere" / "a.snbt"
    entries = sync.build_manifest_from_targets(
        [_mk_target("ftbq_snbt", outside)], tmp_path
    )
    assert entries == []


def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_plan_sync_four_cases(tmp_path):
    client = tmp_path / "client"
    server = tmp_path / "server"
    # 來源檔（客戶端）
    _write(client / "a.snbt", "AAA")   # 伺服器缺 → copy
    _write(client / "b.snbt", "NEW")   # 伺服器不同 → overwrite
    _write(client / "c.snbt", "SAME")  # 相同 → skip
    _write(client / "d.snbt", "X")     # 來源在但 manifest 也列;伺服器缺 → copy
    # 伺服器端既有
    _write(server / "b.snbt", "OLD")
    _write(server / "c.snbt", "SAME")
    _write(server / "extra.snbt", "KEEP")  # manifest 未涵蓋 → 不得出現在 plan
    manifest = [
        sync.ManifestEntry("a.snbt", "ftbq_snbt"),
        sync.ManifestEntry("b.snbt", "ftbq_snbt"),
        sync.ManifestEntry("c.snbt", "ftbq_snbt"),
        sync.ManifestEntry("d.snbt", "ftbq_snbt"),
    ]
    plan = sync.plan_sync(client, server, manifest)
    actions = {i.rel_path: i.action for i in plan.items}
    assert actions == {"a.snbt": "copy", "b.snbt": "overwrite", "c.snbt": "skip", "d.snbt": "copy"}
    assert "extra.snbt" not in actions
    assert {i.rel_path for i in plan.copies} == {"a.snbt", "d.snbt"}
    assert {i.rel_path for i in plan.overwrites} == {"b.snbt"}
    assert {i.rel_path for i in plan.skips} == {"c.snbt"}


def test_plan_sync_skips_missing_source(tmp_path):
    client = tmp_path / "client"
    server = tmp_path / "server"
    client.mkdir()
    manifest = [sync.ManifestEntry("gone.snbt", "ftbq_snbt")]  # 客戶端無此檔
    plan = sync.plan_sync(client, server, manifest)
    assert plan.items == []
