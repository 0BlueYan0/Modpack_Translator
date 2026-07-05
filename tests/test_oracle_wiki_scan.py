import json, zipfile
from modpack_translator.pipeline.scanner import ModpackScanner

def _make(tmp_path):
    jar = tmp_path / "oritech.jar"
    base = "assets/oracle_index/books/oritech"
    mdx = "---\r\ntitle: Chainsaw\r\ntype: item\r\nid: oritech:chainsaw\r\n---\r\n\r\nThe chainsaw is a fast tool for cutting wood.\r\n"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr(f"{base}/content/equipment/chainsaw.mdx", mdx)
        zf.writestr(f"{base}/content/_meta.json", json.dumps({"equipment": "Equipment"}))
        zf.writestr(f"{base}/sinytra-wiki.json", json.dumps({"id": "oritech"}))  # 非目標
    return jar

def test_scan_emits_oracle_mdx_and_meta_targets(tmp_path):
    jar = _make(tmp_path)
    targets = ModpackScanner()._scan_jar(jar, "zh_tw", None)
    by_fmt = {}
    for t in targets:
        by_fmt.setdefault(t.format, []).append(t)
    mdx = by_fmt.get("oracle_mdx", [])
    meta = [t for t in by_fmt.get("json_lang", []) if t.path_in_jar.endswith("_meta.json")]
    assert len(mdx) == 1
    assert mdx[0].target_path_in_jar == "assets/oracle_index/books/oritech/translated/zh_tw/content/equipment/chainsaw.mdx"
    assert mdx[0].existing_path_in_jar is None
    assert len(meta) == 1
    assert meta[0].target_path_in_jar == "assets/oracle_index/books/oritech/translated/zh_tw/content/_meta.json"

def test_scan_skips_sinytra_manifest_and_translated_tree(tmp_path):
    jar = _make(tmp_path)
    with zipfile.ZipFile(jar, "a") as zf:  # 加一個已存在的 translated 檔,不應被當來源
        zf.writestr("assets/oracle_index/books/oritech/translated/zh_tw/content/x.mdx", "---\r\ntitle: Y\r\n---\r\n")
    targets = ModpackScanner()._scan_jar(jar, "zh_tw", None)
    srcs = [t.path_in_jar for t in targets]
    assert not any("sinytra-wiki.json" in s for s in srcs)
    assert not any("/translated/" in s for s in srcs)
