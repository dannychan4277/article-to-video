# 字型授權 · Font licenses

這個資料夾的字型都**不屬於**引擎程式碼的 MIT 授權，各自沿用原本的授權。全部是 SIL Open Font License 1.1（全文見同資料夾的 `OFL.txt`）：可以隨軟體一起散布、可以商用，但不能單獨販售字型檔。部分檔案是抽出來的子集，並轉成 WOFF。

The fonts in this directory are NOT covered by the engine's MIT license. Each keeps its own license; all of them are SIL Open Font License 1.1 (full text in `OFL.txt`). Some files are subsets converted to WOFF.

| 檔案 | 字型 | 版權 | 來源 |
|---|---|---|---|
| `NotoSansTC-500.woff` `NotoSansTC-700.woff` `NotoSansTC-900.woff` | 思源黑體 Noto Sans CJK TC（Medium／Bold／Black，Version 2.004）的 GB2312＋Big5 子集；引擎程式碼裡的 family 名叫 `PuHui-*` | © 2014-2021 Adobe (http://www.adobe.com/)，Reserved Font Name "Source" | github.com/notofonts/noto-cjk |
| `RobotoCondensed-var.woff` | Roboto Condensed（可變字重 100–900，Version 3.008） | © 2011 The Roboto Project Authors | Google Fonts |

帶保留字型名稱（Reserved Font Name）的字型，改過的版本不能沿用保留名。上面的子集檔內部字型名稱仍是 "Noto Sans CJK TC"，沒有使用保留名 "Source"。

這份清單整理自 huashu-art-motion 引擎的 `lib/fonts/LICENSES.md`（作者花叔 alchaincyf），只留下這個外掛實際附帶的檔案，並補上原清單沒有列到的 Noto Sans CJK TC 子集。
