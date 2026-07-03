from __future__ import annotations

import json

from modpack_translator.pipeline.runner import (
    cache_key,
    source_sidecar_path,
    sync_source_sidecar,
)


def test_sidecar_keys_match_cache_and_resolve_known(tmp_path):
    src_a, src_b = "Twilight Forest", "Applied Energistics 2"
    cache = {
        cache_key(src_a): "暮光森林",
        cache_key(src_b): "應用能源2",
        "deadbeef000000000000000a": "來自別包的舊譯文",  # 來源不在本包
    }
    sidecar = tmp_path / "translation_sources.json"
    resolved = sync_source_sidecar(cache, [src_a, src_b], sidecar)

    out = json.loads(sidecar.read_text(encoding="utf-8"))
    # key 集合與 cache 完全一致
    assert set(out) == set(cache)
    # 可反查者填英文，反查不到者填空字串
    assert out[cache_key(src_a)] == src_a
    assert out[cache_key(src_b)] == src_b
    assert out["deadbeef000000000000000a"] == ""
    assert resolved == 2


def test_sidecar_merges_prior_for_cross_pack_entries(tmp_path):
    sidecar = tmp_path / "translation_sources.json"
    # 前一輪（別包）已記錄的來源
    sidecar.write_text(json.dumps({"deadbeef000000000000000a": "Prior Pack String"}),
                       encoding="utf-8")
    cache = {"deadbeef000000000000000a": "舊譯文"}
    resolved = sync_source_sidecar(cache, [], sidecar)  # 本包無此來源
    out = json.loads(sidecar.read_text(encoding="utf-8"))
    assert out["deadbeef000000000000000a"] == "Prior Pack String"  # 由既有 sidecar 補回
    assert resolved == 1


def test_sidecar_drops_keys_absent_from_cache(tmp_path):
    sidecar = tmp_path / "translation_sources.json"
    sidecar.write_text(json.dumps({"oldhashnolongercached0000": "Gone"}), encoding="utf-8")
    cache = {cache_key("Nether"): "地獄"}
    sync_source_sidecar(cache, ["Nether"], sidecar)
    out = json.loads(sidecar.read_text(encoding="utf-8"))
    assert "oldhashnolongercached0000" not in out  # 不在 cache 的 key 不保留
    assert set(out) == set(cache)


def test_source_sidecar_path():
    p = source_sidecar_path(r"C:/x/outputs/translation_cache.json")
    assert p.name == "translation_sources.json"
    assert p.parent.name == "outputs"
