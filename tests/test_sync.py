import shutil
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


def test_build_manifest_inline_snbt_uses_source_file(tmp_path):
    # inline snbt 就地改寫來源檔：target_file 為 None，輸出即 source_file。
    # 不 fallback 到 source_file 的話，FTB Quests 章節會被漏掉不同步。
    root = tmp_path
    chapter = root / "config" / "ftbquests" / "quests" / "chapters" / "ch1.snbt"
    inline_target = TranslationTarget(
        source_file=chapter, path_in_jar=None, mod_id="ftbquests",
        format="ftbq_inline_snbt", output_mode="in_place", target_file=None,
    )
    entries = sync.build_manifest_from_targets([inline_target], root)
    assert entries == [
        sync.ManifestEntry("config/ftbquests/quests/chapters/ch1.snbt", "ftbq_inline_snbt")
    ]


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


def test_apply_sync_copies_overwrites_backs_up(tmp_path):
    client = tmp_path / "client"
    server = tmp_path / "server"
    _write(client / "a.snbt", "AAA")
    _write(client / "b.snbt", "NEW")
    _write(client / "c.snbt", "SAME")
    _write(server / "b.snbt", "OLD")
    _write(server / "c.snbt", "SAME")
    manifest = [
        sync.ManifestEntry("a.snbt", "ftbq_snbt"),
        sync.ManifestEntry("b.snbt", "ftbq_snbt"),
        sync.ManifestEntry("c.snbt", "ftbq_snbt"),
    ]
    plan = sync.plan_sync(client, server, manifest)
    backup = server / ".modpack_translator" / "sync_bak" / "20260720_120000"
    result = sync.apply_sync(plan, client, server, backup)

    # 複製與覆蓋生效
    assert (server / "a.snbt").read_text(encoding="utf-8") == "AAA"
    assert (server / "b.snbt").read_text(encoding="utf-8") == "NEW"
    # 覆蓋前的原檔已備份且可還原
    assert (backup / "b.snbt").read_text(encoding="utf-8") == "OLD"
    # skip 的檔不進備份
    assert not (backup / "c.snbt").exists()
    assert set(result.copied) == {"a.snbt"}
    assert set(result.overwritten) == {"b.snbt"}
    assert set(result.skipped) == {"c.snbt"}
    assert result.backup_dir == backup


def test_apply_sync_no_backup_dir_when_no_overwrite(tmp_path):
    client = tmp_path / "client"
    server = tmp_path / "server"
    _write(client / "a.snbt", "AAA")
    plan = sync.plan_sync(client, server, [sync.ManifestEntry("a.snbt", "ftbq_snbt")])
    backup = server / ".modpack_translator" / "sync_bak" / "ts"
    sync.apply_sync(plan, client, server, backup)
    assert (server / "a.snbt").read_text(encoding="utf-8") == "AAA"
    assert not backup.exists()   # 沒有 overwrite 就不建備份資料夾


def test_apply_sync_never_deletes_server_extra(tmp_path):
    client = tmp_path / "client"
    server = tmp_path / "server"
    _write(client / "a.snbt", "AAA")
    _write(server / "extra.snbt", "KEEP")
    plan = sync.plan_sync(client, server, [sync.ManifestEntry("a.snbt", "ftbq_snbt")])
    sync.apply_sync(plan, client, server, server / "bak")
    assert (server / "extra.snbt").read_text(encoding="utf-8") == "KEEP"


