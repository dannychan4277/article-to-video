# 只支援 Apple Silicon、macOS 15 以上

做片要用能燒字幕的 ffmpeg（Homebrew 的 `ffmpeg-full`，有 libass）。2026-10-11 查 Homebrew 的 formula API：`ffmpeg-full` 和它整棵依賴樹（103 個套件）只有 Apple Silicon 的 macOS 15 Sequoia（`arm64_sequoia`）、26 Tahoe（`arm64_tahoe`）、27（`arm64_golden_gate`）有現成安裝包（bottle）；`ca-certificates` 是不分平台的 `all`。macOS 14 以前沒有，Intel Mac 一個都沒有。沒有現成安裝包時 `brew install ffmpeg-full` 不會報錯，而是默默改成從原始碼編：八十幾個套件要編、另外三十幾個要升級，好幾個小時，常常失敗。作者不熟電腦，看終端機跑一個下午再失敗，是最糟的情況。Homebrew 自己的原始碼也註明預計 2027 年 9 月（或之後）拿掉 macOS x86_64 的支援。

Danny 決定：**只支援 Apple Silicon＋macOS 15 以上**。

1. **靜態規則**（`mac_platform.py`）：「安裝」的第一步（還沒裝 Homebrew、什麼都還沒動）和「檢查環境」的第一項。看硬體（`sysctl -n hw.optional.arm64`，Rosetta 下跑的 x86_64 Python 也認得出 Apple Silicon）和 `sw_vers` 的 macOS 版本。不支援就用白話說哪裡不對、怎麼自己確認（蘋果選單 →「關於這台 Mac」看晶片和 macOS 版本）、怎麼辦（macOS 太舊：系統設定 → 一般 → 軟體更新到 15 以上；Intel Mac：聯絡 Danny），結束碼 1。setup skill 在裝 uv 之前就先用這兩行指令看一次。
2. **動態檢查**（`homebrew.check_bottles`）：靜態規則放行之後、`brew install ffmpeg-full` 之前，用 `brew deps` 和 `brew info --json=v2` 看整棵樹裡要下載的套件，這台 Mac 有沒有現成安裝包（Homebrew 7 的 `brew info` 只列這台 Mac 會用的那一個）。有任何一個要從原始碼編就停下來說明。查不了（離線之類）就照靜態規則繼續裝，安裝紀錄裡寫一句。
3. **維護者的逃生門**：`install --allow-source-build`（不列在 `--help`、skill 不提），見 `docs/maintaining.md`。

## 後果

- **Intel Mac 不支援**。spec #1 原本寫 Apple Silicon 與 Intel 都支援，這份決定取代那一條。`/usr/local` 的 Homebrew 路徑處理留著：Apple Silicon 上從舊 Mac 搬過來、用 Rosetta 跑的 Intel 版 Homebrew 還認得（它只拿得到 Intel 的現成安裝包，動態檢查會擋下來、請作者找 Danny），維護者模式也用得到。
- macOS 14 以前的 Apple Silicon 要先更新系統（macOS 15 支援所有 Apple Silicon 的 Mac）。
- **Homebrew 的支援範圍會往前移**：新 macOS 出來、舊的掉出支援，就不再做那一版的現成安裝包。到時靜態規則還放行，動態檢查會先擋下，作者看到「多半是 Homebrew 已經不支援這個 macOS 版本，先更新 macOS」。收到這種回報就回來重看這份決定：把 `mac_platform.MIN_MACOS`、setup skill 第 0 步、check-env skill 的版本一起往上調。
- 測試一律注入平台（假的 sysctl／sw_vers、假 brew 的 `deps`／`info`），不連網、不真的裝任何東西。
