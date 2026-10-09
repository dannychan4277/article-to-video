---
name: make-video
description: 做成影片：把作者自己的財經文章（PDF、Word、Google 文件下載的檔，或直接貼上的文字）做成 2–4 分鐘的解說影片，Claude 寫旁白、配音、畫財經圖表、把文章原圖用 Vox 剪報畫面放進來，自己檢查數字和畫面、跑一輪獨立審片，最後打開審片頁給作者看。作者說「做成影片」「把這篇做成影片」「做一支影片」「做一支直式的」，或丟文章檔、貼文章說要做片時使用。作者看過審片頁之後的修改和出片，用 edit-video。
allowed-tools:
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video projects *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video new *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video lint *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video preview *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video boxes *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video confirm-box *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video build *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video checks *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video review-prep *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video review-record *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video resolve *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video settings *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video open-review *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video review-frames *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video extract *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video projects *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video new *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video lint *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video preview *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video boxes *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video confirm-box *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video build *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video checks *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video review-prep *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video review-record *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video resolve *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video settings *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video open-review *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video review-frames *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video extract *)
  - Read(~/Movies/解說影片/**)
  - Edit(~/Movies/解說影片/**)
---

# 做成影片

作者不熟電腦。他只做三件事：給文章、在審片頁留言貼回來、點頭。其他全部是你的事：寫旁白、配音、分鏡、檢查。
**他第一次打開審片頁之前，數字、畫面、框、自動檢查、獨立審片你都要先處理完**——他不該當除錯員。
跟他講話用口語，不講指令、JSON、代號；指令印出來的錯誤，你轉成白話。

## 開始前

- 這個外掛只處理**作者本人的文章**。文章裡的素材（含他引用的別人的圖）授權由他自己負責；不是他寫的文章就不要做。
- 指令照下面寫的跑（開頭都是 uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video）。找不到 `uv` 就把開頭的 `uv` 換成
  `~/.local/bin/uv`；兩個都找不到＝還沒安裝，請他說「安裝」。
- 預先核准只在叫出這個 skill 的那一回合有效。作者回覆之後要繼續跑指令時，先再叫一次這個 skill（看過審片頁之後的事叫 `edit-video`），
  他才不用一直按「允許」。
- 他說「直式」：下面每個 `preview`、`boxes`、`build`、`checks`、`review-*`、`open-review` 都加 `--aspect 9:16`。
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
  用 Write 把全文寫成 `<影片資料夾>/.貼上的文章.txt`，再用上面那行 `new` 給這個檔。貼的文字抓不到圖：要用文章原圖就請他給檔案。

## 2. 讀文章

Read `<專案>/article/text.txt`（全文）和 `<專案>/article/article.json`（段落編號、每張原圖的大小與圖說），
再 Read 每一張原圖（`<專案>/article/images/圖N.*`），知道每張圖在講什麼、數字在哪裡。

## 3. 寫旁白 → 旁白規則檢查

照 `${CLAUDE_PLUGIN_ROOT}/skills/make-video/references/narration-rules.md` 寫 `<專案>/narration.json`
（格式見 `${CLAUDE_PLUGIN_ROOT}/skills/make-video/references/formats.md`）。重點：用作者的詞、不加文章沒有的主張、2–4 分鐘、第一句就是重點。

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video lint "<專案>"
```

改到「要改 0 項」為止；「看一下」的逐條判斷。lint 查不到的（用詞、有沒有加主張）自己對照文章逐句查。

## 4. 寫分鏡

照 `${CLAUDE_PLUGIN_ROOT}/skills/make-video/references/storyboard-rules.md`（一句一張圖、圖型有變化、標註寫意思…）、
`${CLAUDE_PLUGIN_ROOT}/skills/make-video/references/vox-rules.md`（講到文章的圖就用 Vox）寫 `<專案>/shots.json`，
格式見 `formats.md`。用公有數據畫走勢時第一次要連網。

## 5. 預覽：配音、分鏡靜態圖、數字對帳（先不給作者看）

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video preview "<專案>" --no-open --data-dir ${CLAUDE_PLUGIN_DATA}
```

- 設定寫錯會一次列出全部問題（哪個分鏡、哪個欄位），照著改再跑。
- 看「總長」：不在 2–4 分鐘就回去調旁白。
- 看「數字對帳」那行：有「找不到出處」的數字，改成文章裡的數字，或在那個分鏡宣告算式（`derived`）。
  對帳的規則：正負號要跟文章一樣；四捨五入只能少寫幾位、至少留兩位有效數字（4.58 寫成 4.6 可以，寫成 5 不行）；
  詞裡的中文數字（十年期、兩年期）不算數字，對不到它。差值、變號、換單位都是推算，要宣告算式。
- 它會印每個分鏡完成狀態那張靜態圖的路徑：**每一張都用 Read 看**，照 storyboard-rules.md 最後的檢查清單。
  有問題改 shots.json 再預覽（只有改到的分鏡會重渲，幾秒鐘）。

## 6. 框核對（有 Vox 框才要）

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video boxes "<專案>" --data-dir ${CLAUDE_PLUGIN_DATA}
```

每個框 Read「畫面」那張圖（必要時「原圖」那張對座標），照 vox-rules.md 確認框在旁白唸的那個數字上，都對了才記下（代號照抄 boxes 印的）：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video confirm-box "<專案>" SH05:1@xxxxxxxx --note "框在哪個數字上"
```

## 7. 建置審片用的成片 → 自動檢查 → 獨立審片

這一步的成片只在專案資料夾裡給審片用，**還不是出片**（作者點頭之後才出片）。

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video build "<專案>" --no-open --data-dir ${CLAUDE_PLUGIN_DATA}
```

第一次要渲每個片段，3 分鐘的片要幾分鐘：用 Bash 的背景執行（run_in_background），等的時候跟作者說一聲。
被擋下（數字對帳、框核對）就照訊息改、回到第 5 步。
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
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video resolve "<專案>" R1-2 --fixed "怎麼改的"
```

改了分鏡或旁白就回到第 5 步（預覽、看改到的靜態圖），再 build、checks；改得多再審一輪。

## 8. 打開審片頁給作者看

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video open-review "<專案>" --data-dir ${CLAUDE_PLUGIN_DATA}
```

它會在瀏覽器打開審片頁、印出這是**第幾版**（記住，出片時要用）。你自己檢查時改過幾輪，頁首就不是第 1 版、
卡片上也會標「這版改過」：跟他說那是你自己檢查時改的，從這一版開始看就好。用口語跟作者說：

- 每張卡是一個分鏡：編號、畫面、那句旁白、畫面上數字的出處。上面可以看整支片。
- 想改哪一張就在那張卡的格子寫，不屬於某一張的寫在「整體意見」；底下有「能加什麼」可以參考。
- 寫完按「複製給 Claude」，回到這裡貼上。全部都可以的話，直接說「可以出片」。
- 機器檢查和另一位審片員已經看過，你改了哪些；有要他決定的必修，這時候問他（不用講代號）。

他貼回留言、說可以出片、或想改什麼：叫 `edit-video` 接著做。

## 出狀況時

- 找不到 ffmpeg、Chromium 開不起來、配音失敗（edge-tts）：請他說「檢查環境」。
- 看不懂的錯誤、一大段英文：請他說「回報問題」，把打包好的檔案用 LINE 傳給 Danny。
- 不要自己用別的方法繞過（例如手動改檔名、換指令）。

## 大概要多久（Danny 的 Mac 量的，給作者一個預期）

- 預覽：第一次 20 個分鏡約 15 秒（配音要連網再加十幾秒），之後只改幾個分鏡幾秒鐘。
- 第一次建置 3 分鐘的片：幾分鐘（圖表片段一個約 5 秒；Vox 剪報段落慢很多，一段可能半分鐘以上）。
- 自動檢查第一次約 1 分鐘；獨立審片素材約 10 秒，加上審片員看片的時間。
