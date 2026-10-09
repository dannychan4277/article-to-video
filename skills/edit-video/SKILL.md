---
name: edit-video
description: 改影片與出片：作者看過審片頁之後的事——他貼回「審片留言｜…」開頭的留言、用講的要改哪一張圖或哪句旁白（「改影片」「第 5 張圖改成…」「旁白改成…」），Claude 只改那幾處、只重渲改到的分鏡、更新審片頁給他看；他說「可以出片」「出片」就把成片和字幕檔放進「影片／解說影片／日期-標題」。也用來打開舊專案修改（「把上次那支第 5 張圖改成…」「打開之前那支殖利率的片」），或把做好的片再出一支直式的。
allowed-tools:
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video projects *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video outline *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video feedback *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video lint *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video preview *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video boxes *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video confirm-box *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video open-review *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video build *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video checks *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video review-prep *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video review-record *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video resolve *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video publish *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video settings *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video review-frames *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video extract *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video narrate *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video projects *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video outline *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video feedback *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video lint *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video preview *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video boxes *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video confirm-box *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video open-review *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video build *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video checks *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video review-prep *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video review-record *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video resolve *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video publish *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video settings *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video review-frames *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video extract *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video narrate *)
  - Read(~/Movies/解說影片/**)
  - Edit(~/Movies/解說影片/**)
---

# 改影片與出片

作者看過審片頁了。這裡做三件事：照他的話改（只改他說的）、給他看新的一版、他點頭就出片。
跟他講話用口語，不講指令、JSON、代號。找不到 `uv` 就把指令開頭的 `uv` 換成 `~/.local/bin/uv`。
規則與格式都在 `${CLAUDE_PLUGIN_ROOT}/skills/make-video/references/`：narration-rules.md、storyboard-rules.md、vox-rules.md、formats.md、checks-and-review.md。

預先核准只在叫出這個 skill 的那一回合有效：作者每次回覆、你要繼續跑指令時，先再叫一次這個 skill。

## 0. 是哪一支片

這次對話剛做的那支：就是那個專案資料夾（下面寫成 `<專案>`）。不然列出做過的片，最近動過的在最前面：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video projects
```

「上次那支」＝第一個；他講片名就加關鍵字（`projects 殖利率`）。不確定就把片名唸給他確認。再看這支片每個分鏡在畫什麼、唸哪幾段旁白：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video outline "<專案>"
```

他說「第 5 張圖」＝分鏡 5（審片頁卡片上的編號）。

## 1. 讀懂他要改什麼

他貼回來的審片留言（「審片留言｜…」開頭）用 Write 原封不動存成 `<專案>/審片留言.txt`，再：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video feedback "<專案>" --file "<專案>/審片留言.txt"
```

每則留言會對到分鏡 id、種類、畫面上的字、那個分鏡唸的**旁白段**和旁白原文。
`problems` 不是空的（審片頁是舊的、編號對不上）：先跟他確認是哪一張，再改。用講的（沒貼留言）也一樣：先用 outline 對到分鏡。

## 2. 改

- **畫面**（標題、標註、換圖型、換剪報、框、時間點）：改 `<專案>/shots.json` 裡那個 id 的分鏡，照 storyboard-rules.md、vox-rules.md。
- **旁白**（「旁白改成…」「這句太長」）：只改 feedback 列出的那幾個旁白段（`<專案>/narration.json`），照 narration-rules.md；
  被錨點用到的詞改掉了，分鏡的錨點跟著改。改完跑：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video lint "<專案>"
```

  再配音（只重配改到的段），看新的秒數調錨點、框和下一件的間隔：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video narrate "<專案>" --data-dir ${CLAUDE_PLUGIN_DATA}
```

- **只改他說的**：沒提到的分鏡、旁白段一個字都不要動——沒動的不會重配音、不會重渲，也不會被標成「這版改過」。
- 做不到的（引擎畫不出來的東西、文章裡沒有的數字）老實說，提一個做得到的替代。
- 換聲音、配樂、品牌色只改這支片（整支片會重新配音、重渲；以後的片都換，請他說「換聲音」，那是 setup 的事）：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video settings set --project "<專案>" --voice 曉雨 --data-dir ${CLAUDE_PLUGIN_DATA}
```

## 3. 預覽（只重渲改到的分鏡）

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video preview "<專案>" --no-open --data-dir ${CLAUDE_PLUGIN_DATA}
```

- 只有改到的分鏡重渲靜態圖、只有改到的旁白段重新配音，幾秒鐘。它會印這是**第幾版**和這版改了哪些分鏡的靜態圖路徑：每一張都 Read 看過。
- 數字對帳那行有「找不到出處」就先改好。改到 Vox 框（或框所在那句旁白）要重新核對：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video boxes "<專案>" --data-dir ${CLAUDE_PLUGIN_DATA}
```

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video confirm-box "<專案>" SH05:1@xxxxxxxx --note "框在哪個數字上"
```

- 改得很多（大約三分之一以上的分鏡，或整段旁白重寫）：照 checks-and-review.md 再 build、checks、審一輪，再給他看。

## 4. 給他看新的一版

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video open-review "<專案>" --data-dir ${CLAUDE_PLUGIN_DATA}
```

跟他說這是第幾版、改了哪幾張（頁面上標「這版改過」）、怎麼改的。這一版先看靜態圖，不用等整支片重做。
他想先看整支片再決定：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video build "<專案>" --data-dir ${CLAUDE_PLUGIN_DATA}
```

（只重渲改到的片段，做完審片頁會內嵌新的成片並打開。）還要改就回到第 1 步。

## 5. 他點頭 → 出片

他說「可以出片」「OK」時，用他看的那一版的版號（審片頁頁首寫的、上一步印的）：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video publish "<專案>" --version 3 --data-dir ${CLAUDE_PLUGIN_DATA}
```

它會建置成片（沒改的片段用快取）、跑自動檢查，把成片和字幕檔放進「影片 → 解說影片 → 日期-片名」，在 Finder 裡指給他看。
片長或改得多時用背景執行（run_in_background）。被擋下時：

- **版本對不上、改過還沒給他看**：先預覽、給他看新的一版，他點頭再出片。不要自己換版號硬出。
- **還沒做過獨立審片**：照 checks-and-review.md 做一輪再出片。
- **自動檢查查出必修、數字對帳、框核對**：照訊息改 → 預覽 → 給他看 → 他點頭 → 出片。
  你覺得不用改的必修要問他，他說不改才記下（代號照訊息抄）：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video resolve "<專案>" R1-2 --accept "作者說…" --by 作者
```

出片完成後跟他說：成片（字幕已經燒在畫面上）可以直接上傳；字幕檔（.srt）上傳 YouTube 時可以一起給。
之後想再改，說「把上次那支第幾張圖改成…」就好；再出片會換掉資料夾裡的舊檔。

## 直式

他要直式（短影音用）：下面每個指令都加 `--aspect 9:16`，直式有自己的審片頁（從第 1 版開始），流程一樣：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video preview "<專案>" --aspect 9:16 --no-open --data-dir ${CLAUDE_PLUGIN_DATA}
```

看過靜態圖（直式字幕一行約 17 字，標題可以三行）→ `open-review` 加 `--aspect 9:16` 給他看 → 他點頭：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video publish "<專案>" --aspect 9:16 --version 1 --data-dir ${CLAUDE_PLUGIN_DATA}
```

## 出狀況時

- 找不到 ffmpeg、Chromium、配音失敗：請他說「檢查環境」。看不懂的錯誤：請他說「回報問題」。
