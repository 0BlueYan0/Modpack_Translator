"""Radical Cobblemon Trainers (rctmod) 訓練家名稱翻譯。

rctmod 的訓練家顯示名稱不在 en_us.json：mod 執行期以
Component.translatableWithFallback("trainer.rctmod.<id>.name", 資料檔英文名)
渲染世界名牌、訓練家卡與進度圖（rctapi Text.getComponent），lang 檔沒有該鍵
時顯示英文 fallback。在 zh_tw.json 補上這些鍵即可翻譯，資料檔本身不需修改。

名稱來源：
1. jar 內 data/rctmod/trainers/<id>.json 的 "name" 欄位（rctmod 本體 1500+ 名）
2. modpack 本地 kubejs/data/rctmod/trainers/（整合包自加訓練家）

輸出寫入各自來源載入鏈可讀的 assets/rctmod/lang/<lang>.json
（jar 注入 / kubejs 資源樹）；語言系統會合併所有資源包的同語言檔。

譯法三層（與任務書、advancement 既有譯法一致）：
1. STATIC_NAMES 整串命中 → 官方/既定譯名，不呼叫模型
   （寶可夢官方人名不可音譯：Cynthia=竹蘭；任務書保留英文的人名照樣保留）
2. CLASS_MAP 職業前綴命中 → 職業確定性翻譯，其餘人名交模型音譯
   （同名人名經快取去重，全 roster 一致）
3. 皆未命中 → 整串交模型
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from modpack_translator.pipeline.scanner import TranslationTarget

TRAINERS_PREFIX = "data/rctmod/trainers/"
LANG_DIR_IN_JAR = "assets/rctmod/lang"


def lang_key(trainer_id: str) -> str:
    return f"trainer.rctmod.{trainer_id}.name"


# ---------------------------------------------------------------- 名稱讀取

def read_trainer_names(target: "TranslationTarget") -> dict[str, str]:
    """依目標形態讀出 {trainer.rctmod.<id>.name: 英文名}。"""
    if target.path_in_jar:
        try:
            with zipfile.ZipFile(target.source_file) as zf:
                return read_zip_trainer_names(zf)
        except (zipfile.BadZipFile, OSError):
            return {}
    return read_dir_trainer_names(Path(target.source_file))


def read_zip_trainer_names(zf: zipfile.ZipFile) -> dict[str, str]:
    names: dict[str, str] = {}
    for entry in zf.namelist():
        if not (entry.startswith(TRAINERS_PREFIX) and entry.endswith(".json")):
            continue
        trainer_id = entry.rsplit("/", 1)[1][:-5]
        if not trainer_id:
            continue
        try:
            data = json.loads(zf.read(entry).decode("utf-8-sig"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        name = data.get("name") if isinstance(data, dict) else None
        if isinstance(name, str) and name.strip():
            names[lang_key(trainer_id)] = name
    return names


def read_dir_trainer_names(trainers_dir: Path) -> dict[str, str]:
    names: dict[str, str] = {}
    if not trainers_dir.is_dir():
        return names
    for file in sorted(trainers_dir.glob("*.json")):
        try:
            data = json.loads(file.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        name = data.get("name") if isinstance(data, dict) else None
        if isinstance(name, str) and name.strip():
            names[lang_key(file.stem)] = name
    return names


# ---------------------------------------------------------------- 靜態譯名

# 整串精確比對。官方人名依寶可夢官方繁中譯名；粉絲作角色（Unbound/Radical Red）
# 依本包任務書/advancement 既有譯法（含「保留英文」的選擇），確保全包一致。
# 人名詞條刻意不進共用用語庫：Flint（燧石）、Lance（長槍）、Misty 等與一般
# 詞彙衝突，只能限定在訓練家名稱範圍使用。
STATIC_NAMES: dict[str, str] = {
    # ── 關都 道館館主 / 火箭隊 ──
    "Brock": "小剛", "Leader Brock": "館主小剛",
    "Misty": "小霞", "Leader Misty": "館主小霞",
    "Lt. Surge": "馬志士", "Leader Lt. Surge": "館主馬志士",
    "Erika": "莉佳", "Leader Erika": "館主莉佳",
    "Koga": "阿桔", "Leader Koga": "館主阿桔",
    "Sabrina": "娜姿", "Leader Sabrina": "館主娜姿",
    "Blaine": "夏伯", "Leader Blaine": "館主夏伯",
    "Giovanni": "坂木", "Boss Giovanni": "首領坂木",
    "Rocket Admin Archer": "火箭隊幹部阿波羅",
    "Rocket Admin Ariana": "火箭隊幹部雅典娜",
    "Rocket Admins Archer & Ariana": "火箭隊幹部阿波羅與雅典娜",
    "Team Rocket Admin": "火箭隊幹部",
    "Team Rocket Grunt": "火箭隊手下",
    # ── 關都 四天王 ──
    "Lorelei": "科拿", "Elite Four Lorelei": "四天王科拿",
    "Bruno": "希巴", "Elite Four Bruno": "四天王希巴",
    "Agatha": "菊子", "Elite Four Agatha": "四天王菊子",
    "Lance": "阿渡", "Elite Four Lance": "四天王阿渡",
    # ── 城都 道館館主 ──
    "Falkner": "阿速", "Bugsy": "阿筆", "Whitney": "小茜", "Morty": "松葉",
    "Chuck": "阿四", "Jasmine": "阿蜜", "Pryce": "柳伯",
    "Clair": "小椿", "Leader Clair": "館主小椿",
    # ── 神奧 道館館主（任務書已定譯法）──
    "Roark": "瓢太", "Gardenia": "菜種", "Maylene": "阿李", "Wake": "吉憲",
    "Fantina": "梅麗莎", "Byron": "東鋼", "Candice": "小菘", "Volkner": "電次",
    # ── 神奧 四天王 / 冠軍 ──
    "Aaron": "阿柳", "Elite Four Aaron": "四天王阿柳",
    "Bertha": "菊野", "Elite Four Bertha": "四天王菊野",
    "Flint": "大葉", "Elite Four Flint": "四天王大葉",
    "Lucian": "悟松", "Elite Four Lucian": "四天王悟松",
    "Cynthia": "竹蘭", "Champion Cynthia": "冠軍竹蘭",
    # ── 銀河隊 ──
    "Commander Mars": "幹部夥星",
    "Commander Jupiter": "幹部歲星",
    "Commander Saturn": "幹部鎮星",
    "Commanders Mars & Jupiter": "幹部夥星與歲星",
    "Team Galactic Boss Cyrus": "銀河隊老大赤日",
    "Team Galactic Grunt": "銀河隊手下",
    # ── Radical Red / Unbound 粉絲作角色（依任務書既有譯法，含保留英文者）──
    "Champion Terry": "冠軍泰瑞", "Rival Terry": "勁敵泰瑞",
    "Champion Jax": "冠軍 Jax",
    "Rival Wayne": "勁敵 Wayne",
    "Boss Zeph": "首領 Zeph",
    "Leader Alice": "館主愛麗絲", "Battleground Alice": "戰場愛麗絲",
    "Leader Benjamin": "館主班傑明", "Battleground Benjamin": "戰場班傑明",
    "Leader Big Mo": "館主大莫", "Battleground Big Mo": "戰場大莫",
    "Leader Mel": "館主梅爾", "Battleground Mel": "戰場梅爾",
    "Leader Tessy": "館主 Tessy", "Battleground Tessy": "戰場 Tessy",
    "Leader Vega": "館主維加", "Battleground Vega": "戰場維加",
    "Light of Ruin Vega": "破滅光源維加",
    "Leader Mirskle": "館主米斯克爾",
    "Leader Galavan": "館主加拉凡",
    "Elite Four Penny": "四天王 Penny",
    "Elite Four Elias": "四天王 Elias",
    "Elite Four Arabella": "四天王 Arabella",
    "Elite Four Moleman": "四天王鼴鼠人",
    "Shadow Admin Marlon": "暗影幹部馬隆",
    "Shadow Admin Marlon & Grunt": "暗影幹部馬隆與手下",
    "Ex-Shadow Admin Marlon": "前暗影幹部馬隆",
    "Shadow Admin Ivory": "暗影幹部艾沃莉",
    "Shadow Admin Ivory & Grunt": "暗影幹部艾沃莉與手下",
    "Ruin Admin Ivory": "遺跡幹部艾沃莉",
    "Shadow Grunt": "暗影手下",
}


def static_name(name: str) -> str | None:
    return STATIC_NAMES.get(name.strip())


# ---------------------------------------------------------------- 職業前綴

# 職業 → 官方/社群慣用繁中稱號。只收有把握的詞條；未命中者整串交模型。
# 長詞優先比對（Pokémon Ranger 先於 Ranger）。
CLASS_MAP: dict[str, str] = {
    "Ace Trainer": "精英訓練家",
    "Aroma Lady": "芳香姐姐",
    "Artist": "藝術家",
    "Battle Girl": "對戰少女",
    "Battleground": "戰場",
    "Beauty": "大姊姊",
    "Biker": "飆車族",
    "Bird Keeper": "養鳥人",
    "Black Belt": "空手道王",
    "Boss": "首領",
    "Breeder": "培育家",
    "Bug Catcher": "捕蟲少年",
    "Burglar": "盜賊",
    "Cameraman": "攝影師",
    "Camper": "露營少年",
    "Champion": "冠軍",
    "Channeler": "通靈師",
    "Clown": "小丑",
    "Collector": "收藏家",
    "Commander": "幹部",
    "Cowgirl": "牛仔少女",
    "Crush Girl": "空手道少女",
    "Crush Kin": "空手道親子",
    "Cue Ball": "光頭男",
    "Cyclist": "自行車手",
    "Dragon Tamer": "馭龍者",
    "Elite Four": "四天王",
    "Engineer": "工程師",
    "Ex-Shadow Admin": "前暗影幹部",
    "Expert": "行家",
    "Fisher": "垂釣者",
    "Fisherman": "垂釣者",
    "Gambler": "賭徒",
    "Gentleman": "紳士",
    "Guitarist": "吉他手",
    "Gym Leader": "道館館主",
    "Hiker": "登山男",
    "Idol": "偶像",
    "Jogger": "慢跑者",
    "Juggler": "雜技師",
    "Lady": "大小姐",
    "Lass": "迷你裙",
    "Leader": "館主",
    "Light of Ruin": "破滅光源",
    "Madame": "女士",
    "Mega Trainer": "超級訓練家",
    "Ninja Boy": "忍者小子",
    "Painter": "畫家",
    "Parasol Lady": "陽傘姐姐",
    "Picnicker": "野餐少女",
    "Police Officer": "警察",
    "Pokémaniac": "寶可夢迷",
    "Pokémon Breeder": "寶可夢培育家",
    "Pokémon Ranger": "寶可夢巡護員",
    "Pokémon Trainer": "寶可夢訓練家",
    "Professor": "博士",
    "Psychic": "超能力者",
    "Rancher": "牧場主",
    "Ranchers": "牧場主",
    "Ranger": "巡護員",
    "Reporter": "記者",
    "Rich Boy": "富家少爺",
    "Rival": "勁敵",
    "Rocker": "搖滾樂手",
    "Rocket Admin": "火箭隊幹部",
    "Ruin Admin": "遺跡幹部",
    "Ruin Leader": "遺跡首領",
    "Ruin Maniac": "遺跡狂",
    "Ruin Mamoac": "遺跡狂",  # 資料檔本身的拼字錯誤（Maniac）
    "Sailor": "水手",
    "School Kid": "補習班學生",
    "Scientist": "研究員",
    "Shadow Admin": "暗影幹部",
    "Sinnoh Leader": "神奧館主",
    "Skier": "滑雪者",
    "Super Nerd": "超級書呆子",
    "Swimmer": "泳裝訓練家",
    "Swimmer♀": "泳裝女孩",
    "Swimmer♂": "泳裝小哥",
    "Tamer": "馴獸師",
    "Team Galactic": "銀河隊",
    "Team Rocket": "火箭隊",
    "Trainer": "訓練家",
    "Tuber": "泳圈小童",
    "Twin": "雙胞胎",
    "Twins": "雙胞胎",
    "Veteran": "資深訓練家",
    "Waitress": "女服務生",
    "Worker": "工人",
    "Young Couple": "恩愛情侶",
    "Youngster": "短褲小子",
}

_CLASSES_BY_LENGTH = sorted(CLASS_MAP, key=len, reverse=True)
_CJK_START_RE = re.compile(r"^[㐀-鿿]")


def split_class(name: str) -> tuple[str, str] | None:
    """命中職業前綴時回傳 (職業譯名, 人名餘段)；人名餘段可為空字串。"""
    text = name.strip()
    for cls in _CLASSES_BY_LENGTH:
        if text == cls:
            return CLASS_MAP[cls], ""
        if text.startswith(cls + " "):
            return CLASS_MAP[cls], text[len(cls) + 1:].strip()
    return None


def compose(class_zh: str, given_zh: str) -> str:
    """職業譯名＋人名。人名為中文緊接、保留英文時以空格分隔（任務書慣例）。"""
    given_zh = given_zh.strip()
    if not given_zh:
        return class_zh
    if _CJK_START_RE.match(given_zh):
        return f"{class_zh}{given_zh}"
    return f"{class_zh} {given_zh}"


def model_source(name: str) -> str | None:
    """該名稱會送模型的字串：靜態命中回 None；職業命中回人名餘段；否則整串。

    供批次預翻譯與逐條處理共用，保證兩邊送模型的字串一致（快取互通）。
    """
    if static_name(name) is not None:
        return None
    split = split_class(name)
    if split is not None:
        return split[1] or None
    return name.strip() or None