def test_apply_sync_failed_item_does_not_stop_loop(tmp_path, monkeypatch):
    client = tmp_path / "client"
    server = tmp_path / "server"
    _write(client / "bad.snbt", "BAD")
    _write(client / "good.snbt", "GOOD")
    # 伺服器兩者皆缺 → 都是 copy
    manifest = [
        sync.ManifestEntry("bad.snbt", "ftbq_snbt"),
        sync.ManifestEntry("good.snbt", "ftbq_snbt"),
    ]
    plan = sync.plan_sync(client, server, manifest)

    real_copy2 = shutil.copy2

    def fake_copy2(src, dst, *args, **kwargs):
        if Path(src).name == "bad.snbt":
            raise OSError("boom")
        return real_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr(sync.shutil, "copy2", fake_copy2)

    backup = server / ".modpack_translator" / "sync_bak" / "ts"
    result = sync.apply_sync(plan, client, server, backup)

    # bad.snbt 失敗但不中斷迴圈，good.snbt 仍正常複製
    assert len(result.failed) == 1
    failed_path, err_msg = result.failed[0]
    assert failed_path == "bad.snbt"
    assert "boom" in err_msg
    assert result.copied == ["good.snbt"]
    assert (server / "good.snbt").read_text(encoding="utf-8") == "GOOD"
    assert not (server / "bad.snbt").exists()


def test_apply_sync_on_progress_only_counts_actionable(tmp_path):
    client = tmp_path / "client"
    server = tmp_path / "server"
    _write(client / "a.snbt", "AAA")   # 伺服器缺 → copy
    _write(client / "c.snbt", "SAME")  # 相同 → skip
    _write(server / "c.snbt", "SAME")
    manifest = [
        sync.ManifestEntry("a.snbt", "ftbq_snbt"),
        sync.ManifestEntry("c.snbt", "ftbq_snbt"),
    ]
    plan = sync.plan_sync(client, server, manifest)
    calls: list[tuple[int, int]] = []
    sync.apply_sync(
        plan, client, server, server / "bak",
        on_progress=lambda done, total: calls.append((done, total)),
    )
    # 只有 copy 這步觸發一次 on_progress，total 不含 skip
    assert calls == [(1, 1)]


from modpack_translator.pipeline.scanner import resolve_game_root


def test_end_to_end_only_server_side_synced(tmp_path):
    # 假客戶端：伺服器端(ftbq + datapack) + 客戶端(mods jar + the_vault)
    client = tmp_path / "client"
    _write(client / "config" / "ftbquests" / "quests" / "ch1.snbt", "任務一")
    _write(client / "kubejs" / "data" / "skilltree" / "skills" / "mage.json", '{"title":"法師"}')
    _write(client / "config" / "the_vault" / "lang" / "zh_tw" / "x.json", '{"a":"甲"}')
    (client / "mods").mkdir()
    (client / "mods" / "x.jar").write_bytes(b"PK\x03\x04zip")
    server = tmp_path / "server"
    server.mkdir()

    targets = [
        _mk_target("ftbq_inline_snbt", client / "config" / "ftbquests" / "quests" / "ch1.snbt"),
        _mk_target("datapack_json", client / "kubejs" / "data" / "skilltree" / "skills" / "mage.json"),
        _mk_target("vh_config_json", client / "config" / "the_vault" / "lang" / "zh_tw" / "x.json"),
    ]
    manifest = sync.build_manifest_from_targets(targets, client)
    plan = sync.plan_sync(client, server, manifest)
    sync.apply_sync(plan, client, server, server / "bak")

    # 伺服器端內容被複製
    assert (server / "config" / "ftbquests" / "quests" / "ch1.snbt").exists()
    assert (server / "kubejs" / "data" / "skilltree" / "skills" / "mage.json").exists()
    # 客戶端內容（the_vault、mods）不被複製
    assert not (server / "config" / "the_vault").exists()
    assert not (server / "mods").exists()


def test_server_root_resolution_layouts(tmp_path):
    # PrismLauncher 式：<instance>/minecraft/
    prism = tmp_path / "inst"
    (prism / "minecraft" / "config").mkdir(parents=True)
    assert resolve_game_root(prism) == prism / "minecraft"
    # 專用伺服器式：config/ 直接在頂層
    ded = tmp_path / "server"
    (ded / "config").mkdir(parents=True)
    assert resolve_game_root(ded) == ded
