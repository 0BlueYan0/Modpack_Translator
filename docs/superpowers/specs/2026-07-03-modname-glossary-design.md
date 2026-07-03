# 模組名譯名與自訂用語庫 設計文件

日期：2026-07-03
狀態：已與使用者確認方向（方案 A：併入現有 Glossary 管線）

## 問題

模型把模組名稱（如 Twilight Forest、Applied Energistics 2）當作專有名詞原樣保留英文，
`_looks_like_proper_noun_phrase` 的放行邏輯也接受這種原樣返回。但許多模組名有社群通行
的繁中譯名（暮光森林、應用能源 2），使用者希望譯文採用這些譯名，並能自行補充或覆蓋。

「原樣返回放行」有三個入口，任一沒堵住都會讓英文名復活：

1. 模型輸出關卡：`runner.py` `_translate_validated` 的 `accept_identical_proper_noun=True`。
2. 快取讀取：`runner.py` 與 `batch_prefill.py` 讀快取時同樣開啟專有名詞豁免，
   舊的「英文→英文」快取條目會一直被接受。
3. 既有譯文檢查：`preprocessor.py` `is_usable_translation` 對任務標題
   （`_is_quest_title_key`）接受「既有翻譯 == 原文」視為譯者選擇。

## 方案總覽

不新造「字詞替換」系統。把模組名視為另一層用語庫，與官方用語庫合併後走同一條
既有管線（prompt 注入 + 整串短路），再補上「事後保證」與三個入口的守門。
一份對照表同時餵給 prompt、預比對、事後保證、專有名詞判定四個環節，行為一致。

## 1. 資料層：三層用語庫，合併成一個 Glossary

| 層 | 檔案 | 來源 | 優先序 |
|---|---|---|---|
| 自訂 | 使用者設定目錄 `custom_glossary.json` | GUI 表格編輯器 | 高 |
| 模組名 | `assets/glossary/modnames_zh_tw.json` | 專案預建、隨版本 commit | 中 |
| 官方 | `assets/glossary/zh_tw_{版本}.json`（現有） | `scripts/build_glossary.py`（現有） | 低 |

- `glossary.py` 新增 `load_merged_glossary(official_path, modnames_path, custom_path)`：
  三份 dict 依優先序合併成一個 `Glossary` 物件，下游管線不感知層數。
- 檔案格式與現有一致：`{"英文": "繁中譯名", ...}` 的 JSON dict。
- 自訂條目譯名為空字串 = 刪除該詞條（保留英文），可壓掉預建表或官方表中不想要的條目。
- 預建表初版人工整理熱門模組約 100–200 條（CurseForge 熱門榜 × 中文社群通行譯名），
  品質重於數量，冷門模組靠自訂補充。
- `LanguageConfig` 新增欄位：`modnames_glossary_path: str | None`、
  `custom_glossary_path: str | None`（語義同現有 `glossary_path`：None/空字串＝停用）。
  GUI 與 CLI 各自解析路徑後傳入，管線不依賴 GUI。
- 自訂檔存於使用者設定目錄（`QStandardPaths.AppConfigLocation`），
  更新程式（覆蓋 src 發行版）不會清掉使用者的自訂表。

## 2. 套用點：既有機制免費獲得 + 三個入口的守門

合併後的 Glossary 自動獲得現有機制：

- prompt 尾端 `[Glossary]` 區塊注入（引導模型翻對句中出現的模組名）。
- 整串（trim 後）命中時直接回譯名，不呼叫模型。

新增守門規則（本次的核心修復）：

> **src == dst 且整串命中用語庫的原樣返回，一律不放行。**

套用在三處，判定為不放行後各自的出路都會落到 exact_match，零 API 成本：

1. 模型輸出關卡：原樣返回命中時不視為合格，直接以 exact_match 譯名取代模型輸出
   （不是單純拒絕——拒絕會讓重試耗盡而進 Failed Items）。此路徑實務上很少觸發：
   整串命中的字串在呼叫模型前就被既有短路擋下，此處是一致性保險。
