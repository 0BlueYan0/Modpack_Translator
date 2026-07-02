"""離線建置 Minecraft 官方用語庫（en→zh_tw 對照表）。

從 InventivetalentDev/minecraft-assets（mcasset.cloud 的資料來源）下載指定
版本的官方 en_us.json 與 zh_tw.json，過濾出「名稱類」詞條後輸出扁平 JSON
至 assets/glossary/zh_tw_<version>.json。產物 commit 進 repo，執行期不連網。

用法：
    python scripts/build_glossary.py --mc-version 1.21.1
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).parent.parent
GLOSSARY_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/InventivetalentDev/minecraft-assets/"
    "{version}/assets/minecraft/lang/{name}"
)

# 只收「key 前綴命中且結尾為單一段」的詞條：排除旗幟圖樣
# （block.minecraft.banner.base.black）、唱片作者（item.minecraft.music_disc_13.desc）、
# 床訊息（block.minecraft.spawn.not_valid）等非名稱類深層 key。
_SINGLE_SEGMENT_PREFIXES = (
    "block.minecraft.",
    "item.minecraft.",
    "entity.minecraft.",
    "biome.minecraft.",
    "effect.minecraft.",
    "enchantment.minecraft.",
    "structure.minecraft.",
)
# 深層 key 白名單：屬性名與藥水家族（「迅捷藥水」等）值得收錄。
_DEEP_PREFIXES = (
    "attribute.name.",
    "item.minecraft.potion.effect.",
    "item.minecraft.splash_potion.effect.",
    "item.minecraft.lingering_potion.effect.",
    "item.minecraft.tipped_arrow.effect.",
)
# 高價值特定 key 白名單：維度名不在名稱類前綴下，但正是最常被誤譯的詞
# （Nether 誤譯「下界」、Overworld 誤譯「主世界以外譯法」）。
_EXTRA_KEYS = (
    "advancements.nether.root.title",       # Nether = 地獄
    "advancements.end.root.title",          # The End = 終界
    "flat_world_preset.minecraft.overworld",  # Overworld = 主世界
)
# 同一英文詞對到不同中文時，key 前綴排名靠前者勝
# （→ Wither=凋零怪 而非狀態效果的凋零、Speed=加速 而非屬性的速度）。
_PRIORITY = (
    "entity.minecraft.",
    "block.minecraft.",
    "item.minecraft.",
    "effect.minecraft.",
    "enchantment.minecraft.",
    "biome.minecraft.",
    "structure.minecraft.",
    "attribute.name.",
)


def fetch_lang(version: str, name: str, timeout: float = 30.0) -> dict[str, str]:
    url = GLOSSARY_URL_TEMPLATE.format(version=version, name=name)
    request = Request(url, headers={"User-Agent": "Modpack-Translator-GlossaryBuilder/1.0"})
    with urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{url} 不是 JSON object")
    return data


def _eligible_key(key: str) -> bool:
    if key in _EXTRA_KEYS:
        return True
    for prefix in _DEEP_PREFIXES:
        if key.startswith(prefix) and len(key) > len(prefix):
            return True
    for prefix in _SINGLE_SEGMENT_PREFIXES:
        if key.startswith(prefix) and "." not in key[len(prefix):]:
            return True
    return False


def _priority_rank(key: str) -> int:
    for i, prefix in enumerate(_PRIORITY):
        if key.startswith(prefix):
            return i
    return len(_PRIORITY)


def build_glossary_map(en: dict[str, str], zh: dict[str, str]) -> dict[str, str]:
    """純函式：從兩份官方 lang dict 建對照表 {英文詞: 繁中譯名}。"""
    best_rank: dict[str, int] = {}
    result: dict[str, str] = {}
    for key, en_value in en.items():
        if not _eligible_key(key):
            continue
        zh_value = zh.get(key)
        if not isinstance(en_value, str) or not isinstance(zh_value, str):
            continue
        en_term = en_value.strip()
        zh_term = zh_value.strip()
        if len(en_term) < 3 or not zh_term:
            continue
        if "%" in en_term or "%" in zh_term:
            continue
        if en_term == zh_term:  # 未翻譯詞條（如 TNT）
            continue
        rank = _priority_rank(key)
        if en_term in result and best_rank[en_term] <= rank:
            continue
        result[en_term] = zh_term
        best_rank[en_term] = rank
    return dict(sorted(result.items(), key=lambda kv: kv[0].lower()))


def main() -> None:
    parser = argparse.ArgumentParser(description="建置 Minecraft 官方 en→zh_tw 用語庫")
    parser.add_argument("--mc-version", default="1.21.1", help="Minecraft 版本（預設 1.21.1）")
    parser.add_argument(
        "--output",
        default=None,
        help="輸出路徑（預設 assets/glossary/zh_tw_<version>.json）",
    )
    args = parser.parse_args()

    print(f"下載 {args.mc_version} 官方語言檔…")
    en = fetch_lang(args.mc_version, "en_us.json")
    zh = fetch_lang(args.mc_version, "zh_tw.json")
    print(f"en_us.json {len(en):,} keys / zh_tw.json {len(zh):,} keys")

    glossary = build_glossary_map(en, zh)

    output = Path(args.output) if args.output else (
        PROJECT_ROOT / "assets" / "glossary" / f"zh_tw_{args.mc_version}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(glossary, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    print(f"已寫出 {len(glossary):,} 條詞彙 -> {output}")


if __name__ == "__main__":
    main()
