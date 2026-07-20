# 伺服器同步功能設計（Server Sync）

日期：2026-07-20
狀態：設計核准，待實作

## 背景與動機

翻譯器把繁中譯文寫進**客戶端**模組包實例（就地覆蓋或寫 zh_tw 變體）。但譯文分兩類：

- **客戶端文字**（物品名 / tooltip / GUI，走 translate key，由客戶端語言檔解析）：mod jar、資源包、光影、`kubejs/assets` lang、Patchouli/GuideME/Citadel。這些只要客戶端有就好，**伺服器不需要**。
- **伺服器端文字**（任務資料、資料包字面文字，伺服器載入後同步給客戶端顯示）：連專用伺服器時，客戶端顯示的是伺服器送來的字串，因此**必須讓伺服器那份也翻好**才會生效（FTB Quests、技能樹/起源資料包即此類）。

單人遊戲時客戶端＝內建伺服器，全部就地生效；但玩專用伺服器時，伺服器端文字得額外複製到伺服器實例。本功能讓使用者設定客戶端與伺服器資料夾，一鍵把「伺服器端才需要的已翻內容」同步過去。

### 5 個實例的調查結論（決定分類的證據）

用 5 個子代理盤點 `PrismLauncher/instances` 下 5 個包的伺服器端可翻內容：

| 包 | 伺服器端翻譯內容 |
|---|---|
| Soulrend | `config/ftbquests/quests`、`config/paxi/datapacks`、`kubejs/data`（技能樹/起源）|
| Beyond Depth | `config/ftbquests/quests`、`kubejs/data` |
| All the Mons | `config/ftbquests/quests`（含 `lang/`）、`kubejs/data`（rctmod）|
| DawnCraft | `config/ftbquests/quests`、`global_packs/required_data/`（公會任務等）|
| Vault Hunters | `config/the_vault`（**經反編譯確認為客戶端載入，不同步**）|

兩個關鍵發現改變了設計：

1. **`config/the_vault`（`vh_config_json`）是客戶端**。反編譯 `the_vault` jar：`Config.getClientLocale()` 在專用伺服器上永遠回傳 `en_us`，所有在地化呼叫端都是 client GUI，同步封包只傳「設定檔名稱」不傳內容——每個玩家靠自己本機 `config/the_vault` 渲染。故 `vh_config_json` 歸為客戶端格式、**不同步**（先前「防禦性納入伺服器」的假設是錯的）。
2. **伺服器端位置因包而異**（DawnCraft 用 `global_packs/required_data/`，其他包沒有）。硬寫死一份「伺服器端目錄清單」很脆弱，換個包就漏。→ 採 **manifest（依 format 分類）** 驅動同步，未來翻譯器支援新的伺服器端位置時，同步自動涵蓋，不必改同步程式。

## 目標與非目標

**目標**
- GUI 可設定客戶端資料夾（沿用既有「模組包」欄位）與伺服器資料夾（選填）。
- 一鍵把伺服器端格式的已翻檔從客戶端同步到伺服器實例。
- 同步只增不減、覆蓋前備份、動手前先預覽確認。
- 相容既有已翻實例（尚無 manifest 者可即時建立）。

**非目標**
- 不擴充翻譯覆蓋率（調查中發現的翻譯缺口另案處理，見附錄 B）。
- 不同步客戶端內容（jar/資源包/光影等）到伺服器。
- 不做雙向同步、不刪除伺服器端既有檔。
- 不處理 NBT 內嵌文字（需另外的 NBT 工具）。

## 格式分類（同步的核心依據）

翻譯器對每個輸出目標都有 `format` 欄位。同步只搬**伺服器端格式**：

```
SERVER_SIDE_FORMATS = {
    "ftbq_snbt", "ftbq_inline_snbt",
    "heracles_snbt", "heracles_inline_snbt",
    "bq_lang",
    "datapack_json",
}
```

**明確不含**（客戶端格式，不同步）：`json_lang`、`legacy_lang`、`pack_json_lang`、`pack_legacy_lang`、`patchouli_json`、`oracle_mdx`、`oracle_meta`、`guideme_md`、`citadel_book_txt`、`rct_names`、`kubejs_json`、`vh_config_json`。

