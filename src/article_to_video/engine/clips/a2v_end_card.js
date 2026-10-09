// [article-to-video 自己加的語法，不是花叔原版] 片段 · 片尾卡：作者名（＋logo）、本片資料來源、免責聲明、AI 配音聲明。
// data: { channel（頻道名）, image?（logo；render.py 會改寫成本機網址）, sourcesLabel='資料來源', sources: [字串],
//         notes: [字串]（「僅供教育，非投資建議」「配音為 AI 合成」…）, brand?（品牌色，預設跟財經圖表一樣的紅） }
// 版式跟財經圖表（t3_finance_chart）同一套：白底、頂端品牌色細線＋左上小旗（第一幀就在，從圖表切過來不跳）、
// 黑字、灰色說明、同一組字型（缺字由 render.py --strict-glyphs 擋）。名字→來源→聲明依序淡入上移，0.8 秒內到位，之後靜止。
// 來源太多放不下時先縮字（最小 0.7 倍），再放不下就只列前幾項加「等共 N 項」。直式（H > W）小字放大兩成、底部多留。
CLIPS.a2v_end_card = (() => {
const RED = '#E3120B', INK = '#0C0C0C', SUB = '#4F5B61', RULE = 'rgba(12,12,12,0.16)';
const CN = '"PuHui-Medium"', CNB = '"PuHui-Bold"';
let L;
const wrapAt = (c, text, maxW, size, weight, fam) => TY.wrap(c, text, maxW, `${weight} ${size}px ${fam}`);
return {
  fonts: ['PuHui-Medium', 'PuHui-Bold'],
  init(ctx) {
    const { W, H, u, data: d, portrait } = ctx, c = document.createElement('canvas').getContext('2d');
    const fs = portrait ? 1.2 : 1, m = (portrait ? 70 : 90) * u, maxW = W - 2 * m;
    const img = d.image ? ctx.IMG[d.image] : null;
    const logoH = img ? 132 * u * fs : 0, logoW = img ? Math.min(logoH * img.width / img.height, 320 * u * fs) : 0;
    const logoGap = img ? 36 * u : 0;
    const name = TY.fit(c, d.channel || '', maxW - logoW - logoGap, 76 * u * fs, 'PuHui-Bold', { maxLines: 2, weight: '700 ', min: 0.6 });
    const nameH = name.lines.length * name.size * 1.2;
    const headH = Math.max(logoH, nameH);
    // 聲明：底部，一行一則
    const noteSz = 30 * u * fs, notes = (d.notes || []).flatMap(n => wrapAt(c, n, maxW, noteSz, 500, CN));
    const notesTop = H - (portrait ? 260 : 96) * u - notes.length * noteSz * 1.5;
    // 來源：名字下面一條細線、「資料來源」小標，再一項一行（長的折兩行）
    const y0 = (portrait ? 220 : 150) * u, labelSz = 28 * u * fs;
    const srcTop = headH + 48 * u + 2 * u + 40 * u + labelSz * 1.4;          // 相對內容區頂端
    const srcAvail = notesTop - 70 * u - y0 - srcTop;
    const sources = (d.sources || []).filter(s => String(s).trim());
    let srcSz = 36 * u * fs, srcLines = [];
    const layout = (sz, list) => list.map(s => wrapAt(c, s, maxW, sz, 500, CN).slice(0, 2));
    for (; ; srcSz *= 0.94) {
      srcLines = layout(srcSz, sources);
      const h = srcLines.reduce((a, ls) => a + ls.length, 0) * srcSz * 1.4;
      if (h <= srcAvail || srcSz < 36 * u * fs * 0.7) break;
    }
    const count = n => srcLines.slice(0, n).reduce((a, ls) => a + ls.length, 0);
    if (count(sources.length) * srcSz * 1.4 > srcAvail) {                    // 縮到最小還放不下：只列前幾項
      let shown = sources.length - 1;
      while (shown > 1 && (count(shown) + 1) * srcSz * 1.4 > srcAvail) shown--;
      srcLines = [...srcLines.slice(0, shown), [`等共 ${sources.length} 項來源`]];
    }
    const srcH = sources.length ? srcLines.reduce((a, ls) => a + ls.length, 0) * srcSz * 1.4 : 0;
    const total = sources.length ? srcTop + srcH : headH;
    const top = y0 + Math.max(0, (notesTop - 70 * u - y0 - total) / 2);      // 內容整塊在可用高度裡置中
    L = { m, maxW, img, logoH, logoW, logoGap, name, nameH, headH, top, notes, noteSz, notesTop, labelSz, srcSz, srcLines,
          hasSources: sources.length > 0 };
  },
  draw(c, t, ctx) {
    const { W, H, u, data: d } = ctx, brand = d.brand || RED;
    c.fillStyle = '#FFFFFF'; c.fillRect(0, 0, W, H);
    c.fillStyle = brand; c.fillRect(0, 0, W, 8 * u); CH.tag(c, L.m, 52 * u, 96 * u, 22 * u, brand);   // 跟圖表同一條線、同一面小旗
    const rise = (a, b) => { const p = MO.quintOut(MO.seg(t, a, b)); return [p, (1 - p) * 18 * u]; };
    c.textAlign = 'left'; c.textBaseline = 'alphabetic';
    // 名字（＋logo）
    let [p, dy] = rise(0, 0.5);
    c.save(); c.globalAlpha = p;
    if (L.img) {
      c.imageSmoothingEnabled = true; c.imageSmoothingQuality = 'high';
      c.drawImage(L.img, L.m, L.top + (L.headH - L.logoH) / 2 + dy, L.logoW, L.logoH);
    }
    const nx = L.m + L.logoW + L.logoGap, ny = L.top + (L.headH - L.nameH) / 2;
    c.fillStyle = INK; c.font = `700 ${L.name.size}px ${CNB}`;
    L.name.lines.forEach((ln, i) => c.fillText(ln, nx, ny + (i + 1) * L.name.size * 1.2 - L.name.size * 0.22 + dy));
    c.restore();
    // 資料來源
    if (L.hasSources) {
      [p, dy] = rise(0.15, 0.65);
      c.save(); c.globalAlpha = p;
      let y = L.top + L.headH + 48 * u;
      c.fillStyle = RULE; c.fillRect(L.m, y, L.maxW, 2 * u);
      y += 2 * u + 40 * u + L.labelSz;
      c.fillStyle = SUB; c.font = `700 ${L.labelSz}px ${CN}`; c.fillText(d.sourcesLabel || '資料來源', L.m, y + dy);
      y += L.labelSz * 0.4;
      c.fillStyle = INK; c.font = `500 ${L.srcSz}px ${CN}`;
      for (const ls of L.srcLines) for (const ln of ls) { y += L.srcSz * 1.4; c.fillText(ln, L.m, y - L.srcSz * 0.3 + dy); }
      c.restore();
    }
    // 聲明
    [p, dy] = rise(0.3, 0.8);
    c.save(); c.globalAlpha = p; c.fillStyle = SUB; c.font = `500 ${L.noteSz}px ${CN}`;
    L.notes.forEach((ln, i) => c.fillText(ln, L.m, L.notesTop + (i + 1) * L.noteSz * 1.5 - L.noteSz * 0.3 + dy));
    c.restore();
  },
};
})();
