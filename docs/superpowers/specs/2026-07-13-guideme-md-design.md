# GuideME 指南(Markdown)翻譯支援 設計文件

日期：2026-07-13
狀態：實作中（使用者回報 AE2 按 G 指南未翻譯；沿用 oracle wiki(v1.7.3) 既有架構擴充）

## 問題

AE2 按 G 開啟的遊戲內指南由 **GuideME** 函式庫（`guideme-21.1.16.jar`）渲染，
頁面是打包在各 mod jar 內的 **Markdown（GuideME 風味：YAML frontmatter + JSX 元件）**：

```
assets/ae2/ae2guide/**/*.md                        ← AE2 本體：125 頁
assets/<addon-ns>/ae2guide/**/*.md                 ← AE2 生態系 addon 各自貢獻頁面
assets/<ns>/guides/<guide-ns>/<guide-name>/**/*.md ← GuideME 預設佈局（Powah、Logistics Networks）
assets/<ns>/guide/**/*.md                          ← 自訂資料夾（Little Big Redstone）
```

掃描器只認 lang / Patchouli / 任務 / KubeJS / **oracle wiki（assets/oracle_index/books/…）**，
完全不掃上述路徑 → All the Mons 全包 **14 個 jar、297 頁英文指南**整包維持英文。

## 背景：交付機制（已由 GuideME v21.1.16 原始碼 + 包內實證確認）

- `guideme/internal/util/LangUtil.getTranslatedAsset(assetId, language)` =
  `assetId.withPrefix("_" + language + "/")`——譯文放**指南根目錄下的 `_<lang>/` 子資料夾**，
  相對路徑與原文相同。
- `GuideReloadListener` 以 `resourceManager.listResources(contentRoot, *.md)` 跨**所有命名空間**
  列舉頁面；目前語言的譯頁存在則用之，否則**逐頁 fallback** 回預設語言 → 部分翻譯安全。
- 頁面 id 經 `stripLangFromPageId` 去除語言前綴 → 譯頁內的**相對連結/圖片路徑不需改寫**。
- **包內實證**：Powah 內建 `assets/powah/guides/powah/book/_fr_fr/**`、
  Little Big Redstone 內建 `assets/little_big_redstone/guide/_zh_cn/**`，均為此格式。
- 交付規則：**`<指南根>/_zh_tw/<相同相對路徑>.md`**，jar_inject 寫回各來源 jar。
  語言碼一律小寫（LangUtil 以小寫比對；vanilla 語言清單含 zh_tw）。

## 偵測層（`scanner.py`）

`_scan_jar` 的 else 鏈在 oracle 分支後新增 `_scan_guideme_page`：

- 指南根判定（涵蓋包內全部三種形態，不硬編 mod 名）：
  - `assets/<ns>/guides/<a>/<b>/**.md`（len≥6 且 parts[2]=="guides"）→ root = `assets/<ns>/guides/<a>/<b>`；
    parts[2]=="guides" 但層數不足 → 跳過（無法安全定位 root）。
  - 其餘 `assets/<ns>/<folder>/**.md`（len≥4）→ root = `assets/<ns>/<folder>`。
  - `assets/<ns>/<file>.md`（len==3）→ 跳過。
- **資格審查**（排除 credits/README/lang 目錄雜訊）：root 子樹內至少一個 `.md` 的
  frontmatter 含頂層 `navigation:` 鍵（GuideME 專屬慣例；全語料 297/297 皆有）。
  每 (jar, root) 快取判定結果。
- 來源排除：相對路徑首段符合 `^_[a-z]{2,3}_[a-z]{2,4}$`（既有翻譯樹：`_fr_fr`、`_zh_cn`、
  我們自己的 `_zh_tw`）→ 不當來源（冪等）。
- `target_path_in_jar = <root>/_zh_tw/<rel>`；`existing_path_in_jar` 依 `_zh_tw`/`_zh_TW`
  候選查 name_set。需翻判定沿用 `_oracle_mdx_needs_translation`（改名共用；extract → diff_keys）。
- `format="guideme_md"`、`output_mode="jar_inject"`、`mod_id=<ns>`。

## 切段層（`pipeline/mdx.py` 擴充；oracle 與 GuideME 共用）

全語料（297 頁）統計驅動的改動：

1. **Frontmatter 巢狀 `navigation: title:`**（297/297 頁；現行只認頂層 `title:` → 全漏）：
   `navigation:` 區塊內、與首個子鍵同縮排的 `title:` 值可翻；支援引號變體
   `title: "Logic Arrays"`（29 頁）——引號保留為字面、翻內文。
   `parent/icon/position/icon_components`、頂層 `categories:`/`item_ids:` 一律保留
   （categories 參與 `<CategoryIndex>` 比對，翻了會壞索引）。