備註：`bq_lang`（Better Questing）5 包皆未安裝、未實測，暫定伺服器端（quest 資料伺服器載入）；日後遇到實際使用的包再驗證。

伺服器端格式的輸出一律是 `output_mode="in_place"` 的實體檔（`target_file`），故 manifest 記的都是真實檔案路徑，無 jar 內路徑問題。

## 架構

### 新模組 `src/modpack_translator/pipeline/sync.py`（純邏輯、不依賴 GUI）

```
SERVER_SIDE_FORMATS: frozenset[str]

# manifest：客戶端 <遊戲根>/.modpack_translator/sync_manifest.json
def manifest_path(game_root: Path) -> Path
def load_manifest(game_root: Path) -> list[ManifestEntry]
def merge_manifest(game_root: Path, entries: list[ManifestEntry]) -> None   # 聯集合併寫回
def build_manifest_from_scan(targets, game_root) -> list[ManifestEntry]      # 從掃描結果挑伺服器端

# 規劃與執行
def plan_sync(client_root, server_root, manifest) -> SyncPlan                # 純函式、無副作用
def apply_sync(plan, client_root, server_root, backup_dir) -> SyncResult

# 沿用 scanner.resolve_game_root
```

- `ManifestEntry`：`{rel_path: str, format: str}`（相對客戶端遊戲根）。只記伺服器端格式。
- `SyncPlan`：每筆 `{rel_path, action: "copy"|"overwrite"|"skip"}`。`copy`＝伺服器缺、`overwrite`＝存在但內容不同、`skip`＝相同。內容比對用位元組級比較（`filecmp.cmp(a, b, shallow=False)`），避免僅比大小/時間戳漏判。
- `apply_sync`：對 `overwrite` 先把伺服器原檔複製到 `backup_dir` 保留相對路徑，再覆蓋；`copy` 直接建立（含缺少的父目錄）；**絕不刪除**伺服器端 plan 未涵蓋的檔。回傳 `SyncResult`（copied/overwritten/skipped 計數與清單、backup_dir）。
  - 預設 `backup_dir` ＝ `<伺服器遊戲根>/.modpack_translator/sync_bak/<時間戳>/`（時間戳由呼叫端以 `datetime.now()` 產生後傳入，維持 `sync.py` 無時間相依、可測）。僅在實際發生 `overwrite` 時才建立該資料夾。

### 翻譯流程掛鉤（`runner.py`）

翻譯主迴圈處理完每個目標後，若 `format in SERVER_SIDE_FORMATS` 且輸出檔存在，收集 `ManifestEntry(rel_path=target_file 相對遊戲根, format=format)`；翻譯結束時呼叫 `merge_manifest` 聯集寫入。客戶端格式不記。

### 相容既有已翻實例

使用者現有實例都已翻好、尚無 manifest。同步時：
1. `load_manifest`；若不存在或使用者要求重建 → 跑 `ModpackScanner().scan(client, include_translated=True)`，`build_manifest_from_scan` 挑出伺服器端目標建 manifest（這是備援路徑，非每次同步都掃）。
2. 之後正常翻譯會增量維護 manifest，一般同步不需重掃。

### GUI（`main_window.py`）

- 「模組包」群組下新增「伺服器同步」區塊：伺服器資料夾 `QLineEdit` ＋「瀏覽…」＋「同步到伺服器」按鈕。伺服器路徑存 `QSettings` 的 `sync/server_dir`。
- 未填伺服器資料夾 → 按鈕禁用並提示。
- 按下流程：解析 client/server 遊戲根（`resolve_game_root`）→ 取得/建立 manifest → `plan_sync` → 跳**預覽對話框**（「將複製 N、覆蓋 K（會備份）、略過 M」＋清單）→ 確認後在 `SyncWorker`（QThread）跑 `apply_sync`（檔數可達上千，避免卡 UI）→ 完成顯示摘要。
- 伺服器端格式一個都沒有時，明確回報「沒有需要同步的伺服器端內容」（例如 VH 包）。

## 資料流

