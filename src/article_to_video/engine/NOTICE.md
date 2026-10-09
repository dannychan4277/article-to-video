# 內嵌引擎出處與修改紀錄

這個資料夾的動畫引擎來自 **huashu-art-motion**，作者是**花叔**（alchaincyf，<https://github.com/alchaincyf/huashu-art-motion>），以 MIT 授權釋出，授權原文保留在同資料夾的 `LICENSE`。感謝花叔把整套引擎開源。

外掛只帶「財經圖表」（`t3_finance_chart`）和「Vox 剪報」（`y2_vox`）兩種片段用得到的部分，另外帶了驗收工具與程式作曲範本。複製來源是 2026-10-06 發布包，加上 Danny 本機做第一支財經解說片時的修正（上游 CHANGELOG 2026-10-08 那一條）。

## 帶了什麼

| 檔案 | 用途 |
|---|---|
| `render.py`、`clip.html`、`clip.js` | 片段渲染：一份分鏡設定（JSON）→ 時長精確的片段或靜幀 |
| `clips/t3_finance_chart.js`、`clips/y2_vox.js` | 兩種片段的畫法 |
| `lib/{util,paint,kit,motion,camera,diagram,typo,chart,ui,collage,fonts}.js` | 兩種片段共用的函式庫 |
| `lib/fonts/` | 字型子集（SIL OFL 1.1，見 `lib/fonts/LICENSES.md`、`lib/fonts/OFL.txt`），不屬於 MIT |
| `qa.py`、`subzone_gate.py` | 片段驗收（穩定、動感、框景）、字幕帶圖形檢查 |
| `audio/synth_template.py` | 程式作曲範本（上游 `scripts/audio/synth_艺术史速通.py`，內容沒改） |
| `examples/t3_finance_chart.json` | 外掛自己寫的繁中財經圖表範例（數字取自 FRED 公開資料 DGS2） |
| `clips/a2v_end_card.js` | **外掛自己寫的**片尾卡語法（不是花叔原版）：頻道名＋logo、資料來源、免責與 AI 配音聲明，沿用引擎的字型與版式工具 |

沒帶的：其他語法與場景、示範片、參考影片、上游的範例資料、花叔的卡通形象與角色素材（`toon.js`、`rig_huashu.js` 等，上游明訂不隨 MIT），以及這兩種片段用不到的字型。

## Danny 本機修正（複製時就已經在程式裡，搜「Danny 本机改」）

財經圖表 `clips/t3_finance_chart.js`：
- `data.axisNote`：縱軸沒從 0 開始時的提示文字可以自己寫（第 8、31 行）
- `data.xTicks`：指定哪幾根要寫 x 軸名字（第 94 行）
- `data.push`、`data.pushFrom`：整鏡慢推、接續分鏡從上一段的幅度接著推（第 73 行）
- `series[i].text`：柱上數值改寫成指定文字（第 116 行）
- 標最高那根柱時也在柱頂上方留白，標註文字不會被壓回柱子上（第 53–56 行）；折線有標註時頂上留白（第 57–60 行）
- 標註就是最後一點時，線頭數值淡出讓位（第 126 行）
- 柱正上方的標註，圓點改放柱頂左角，不壓到柱上數值（第 153 行）

Vox 剪報 `clips/y2_vox.js`：
- `data.drift`：停在同一張卡超過 1.5 秒時慢慢推近（第 81 行）
- 截圖縮放用 `imageSmoothingQuality = 'high'`（第 115 行）

## 這個外掛另外做的修改（搜「article-to-video 內嵌」）

- `clip.html`：拿掉 `lib/toon.js`（花叔角色，兩種片段都用不到）。
- `lib/fonts.js`：只列外掛附帶的 5 個字型（clip.js 會逐一載入清單上的每個檔，少一個就開機失敗）。
- `lib/kit.js`：只留 `diagram.js` 用到的 `densify`、`resample`。
- `lib/fonts/LICENSES.md`：只列附帶的字型，補上原清單沒有的 Noto Sans CJK TC 子集。
- `clip.js`：缺字除了告警，也記在 `window.__missingGlyphs`。
- `render.py`：spec 用 UTF-8 讀；`--ffmpeg <路徑>` 指定 ffmpeg（不靠 PATH）；檢查 ffmpeg 結束碼；`--strict-glyphs` 有缺字就在渲染前失敗；頁面 `console.error` 印成 `[page-error]`，跟告警 `[page]` 分開。
- `qa.py`：`--project` 預設是本資料夾（qa.py 跟引擎放在一起）；spec、`qa.json`、`qa.md` 用 UTF-8 讀寫。
- `subzone_gate.py`：`--ffmpeg <路徑>` 指定 ffmpeg。
- `audio/synth_template.py` 只改了檔名。注意它會把輸出寫在腳本旁邊、需要 `soundfile`，要用請先複製出去再改。
- `clips/t3_finance_chart.js`：`data.colors.brand` 品牌色，取代「當品牌用的紅」（頂端色條、小旗、角標、標註圓點、大數字、折線預設色）；沒寫就是原本的紅 `#E3120B`。負數柱的標註改放在那根柱的數值標籤正下方（讓開它橫跨到的負數柱與標籤，init 裡最低那根柱下面多留文字塊的高度）：原版的斜向標註往下拉，會壓在柱底下方的數值標籤上（16:9、9:16 都會；tests/test_render_clip.py 有靜態圖測試）。`data.colors.ink`：要讀的字與記號（角標、標註圓點、大數字）用的顏色，沒寫就跟 brand 一樣——品牌色太淺時 brand.py 給它同色相加深的版本，色條、小旗照用原色。折線下方漸層改跟著線的顏色（原本寫死紅色）。K 線漲跌的紅綠不變。
- `clips/t3_finance_chart.js`（直式 9:16，#10）：
  - 左右邊距讓開 `safe.left`／`safe.right`（短影音平台右側的按鈕欄）；頂端色條畫在 `safe.top`（原版畫在 0）。safe 沒寫左右、上方時跟原版一樣。
  - 原版「竖屏」的「標題最多 3 行、多一格刻度、底部多留 260」改成只在讓開 safe 之後內容框真的很高（高 > 寬 × 1.25）時才用；直式成片上下讓給平台介面與字幕後內容框接近方形，照原版會擠出 8 條刻度、長標題吃掉整個圖區。小字放大兩成、邊距 70、x 軸抽稀 n/6 照舊。
  - 圖區放不下標註留白時直接報錯並說明（原版會無限加刻度，當掉時只看到 `Invalid array length`）。
  - 時間軸（折線、K 線）的 x 軸名字互相重疊就不寫：被標註那根優先，其餘照原規則（含 `data.xTicks`）由左到右放得下才寫。沒有重疊時跟原版一樣；直式畫面窄、字放大兩成，原版的 n/6 抽稀常常疊在一起。
- `clips/y2_vox.js`（直式 9:16，#10）：相機的縮放與「框在不在鏡頭裡」改用讓開 `safe.left`／`safe.right` 之後的寬度，畫面中心也左右平移（原版只處理上下）。safe 沒寫左右時跟原版一樣。
- `clips/a2v_end_card.js`（外掛自己的片尾卡語法）：接 `safe`（直式成片的平台介面區）。
- 配樂：外掛的 `article_to_video/music.py` 照 `audio/synth_template.py` 的樂器與混音做法改寫（加法合成、撥弦、ADSR、鋸齒鋪底、合成殘響、電平對齊與軟限幅），改用 numpy 頻域濾波、不需要 scipy／soundfile，編曲另寫成三首 8 小節可循環的原創樂句。範本檔本身沒動。
