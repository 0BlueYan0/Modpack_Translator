from __future__ import annotations

from modpack_translator.pipeline.glossary import (
    Glossary,
    augment_prompt,
    merged_match_pairs,
)


def test_main_glossary_wins_on_conflict():
    main = Glossary({"Nether": "地獄"})
    dyn = Glossary({"Nether": "下界", "Starlight Sanctum": "星輝聖所"})
    out = augment_prompt(
        "SYS", main, ["Go to Nether near Starlight Sanctum"], context_glossary=dyn
    )
    assert "Nether = 地獄" in out
    assert "Starlight Sanctum = 星輝聖所" in out
    assert "下界" not in out
    assert out.startswith("SYS")  # [Glossary] 永遠附加在尾端


def test_context_glossary_alone_injects():
    dyn = Glossary({"Starlight Sanctum": "星輝聖所"})
    out = augment_prompt("SYS", None, ["Starlight Sanctum ahead"], context_glossary=dyn)
    assert "Starlight Sanctum = 星輝聖所" in out


def test_no_hit_returns_prompt_unchanged():
    dyn = Glossary({"Starlight Sanctum": "星輝聖所"})
    assert augment_prompt("SYS", None, ["hello"], context_glossary=dyn) == "SYS"
    assert augment_prompt("SYS", None, ["hello"]) == "SYS"


def test_main_pairs_fill_cap_before_dynamic():
    main = Glossary({f"Main Term {i}": f"主{i}" for i in range(10)})
    dyn = Glossary({"Dyn Term": "動態"})
    text = " ".join(main.terms) + " Dyn Term"
    pairs = merged_match_pairs((main, dyn), [text])
    # 主用語庫的命中排在前面（cap 截斷時動態層先被丟）
    assert pairs[-1] == ("Dyn Term", "動態")
    assert len(pairs) == 11