```
翻譯：scan → process_target（逐目標）→ 寫輸出檔
                                    └→ 伺服器端格式 → 收集 ManifestEntry → merge_manifest

同步：讀 server_dir 設定 → resolve_game_root(client)、resolve_game_root(server)
     → load_manifest（缺則掃描建立）
     → plan_sync（逐檔比對 client vs server）
     → 預覽確認
     → apply_sync（備份→複製/覆蓋；絕不刪）→ 摘要
```

## 錯誤處理

- 伺服器路徑不存在／非資料夾 → 同步前擋下並提示。
- client 與 server 解析後為同一路徑 → 擋下（避免自我覆蓋）。
- 單一檔複製失敗（權限/佔用）→ 記錄該檔失敗、繼續其餘、摘要列出失敗清單，已備份者可還原。
- manifest 損毀無法解析 → 視為不存在，走掃描重建。
- 掃描/複製全程不修改客戶端任何檔（同步是客戶端→伺服器單向讀取來源）。

## 測試（`tests/test_sync.py`，純 pytest、不碰 GUI）

1. `SERVER_SIDE_FORMATS` 分類：`vh_config_json` 不在、`ftbq_snbt`/`ftbq_inline_snbt`/`datapack_json` 在。
2. manifest 讀寫／聯集合併往返（含跨兩次合併去重）。
3. `build_manifest_from_scan` 只挑伺服器端格式，客戶端格式被濾除。
4. `plan_sync` 四情況：伺服器缺→copy、內容不同→overwrite、相同→skip、伺服器端多出的檔→不出現在 plan（絕不刪）。
5. `apply_sync`：實際複製；覆蓋前把原檔備份到 backup_dir 且保留相對路徑；backup 可還原；父目錄自動建立。
6. 端到端：假客戶端（`config/ftbquests/quests/*.snbt` + `kubejs/data/.../skilltree.json` 伺服器端 + `mods/x.jar` + `config/the_vault/...` 客戶端）→ 假伺服器，plan+apply 後斷言**只有伺服器端檔被複製**，mods 與 the_vault 未被複製。
7. 伺服器 `resolve_game_root` 兩種佈局：含 `minecraft/` 子層、與 config/kubejs 直接在頂層。
8. 邊界：manifest 指向的來源檔已不存在 → 該筆略過不報錯；client==server → 擋下。

## 附錄 A：伺服器資料夾佈局

`resolve_game_root` 已能辨識：PrismLauncher/CurseForge（`<instance>/minecraft/`）、MultiMC（`.minecraft/`）、GDLauncher（`files/`）、專用伺服器（`config/`、`kubejs/` 直接在頂層 → 用路徑本身）。同步對伺服器資料夾套用同一函式。

## 附錄 B：調查發現的翻譯覆蓋缺口（非本功能範圍，另案）

同步只能搬「已翻譯的」檔；以下是調查中發現翻譯器**目前未涵蓋**的伺服器端內容，日後可評估補進翻譯器（補進後同步自動涵蓋）：

- **DawnCraft** `global_packs/required_data/DawnCraft_Datapack/`：quest_giver 公會任務（~871 條）、custom advancements、brutalbosses 名牌、loot_table 道具改名。翻譯器 `datapack_json` 目前只掃 `kubejs/data` 與 `config/paxi/datapacks`，未含 `global_packs/required_data`。
- **All the Mons**：`kubejs/data/silentgear/silentgear_traits/*.json`（字面 name/description）、`config/cobblemon_battle_tower/bp_shop_items.json`（GUI display_name，單一 config 檔）。
- **Soulrend**：`config/paxi/datapacks/nerfed_apotheosis/` 迷你王 name、`config/firstjoinmessage.json5`（首次加入聊天訊息，config 根目錄單檔）。
- **Beyond Depth**：`kubejs/data/realmrpg_quests`（questTargetCustomName 混英文）、`kubejs/data/minecraft/advancements` 字面 title、`kubejs/*_scripts` 硬編碼英文（腳本邏輯，需改 JS，超出翻譯器範圍）。
- **NBT 內嵌**：DawnCraft `guild_house.nbt` 的 `CustomName`（需 NBT 讀寫支援）。
