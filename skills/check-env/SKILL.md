---
name: check-env
description: 檢查環境：逐項檢查這台 Mac 做影片要用的東西（這台 Mac 是不是 Apple Silicon、macOS 15 以上，Homebrew、能燒字幕的 ffmpeg、Chromium、uv、edge-tts 配音和它的網路連線、外掛附帶的字型、硬碟空間、頻道設定）是不是都正常，有問題就用白話說怎麼修。作者說「檢查環境」「環境有沒有問題」「是不是哪裡壞了」「為什麼做不了片」時使用。
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
   - **這台 Mac（晶片、macOS 版本）**（一定是第一項）：這個外掛只支援 Apple Silicon、macOS 15 以上的 Mac。
     這一項 ✗ 的話先只講這一項，照它的「修法」用白話說：macOS 太舊就請他到「系統設定」→「一般」→「軟體更新」
     更新到 15 以上；Intel Mac 就請他聯絡 Danny。**不要**叫他說「安裝」補裝其他項目（裝不起來，只會白等），
     也不要想別的方法硬裝。
   - **工具沒裝好**（Homebrew、ffmpeg、Chromium、uv、Playwright、edge-tts）：問他要不要現在補裝。
     要的話就照「安裝與設定」（setup）那個 skill 的第 2 步做，已經裝好的會自動略過。
   - **edge-tts 連線**：多半是沒網路。請他確認 Wi-Fi、Safari 打得開網頁，再檢查一次。
     網路正常還是連不上，可能是微軟改了服務：請他說「回報問題」傳給 Danny。
   - **頻道設定**：只是列出他的設定。頻道名、logo、品牌色都是選填，寫「沒填」是正常的（片尾卡就不放名字），
     不用問他要不要填。這一項 ✗ 的話照它的「修法」講；看不懂就請他說「回報問題」傳給 Danny。
   - **字型**：外掛檔案不完整。請他在 Claude 輸入 `/plugin`，到「Installed」找到 article-to-video，按「Update now」
     重新下載外掛（頻道名、logo 這些設定存在另外的地方，更新不會動到）。**不要叫他解除安裝**：解除安裝會把設定一起刪掉。
     更新完再檢查一次；還是不行，請他說「回報問題」傳給 Danny。
   - **硬碟剩餘空間**：請他清出一些空間（例如清空垃圾桶、刪掉不要的影片）。
   - 其他看不懂的：請他說「回報問題」，打包給 Danny。

4. 全部 ✓：告訴他環境沒問題，可以直接把文章丟給 Claude 說「做成影片」。
