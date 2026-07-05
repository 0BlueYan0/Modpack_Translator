# Oracle Wiki(MDX)翻譯支援 設計文件

日期：2026-07-06
狀態：已與使用者確認方向（自動整合、務實切段器 + 沿用現有 token 機制、範圍涵蓋所有 Oracle 書）

## 問題

oritech 等模組內建「遊戲內 wiki」，由 **Oracle** 指南 mod（`oracle_index-neoforge-*.jar`，
class 於 `rearth/oracle/`）渲染。其內文不是 lang 檔也不是 Patchouli，而是 **MDX**
（Markdown + JSX 元件），打包在提供書的模組 jar 內：

```
assets/oracle_index/books/<book>/content/**/*.mdx     ← oritech：約 165 篇
assets/oracle_index/books/<book>/docs/**/*.mdx        ← oracle_index 自身：5 篇（開發/使用指南）
assets/oracle_index/books/<book>/<root>/**/_meta.json ← 分類/頁面導覽標題
```

翻譯工具目前只支援 lang JSON / Patchouli / 任務(SNBT) / KubeJS，**完全不掃 MDX**，
故整個 wiki 內文維持英文。（wiki 的**外框 UI** 標籤在 `oracle_index` 自身 lang，已有 zh_tw；
本設計不處理外框，只處理**書的內文**。）

## 背景：格式與交付機制（已由反編譯字串與內建範本證實）

- viewer 原生支援翻譯：`rearth/oracle/docs/V1DocsFormat` 內含 `/content`、`/docs`、
  `/translated/`、`locale` 字串，`DocsIndexer` 以 `net.minecraft.client.resources.language.I18n`
  依遊戲語言選檔。
- **實證**：oritech 已內建法文於
  `assets/oracle_index/books/oritech/translated/fr_fr/content/<與 content 相同結構>`。
- 因此交付規則：把譯文放到
  **`assets/oracle_index/books/<book>/translated/zh_tw/<root>/<相同相對路徑>`**，
  viewer 在遊戲語言為 `zh_tw` 時即載入，否則 fallback 回英文原檔。原英文檔不動。

法文範本 `fluxite.mdx` 顯示的翻譯規則：frontmatter 只翻 `title`，保留 `id/icon/type/related_items`；
正文翻散文；`[連結文字](目標)` 只翻文字留目標；`<center>`、`<ModAsset location=... width={..}/>`
等 JSX 標籤與屬性整段保留。

## 方案總覽

把 MDX 當成**又一種可翻格式**，自動納入正常掃描/翻譯流程（不加 GUI 開關）。
採**務實切段器**：比照現有 `read_inline_snbt_text`/Patchouli 處理器自建切段器，
沿用 `preprocessor.encode()/decode()` 的 `{N}` token 機制保護結構，再走既有 translator
（glossary / 快取 / 驗證 / 失敗回退），最後 jar_inject 寫入 `translated/zh_tw/<root>/`。
**不引入 markdown 函式庫**（避免加依賴、AST 重繪改動空白/格式的風險，且與工具 regex 切段風格不合）。

## 1. 偵測層（`scanner.py`）

在 `_scan_jar` 新增分支，偵測 jar 內路徑符合：

```
parts = ['assets', <ns>, 'books', <book>, <root>, ...]  且 <root> ∈ {content, docs}
且 <root> 之後不含 'translated'（不重掃已產生的譯檔）
結尾為 '.mdx'  → format = 'oracle_mdx'
檔名為 '_meta.json' → format = 'oracle_meta'
```

- 沿用剛核准的「寫正規目標 + 讀既有重用」架構：
  - `target_path_in_jar`（寫入）＝把來源路徑的 `<root>` 之前插入 `translated/zh_tw/`：
    `…/books/<book>/translated/zh_tw/<root>/<rel>`。
  - `existing_path_in_jar`（讀既有，供 diff/重用/冪等）＝同上；首次不存在 → None。
- `output_mode = 'jar_inject'`，`mod_id = <book>`。
- 需翻判定：來源抽出的可翻字串集合 `diff` 既有譯檔後非空即產生 target（沿用 `diff_keys` 思路）。

## 2. MDX 切段與重建（新模組 `pipeline/mdx.py`）

獨立模組避免 `preprocessor.py` 膨脹；內部沿用 `preprocessor` 的 `encode/decode`。

- `split_frontmatter(raw) -> (frontmatter_lines, body)`：切出開頭 `---\n…\n---` 區塊。
- `extract_mdx(raw) -> dict[str, str]`：回傳**有序** `{穩定鍵: 原文}`，鍵形如
  `fm.title`、`fm.custom.<label>`、`body.<序號>`，供沿用 `translate_dict`。
- `rebuild_mdx(raw, translations) -> str`：按同一切段規則把譯文填回，未提供譯文的段落保留原文。
- 切段規則：
  - **Frontmatter（逐行定點編輯，不整份重序列化）**：翻 `title:` 的值；翻 `custom:` 子區塊的
    **標籤鍵**；保留 `id/type/icon/related_items` 與 `custom` 的值（數字/單位）。
  - **正文（依空行切塊分類）**：
    - `` ``` `` code fence 整塊保留。
    - JSX 塊（首非空字元為 `<` 的元件）：`<tag …>`/`</tag>`/`<… />` 行保留，塊內非標籤文字行翻譯。
    - 標題 `#{1,6} 文字` → 翻文字、保留 `#` 與空白。
    - 清單 `- `/`* `/`+ `/`N. ` → 翻標記後文字。
    - 其餘為段落（可跨多行軟換行）→ 整段為一個翻譯單位。
  - **行內 token（在每個可翻文字段套用）**：`[文字](目標)` 的 `](目標)` 為硬 token（`目標`
    含 `@ns:ref` 或 `../相對路徑`）；`` `code` ``、行內 `<…/>`/`<tag>` 為硬 token；
    `**`/`*`/`_`/`~~` 強調標記為軟 token（掉了不算失敗）。以 `_preserves_required_tokens` 驗證。

