"""Citadel 書本 txt 掃描:assets/<ns>/**/en_us/**.txt + 書根有頁面 JSON 才視為書。

Beyond Depth 實包:Alex's Mobs 動物圖鑑 (book/animal_dictionary/) 91 檔、
Alex's Caves 洞穴書 (books/) 75 檔、Citadel 自帶書,官方皆出貨 zh_cn 而無
zh_tw——不掃這面,整本書內文 GUI 全英文。
"""
import zipfile

from modpack_translator.pipeline.scanner import ModpackScanner

PAGE_JSON = '{"parent": "root.json", "text": "anteater.txt", "title": "entity.alexsmobs.anteater"}'
EN_TXT = "<NEWLINE>\nThe Anteater is a passive animal found in jungles.\n"
ZH_CN_TXT = "<NEWLINE>\n    食蟻獸是一種被動生物。\n"


def _targets(jar):
    return [t for t in ModpackScanner()._scan_jar(jar, "zh_tw", None) if t.format == "citadel_book_txt"]


def _write_book(zf):
    zf.writestr("assets/alexsmobs/book/animal_dictionary/root.json", PAGE_JSON)
    zf.writestr("assets/alexsmobs/book/animal_dictionary/anteater.json", PAGE_JSON)
    zf.writestr("assets/alexsmobs/book/animal_dictionary/en_us/anteater.txt", EN_TXT)
    zf.writestr("assets/alexsmobs/book/animal_dictionary/zh_cn/anteater.txt", ZH_CN_TXT)


def test_scan_finds_en_us_txt_under_book_root(tmp_path):
    jar = tmp_path / "alexsmobs.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        _write_book(zf)
    targets = _targets(jar)
    assert len(targets) == 1
    t = targets[0]
    assert t.path_in_jar == "assets/alexsmobs/book/animal_dictionary/en_us/anteater.txt"
    assert t.target_path_in_jar == "assets/alexsmobs/book/animal_dictionary/zh_tw/anteater.txt"
    assert t.mod_id == "alexsmobs"
    assert t.output_mode == "jar_inject"
    # zh_cn 是別的語言,不是 zh_tw 的既有譯檔
    assert t.existing_path_in_jar is None


def test_scan_nested_book_layout_alexscaves(tmp_path):
    """Alex's Caves:books/<章節>/*.json + books/<locale>/<章節>/*.txt(巢狀相對路徑)。"""
    jar = tmp_path / "alexscaves.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/alexscaves/books/root.json", PAGE_JSON)
        zf.writestr("assets/alexscaves/books/abyssal_chasm/deep_one.json", PAGE_JSON)
        zf.writestr("assets/alexscaves/books/en_us/abyssal_chasm/deep_one.txt", EN_TXT)
    [t] = _targets(jar)
    assert t.path_in_jar == "assets/alexscaves/books/en_us/abyssal_chasm/deep_one.txt"
    assert t.target_path_in_jar == "assets/alexscaves/books/zh_tw/abyssal_chasm/deep_one.txt"


def test_scan_skips_root_without_page_json(tmp_path):
    """en_us/*.txt 存在但根目錄無任何 JSON → 非 Citadel 書,不掃(避免誤吞一般文檔)。"""
    jar = tmp_path / "docs.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/somemod/docs/en_us/readme.txt", EN_TXT)
    assert _targets(jar) == []


def test_scan_skips_lang_dir_and_non_txt(tmp_path):
    jar = tmp_path / "create.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/create/lang/default/interface.json", "{}")
        zf.writestr("assets/create/lang/en_us.json", '{"k": "V"}')
        # lang/ 根下的 en_us 目錄不視為書
        zf.writestr("assets/somemod/lang/en_us/notes.txt", EN_TXT)
        zf.writestr("assets/somemod/lang/whatever.json", "{}")
    assert _targets(jar) == []


def test_scan_done_when_zh_tw_has_cjk(tmp_path):
    jar = tmp_path / "alexsmobs.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        _write_book(zf)
        zf.writestr(
            "assets/alexsmobs/book/animal_dictionary/zh_tw/anteater.txt",
            "<NEWLINE>\n    食蟻獸是一種被動生物,\n<NEWLINE>\n出沒於叢林。\n",
        )
    assert _targets(jar) == []


def test_scan_pending_when_zh_tw_still_english(tmp_path):
    jar = tmp_path / "alexsmobs.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        _write_book(zf)
        zf.writestr("assets/alexsmobs/book/animal_dictionary/zh_tw/anteater.txt", EN_TXT)
    [t] = _targets(jar)
    assert t.existing_path_in_jar == "assets/alexsmobs/book/animal_dictionary/zh_tw/anteater.txt"


def test_scan_own_output_locale_dir_not_a_source(tmp_path):
    """zh_tw/(或其他 locale)目錄下的 txt 不是來源:只認 en_us/。"""
    jar = tmp_path / "alexsmobs.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/alexsmobs/book/animal_dictionary/root.json", PAGE_JSON)
        zf.writestr("assets/alexsmobs/book/animal_dictionary/zh_tw/anteater.txt", "中文內容\n")
        zf.writestr("assets/alexsmobs/book/animal_dictionary/ja_jp/anteater.txt", "日文\n")
    assert _targets(jar) == []


def test_include_translated_mode_returns_target(tmp_path):
    jar = tmp_path / "alexsmobs.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        _write_book(zf)
        zf.writestr(
            "assets/alexsmobs/book/animal_dictionary/zh_tw/anteater.txt",
            "<NEWLINE>\n    食蟻獸。\n",
        )
    scanner = ModpackScanner()
    scanner._include_translated = True
    try:
        targets = [t for t in scanner._scan_jar(jar, "zh_tw", None) if t.format == "citadel_book_txt"]
    finally:
        scanner._include_translated = False
    assert len(targets) == 1
