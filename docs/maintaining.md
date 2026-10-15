# 維護：怎麼把更新送到作者那台

給維護者（Danny）看的。作者那邊的操作只有最後一節那幾下。

## 版號：不寫，每個 commit 都是一次更新

`.claude-plugin/plugin.json` 故意**不寫 `version`**。

- 沒寫 version：Claude Code 從 git marketplace 裝的外掛用 commit 當版本，推到 `main` 的每個 commit，作者那邊都看得到「有更新」。
- 寫了 version：作者**只有在版號改變時**才拿得到更新；忘了改版號，推上去的修正就到不了他那台。
  所以不要加回去（`tests/test_plugin_manifest.py` 會擋）。`marketplace.json` 的外掛那一筆也不要寫 version。
- `pyproject.toml` 的 `version` 是 Python 套件的版本，跟外掛更新無關，不用動。
- 回報問題的 zip、環境檢查裡的「外掛版本」：作者那台是從 marketplace 裝的，外掛放在
  `~/.claude/plugins/cache/<marketplace>/<外掛>/<版本>/`，版本資料夾的名字就是 commit，會報成「commit 3f2a9c1b7d4e（套件 0.1.0）」；
  `git checkout` 那個 commit 再跑 `reproduce-issue`，就是他那台的版本。（資料夾名就是 commit 這點是照官方文件的路徑推的，
  第一次收到作者的回報時確認一下；不像 commit 的時候只會報套件版本。）

## 發一版

1. `uv run pytest`（全綠）
2. `claude plugin validate .`（結束碼 0，「✔ Validation passed with warnings」；唯一的警告是
   「No version specified」，就是上面故意不寫版號，不用理。不要加 `--strict`，它把這個警告當錯）
3. 推到 `main`。不用改任何版號。

## 作者怎麼升級

第三方 marketplace 預設**不會自動更新**。請作者在 Claude Code 裡：

1. 輸入 `/plugin marketplace update article-to-video`（刷新外掛清單，才看得到新的 commit）
2. 輸入 `/plugin` → **Installed** → 點 article-to-video → **Update now**
3. 照畫面提示重新載入（或重開 Claude Code）

沒有 `/plugin update` 這個指令。終端機的等效寫法是 `claude plugin update article-to-video@article-to-video`。
想讓他以後自動更新：`/plugin` → **Marketplaces** → article-to-video → 打開自動更新。

## 作者的設定與資料

- 頻道名、logo、品牌色、聲音、配樂、試聽檔都在 `${CLAUDE_PLUGIN_DATA}`（`~/.claude/plugins/data/<id>/`），外掛資料夾以外。
  **更新外掛不會動到**。
- **解除安裝會刪掉**（最後一個 scope 解除安裝時），除非加 `--keep-data`：
  `claude plugin uninstall article-to-video@article-to-video --keep-data`。
  所以給作者的修法一律是 **Update now**，不要叫他「重新安裝」（`skills/check-env/SKILL.md` 的字型那一項）。
  真的要重灌，維護者自己帶 `--keep-data` 做，或先跑 `settings show --json` 把設定抄下來。
- 每支片的專案資料夾有自己的設定快照，跟外掛資料夾無關。

## 付費配音（ElevenLabs、Google）：key 放哪裡、怎麼換

預設是免費的 edge 語音。換付費配音只要改設定，不用改程式：

```
uv run --project <外掛> python -m article_to_video settings set --tts elevenlabs --data-dir <資料位置>   # 或 google、edge
uv run --project <外掛> python -m article_to_video settings set --project <專案> --tts elevenlabs        # 只改那支片
uv run --project <外掛> python -m article_to_video build <專案> --tts google --data-dir <資料位置>       # 這一次臨時用
```

- **API key 絕不放進 repo、專案資料夾或 settings.json**。找 key 的順序（第一個有的）：
  1. 環境變數 `ELEVENLABS_API_KEY`／`GOOGLE_TTS_API_KEY`
  2. `<資料位置>/keys/elevenlabs.key`／`google-tts.key`（作者那台的資料位置是 `~/.claude/plugins/data/<id>/`，
     `settings show` 印得出來）——**給作者那台設定時放這裡**，跟著外掛資料走、更新外掛不會掉
  3. `~/.config/article-to-video/elevenlabs.key`／`google-tts.key`（自己這台方便；有設 `XDG_CONFIG_HOME` 就在那底下）

  檔案裡只放 key 一行（`chmod 600` 比較保險）。錯誤訊息、`last-run` 紀錄、回報問題的 zip 裡出現 key 一律換成 `<API key>`。
- 拿 key：ElevenLabs 網站 → Developers → API Keys（權限至少要 Text to Speech；要能列聲音的話加 Voices read）。
  Google Cloud Console：開 Cloud Text-to-Speech API、綁帳單 → 憑證 → 建立 API 金鑰（建議限制只能用這個 API）。
- 確認 key 能用：`check-env --try-voice --data-dir <資料位置>`（真的試唸「測試」兩個字）；不加 `--try-voice` 只看 key 在不在、不連線。
- 聲音：每家有自己的聲音設定，換配音層不用改聲音。
  - ElevenLabs：`--elevenlabs-voice <voice_id>`，預設 Neil Chuang（`auoHciLZJwKTwYUoRTYz`，台灣口音男聲、沉穩，
    已經在 Danny 的帳號裡）。換別的帳號要先在聲音庫把它「Add to My Voices」，或改用每個帳號都有的 Sarah
    （`EXAVITQu4vr4xnSDxMaL`，不是台灣口音）；聲音庫其他台灣口音的聲音也一樣要先加進帳號。
    模型 `--elevenlabs-model`，預設 `eleven_multilingual_v2`。同時只送 2 段（免費方案的同時請求上限）。
  - Google：`--google-voice`，cmn-TW 只有 `Wavenet-A`（女，預設）、`Wavenet-B／C`（男）、`Standard-A／B／C`（較便宜）。
  - 試聽：`samples voice --tts google|elevenlabs --out <資料夾> [--voice <代號>]…`。