## 3. 翻譯與寫回（`runner.py`）

`process_target` 新增兩格式，最大化重用既有機制：

- `oracle_mdx`：`raw = 讀 jar 源檔` → `en = extract_mdx(raw)` →
  `zh_existing = extract_mdx(既有譯檔) or {}` → `translate_dict(en, zh_existing, …)`
  （**沿用 glossary / 快取 / 驗證 / 失敗回退、計入統計**） → `rebuild_mdx(raw, result∪existing)` →
  `write_jar_text(jar, target, 新內容)`。
- `oracle_meta`：`_meta.json` 為 `{檔名/目錄: 標題}`；`en = {鍵: 標題}`（略過 `*.sh` 等雜項鍵）→
  `translate_dict` → 寫回 `translated/zh_tw/<root>/…/_meta.json`（值已翻、鍵保留）。
- `read_existing_target` / `read_target_strings` 增對應分支（讀 existing_path 的 mdx/json）。
- 沿用「result 為空但既有在別處、正規路徑尚不存在 → 仍建立」的建檔條件（本例首次即 result 非空）。

## 4. 輸出/交付（`patcher.py`）

- 新增 `write_jar_text(jar_path, path_in_jar, text: str)`：MDX 為純文字，
  以 UTF-8 bytes 走既有 `_rewrite_jar`（沿用簽章剝除、重複 entry 容錯）。
- `_meta.json` 用既有 `write_jar_json_file`。

## 5. 翻什麼／保留什麼（總表）

| 位置 | 翻譯 | 保留 |
|---|---|---|
| frontmatter | `title` 值、`custom` 標籤鍵 | `id`/`type`/`icon`/`related_items`、`custom` 值 |
| 正文散文/標題/清單 | 文字 | `#`、清單標記、縮排 |
| 連結 `[文字](目標)` | 文字 | `](目標)`（`@ns:ref`、`../路徑`） |
| JSX `<Callout>`/`<center>`/`<ModAsset/>` | 標籤內部文字（如 Callout 內文） | 標籤名與屬性、自閉合元件整段 |
| code fence / 行內 code | — | 整段 |
| `_meta.json` | 值（標題） | 鍵（檔名/目錄）、`*.sh` 雜項 |

## 6. 一致性、在地化、安全網

- **標題一致性**：`title` 走一般 translator + glossary + 快取，使 wiki 標題與物品譯名
  經用語庫保持一致；**不另做 `id`→lang 反查**（YAGNI）。
- **語言碼**：`zh_tw`（小寫，同法文 `fr_fr` 慣例與遊戲設定）。
- **安全網**：沿用「結構 token 沒保住 → 該段回退英文」；單篇即使部分段落未翻仍能正常渲染，
  **絕不弄壞版面**。**冪等**：再跑時既有譯檔進 diff，已翻跳過（零重複成本）。

## 7. 測試策略（TDD）

- `pipeline/mdx.py` 單元：
  - frontmatter title / custom 標籤抽取 + 重建；`id/icon` 等保留。
  - 正文各類塊 round-trip（以 echo/identity 翻譯應結構完全相同）：段落、標題、清單、
    連結（文字翻、目標留）、JSX 保留、code fence 保留。
  - token 保留與失敗回退。
- `scanner.py`：偵測 `content|docs/**.mdx` 與 `_meta.json`；`target_path_in_jar` 為
  `translated/zh_tw/<root>/…`；既有譯檔重用（diff 為空不重掃）。
- `runner.py` 端到端：假 translator 處理 `oracle_mdx` → jar 出現
  `translated/zh_tw/content/x.mdx`，散文已翻、`<ModAsset/>`/連結目標/frontmatter `id` 完好；
  `oracle_meta` 值已翻。
- 全套件維持全綠。

## 待確認／風險

- **`docs/` 型書的譯文路徑**推定為 `translated/<locale>/docs/…`（法文範本只證實 `content/`）。
  實作時再核對 `V1DocsFormat`；若無法確認，v1 先只做 `content/`（＝oritech，玩家面向重點），
  `docs/` 之後補。玩家面向不受影響。
- MDX 為手寫切段，邊角語法（巢狀 JSX、表格）可能未涵蓋 → 靠 per-段回退英文兜底，不弄壞渲染。

## 不做（YAGNI）

- 不引入 markdown 函式庫 / AST 重繪。
- 不做 `id`→lang 物品名反查（用語庫已足夠一致）。
- 不抓線上 wiki（wiki.sinytra.org）；只翻 jar 內建 MDX。
- 不加 GUI 開關（依使用者決定：自動，與其他格式一致）。

## 影響檔案

- 新增：`src/modpack_translator/pipeline/mdx.py`、`tests/test_mdx.py`、`tests/test_oracle_wiki_scan.py`。
- 修改：`scanner.py`（偵測 + target）、`runner.py`（兩格式處理）、`patcher.py`（`write_jar_text`）。
- 版本號 bump + 同步 Downloads 執行版（見既有慣例）。
