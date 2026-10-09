# 專案檔案的格式（你要寫的 JSON）

專案資料夾（`new` 印出來的「專案資料夾」）裡，你要寫的只有兩個檔：`narration.json`（旁白稿）、`shots.json`（分鏡設定）。
都是 UTF-8 的 JSON。寫錯了 `preview` 會一次列出全部問題，每一條都說是哪個分鏡、哪個欄位，照著改就好。

能直接跑的完整例子（外掛自帶，不是任何人的文章）：`${CLAUDE_PLUGIN_ROOT}/src/article_to_video/sample_project/` 的
`narration.json` 和 `shots.json`（一個財經圖表分鏡＋一個接續分鏡）。Vox 分鏡的例子在 `vox-rules.md`。

## narration.json（旁白稿）

```json
{"segments": {
  "s01": "兩年期殖利率，平均四點五八。十年期只有三點九六，曲線倒掛。",
  "s02": "短天期反而比長天期高，相差零點六二個百分點。這通常代表市場預期降息。"
}}
```

- 每一個鍵是一個**旁白段**，照寫的順序唸，段與段之間自動留一點空白。鍵用英數字（s01、s02…），改內容不要改鍵。
- 一段 25–60 字，規則見 `narration-rules.md`；寫完跑 `lint`。

## shots.json（分鏡設定）

```json
{"shots": [
  {"id": "SH01", "note": "給自己看的備註（不上畫面）",
   "start": {"segment": "s01"},
   "chart": {
     "data": {"title": "短天期殖利率反而比較高", "unit": "美國公債殖利率，2023 年平均，%",
              "source": "來源：FRED（DGS2、DGS10），年平均自算", "chart": "bar", "decimals": 2, "suffix": "%",
              "series": [{"label": "兩年期", "value": 4.58}, {"label": "十年期", "value": 3.96}],
              "highlight": {"index": 0, "text": "短天期較高"}},
     "cues": [
       {"at": 0, "kind": "title"},
       {"at": {"segment": "s01", "word": "殖利率"}, "kind": "bar", "data": {"index": 0}},
       {"at": {"segment": "s01", "word": "四點五八", "edge": "end"}, "kind": "highlight"},
       {"at": {"segment": "s01", "word": "十年期"}, "kind": "bar", "data": {"index": 1}}]}},
  {"id": "SH02", "start": {"segment": "s01", "word": "曲線"}, "continues": true,
   "chart": {"data": {"highlight": {"index": 1, "text": "長天期較低"}},
             "cues": [{"at": {"segment": "s01", "word": "倒掛"}, "kind": "highlight"}]}}
]}
```

### 分鏡

| 欄位 | 說明 |
|---|---|
| `id` | 穩定的名字（SH01…，英數字）。審片頁的**分鏡編號**是它在清單裡的位置（1 起算），id 不會變 |
| `start` | 切入點（錨點）。第一個分鏡可以不寫（從 0 開始）；其他都要寫 |
| `chart` 或 `vox` | 財經圖表或 Vox 分鏡，二擇一（Vox 見 `vox-rules.md`） |
| `continues` | `true`＝接續上一個分鏡：同一張圖第一幀就畫好、只換這一句的標註，切點看不出來 |
| `derived` | 推算出來的數字要宣告算式：`[{"value": "0.62", "formula": "4.58 - 3.96", "note": "兩年期減十年期"}]` |
| `note` | 備註，不上畫面 |

### 錨點（畫面事件對上旁白的位置）

`{"segment": "s02", "word": "曲線", "edge": "start", "offset": 0, "occurrence": 1}`

- `segment`：哪個旁白段。只寫這個＝那段開頭。
- `word`：那段裡的幾個字（一字不差）。同一段出現兩次以上要加 `occurrence`（第幾次），或寫長一點讓它只出現一次。
- `edge`：`"start"`（開始唸那個詞，畫面會提早 0.15 秒）或 `"end"`（唸完那個詞）。
- `offset`：再往後（正）或往前（負）幾秒。
- 改旁白時，被錨點用到的詞如果改掉了，錨點也要跟著改（`preview` 會說找不到哪個詞）。