- 花了多少字：`narrate`、`build`、`samples voice` 每次都印「這次送出 N 字給配音服務」；全用快取就是 0。
  Google 的 `<mark>` 不計費，`<speak>` 外殼每段多算 15 字。
- 快取：鍵是「配音層｜模型｜聲音｜語速｜文字」。換配音層整支重配一次；換回原本的直接用舊快取，不再花錢。
- 舊的片：設定快照是付費配音出現之前做的，會照舊用 edge；要換就 `settings set --project <專案> --tts …`。

## edge 語音突然配不出來（微軟擋了舊版 edge-tts）

edge-tts 是非官方介面，微軟換掉驗證（`Sec-MS-GEC` token）後，舊版 edge-tts 一句都唸不出來：
`check-env` 的「edge-tts 連線」連不上，錯誤是 `WSServerHandshakeError: 403, message='Invalid response status'`。
修法是升 edge-tts（2026-10-15：7.2.8 被擋，升 7.3.1 就好）：

1. `uvx --from edge-tts edge-tts --text 你好 --voice zh-TW-HsiaoChenNeural --write-media /tmp/x.mp3`：最新版能唸才往下做
   （最新版也 403 就是要等 edge-tts 出新版）。
2. `uv lock --upgrade-package edge-tts`，`pyproject.toml` 的下限跟著拉到新版（舊版已經沒用，別讓它被解析回去）。
3. `uv run pytest`、`check-env` 看「edge-tts 連線」、真的配一段確認有詞級時間點，推到 `main`，請作者 **Update now**
   （外掛用 `uv run --project` 執行，會照新的 `uv.lock` 自己換版本）。

微軟偶爾整段不回聲音（`NoAudioReceived`），那是另一回事：`EdgeTTS` 會等兩三秒自己重試，最多 3 次。

## 節奏：語速、段間留白、頭尾空白（2026-10 調快）

- 新片預設語速 +15%（之前 +5%）：edge 的 `rate`、ElevenLabs 的 `speed`（1.15，API 範圍 0.7–1.2）、Google 的 `speakingRate`（1.15）
  用同一個設定換算。舊片的設定快照記著當時的語速（多半是 +5%），重建不變；要跟著快就 `settings set --project <專案> --rate +15%`（整支重配）。
- 段與段之間的留白 `gap` 預設 0.25 秒（之前 0.35），不在設定快照裡。
- 每段頭尾的空白修成 0.06／0.15 秒（`narration.LEAD_PAD`／`TAIL_PAD`；edge 原本開頭約 0.2、結尾約 0.8 秒）：
  段與段之間聽到的停頓從約 1.3 秒變約 0.55 秒。原始音檔與快取鍵不變（不用重配、付費的不重花錢）。
- **舊片重建會套用留白與修剪**：旁白變短、分鏡時間重算，審片頁會把時間變了的分鏡標成改過，給作者看就是新的一版。
- 實測（示範片 500 字，edge 曉臻）：+5%＋舊留白 134.1 秒 → +15%＋修剪 114.3 秒（短 14.8%）；每分鐘 224 字 → 262 字。

## 支援的 Mac 與 `install --allow-source-build`

只支援 Apple Silicon、macOS 15 以上（`docs/adr/0002-只支援-Apple-Silicon-macOS-15.md`）：其他 Mac 拿不到 `ffmpeg-full`
整棵依賴樹的現成安裝包（bottle），`brew install` 會默默改成從原始碼編好幾個小時。

- `install` 第一步看這台 Mac（`mac_platform.py`），不支援就什麼都不裝、結束碼 1；`check-env` 第一項也是它。
- 靜態規則放行後，`brew install ffmpeg-full` 之前再問一次 Homebrew（`homebrew.check_bottles`：`brew deps --formula ffmpeg-full`
  ＋`brew info --json=v2 --formula …`，看每個要下載的套件有沒有這台 Mac 能用的 bottle）。有要從原始碼編的就停；
  查不了（離線）就照靜態規則繼續，`install.log` 裡會有一行「沒辦法先確認…」。
- **維護者的逃生門**：自己的 Intel Mac、舊 macOS，或就是想讓 brew 從原始碼編：

  ```
  uv run --project <外掛> python -m article_to_video install --allow-source-build --data-dir <資料位置>
  ```

  不支援的 Mac 照裝；沒有 bottle 也讓 `brew install ffmpeg-full` 從原始碼編（好幾個小時，可能失敗）。
  這個選項不列在 `--help`、skill 不提，**不要告訴作者**。（setup skill 的 allowed-tools `install *` 技術上會放行它，靠的是不提。）
- 自己查某台 Mac 的 bottle：`brew info --json=v2 ffmpeg-full` 看 `bottle.stable.files`。Homebrew 7 的 `brew info` 只列這台會用的
  那一個（舊版 macOS 的 bottle 新版可以用）；是空的就表示會從原始碼編。
- Homebrew 的支援範圍往前移（作者回報「Homebrew 已經不支援這個 macOS 版本」）：把 `mac_platform.MIN_MACOS`、
  `skills/setup/SKILL.md` 第 0 步、`skills/check-env/SKILL.md` 的版本一起往上調，ADR 0002 補一段。

## 私人 repo

repo 還沒公開時，作者那台要有讀 repo 的權限和 git 憑證（`gh auth login` 再 `gh auth setup-git`，或 SSH）。公開之後不用。
