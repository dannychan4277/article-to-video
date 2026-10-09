// 每个字体文件注册成独立 family，用法：ctx.font = '700 58px "PuHui-Bold"'
// [article-to-video 內嵌精簡] 只帶財經圖表（t3_finance_chart）與 Vox（y2_vox）用到的字型。
// clip.js 會逐一載入這裡列的每個檔，少一個就整個開機失敗，所以這份清單要跟 lib/fonts/ 裡的檔案一致。
// 中文黑體的 family 名沿用引擎原本的 PuHui-*（語法程式裡寫死這些名字），檔案是 Noto Sans CJK TC 的 GB2312＋Big5 子集（繁簡都有，共用字是繁中字形）。
// 原始字型沒有 800，Heavy 用 900。版權與授權見 lib/fonts/LICENSES.md、lib/fonts/OFL.txt。
window.FONT_FACES = [
  {family:'PuHui-Medium', url:'lib/fonts/NotoSansTC-500.woff'},
  {family:'PuHui-Bold', url:'lib/fonts/NotoSansTC-700.woff'},
  {family:'PuHui-Heavy', url:'lib/fonts/NotoSansTC-900.woff'},
  {family:'PuHui-Black', url:'lib/fonts/NotoSansTC-900.woff'},
  // 財經數字：Roboto Condensed 可變字重，用 '600 220px "RobotoCondensed"' 選字重
  {family:'RobotoCondensed', url:'lib/fonts/RobotoCondensed-var.woff', desc:{weight:'100 900'}},
];
