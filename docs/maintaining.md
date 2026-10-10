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

## 私人 repo

repo 還沒公開時，作者那台要有讀 repo 的權限和 git 憑證（`gh auth login` 再 `gh auth setup-git`，或 SSH）。公開之後不用。
