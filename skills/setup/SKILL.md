---
name: setup
description: 安裝與設定：第一次用這個外掛時，先確認這台 Mac 是 Apple Silicon、macOS 15 以上（其他 Mac 裝不起來），再把做影片要用的工具裝好（Homebrew、能燒字幕的 ffmpeg、uv、畫圖表用的 Chromium），讓作者試聽挑配音聲音和配樂，想要的話填頻道名、logo、品牌色（只做給自己看的人可以跳過），最後做一支 10 秒測試片打開給他看。之後想換聲音、換配樂、改頻道名或品牌色也用這個。作者說「安裝」「設定」「第一次使用」「裝好環境」「換聲音」「換配樂」「改頻道名」「改品牌色」時使用。
allowed-tools:
  - Bash(sysctl -n hw.optional.arm64)
  - Bash(sw_vers -productVersion)
  - Bash(uv --version)
  - Bash(~/.local/bin/uv --version)
  - Bash(curl -LsSf --create-dirs -o ${CLAUDE_PLUGIN_DATA}/uv-install.sh https://astral.sh/uv/install.sh)
  - Bash(sh ${CLAUDE_PLUGIN_DATA}/uv-install.sh)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video install *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video samples *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video settings *)
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video test-video *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video install *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video samples *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video settings *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video test-video *)
  - Bash(afplay ${CLAUDE_PLUGIN_DATA}/previews/*)
---

# 安裝與設定

作者不熟電腦。整個過程他只需要做三件事：Claude 問權限時按「允許」、在跳出來的終端機視窗輸入一次 Mac 密碼、
回答你的問題（挑聲音、挑配樂、片尾要不要放頻道名⋯）。指令和錯誤訊息都由你轉成白話，**不要叫他自己打指令**。

只想改其中一項（換聲音、換配樂、改頻道名／logo／品牌色）：直接做那一步就好，不用重裝。

## 第 0 步：先看這台 Mac 能不能裝（什麼都還沒動）

這個外掛只支援 **Apple Silicon（M1、M2、M3⋯）晶片、macOS 15 以上**的 Mac。其他 Mac 上，做影片要用的 ffmpeg
沒有現成安裝包，Homebrew 會改成在作者的電腦上從原始碼編譯好幾個小時，而且常常失敗。
所以要裝東西之前（連開場說明都還沒講），先跑這兩行（只看、不改任何東西）：

```
sysctl -n hw.optional.arm64
```

```
sw_vers -productVersion
```

- 第一行印 `1` 是 Apple Silicon；印 `0`、什麼都沒印、或說 unknown oid，都是 Intel Mac。
- 第二行是 macOS 版本，看第一個數字（`26.2` 是 26、`14.7.2` 是 14）。

Apple Silicon、而且第一個數字是 15 以上：往下做。

不符合就**停在這裡**：不要裝 uv、不要裝 Homebrew、不要跑後面任何一步，也不要想別的方法硬裝。用白話跟作者說：

- **Intel Mac**：這台是 Intel 晶片的 Mac，這個外掛沒辦法簡單裝上去（做影片要用的工具沒有給 Intel Mac 的現成安裝包，
  得在他的電腦上編譯好幾個小時，很容易失敗）。請他聯絡 Danny。想自己確認：點螢幕左上角的蘋果選單 →「關於這台 Mac」，
  「晶片」寫 Apple M1、M2 這類才是 Apple Silicon；寫的是「處理器」、後面有 Intel，就是 Intel Mac。
- **Apple Silicon，但 macOS 14 以前**：系統太舊，要先更新到 macOS 15 以上：點蘋果選單 →「系統設定」→「一般」→
  「軟體更新」（macOS 12 以前是「系統偏好設定」→「軟體更新」），照畫面更新；要一段時間，記得接上電源。
  更新完再跟你說「安裝」。想自己確認版本：蘋果選單 →「關於這台 Mac」，看「macOS」那一行。更新不了就聯絡 Danny。

第 2 步的 `install` 自己也會再看一次：印出「✗ 這台 Mac」就照它的說明轉述給作者，一樣停下來（它什麼都沒裝）。

## 開始前先跟作者說

用你自己的話講，大意是：

- 接下來會裝幾個做影片要用的工具，第一次大約 10～30 分鐘，看網路快慢。
- 中間會跳出一個「終端機」視窗，要他輸入一次登入 Mac 的密碼。打字時畫面不會出現任何字，這是正常的。
- Claude 跳出權限詢問時按「允許」。
- 等的時候會一起挑配音的聲音、配樂（片尾要不要放頻道名也會問一句，只是自己看的話不用）。

## 第 1 步：確認有 uv（跑外掛程式用的工具）

跑 `uv --version`。

- 印出版本號：下一步。
- 說找不到：跑 `~/.local/bin/uv --version`。有版本號表示裝過了，只是這次對話還找不到它，
  **下面每一行開頭的 `uv` 都換成 `~/.local/bin/uv`**。
- 兩個都找不到：用 uv 官方的安裝程式裝（不用密碼，幾秒鐘），兩行分開跑：

  ```
  curl -LsSf --create-dirs -o ${CLAUDE_PLUGIN_DATA}/uv-install.sh https://astral.sh/uv/install.sh
  ```

  ```
  sh ${CLAUDE_PLUGIN_DATA}/uv-install.sh
  ```

  裝好之後這次對話一律用 `~/.local/bin/uv`（下次開新對話直接打 `uv` 就行）。

第一次跑下面的外掛指令時，uv 會自己下載 Python 和外掛要用的套件，大約一分鐘，正常。

## 第 2 步：裝工具（放在背景跑）

先照上面跟作者講好終端機和密碼的事，再用 Bash 的背景執行（run_in_background）跑這一行，
第一次可能要 10～30 分鐘：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video install --data-dir ${CLAUDE_PLUGIN_DATA}
```

- 已經裝好的會自己略過。這台 Mac 沒有 Homebrew 時，它會打開「終端機」：請作者照視窗裡的字輸入密碼、按 return，
  然後讓它跑，看到「Homebrew 裝好了」才關那個視窗。
- 不用乾等：等的時候直接做第 3～5 步（試聽和填品牌不需要這些工具）。
- 跑完看最後幾行：
  - 「✗ 這台 Mac」：照第 0 步跟作者說明、停下來（它什麼都沒裝）。
  - 「能燒字幕的 ffmpeg」說要從原始碼編譯、先停下來：照那一項的「修法」講給作者聽，不要自己換方法裝。
  - 「全部裝好了」：等第 5 步做完就做第 6 步。
  - 有「✗」的項目：把那一項的「修法」用白話講給作者聽，處理完再跑一次這一步（裝好的會略過，很快）。
  - 「edge-tts 連線」失敗通常是沒網路：請他確認 Wi-Fi 有連上。

## 第 3 步：挑配音的聲音

產生三個試聽檔（要連網，大約十秒）：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video samples voice --out ${CLAUDE_PLUGIN_DATA}/previews/voices --data-dir ${CLAUDE_PLUGIN_DATA}
```

三個都是台灣口音：曉臻（女聲）、曉雨（女聲）、雲哲（男聲）。一個一個播，播之前先講「現在播第 1 個：曉臻」：

```
afplay ${CLAUDE_PLUGIN_DATA}/previews/voices/voice-zh-TW-HsiaoChenNeural.wav
```

```
afplay ${CLAUDE_PLUGIN_DATA}/previews/voices/voice-zh-TW-HsiaoYuNeural.wav
```

```
afplay ${CLAUDE_PLUGIN_DATA}/previews/voices/voice-zh-TW-YunJheNeural.wav
```

問他喜歡哪一個（想再聽就再播）。決定了就存起來（填他挑的名字：曉臻、曉雨或雲哲）：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video settings set --voice 曉雨 --data-dir ${CLAUDE_PLUGIN_DATA}
```

覺得講太快或太慢：語速預設 +15%，可以改成例如 `--rate +10%`（慢一點）或 `--rate +20%`（快一點），用
`samples voice --rate +10% …`（其他照上面）重產試聽檔再聽一次，滿意了 `settings set` 時一起帶 `--rate`。

### 換成付費配音（ElevenLabs、Google）

上面三個是免費的 edge 語音。作者想換成付費、正式授權的配音時：

1. 要先有 API key，**這件事由 Danny 處理**（key 放哪裡寫在外掛的 docs/maintaining.md）。不要叫作者把 key 貼進對話。
2. 換配音（之後新做的片都用它，已經做好的片不會跟著變）。Google 寫 `--tts google`，換回免費的寫 `--tts edge`：

   ```
   uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video settings set --tts elevenlabs --data-dir ${CLAUDE_PLUGIN_DATA}
   ```

   印出「注意：…找不到它的 API key」就是 key 還沒放好：跟作者說要等 Danny 設定，先用 `--tts edge` 換回來。
3. 試聽（要連網，會用掉一點額度）。Google 有六個台灣口音的聲音（Wavenet-A 女聲、B／C 男聲；Standard-A／B／C 較便宜）：

   ```
   uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video samples voice --tts google --out ${CLAUDE_PLUGIN_DATA}/previews/voices --data-dir ${CLAUDE_PLUGIN_DATA}
   ```

   一樣一個一個 `afplay`（檔名照指令印出來的，例如 `voice-cmn-TW-Wavenet-A.wav`），挑好了存起來：

   ```
   uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video settings set --google-voice cmn-TW-Wavenet-B --data-dir ${CLAUDE_PLUGIN_DATA}
   ```

   ElevenLabs 試聽的是設定好的聲音；Danny 給了別的聲音代號，就加 `--voice <聲音代號>`（可以給好幾個）一起試，
   挑好了用 `settings set --elevenlabs-voice <聲音代號>` 存起來：

   ```
   uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video samples voice --tts elevenlabs --out ${CLAUDE_PLUGIN_DATA}/previews/voices --data-dir ${CLAUDE_PLUGIN_DATA}
   ```

4. 正在做的那支片也要換：再加一次 `settings set --project "<專案資料夾>" --tts elevenlabs`（只改那支片）。
   那支片會整支重新配音一次（費用是一支片的字數）；換回原本的配音不用再花錢（舊的配音還留著）。

## 第 4 步：挑配樂

產生三首的試聽檔（每首 15 秒，幾秒鐘就好）：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video samples music --out ${CLAUDE_PLUGIN_DATA}/previews/music --seconds 15 --data-dir ${CLAUDE_PLUGIN_DATA}
```

三首都是程式作曲的原創配樂，沒有版權問題：沉穩鋼琴（calm）、新聞脈動（pulse）、極簡電子（minimal）。
一首一首播，播之前講名字：

```
afplay ${CLAUDE_PLUGIN_DATA}/previews/music/music-calm.wav
```

```
afplay ${CLAUDE_PLUGIN_DATA}/previews/music/music-pulse.wav
```

```
afplay ${CLAUDE_PLUGIN_DATA}/previews/music/music-minimal.wav
```

跟他說：影片裡配樂會壓得很小聲墊在旁白下面，到片尾卡才大聲一點；也可以不要配樂。存起來（不要配樂寫 `none`）：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video settings set --music pulse --data-dir ${CLAUDE_PLUGIN_DATA}
```

## 第 5 步：頻道名、logo、品牌色（都可以跳過）

這三樣只用在片尾卡和圖表配色。有人做片只是為了自己看懂文章，沒有頻道、也不發布，這一步他整個用不到。
先用一句話問，例如「要不要在每支片最後放你的頻道名或 logo？只是自己看的話不用。」

- 他說不用、沒有頻道、跳過：**整步跳過**，直接到第 6 步。不要再追問 logo、品牌色，也不要問他為什麼不用。
- 他要放：再一次問一個，每一個他說不用就跳過、不追問：
  1. **頻道名**：會出現在每支片最後的片尾卡上，40 個字以內。
  2. **logo**：請他把圖檔（PNG 或 JPG）拖進這個對話視窗，你就拿得到檔案的路徑。
  3. **品牌色**：圖表的主色和片尾卡會用它。他說顏色名稱（例如「深藍」）就由你換成色碼（例如 `#1A4E8A`）；
     不要的話，圖表用預設的紅黑白。

有給的才存，一次存起來（沒給的選項就不要帶；三樣都沒給就不用跑）：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video settings set --channel "<頻道名>" --logo "<logo 檔案路徑>" --brand-color "#1A4E8A" --data-dir ${CLAUDE_PLUGIN_DATA}
```

- 沒填頻道名的片，片尾卡就只放資料來源和兩行聲明（有 logo 的話加上 logo），排版會自己調好。
  之後想加，說「改頻道名」就好；想拿掉，存 `--channel none`。
- 顏色很淺（例如淡黃）也照樣存：指令會印一句「頂端色條和小旗用原色，圖表上的字和線用加深的版本」，用口語轉述給他。
- 有存的話，把印出來的設定唸一遍給他確認。

## 第 6 步：做一支測試片

第 2 步全部裝好、第 5 步做完（跳過也算）之後跑（要連網配音，大約半分鐘到一分鐘，做完會自動用 QuickTime 打開）：

```
uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video test-video --data-dir ${CLAUDE_PLUGIN_DATA}
```

問作者：

- 聽得到配音（是他挑的聲音）、底下有配樂嗎？
- 看得到圖表、字幕，最後有一張列出資料來源的片尾卡嗎？（他有填頻道名或 logo 的話，也問卡上有沒有他的名字、logo）

都沒問題就完成了。測試片放在 Finder 的「影片」→「解說影片」→「安裝測試片」，看完可以整個刪掉；
以後做的片也會放在「解說影片」裡。

做不出來：把錯誤訊息的意思用白話講給他聽；看不懂的，請他說「回報問題」
（出問題的專案資料夾就是指令印出來的那個「安裝測試片」資料夾）。

## 完成後跟作者說

- 以後要做片：把文章（PDF、Word、Google 文件下載的檔，或直接貼文字）丟給 Claude，說「做成影片」。
- 想知道電腦哪裡怪怪的：說「檢查環境」。
- 想換聲音、配樂、頻道名、品牌色：直接說「換聲音」之類的就好，已經做好的片不會跟著變。

## 出狀況時

- 指令印出一大段英文，或你不確定怎麼辦：請作者說「回報問題」，把打包好的檔案用 LINE 傳給 Danny。
- 不要自己用別的方法裝這些工具（例如換別的安裝指令）；照指令印出來的「修法」做。
