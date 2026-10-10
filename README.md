# article-to-video

把你自己寫的財經文章，做成一支 2–4 分鐘的解說影片的 Claude Code 外掛。

丟一篇文章給 Claude，說「把這篇做成影片」。它會寫旁白、AI 配音、畫財經圖表、核對畫面上每個數字的出處、燒好字幕，最後打開一頁審片頁讓你留言修改。你點頭之後，成片（`.mp4`）和字幕檔（`.srt`）會放進 Finder 的「影片 → 解說影片」。

- **畫面**：全部是同一種財經圖表風格（柱狀、折線、大數字、比較圖）。文章裡的圖，會用圖上的數字重畫成圖表。
- **數字**：畫面上每個數字都要對得到出處（你的原文、公有數據或算式），找不到出處的數字不會讓影片出片。
- **配音**：預設用免費的 edge-tts（台灣口音：曉臻、曉雨、雲哲），也可以改用 ElevenLabs 或 Google。
- **公有數據**：可以直接畫聯準會、財政部的公有領域數據（例如十年期、二年期殖利率、實質殖利率、期限溢價）。
- **比例**：預設 16:9，說「直式」就出 9:16。

## 需要什麼

- macOS（Apple Silicon 或 Intel），要能上網。
- Claude 桌面 app 的 **Code** 分頁。
- 其他工具（Homebrew、能燒字幕的 ffmpeg、uv、Chromium）由安裝流程自動裝，已經有的會略過。

## 安裝

在 Claude Code 的對話框輸入：

```
/plugin marketplace add dannychan4277/article-to-video
/plugin install article-to-video@article-to-video
```

開一個新對話，說「**安裝**」，照 Claude 說的做：中間按「允許」、在跳出的終端機視窗輸入一次 Mac 密碼、挑聲音和配樂、填頻道名。最後它會做一支 10 秒的測試片給你看。

## 使用

完整步驟請看 **[使用說明](使用說明.md)**。常用的說法：

| 想做的事 | 跟 Claude 說 |
|---|---|
| 做一支新影片 | （拖文章進來）把這篇做成影片 |
| 做手機直式版 | 做一支直式的 |
| 改之前做好的片 | 把上次那支第 5 張圖改成… |
| 換配音聲音、配樂、品牌色 | 換聲音／換配樂／改品牌色 |
| 東西好像壞了 | 檢查環境 |
| 要把問題傳給維護者 | 回報問題 |

更新外掛：先輸入 `/plugin marketplace update article-to-video`，再到 `/plugin` → **Installed** → **article-to-video** → **Update now**。不要解除安裝再重裝，解除安裝會把你的頻道名、聲音、配樂設定一起刪掉。

## 配音

| 配音 | 費用 | 說明 |
|---|---|---|
| edge-tts（預設） | 免費 | 非官方介面，見下方「授權與注意」 |
| Google Cloud Text-to-Speech | 一般用量落在每月免費額度內 | 台灣口音有 Standard、WaveNet 各 3 個聲音；要有開了帳單的 Google Cloud 專案 |
| ElevenLabs | 依方案點數計費，一支 3 分鐘的片約 600 點 | 付費方案才能商用 |

換付費配音需要一組 API 金鑰，放在 `~/.config/article-to-video/elevenlabs.key` 或 `~/.config/article-to-video/google-tts.key`（也可以用環境變數 `ELEVENLABS_API_KEY`、`GOOGLE_TTS_API_KEY`）。金鑰不會寫進專案檔、紀錄或回報問題的 zip。換配音不用重寫旁白或分鏡，只要重新配音、重新渲染畫面。

## 授權與注意

- **只處理你自己的文章。** 文章內容、文章裡的圖（包含你引用的第三方圖表）的授權，由使用者自己負責。
- **edge-tts 的灰色地帶。** edge-tts 用的是微軟 Edge「大聲朗讀」背後的語音服務。這是非官方的用法，微軟沒有授權拿來公開發布或營利，隨時可能失效。用它發布影片的風險由發布者承擔。每支片的片尾都會註明「配音為 AI 合成」。需要正式授權的話，請改用 Google 或 ElevenLabs，並遵守它們各自的條款。
- **公有數據。** 只抓 FRED 上標示為公有領域的聯準會、財政部系列（DGS10、DGS2、DFII10、THREEFYTP10）。標普五百（SP500）這類指數，以及標示有版權的系列（例如 T10YIE）一律不抓。
- **不是投資建議。** 每支片的片尾都會註明「僅供教育，非投資建議」。
- **內嵌的動畫引擎。** `src/article_to_video/engine/` 是 huashu-art-motion 引擎的修改版，採 MIT 授權，作者是花叔（alchaincyf · 花生，[huasheng.ai](https://www.huasheng.ai)）。原授權文字見 [`engine/LICENSE`](src/article_to_video/engine/LICENSE)，修改紀錄見 [`engine/NOTICE.md`](src/article_to_video/engine/NOTICE.md)。
- **字型。** 隨外掛附帶的字型是思源黑體 Noto Sans CJK TC 的子集，以及 Roboto Condensed，都採 SIL Open Font License 1.1：可以燒進影片、可以商用，但不能單獨販售字型檔。詳見 [`engine/lib/fonts/LICENSES.md`](src/article_to_video/engine/lib/fonts/LICENSES.md) 與 [`OFL.txt`](src/article_to_video/engine/lib/fonts/OFL.txt)。
- **外掛本身的程式碼。** 授權待定。

## 給維護者

- 跑測試：`uv run pytest`（不連網，配音用假配音層）。
- 發新版、作者怎麼升級、金鑰放哪：[`docs/maintaining.md`](docs/maintaining.md)。
- 用語：[`GLOSSARY.md`](GLOSSARY.md)；設計決定：[`docs/adr/`](docs/adr/)。
- 這個 repo 不能放任何作者的文章內容，測試素材都是自製的，數字取自公有領域數據。
