---
name: check-env
description: 檢查環境：逐項檢查這台 Mac 做影片要用的東西（Homebrew、能燒字幕的 ffmpeg、Chromium、uv、edge-tts 配音和它的網路連線、外掛附帶的字型、硬碟空間、頻道設定）是不是都正常，有問題就用白話說怎麼修。作者說「檢查環境」「環境有沒有問題」「是不是哪裡壞了」「為什麼做不了片」時使用。
allowed-tools:
  - Bash(uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video check-env *)
  - Bash(~/.local/bin/uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video check-env *)
---

# 檢查環境

作者想知道這台 Mac 能不能做片、哪裡壞了。你跑一次檢查，用白話告訴他結果和怎麼修。
這個檢查只看、不改任何東西，大約十秒。

## 步驟

1. 跑這一行：

   ```
   uv run --project ${CLAUDE_PLUGIN_ROOT} python -m article_to_video check-env --data-dir ${CLAUDE_PLUGIN_DATA}
   ```

   - 說找不到 `uv`：改用 `~/.local/bin/uv` 開頭再跑一次。還是找不到，表示還沒安裝：請他說「安裝」。
   - 結束碼 1 的意思是「有項目要處理」，不是指令壞了。

2. 看結果：每一行開頭 ✓ 是正常、✗ 是要處理、・ 只是資訊（macOS 版本這類）。
   不用整份唸給他聽。先講「N 項都正常」，再一項一項講 ✗ 的：哪裡有問題、怎麼修（照那一項的「修法」，換成口語）。
   最後一行是總結。

3. 依 ✗ 的種類處理：
   - **工具沒裝好**（Homebrew、ffmpeg、Chromium、uv、Playwright、edge-tts）：問他要不要現在補裝。
     要的話就照「安裝與設定」（setup）那個 skill 的第 2 步做，已經裝好的會自動略過。
   - **edge-tts 連線**：多半是沒網路。請他確認 Wi-Fi、Safari 打得開網頁，再檢查一次。
     網路正常還是連不上，可能是微軟改了服務：請他說「回報問題」傳給 Danny。
   - **頻道設定**：還沒設定頻道名，做不了片（片尾卡要用）。照「安裝與設定」（setup）的第 5 步問他。
   - **字型**：外掛檔案不完整。請他在 Claude 輸入 `/plugin`，到「Installed」找到 article-to-video，按「Update now」
     重新下載外掛（頻道名、logo 這些設定存在另外的地方，更新不會動到）。**不要叫他解除安裝**：解除安裝會把設定一起刪掉。
     更新完再檢查一次；還是不行，請他說「回報問題」傳給 Danny。
   - **硬碟剩餘空間**：請他清出一些空間（例如清空垃圾桶、刪掉不要的影片）。
   - 其他看不懂的：請他說「回報問題」，打包給 Danny。

4. 全部 ✓：告訴他環境沒問題，可以直接把文章丟給 Claude 說「做成影片」。
