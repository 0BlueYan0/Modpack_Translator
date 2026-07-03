from __future__ import annotations

from modpack_translator.pipeline.batch_prefill import PrefillItem, _build_batches


def _mk(i: int, group: str) -> PrefillItem:
    return PrefillItem(source=f"text {i}", ck=f"ck{i}", group=group)


def test_group_never_split_when_it_fits():
    # 10 + 10：第二組裝不進硬上限（12 + 12//3 = 16）→ 各自成批
    items = [_mk(i, "f::quest.AA") for i in range(10)]
    items += [_mk(100 + i, "f::quest.BB") for i in range(10)]
    batches = _build_batches(items, batch_size=12)
    assert len(batches) == 2
    assert {e.item.group for e in batches[0]} == {"f::quest.AA"}
    assert {e.item.group for e in batches[1]} == {"f::quest.BB"}


def test_small_groups_share_batch_across_files():
    # 小組（含跨檔）併批維持填充率
    items = [_mk(1, "f1::quest.AA"), _mk(2, "f1::quest.AA"), _mk(3, "f2::__file__")]
    batches = _build_batches(items, batch_size=12)
    assert len(batches) == 1


def test_oversized_group_splits():
    items = [_mk(i, "f::__file__") for i in range(30)]
    batches = _build_batches(items, batch_size=12)
    assert all(len(b) <= 12 for b in batches)
    assert sum(len(b) for b in batches) == 30


def test_overflow_allowed_to_keep_group_whole():
    # 8 條已在批中，下一組 6 條：8+6=14 ≤ 16 硬上限 → 同批不拆組
    items = [_mk(i, "f::quest.AA") for i in range(8)]
    items += [_mk(100 + i, "f::quest.BB") for i in range(6)]
    batches = _build_batches(items, batch_size=12)
    assert len(batches) == 1
    assert len(batches[0]) == 14


def test_group_id_derivation():
    from modpack_translator.pipeline.batch_prefill import _group_id

    class T:
        source_file = "chapters/foo.snbt"

    assert _group_id(T(), "quest.1A2B3C.title") == "chapters/foo.snbt::quest.1A2B3C"
    assert _group_id(T(), "quest.1A2B3C.quest_desc[3]") == "chapters/foo.snbt::quest.1A2B3C"
    assert _group_id(T(), "item.mymod.thing") == "chapters/foo.snbt::__file__"


def test_interleaved_groups_are_regrouped_by_sort():
    # diff_keys 回傳 set → 同組在收集順序中不相鄰。_build_batches 必須先排序，
    # 否則 groupby 會把每個 item 切成獨立 run，同任務不會同批。
    items = [
        _mk(1, "f::quest.AA"), _mk(2, "f::quest.BB"), _mk(3, "f::quest.AA"),
        _mk(4, "f::quest.BB"), _mk(5, "f::quest.AA"), _mk(6, "f::quest.BB"),
    ]
    batches = _build_batches(items, batch_size=12)
    # 排序後 AA 三條、BB 三條各自聚合；6 條 ≤ 硬上限 → 同批但同組相鄰
    assert len(batches) == 1
    groups = [e.item.group for e in batches[0]]
    assert groups == ["f::quest.AA"] * 3 + ["f::quest.BB"] * 3
