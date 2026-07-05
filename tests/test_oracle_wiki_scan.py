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
    # 非空洞:合法目標仍在同一次掃描中被偵測到(否則排除斷言可能空過)
    fmts = {t.format for t in targets}
    assert "oracle_mdx" in fmts
    assert any(s.endswith("/content/equipment/chainsaw.mdx") for s in srcs)
    assert any(s.endswith("/content/_meta.json") for s in srcs)

def test_scan_docs_root_emits_target(tmp_path):
    jar = tmp_path / "docsbook.jar"
    base = "assets/oracle_index/books/mybook"
    mdx = "---\r\ntitle: Guide\r\n---\r\n\r\nHow to use the machine.\r\n"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr(f"{base}/docs/guide.mdx", mdx)
    targets = ModpackScanner()._scan_jar(jar, "zh_tw", None)
    mdx_targets = [t for t in targets if t.format == "oracle_mdx"]
    assert len(mdx_targets) == 1
    assert mdx_targets[0].target_path_in_jar == "assets/oracle_index/books/mybook/translated/zh_tw/docs/guide.mdx"

def test_translated_only_excludes_output_tree_not_literal_names(tmp_path):
    """收窄後的排除:只有輸出/既有譯樹(.../translated/<locale>/<root>/...)被排除,
    book id 或子資料夾剛好叫 translated 的合法內容仍要偵測到。"""
    jar = tmp_path / "edgey.jar"
    mdx = "---\r\ntitle: T\r\n---\r\n\r\nBody text here.\r\n"
    with zipfile.ZipFile(jar, "w") as zf:
        # 合法:子資料夾叫 translated(parts[4]=="content")
        zf.writestr("assets/oracle_index/books/mybook/content/translated/tutorial.mdx", mdx)
        # 合法:book id 叫 translated(parts[3]=="translated", parts[4]=="content")
        zf.writestr("assets/oracle_index/books/translated/content/guide.mdx", mdx)
        # 排除:真正的輸出譯樹(parts[4]=="translated")
        zf.writestr("assets/oracle_index/books/oritech/translated/zh_tw/content/x.mdx", mdx)
        zf.writestr("assets/oracle_index/books/oritech/translated/fr_fr/content/x.mdx", mdx)
    targets = ModpackScanner()._scan_jar(jar, "zh_tw", None)
    srcs = [t.path_in_jar for t in targets if t.format == "oracle_mdx"]
    assert "assets/oracle_index/books/mybook/content/translated/tutorial.mdx" in srcs
    assert "assets/oracle_index/books/translated/content/guide.mdx" in srcs
    assert "assets/oracle_index/books/oritech/translated/zh_tw/content/x.mdx" not in srcs
    assert "assets/oracle_index/books/oritech/translated/fr_fr/content/x.mdx" not in srcs
    assert len(srcs) == 2
    # 子資料夾 translated 案例的輸出路徑:translated/<locale> 插在 root(content)前
    tut = next(t for t in targets if t.path_in_jar.endswith("content/translated/tutorial.mdx"))
    assert tut.target_path_in_jar == "assets/oracle_index/books/mybook/translated/zh_tw/content/translated/tutorial.mdx"