2. **JSX 容器透明化**（語料 550 處配對標籤內含散文；現行 `_jsx_block_end` 掃到首個 `/>`
   會把容器內散文整段吞掉，未閉合標籤更會吞到檔尾）：
   - 開標籤（可跨多行，至 `>`）：`/>` 結尾 → 自閉合，整段保留（既有行為）。
   - 否則為容器：保留開標籤行，**內部行遞迴切段**（散文/標題/清單/表格照翻，
     巢狀標籤照保留），至同名閉標籤（同名巢狀計深度）；**找不到閉標籤 → 視為
     立即自閉合**，只保留開標籤行（容忍 AdvancedAE `<ItemImage …>`、`<CategoryIndex>`
     等未閉合的手寫頁）。
   - `<Callout>` 特例維持現狀（測試鎖定；oritech 語料為單段散文）。
3. **行首內聯標籤散文**（`<Color …>south</Color>, …` 段落續行、`<a href=…>Facades</a> …`）：
   行內所有 `<…>` 皆同行完結、去標籤後仍有字母/數字 → 視為**散文**（可翻，段落不斷開）；
   去標籤後無殘餘（`<a name="x"></a>`）→ 字面保留。
4. **表格**（29 頁）：`|` 起始的連續行逐**列**切段；分隔列（`|---|:---:|`）字面保留。
   純數值列由 `_is_translatable_entry` 既有過濾自然跳過，不送 API。
5. **純圖片段**（`![Plus](assets/diagrams/plus.png)`）：去除 `![alt](src)` 構造後無
   字母/數字 → 字面保留（避免產生「模型原樣返回 → 永遠待翻」的新殘留類）。
   純連結段（`[Getting Started](getting-started.md)`）**仍可翻**（連結文字是顯示文案）。

抽取規則對英文源檔與既有譯檔**必須一致**（位置鍵對齊 diff 的前提），以上規則均滿足。
oracle 既有譯檔在規則變動處（多段 Callout、圖片段）可能一次性位移重對齊，
由內容鍵快取吸收，不重送 API。

## 翻譯與寫回（`runner.py`）

`guideme_md` 直接路由至既有 oracle mdx 處理器（其邏輯全由 target 路徑驅動、無 oracle 專屬）：
`process_target` / `read_target_strings` / `read_existing_target` 三處
`== "oracle_mdx"` → `in ("oracle_mdx", "guideme_md")`。寫回沿用 `write_jar_text`
（簽章剝除、重複 entry 容錯、內容未變不重寫）。

## GUI（`main_window.py`）

格式顯示名新增 `guideme_md: "GuideME 指南內文 (Markdown)"`。無新開關（與 oracle 同：自動納入）。

## 影響範圍（All the Mons 實測）

AE2(125)、ExtendedAE(46)、Powah(28)、Little Big Redstone(25)、Logistics Networks(23)、
AdvancedAE(13)、AppliedFlux(12)、ae2wtlib(8)、MEGA Cells(7)、ExpandedAE(5)、
AE2NetworkAnalyzer(2)、ae2importexportcard(1)、arseng(1)、MERequester(1)＝**297 頁**。
（Powah/LBR 的 `_fr_fr`/`_zh_cn` 等 498 個既有譯檔已被來源排除規則跳過。）

## 測試策略（TDD）

- `test_guideme_md.py`（切段）：巢狀 nav title（含引號、值含冒號）、容器內散文翻譯、
  未閉合標籤收斂、行首內聯標籤散文、GameScene 多行標籤 byte-exact round-trip、
  表格逐列、純圖片段保留；既有 `test_mdx.py` 全綠（oracle 行為不回歸）。
- `test_guideme_scan.py`：三種 root 形態、`_<lang>` 來源排除、navigation 資格審查
  （credits/lang 雜訊不入）、target/existing 路徑、已譯冪等（diff 空 → 0 目標）。
- `test_guideme_run.py`：假譯者端到端 → jar 出現 `<root>/_zh_tw/**.md`、散文已翻、
  結構逐字保留、冪等不重寫（比照 oracle run 測試）。

## 不做（YAGNI）

- 不翻 `categories:` 值、不做 zh_cn→zh_tw 轉換複用、不翻搜尋索引、不加 GUI 開關。
- 不引入 markdown/YAML 函式庫（沿用 regex 逐行定點編輯）。

## 影響檔案

- 修改：`pipeline/mdx.py`、`pipeline/scanner.py`、`pipeline/runner.py`、`gui/main_window.py`、
  `version.py`(1.8.0)、`pyproject.toml`、`uv.lock`。
- 新增：`tests/test_guideme_md.py`、`tests/test_guideme_scan.py`、`tests/test_guideme_run.py`。
- 同步 Downloads 執行版（既有慣例）。
