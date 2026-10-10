---
name: make-video
description: 做成影片：把作者自己的財經文章（PDF、Word、Google 文件下載的檔，或直接貼上的文字）做成 2–4 分鐘的解說影片，Claude 寫旁白、配音、畫財經圖表（文章裡的圖照數字重畫成同一種圖表），自己檢查數字和畫面、跑一輪獨立審片，最後打開審片頁給作者看。作者說「做成影片」「把這篇做成影片」「做一支影片」「做一支直式的」，或丟文章檔、貼文章說要做片時使用。作者看過審片頁之後的修改和出片，用 edit-video。
allowed-tools:
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video projects *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video new *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video lint *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video preview *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video build *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video checks *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video review-prep *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video review-record *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video resolve *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video settings *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video open-review *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video review-frames *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video extract *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video narrate *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video projects *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video new *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video lint *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video preview *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video build *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video checks *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video review-prep *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video review-record *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video resolve *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video settings *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video open-review *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video review-frames *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video extract *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video narrate *)
  - Read(~/Movies/解說影片/**)
  - Edit(~/Movies/解說影片/**)
---

# 做成影片

作者不熟電腦。他只做三件事：給文章、在審片頁留言貼回來、點頭。其他全部是你的事：寫旁白、配音、分鏡、檢查。
**他第一次打開審片頁之前，數字、畫面、自動檢查、獨立審片你都要先處理完**——他不該當除錯員。
跟他講話用口語，不講指令、JSON、代號；指令印出來的錯誤，你轉成白話。

**整支片只用一種畫面：財經圖表**（柱狀、折線、大數字、標註）。講到作者文章裡的某張圖，就照那張圖的數字重畫成財經圖表，
不把原圖貼上畫面——風格才一致。

## 開始前

- 這個外掛只處理**作者本人的文章**。文章裡的素材授權由他自己負責；不是他寫的文章就不要做。
- 指令照下面寫的跑（開頭都是 uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video）。找不到 `uv` 就把開頭的 `uv` 換成
  `~/.local/bin/uv`；兩個都找不到＝還沒安裝，請他說「安裝」。
- 預先核准只在叫出這個 skill 的那一回合有效。作者回覆之後要繼續跑指令時，先再叫一次這個 skill（看過審片頁之後的事叫 `edit-video`），
  他才不用一直按「允許」。
- 他說「直式」：下面每個 `preview`、`build`、`checks`、`review-*`、`open-review` 都加 `--aspect 9:16`。
- 跟他說大概要多久（見最後一節），然後就開始做，不用每一步都問他。

## 1. 收文章（含截字檢查）

文章是檔案：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video new "<文章檔的路徑>" --title "<片名>"
```

- 片名：文章標題的精簡版（20 字內，用作者的詞）；不確定就不帶 `--title`（會用文章第一段）。
  它會在「影片／解說影片」建「日期-片名」資料夾，做片的東西都在裡面的「專案」資料夾（印出來的**專案資料夾**，下面寫成 `<專案>`）。
- **截字**：PDF 有字被頁面切掉時會失敗、什麼都不留。把印出來的重存方法用口語講給他（重點：列印時「縮放」調小），
  等他給新的檔再跑一次。最省事的是請他給 Word 檔（.docx）。
- 作者直接貼文字：先跑 `uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video projects`，從印出來的影片資料夾路徑，
  用 Write 把全文寫成 `<影片資料夾>/.貼上的文章.txt`，再用上面那行 `new` 給這個檔。貼的文字抓不到圖：文章的圖要重畫成圖表，就請他給檔案。

## 2. 讀文章、讀圖

Read `<專案>/article/text.txt`（全文）和 `<專案>/article/article.json`（段落編號、每張原圖的大小與圖說），
再 Read 每一張原圖（`<專案>/article/images/圖N.*`），知道每張圖在講什麼、數字在哪裡。

**圖上的數字文字裡沒有時，讀圖記下來**：旁白可能講到、畫面要重畫的圖，把你在圖上讀到的數字寫進
`<專案>/article/figure_numbers.json`（格式見 `${CLAUDE_PLUGIN_ROOT}/skills/make-video/references/formats.md`「讀圖記下的數字」）。

- 只記圖上**清楚寫出來**的數字（柱上的數值、表格的格子、標出來的點），照圖上的寫法（圖上 4.50 就寫 "4.50"）；
  `label` 寫它在圖上是什麼（哪一列、哪一年、哪個系列），作者要拿它對照原圖。
- **軸上、類目上的數字也要記**：重畫時會照抄到畫面的橫軸年份（2020）、月份、類目名裡的數字，文字裡沒有的一樣記
  （`label` 寫「橫軸年份」「第二根柱的類目名」）。數字對帳把類目名、單位行的數字也當成畫面上的數字，沒記就是無出處。
- 沒標數值的線、要用尺量的高度：**不要估**。改用文章文字裡的數字、公有數據（見 formats.md），或只畫圖上有標的那幾點。
- 圖太糊、看不清楚：不要猜，請他給清楚一點的檔（PDF 原檔、Word 檔）。
- 數字對帳會把這些數字標成「原文圖表」，審片頁請作者對照原圖——所以讀錯會被他看到，寧可少記。

## 3. 寫旁白 → 旁白規則檢查

照 `${CLAUDE_PLUGIN_ROOT}/skills/make-video/references/narration-rules.md` 寫 `<專案>/narration.json`
（格式見 `${CLAUDE_PLUGIN_ROOT}/skills/make-video/references/formats.md`）。重點：用作者的詞、不加文章沒有的主張、2–4 分鐘、第一句就是重點。

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video lint "<專案>"
```

改到「要改 0 項」為止；「看一下」的逐條判斷。lint 查不到的（用詞、有沒有加主張）自己對照文章逐句查。

## 4. 配音（寫分鏡之前）

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video narrate "<專案>" --data-dir ${CLAUDE_PLUGIN_DATA}
```

要連網（edge-tts），約十幾秒。它印出：

- **總長**：不在 2–4 分鐘就回第 3 步調旁白，再配一次（只有改到的段會重配）。
- **每段在成片裡從幾秒起、多長**，和**每個詞開始唸的秒數**。寫分鏡時用它：錨點的 `word` 要是旁白裡的字；
  一句一個畫面事件、約 5 秒沒有新東西就太久——直接拿這些秒數算，不要猜。

## 5. 寫分鏡

照 `${CLAUDE_PLUGIN_ROOT}/skills/make-video/references/storyboard-rules.md`（一句一張圖、圖型有變化、標註寫意思、
文章的圖怎麼重畫…）寫 `<專案>/shots.json`，格式見 `formats.md`。每個分鏡都是財經圖表（`chart`）。
用公有數據畫走勢時第一次要連網。

## 6. 預覽：分鏡靜態圖、數字對帳（先不給作者看）

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video preview "<專案>" --no-open --data-dir ${CLAUDE_PLUGIN_DATA}
```

- 設定寫錯會一次列出全部問題（哪個分鏡、哪個欄位），照著改再跑。配音用第 4 步配好的，不會重配。
- 看「數字對帳」那行：有「找不到出處」的數字，改成文章裡的數字；數字在文章的圖上（含重畫的圖的橫軸年份、類目名），
  就照第 2 步記進 figure_numbers.json——那行會寫「重畫圖N：…記進 figure_numbers.json 的「圖N」」；
  算出來的在那個分鏡宣告算式（`derived`）。
  對帳的規則：正負號要跟文章一樣；四捨五入只能少寫幾位、至少留兩位有效數字（4.58 寫成 4.6 可以，寫成 5 不行）；
  詞裡的中文數字（十年期、兩年期）不算數字，對不到它。差值、變號、換單位都是推算，要宣告算式。
- 它會印每個分鏡完成狀態那張靜態圖的路徑：**每一張都用 Read 看**，照 storyboard-rules.md 最後的檢查清單。
  重畫文章的圖的分鏡，把靜態圖和原圖並排看：數字、順序、類目名都要跟原圖一樣。
  有問題改 shots.json 再預覽（只有改到的分鏡會重渲，幾秒鐘）。

## 7. 建置審片用的成片 → 自動檢查 → 獨立審片

這一步的成片只在專案資料夾裡給審片用，**還不是出片**（作者點頭之後才出片）。

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video build "<專案>" --no-open --data-dir ${CLAUDE_PLUGIN_DATA}
```

第一次要渲每個片段，3 分鐘的片要幾分鐘：用 Bash 的背景執行（run_in_background），等的時候跟作者說一聲。
被擋下（數字對帳）就照訊息改、回到第 6 步。
接著照 `${CLAUDE_PLUGIN_ROOT}/skills/make-video/references/checks-and-review.md` 做完：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video checks "<專案>" --data-dir ${CLAUDE_PLUGIN_DATA}
```

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video settings show --json --data-dir ${CLAUDE_PLUGIN_DATA}
```

`independent_review` 是 true 才做獨立審片：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video review-prep "<專案>" --data-dir ${CLAUDE_PLUGIN_DATA}
```

用 Agent 工具派一個 agent，prompt＝它印出來的 `prompt.txt` 全文（審片員的說明在
`${CLAUDE_PLUGIN_ROOT}/skills/make-video/references/independent-review.md`，素材資料夾裡也有一份）。它寫好 review.json 後：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video review-record "<專案>" --data-dir ${CLAUDE_PLUGIN_DATA}
```

必修改好之後記下來。你覺得不用改的要留給作者決定（第 8 步問他）；還有沒處理的必修時 build 會被擋，
這時先做第 8 步（審片頁用靜態圖給他看），他決定了再回來 build：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video resolve "<專案>" R1-2 --fixed "怎麼改的" --data-dir ${CLAUDE_PLUGIN_DATA}
```

改了旁白就回到第 4 步（配音，看秒數、調錨點），只改分鏡就回到第 6 步（預覽、看改到的靜態圖），再 build、checks；改得多再審一輪。

## 8. 打開審片頁給作者看

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video open-review "<專案>" --data-dir ${CLAUDE_PLUGIN_DATA}
```

它會在瀏覽器打開審片頁、印出這是**第幾版**（記住，出片時要用）。你自己檢查時改過幾輪，頁首就不是第 1 版、
卡片上也會標「這版改過」：跟他說那是你自己檢查時改的，從這一版開始看就好。用口語跟作者說：

- 每張卡是一個分鏡：編號、畫面、那句旁白、畫面上數字的出處。上面可以看整支片。
- 有重畫他文章的圖的話：那幾張卡的數字標「原文圖表」，是你看他的圖讀出來的，**請他對照自己的原圖**，讀錯了就在那張卡寫正確的數字。
- 想改哪一張就在那張卡的格子寫，不屬於某一張的寫在「整體意見」；底下有「能加什麼」可以參考。
- 寫完按「複製給 Claude」，回到這裡貼上。全部都可以的話，直接說「可以出片」。
- 自動檢查和獨立審片（另一位只看成片的審片員）已經看過，你改了哪些；有要他決定的必修，這時候問他（不用講代號）。

他貼回留言、說可以出片、或想改什麼：叫 `edit-video` 接著做。

## 出狀況時

- 找不到 ffmpeg、Chromium 開不起來、配音失敗（edge-tts）：請他說「檢查環境」。
- 看不懂的錯誤、一大段英文：請他說「回報問題」，把打包好的檔案用 LINE 傳給 Danny。
- 不要自己用別的方法繞過（例如手動改檔名、換指令）。

## 大概要多久（Danny 的 Mac 量的，給作者一個預期）

- 配音：3 分鐘的旁白第一次十幾秒（要連網），之後只重配改到的段。
- 預覽：第一次 20 個分鏡約 15 秒，之後只改幾個分鏡幾秒鐘。
- 第一次建置 3 分鐘的片：幾分鐘（圖表片段一個約 5 秒）。
- 自動檢查第一次約 1 分鐘；獨立審片素材約 10 秒，加上審片員看片的時間。