### 財經圖表 `chart.data`

| 欄位 | 說明 |
|---|---|
| `title` | 一句結論（不是「XX 走勢」這種名稱）。橫式最多兩行 |
| `unit` | 副標：指標與單位，例「美國十年期公債殖利率，%」 |
| `source` | 來源行，例「來源：作者原文」「來源：FRED（DGS2）」。用 `publicData` 時自動寫，不要自己寫 |
| `chart` | `"bar"`（柱，一律從 0 起）、`"line"`（折線；不從 0 起時副標自動加「縱軸未從 0 開始」） |
| `series` | `[{"label": "兩年期", "value": 4.58}]`；`text` 可蓋掉柱上的數字（例 `"＞50%"`），`color` 單根改色 |
| `decimals`、`prefix`、`suffix` | 柱上、線頭數字的小數位數（預設 1）與前後綴 |
| `highlight` | 標註的預設值 `{"index": 0, "text": "意思", "sub": "第二行（選填）"}` |
| `xTicks` | 折線的 x 軸只在這些位置寫名字（索引清單），例每年第一個點 |
| `badge` | 右下角小字，例「示意」（真實數據不要寫） |
| `axisNote` | 換掉「縱軸未從 0 開始」這句（很少需要） |
| `push` | 整鏡慢慢推近的總量（預設會自動算，不用寫；`0`＝不推，會被自動檢查判成「沒在動」） |
| `publicData` | 公有數據：見下面 |

### 財經圖表的 cue（畫面事件）

| kind | 欄位 | 畫面 |
|---|---|---|
| `title` | `at` | 版式、標題、格線。通常 `"at": 0` |
| `bar`／`line` | `at`、柱可寫 `"data": {"index": 0}`（或清單）只長那幾根 | 第一個 bar／line 決定圖型與主生長；帶 index 的柱在自己的時刻長出來 |
| `highlight` | `at`、`text`、`sub`、`data.index` | 標註（圓點＋引線＋字）。**一個分鏡只認第一個**，要換標註就開接續分鏡 |
| `number` | `at`（數字落定的時刻）、`text`（說明）、`data.value`、`data.prefix`／`suffix`／`decimals` | 右上大數字，從 0 往上滾。一個分鏡只放一個；**下跌的數字不要用** |

`at`：數字＝這個分鏡開始後幾秒；或錨點。資料類 cue（bar、line、highlight、number）**一律用錨點**，對上旁白唸到的詞。

### 公有數據（自動抓、自動寫出處）

```json
"data": {"title": "通膨預期一年來大致持平", "unit": "美國十年期通膨預期，%", "chart": "line", "decimals": 2, "suffix": "%",
         "publicData": {"series": "DGS10-DFII10", "from": "2025-01-01", "to": "2025-12-31", "every": "month"}}
```

- `series`：只能用 `DGS10`（十年期）、`DGS2`（兩年期）、`DFII10`（實質殖利率）、`THREEFYTP10`（期限溢價），或兩個相減（`"DGS10-DFII10"`＝通膨預期、`"DGS10-DGS2"`＝利差）。
  標普五百等指數、`T10YIE` 一律不抓（版權）：只能用文章裡寫的數字或文章裡的圖。
- `every`：`day`／`month`／`quarter`／`year`（期間平均）。`from`、`to` 都要寫（YYYY-MM-DD）。
- 寫了 `publicData` 就不要寫 `series`、`source`。第一次抓要有網路；抓過的存在專案裡，之後重建不用網路。

## settings.json（設定快照，通常不用動）

第一次預覽時會從作者的使用者設定複製進來（頻道名、聲音、配樂、品牌色、預設比例）。只改這一支片時用
`settings set --project "<專案>" --voice 曉雨 …`，不要直接手改。`tail`＝片尾卡秒數（預設 5）。