2. 快取讀取：命中的「英文→英文」舊快取視為無效，落回翻譯流程，
   由 exact_match 短路直接得到譯名。
3. 既有 zh 檔譯文檢查：既有翻譯 == 原文且命中用語庫 → 進 diff 重翻，
   同樣被 exact_match 短路。

實作：`is_usable_translation` 增加可選參數 `glossary: Glossary | None = None`，
命中判斷用 `glossary.exact_match(src)`；三個呼叫點傳入 glossary。

## 3. 事後保證：`Glossary.enforce(text) -> str`

模型輸出通過所有現有驗證**之後**，對輸出做一次替換。定位是後掛的 polish，
不是驗證前的修補——避免把整句未翻譯的英文塞進一個中文詞而混過 CJK 檢查。

規則：

- 區分大小寫、整詞邊界（沿用現有 lookaround 寫法，不用 `\b`）、長詞優先、
  容忍複數形 `(?:e?s)?`。區分大小寫是為了避免動詞 create 被誤傷。
- 多字詞條目（Twilight Forest、Applied Energistics 2）：句中殘留即替換。
  例：「歡迎來到 Twilight Forest！」→「歡迎來到暮光森林！」
- 單字詞條目（Create、Quark）：只在整串（trim 後）完全等於該詞時替換，句中不動——
  避免「Create New World」被打成「機械動力 New World」。句中的單字名交給
  prompt 注入讓模型自行判斷語境。
- 替換後重跑 `_preserves_required_tokens`；失敗則退回未替換版本（fail-safe，
  接住替換到還原後 token 內容的極端情況）。
- 套用對象：模型新輸出、快取命中值。**不碰**既有 zh 檔中與原文不同的人工翻譯
  （完整改寫是譯者的選擇，盲改太侵入）。

## 4. GUI

垂直空間約束：主視窗預設大小已擁擠，本次新增控件淨高度不得增加。做法是把
「選項」群組從三列收斂成兩列：

- 列 1：翻譯模組 (.jar)、翻譯任務書 兩個 checkbox ＋ **重試次數**（自 retry_row 併入）。
- 列 2：官方用語庫 combo ＋ **「模組名譯名」checkbox（預設開啟）** ＋
  **「自訂用語…」按鈕** ＋ help 圖示。

淨效果：功能變多、列數少一列。不做整體排版重構（另案處理）。

「自訂用語…」開啟 QDialog：

- 兩欄 QTableWidget（英文原文｜繁中譯名）、新增列/刪除列按鈕、儲存/取消。
- 儲存寫回使用者設定目錄的 `custom_glossary.json`。
- 對話框內註明：譯名留空 = 保留英文（停用該詞條，可覆蓋預建表）。
- JSON 讀寫獨立成可測試的純函式層，對話框只是它的介面。

設定持久化：「模組名譯名」checkbox 狀態存入 QSettings（同現有 glossary_version 模式）。

## 5. 測試

- 合併：優先序（自訂 > 模組名 > 官方）、空譯名刪條目、任一層缺檔時的容錯。
- enforce：大小寫敏感（create 不動、Create 整串才換）、多字句中替換、複數容忍、
  token 保全失敗回退、`{N}` 佔位符不受影響。
- 守門三入口各一個回歸測試：模型輸出原樣返回命中詞不放行、
  快取「英文→英文」命中詞不採用、既有譯文 == 原文命中詞進 diff。
- GUI：只測 custom_glossary.json 讀寫層（headless），不測 QDialog 互動。

## 風險與取捨

- 既有翻譯包刻意保留英文的模組名，這次會被換成中文——這是此功能的目的；
  想保留英文的名字，在自訂表留空譯名即可。
- 單字模組名與常用英文單字同形（Create）在**標題整串**情境仍可能誤替換
  （任務標題「Create」幾乎都指模組，接受此風險；不接受時同樣用自訂表壓掉）。
- 預建表譯名品質依賴人工整理；有爭議的名稱寧缺勿錯，留給自訂表。
