// 片段 · 财经图表（t3）：版式先出 → 坐标系 → 数据长出 → 只标一件事。真实数据驱动，单位/来源/角标全从 data 读。
// data: { title, unit（副标题：指标与单位）, source（来源，左下）, badge?（只有写了才画右下角标，如 "示意数据"），
//         chart: 'bar'|'line'|'candle', series: [{label, value}] 或 K 线 [{label, o, h, l, c}], decimals=1, prefix='', suffix='',
//         highlight?: {index, text, sub}, colors?: {main, accent, dim}, upDown?: 'cn'（红涨绿跌，默认）|'us' }
// cues（at 秒）：title（版式）· bar|line|candle（数据从 at 开始长出，dur 默认按根数；柱状图可写多个 bar cue，各带 data.index（数或数组）＝这几根在这一刻才长，让每根柱子踩自己的那个词）
//         series 每项可写 color（柱子自己的颜色，和片里别处同一个量的配色对上）· highlight（at 这一帧标注圆点弹出；text/sub 覆盖 data.highlight，data.index 指定第几根）
//         number（大数字：data {value, prefix, suffix, decimals}，at = 数字落定的时刻，提前 0.9s 开始滚）
// 规矩：柱一律从 0 起；折线/K 线纵轴不从 0 起时自动在副标题后加「纵轴未从 0 开始」（[Danny 本机改] data.axisNote 可换这句，如繁中「縱軸未從 0 開始」）。
CLIPS.t3_finance_chart = (() => {
const { clamp, lerp } = U;
const RED = '#E3120B', INK = '#0C0C0C', SUB = '#4F5B61', GRID = 'rgba(12,12,12,0.13)';
const CN = '"PuHui-Medium"', CNB = '"PuHui-Bold"', NUM = '"RobotoCondensed", "PuHui-Medium"';
let L, BUF;
const fmtv = (d, v) => (d.prefix || '') + TY.fmt(v, d.decimals ?? 1) + (d.suffix || '');
// [article-to-video 內嵌修改] 負數柱標註的版面（×u×字級倍數）：數值標籤占柱底下方多高（CH.bars 寫在柱底 +12、字最大 34）、
// 標籤到文字塊的間隔、第一行（50 號粗體）基線離文字塊上緣多遠、第二行基線再往下多少
const NEG_LABEL = 50, NEG_GAP = 18, NEG_LINE1 = 44, NEG_LINE2 = 40;
// [article-to-video 內嵌修改] 柱正上方的標註：引線至少露出這麼長（×u×字級倍數），不會被柱上數值標籤蓋得只剩一小截
const LEADER_MIN = 40;
// [article-to-video 內嵌修改] 品牌色：data.colors.brand 取代「當品牌用的紅」（頂端色條、小旗、角標、標註圓點、大數字、折線預設色）；
// 沒寫就是原本的紅。K 線的漲跌紅綠是語意色，不跟品牌走。#RRGGBB／#RGB 換成 rgba（折線下方漸層、角標底色用）。
const rgba = (col, a, fb) => { const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(col || ''); if (!m) return fb; let h = m[1]; if (h.length === 3) h = [...h].map(x => x + x).join('');
  return `rgba(${parseInt(h.slice(0, 2), 16)},${parseInt(h.slice(2, 4), 16)},${parseInt(h.slice(4, 6), 16)},${a})`; };
return {
  fonts: ['PuHui-Medium', 'PuHui-Bold'],
  safe: true,
  init(ctx) {
    const { W, H, u, data: d } = ctx, m = (ctx.portrait ? 70 : 90) * u, c = document.createElement('canvas').getContext('2d');
    const mL = Math.max(m, ctx.safe.left), mR = Math.max(m, ctx.safe.right);   // [article-to-video 內嵌] 左右邊距讓開 safe.left/right（直式：短影音平台右側的按鈕欄）；safe 沒寫左右時＝m，跟原版一樣
    const tall = ctx.portrait && ctx.box.h > W * 1.25;                    // [article-to-video 內嵌] 竖屏的「標題 3 行、多一格刻度、底部多留」只在讓開 safe 之後畫面真的很高時才用：直式上下讓給平台介面與字幕後，內容框接近方形，照原版會擠成 8 條刻度、圖區被標題吃光
    const kind = (ctx.of('bar', 'line', 'candle')[0] || {}).kind || d.chart || 'bar';
    const S = d.series || [];
    const vals = kind === 'candle' ? S.flatMap(r => [r.h, r.l]) : S.map(r => r.value);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    let zeroBased = kind === 'bar' || lo <= 0 || (hi - lo) / hi > 0.6;
    if (zeroBased) lo = Math.min(0, lo);
    const pad = (hi - lo) * 0.08, ticks = CH.ticks(zeroBased ? lo : lo - pad, hi + pad, tall ? 5 : 4);
    const step = ticks[1] - ticks[0];                                     // 刻度要把数据整个包住（CH.ticks 只给区间内的整刻度）
    while (ticks[ticks.length - 1] < hi + pad * 0.5) ticks.push(+(ticks[ticks.length - 1] + step).toFixed(10));
    while (ticks[0] > (zeroBased ? lo : lo - pad * 0.5)) ticks.unshift(+(ticks[0] - step).toFixed(10));
    const titleSz = (ctx.portrait ? 64 : 58) * u, t = TY.fit(c, d.title || '', W - mL - mR, titleSz, 'PuHui-Bold', { maxLines: tall ? 3 : 2, weight: '700 ' });
    const sub = (d.unit || '') + (!zeroBased ? (d.unit ? '　' : '') + (d.axisNote || '纵轴未从 0 开始') : '');
    // 副标题、来源行：放不下先缩一点，再折两行（竖屏长口径会出画）；来源行要让开右下角标
    const fsz = ctx.portrait ? 1.2 : 1, subF = TY.fit(c, sub, W - mL - mR, 32 * u * fsz, 'PuHui-Medium', { maxLines: 2, min: 0.8 });
    c.font = `700 ${28 * u}px "PuHui-Medium"`; const badgeW = d.badge ? c.measureText(d.badge).width + 28 + 30 * u : 0;
    const srcF = TY.fit(c, d.source || '', W - mL - mR - badgeW, 26 * u * fsz, 'PuHui-Medium', { maxLines: 2, min: 0.8 });
    const top = ctx.safe.top + 80 * u + t.lines.length * t.size * 1.25 + 60 * u + (subF.lines.length - 1) * subF.size * 1.3;
    const band = ctx.of('number').length ? 150 * u : 0;                 // 有 number：给大数字单独留一条，不压在图区里（折线一上涨，右上角正好是线头）
    const F = { x: mL + 20 * u, y: top + 40 * u + band, w: W - mL - mR - 40 * u, h: H - top - 40 * u - band - (tall ? 260 : 190) * u - ctx.safe.bottom - (srcF.lines.length - 1) * srcF.size * 1.3 };   // safe.bottom：字幕带整条让出来，来源行落在它上面
    const n = S.length, slot = (F.w - 80 * u) / n, xs = S.map((_, i) => F.x + slot * (i + 0.5));   // 右边留 80px 给刻度值，别压在最后一根柱上
    // x 轴：时间序列（折线/K 线，或 data.xAxis:'time'）按间隔抽稀；类目（柱状图默认）每根都要有名字，放不下就缩字、再放不下错成两行
    const category = (d.xAxis || (kind === 'bar' ? 'category' : 'time')) === 'category';
    const fs0 = ctx.portrait ? 1.2 : 1, xf = 30 * u * fs0;
    c.font = `400 ${xf}px "RobotoCondensed", "PuHui-Medium"`;
    const maxW = Math.max(1, ...S.map(r => c.measureText(String(r.label ?? '')).width));
    const xScale = category ? clamp(slot * 0.94 / maxW, 0.7, 1) : 1, stagger = category && slot * 0.94 < maxW * 0.7;
    // 刻度标签按步长定小数位：步长 2.5 就要 1 位，不然 −2.5/2.5/7.5 会被四舍五入印成 −3/3/8（刻度撒谎）
    let tdec = 0; while (tdec < 4 && Math.abs(step * 10 ** tdec - Math.round(step * 10 ** tdec)) > 1e-6) tdec++;
    const roomFor = need => { if (F.h - need < 60 * u) throw new Error(`[article-to-video 內嵌] 圖區太矮：讓開上下 safe（字幕、平台介面）之後只剩 ${Math.round(F.h)}px 畫資料，標註留白要 ${Math.round(need)}px。標題、副標或來源太長，或多了大數字那一條：請縮短文字、拿掉大數字，或把標註改短`); };   // [article-to-video 內嵌] 原版這裡放不下時下面的 while 會無限加刻度，當掉時只看到 Invalid array length
    roomFor(0);
    if (kind === 'bar' && Math.min(...vals) < 0) {                       // 负数柱的数值写在柱底下方：最低那根下面留出一行，不然压到类目名
      const vmin = Math.min(...vals), need = 56 * u * fs0; roomFor(need);
      while ((vmin - ticks[0]) / (ticks[ticks.length - 1] - ticks[0]) * F.h < need) ticks.unshift(+(ticks[0] - step).toFixed(10));
    }
    const qh1 = ctx.of('highlight')[0], H1 = d.highlight || {}, hiI = qh1 ? (qh1.data && qh1.data.index != null ? qh1.data.index : (H1.index != null ? H1.index : S.length - 1)) : -1;
    if (kind === 'bar' && hiI >= 0 && S[hiI] && S[hiI].value >= 0) {   // 标注的柱子上方要留够文字块（[Danny 本机改] 原本只管「不是最高那根」；标最高那根时斜向标注被 ty 夹在 F.y+60，文字压回柱子上）
      const nL = [C(qh1.text, H1.text), C(qh1.sub, H1.sub)].filter(Boolean).length, need = (52 * nL + 44 + 30) * u * fs0, vmax = Math.max(...vals); roomFor(need);
      while ((ticks[ticks.length - 1] - vmax) / (ticks[ticks.length - 1] - ticks[0]) * F.h < need) ticks.push(+(ticks[ticks.length - 1] + step).toFixed(10));
    }
    if (kind === 'bar' && hiI >= 0 && S[hiI] && S[hiI].value < 0) {    // [article-to-video 內嵌修改] 負數柱的標註放在數值標籤正下方：最低那根柱下面再留出標註文字的高度
      const nL = [C(qh1.text, H1.text), C(qh1.sub, H1.sub)].filter(Boolean).length, vmin = Math.min(...vals);
      const need = (NEG_LABEL + NEG_GAP + NEG_LINE1 + NEG_LINE2 * (nL - 1) + 12) * u * fs0; roomFor(need);
      while ((vmin - ticks[0]) / (ticks[ticks.length - 1] - ticks[0]) * F.h < need) ticks.unshift(+(ticks[0] - step).toFixed(10));
    }
    if (kind === 'line' && hiI >= 0) {                                    // [Danny 本机改] 折线标注：线的最高点之上留够文字块，斜向标注才不会压在线上
      const vmax = Math.max(...vals), need = 170 * u * fs0; roomFor(need);
      while ((ticks[ticks.length - 1] - vmax) / (ticks[ticks.length - 1] - ticks[0]) * F.h < need) ticks.push(+(ticks[ticks.length - 1] + step).toFixed(10));
    }
    L = { subF, srcF, m: mL, mR, kind, S, zeroBased, ticks, tdec, yS: CH.lin(ticks[0], ticks[ticks.length - 1], F.y + F.h, F.y), F, xs, slot, t, sub, band, category, xFont: xf * xScale, stagger,
      every: category ? 1 : Math.max(1, Math.ceil(n / (ctx.portrait ? 6 : 10))) };
    const brand = (d.colors && d.colors.brand) || RED;                    // [article-to-video 內嵌修改] 品牌色
    const main = (d.colors && d.colors.main) || (kind === 'bar' ? '#006BA2' : brand);
    const ink = (d.colors && d.colors.ink) || brand;                      // [article-to-video 內嵌修改] 品牌色太淺時，要讀的字與記號用加深的 ink（色條、小旗照用原色）
    L.col = { main, accent: (d.colors && d.colors.accent) || main, dim: (d.colors && d.colors.dim) || '#C6D2D8', brand, ink };
    L.updown = d.upDown === 'us' ? ['#1B9E5A', RED] : [RED, '#1B9E5A'];
    if (!category) {   // [article-to-video 內嵌] 時間軸的名字互相重疊就不寫：直式畫面窄、字又放大兩成，原版的抽稀間隔常讓相鄰名字疊在一起。被標註那根優先，其餘照原本的規則（含 xTicks）從左到右、放得下才寫；沒有重疊時跟原版一樣
      c.font = `400 ${L.xFont}px ${NUM}`;
      const half = i => c.measureText(String(S[i].label ?? '')).width / 2;
      const base = i => d.xTicks ? d.xTicks.includes(i) : i % L.every === 0 || (i === n - 1 && (n - 1) % L.every >= L.every / 2);
      const kept = hiI >= 0 && S[hiI] ? [hiI] : [];
      for (let i = 0; i < n; i++) if (i !== hiI && base(i) && kept.every(j => Math.abs(xs[i] - xs[j]) >= half(i) + half(j) + 16 * u)) kept.push(i);
      L.xKeep = new Set(kept);
    }
    if (kind === 'line') placeLineText(ctx, c, hiI, qh1, H1);
  },
  draw(c0, t, ctx) {
    // alpha：图是白纸黑字，叠在深色画面上墨色看不见。先画进缓冲，再带一圈白色光晕合成（和 t1 同一招）
    const c = ctx.alpha ? (BUF = BUF || document.createElement('canvas'), BUF.width = ctx.W, BUF.height = ctx.H, BUF.getContext('2d')) : c0;
    const { W, H, u, data: d } = ctx, { m, F, yS, xs, S, kind } = L, fs = ctx.portrait ? 1.2 : 1;   // 竖屏在手机上看：小字放大两成
    const qt = ctx.of('title')[0] || { at: 0 }, lt = ctx.lt(t, qt.at);
    if (!ctx.alpha) { c.fillStyle = '#FFFFFF'; c.fillRect(0, 0, W, H); }
    c.save(); if (d.push) { const s = 1 + (d.pushFrom || 0) + d.push * clamp(t / ctx.dur); c.translate(W / 2, H / 2); c.scale(s, s); c.translate(-W / 2, -H / 2); }   // [Danny 本机改] data.push：整镜匀速矢量慢推（语法卡 ③「图表镜头匀速慢推 1→1.03」），默认 0 不推
    // ---- 版式：顶红线 → 小旗 → 标题 → 副标题（单位/口径）→ 来源 → 角标 ----
    if (lt > 0) {
      const p = MO.quintOut(MO.seg(lt, 0, 0.45));
      c.fillStyle = L.col.brand; c.fillRect(0, ctx.safe.top, W * p, 8 * u);   /* [article-to-video 內嵌] 色條畫在 safe.top（原版 0；16:9 safe.top＝0 不變） */ CH.tag(c, m, ctx.safe.top + 52 * u, 96 * p * u, 22 * u, L.col.brand);
      c.save(); c.textBaseline = 'alphabetic'; c.textAlign = 'left';
      const tp = MO.quintOut(MO.seg(lt, 0.05, 0.5)); c.globalAlpha = tp; c.fillStyle = INK; c.font = `700 ${L.t.size}px ${CNB}`;
      L.t.lines.forEach((ln, i) => c.fillText(ln, m, ctx.safe.top + 80 * u + (i + 1) * L.t.size * 1.2 + (1 - tp) * 18 * u));
      const sp = MO.quintOut(MO.seg(lt, 0.15, 0.6)), sy = ctx.safe.top + 80 * u + L.t.lines.length * L.t.size * 1.2 + 54 * u;
      c.globalAlpha = sp; c.font = `400 ${L.subF.size}px ${CN}`; c.fillStyle = SUB; L.subF.lines.forEach((ln, k) => c.fillText(ln, m, sy + k * L.subF.size * 1.3 + (1 - sp) * 14 * u));
      c.globalAlpha = MO.seg(lt, 0.2, 0.6); c.font = `400 ${L.srcF.size}px ${CN}`; c.fillStyle = '#7c868b';
      L.srcF.lines.forEach((ln, k, a) => c.fillText(ln, m, H - 40 * u - ctx.safe.bottom - (a.length - 1 - k) * L.srcF.size * 1.3));
      c.restore();
      if (d.badge) { c.save(); c.globalAlpha = MO.seg(lt, 0.2, 0.6); CH.demoBadge(c, d.badge, { x: W - L.mR, y: H - 50 * u - ctx.safe.bottom, font: `700 ${28 * u}px ${CN}`, col: L.col.ink, box: rgba(L.col.ink, 0.06, 'rgba(227,18,11,0.06)') }); c.restore(); }
    }
    // ---- 坐标系（版式之后 0.25s） ----
    const qd = ctx.of('bar', 'line', 'candle')[0] || { at: qt.at + 0.75 }, gStart = Math.min(qt.at + 0.25, qd.at - 0.4);
    const gp = MO.seg(ctx.lt(t, gStart), 0, 0.6);
    const fmtTick = v => TY.fmt(v, L.tdec);
    CH.grid(c, F, L.ticks, yS, gp, { col: GRID, zeroCol: L.zeroBased ? INK : GRID, font: `400 ${28 * u * fs}px ${NUM}`, labelCol: '#5b666b', side: 'right', lw: 1.5 * u, zeroLw: 3 * u, fmt: fmtTick, labelDy: -10 * u });
    const qh0 = ctx.of('highlight')[0], hi0 = qh0 ? (qh0.data && qh0.data.index != null ? qh0.data.index : (d.highlight && d.highlight.index != null ? d.highlight.index : S.length - 1)) : -1;
    const keep = L.xKeep ? (i => L.xKeep.has(i)) : i => d.xTicks ? (d.xTicks.includes(i) || i === hi0) : i % L.every === 0 || i === hi0 ||   // [Danny 本机改] data.xTicks：自己指定要写名字的索引（时间轴按月/按年对齐）
      (i === S.length - 1 && (S.length - 1) % L.every >= L.every / 2);   // 被标注的那根永远有名字；末尾标签离上一个太近就不硬塞
    const xp = MO.seg(ctx.lt(t, gStart + 0.15), 0, 0.5), xy = F.y + F.h + 46 * u, xo = { font: `400 ${L.xFont}px ${NUM}`, col: '#3a4246' };
    if (!L.stagger) CH.xLabels(c, S.map((r, i) => ({ x: xs[i], label: keep(i) ? String(r.label ?? '') : '' })), xy, xp, xo);
    else { CH.xLabels(c, S.map((r, i) => ({ x: xs[i], label: i % 2 ? '' : String(r.label ?? '') })), xy, xp, xo); CH.xLabels(c, S.map((r, i) => ({ x: xs[i], label: i % 2 ? String(r.label ?? '') : '' })), xy + L.xFont * 1.15, xp, xo); }
    // ---- 数据 ----
    const n = S.length, ddur = qd.dur || Math.min(2.2, Math.max(0.9, n * (kind === 'bar' ? 0.22 : 0.06)));
    const dp = ctx.p(t, qd.at, ddur);
    const qh = ctx.of('highlight')[0], hi = qh ? (qh.data && qh.data.index != null ? qh.data.index : (d.highlight && d.highlight.index != null ? d.highlight.index : n - 1)) : -1;
    const hiP = qh ? MO.smooth(ctx.p(t, qh.at, 0.4)) : 0;
    let anchor = null;
    if (kind === 'bar') {
      const bw = Math.min(150 * u, L.slot * 0.62);
      const on = qh && ctx.lt(t, qh.at) > 0, opt = { col: L.col.main, hiP, hiCol: L.col.accent, dimCol: L.col.dim,
        fmt: v => fmtv(d, v), font: `600 ${Math.max(24 * u * fs, Math.min(34 * u * fs, L.slot * 0.3))}px ${NUM}`, labelCol: INK, label: n <= 14 };
      const own = {};                                                      // 带 data.index 的 bar cue：这几根柱子从它的 at 起单独长
      for (const q of ctx.of('bar')) if (q.data && q.data.index != null) [].concat(q.data.index).forEach(i => { own[i] = q; });
      let tops;
      if (!Object.keys(own).length && !S.some(r => r.color)) tops = CH.bars(c, S.map((r, i) => ({ x: xs[i], v: r.value })), yS, bw, dp, { ...opt, lag: Math.min(0.22, 2 / n), hi: on ? hi : -1 });
      else tops = S.map((r, i) => {
        const q = own[i], p = q ? ctx.p(t, q.at, q.dur || 0.7) : MO.lagged(i, n, dp, Math.min(0.22, 2 / n));
        const col = r.color || L.col.main;
        return CH.bars(c, [{ x: xs[i], v: r.value }], yS, bw, p, { ...opt, ...(r.text != null ? { fmt: () => r.text } : {}), lag: 0, col, hiCol: col, hi: on ? (i === hi ? 0 : 1) : -1 })[0];   // [Danny 本机改] series 项可写 text 覆盖柱上数值（如「＞50%」；只在逐根长出的路径生效：有 data.index 的 bar cue 或 color）
      });
      if (hi >= 0) anchor = [xs[hi], tops[hi][1]];
      L.neg = hi >= 0 && S[hi].value < 0; L.tops = tops;
      L.barAbove = hi >= 0 && S[hi].value >= 0 && S.some((r, j) => j !== hi && r.value > S[hi].value * 1.5);   // 有更高的柱子挡在旁边：标注放在这根柱子正上方，别压进别的柱子
    } else if (kind === 'line') {
      const pts = S.map((r, i) => [xs[i], yS(r.value)]);
      const lp = MO.smooth(dp);
      const hp = CH.line(c, pts, lp, { col: L.col.main, lw: 5 * u, headR: 9 * u, area: { top: F.y, base: F.y + F.h, c0: rgba(L.col.main, 0.12, 'rgba(227,18,11,0.12)'), c1: rgba(L.col.main, 0, 'rgba(227,18,11,0)') } });
      if (hp && lp < 1) { const v = yS.inv(hp[1]); c.save(); c.font = `700 ${36 * u}px ${NUM}`; c.fillStyle = L.col.main; c.textAlign = 'left'; c.textBaseline = 'middle'; haloTabular(c, fmtv(d, v), hp[0] + 20 * u, hp[1] - 4 * u, {}, 8 * u); c.restore(); }   // [article-to-video 內嵌修改] 描白邊
      else if (lp >= 1) { const v = S[n - 1].value; c.save(); if (qh && hi === n - 1) c.globalAlpha = 1 - clamp(ctx.lt(t, qh.at) / 0.25);   // [Danny 本机改] 标注就是最后一点时，线头数值让位，免得两个数字叠在一起
        c.font = `700 ${36 * u}px ${NUM}`; c.fillStyle = L.col.main; c.textAlign = 'right'; c.textBaseline = 'alphabetic'; haloTabular(c, fmtv(d, v), L.endLab.x, L.endLab.y, { align: 'right' }, 8 * u); c.restore(); }   // [article-to-video 內嵌修改] 位置挑不被線穿過的（placeLineText）、描白邊
      if (hi >= 0) anchor = pts[hi];
    } else {
      const cw = Math.min(30 * u, L.slot * 0.6);
      CH.candles(c, S, xs, yS, cw, dp, { up: L.updown[0], down: L.updown[1], lag: Math.min(0.06, 1.5 / n) });
      if (hi >= 0) anchor = [xs[hi], yS(S[hi].c)];
    }
    // ---- 标注：一镜只标一件事 ----
    let above = L.barAbove;
    if (qh && anchor && kind === 'bar' && !above && !L.neg) {             // 预演斜向标注的文字框：压到别的柱子或它的数值，就改放这根柱子正上方
      const H0 = d.highlight || {}, ls = [C(qh.text, H0.text), C(qh.sub, H0.sub)].filter(Boolean);
      const ax = anchor[0] - Math.min(150 * u, L.slot * 0.62) / 2, right = ax > F.x + F.w * 0.55, tx = right ? ax - 60 * u : ax + 60 * u, ty = Math.max(F.y + 60 * u, anchor[1] - 140 * u);
      c.save(); c.font = `700 ${50 * u}px ${NUM}`; let tw = c.measureText(ls[0] || '').width; c.font = `400 ${28 * u * fs}px ${CN}`; if (ls[1]) tw = Math.max(tw, c.measureText(ls[1]).width); c.restore();
      const bx0 = right ? tx - 12 - tw : tx + 12, bx1 = bx0 + tw, by1 = ty + (ls.length > 1 ? 56 : 16) * u;
      above = L.tops.some(([bx, by], j) => j !== hi && bx + L.slot * 0.45 > bx0 && bx - L.slot * 0.45 < bx1 && by - 44 * u * fs < by1);
    }
    if (qh && anchor && kind === 'bar' && L.neg) {
      // [article-to-video 內嵌修改] 負數柱：數值標籤寫在柱底下方（CH.bars），原版的斜向標註往下拉會壓在標籤上（「-2.6%」被「跌最深」蓋住）。
      // 改成文字塊放在這根柱的數值標籤正下方（也讓開它橫跨到的其他負數柱與它們的標籤），引線從標籤下緣垂直拉下來；圓點照舊在柱底左角。
      const H0 = d.highlight || {}, lines = [C(qh.text, H0.text), C(qh.sub, H0.sub)].filter(Boolean), k = ctx.lt(t, qh.at);
      if (k > 0) {
        const s = MO.spring(k, { duration: 0.45, bounce: 0.25 }), lp = MO.quintOut(MO.seg(k, 0.12, 0.45)), fp = MO.quintOut(MO.seg(k, 0.3, 0.7));
        const [x, y] = anchor, vlab = NEG_LABEL * u * fs;
        c.save(); c.font = `700 ${50 * u * fs}px ${NUM}`; let tw = c.measureText(lines[0]).width; c.font = `400 ${28 * u * fs}px ${CN}`; if (lines[1]) tw = Math.max(tw, c.measureText(lines[1]).width);
        const cx = clamp(x, F.x + tw / 2, F.x + F.w - 80 * u - tw / 2);
        const cover = L.tops.filter(([bx]) => bx > cx - tw / 2 - L.slot / 2 && bx < cx + tw / 2 + L.slot / 2).map(([, by]) => by > yS(0) ? by + vlab : yS(0));
        const top = Math.max(y + vlab, ...cover) + NEG_GAP * u * fs;   // 文字塊上緣
        c.fillStyle = L.col.ink; c.beginPath(); c.arc(x - Math.min(150 * u, L.slot * 0.62) / 2, y, 9 * u * Math.max(0, s), 0, 7); c.fill();
        const y0 = y + vlab, y1 = top - 6 * u;
        if (y1 - y0 > 6 * u) { c.strokeStyle = INK; c.lineWidth = 2 * u; c.beginPath(); c.moveTo(x, y0); c.lineTo(lerp(x, cx, lp), lerp(y0, y1, lp)); c.stroke(); }
        c.globalAlpha = fp; c.fillStyle = INK; c.textAlign = 'center'; c.textBaseline = 'alphabetic';
        lines.forEach((ln, i) => { c.font = i === 0 ? `700 ${50 * u * fs}px ${NUM}` : `400 ${28 * u * fs}px ${CN}`; haloText(c, ln, cx, top + (NEG_LINE1 + i * NEG_LINE2) * u * fs + (1 - fp) * 12 * u, 10 * u); });   // [article-to-video 內嵌修改] 描白邊（試跑 #9）
        c.restore();
      }
    } else if (qh && anchor && kind === 'bar' && above) {
      const H0 = d.highlight || {}, lines = [C(qh.text, H0.text), C(qh.sub, H0.sub)].filter(Boolean), k = ctx.lt(t, qh.at);
      if (k > 0) {
        const s = MO.spring(k, { duration: 0.45, bounce: 0.25 }), lp = MO.quintOut(MO.seg(k, 0.12, 0.45)), fp = MO.quintOut(MO.seg(k, 0.3, 0.7));
        // 文字块放在这根柱子正上方：先量宽，横向夹在图区里；再抬到它横跨的所有柱子（含数值标签）之上，引线从本柱数值标签上沿连上去
        const [x, y] = anchor, lh = 52 * u * fs, vlab = 44 * u * fs;
        c.save(); c.font = `700 ${50 * u * fs}px ${NUM}`; let tw = c.measureText(lines[0]).width; c.font = `400 ${28 * u * fs}px ${CN}`; if (lines[1]) tw = Math.max(tw, c.measureText(lines[1]).width);
        const cx = clamp(x, F.x + tw / 2, F.x + F.w - 80 * u - tw / 2);
        const cover = L.tops.filter(([bx]) => bx > cx - tw / 2 - L.slot / 2 && bx < cx + tw / 2 + L.slot / 2).map(([, by]) => by);
        // [article-to-video 內嵌修改] 引線至少露出 LEADER_MIN：原版文字塊緊貼數值標籤（只隔 18），引線只剩 10 像素、看起來被標籤蓋掉（短柱最明顯）
        const y0 = y - vlab - 4 * u, bottom = Math.max(F.y + lh * lines.length, Math.min(Math.min(y, ...cover) - vlab - 18 * u, y0 - LEADER_MIN * u * fs - 10 * u));   // 文字块底边
        c.fillStyle = L.col.ink; c.beginPath(); c.arc(x - Math.min(150 * u, L.slot * 0.62) / 2, y, 9 * u * Math.max(0, s), 0, 7); c.fill();   // [Danny 本机改] 圆点放在柱顶左角：放柱顶正中会压到柱上数值的字底
        const y1 = bottom + 10 * u;
        if (y0 - y1 > 6 * u) { c.strokeStyle = INK; c.lineWidth = 2 * u; c.beginPath(); c.moveTo(x, y0); c.lineTo(lerp(x, cx, lp), lerp(y0, y1, lp)); c.stroke(); }
        c.globalAlpha = fp; c.fillStyle = INK; c.textAlign = 'center'; c.textBaseline = 'alphabetic';
        lines.forEach((ln, i) => { c.font = i === 0 ? `700 ${50 * u * fs}px ${NUM}` : `400 ${28 * u * fs}px ${CN}`; haloText(c, ln, cx, bottom - (lines.length - 1 - i) * lh * (i === 0 ? 1 : 0.62) + (1 - fp) * 12 * u, 10 * u); });   // [article-to-video 內嵌修改] 描白邊（試跑 #9）
        c.restore();
      }
    } else if (qh && anchor) {
      if (kind === 'bar') anchor = [anchor[0] - Math.min(150 * u, L.slot * 0.62) / 2, anchor[1]];
      const H0 = d.highlight || {}, lines = [C(qh.text, H0.text), C(qh.sub, H0.sub)].filter(Boolean);
      const right = anchor[0] > F.x + F.w * 0.55, co = kind === 'line' ? L.lineCo : null;   // [article-to-video 內嵌修改] 折線：位置照 placeLineText 挑的（不壓線、零軸）
      const tx = co ? co.tx : right ? anchor[0] - 60 * u : anchor[0] + 60 * u;
      const ty = co ? co.ty : Math.max(F.y + 60 * u, anchor[1] - 140 * u);   // [article-to-video 內嵌修改] 負數柱另外處理（上面那段），這裡只剩正數柱與折線、K 線
      CH.callout(c, ctx.lt(t, qh.at), 0, { x: anchor[0], y: anchor[1], tx, ty, lines, col: INK, dotCol: L.col.ink, font: `700 ${50 * u}px ${NUM}`, font2: `400 ${28 * u * fs}px ${CN}`, align: co ? co.align : right ? 'right' : 'left', r: 9 * u, halo: 10 * u });
    }
    // ---- 大数字（number cue：at = 落定时刻） ----
    for (const q of ctx.of('number')) {
      const nd = q.data || {}, k = MO.expoOut(clamp((t - (q.at - 0.9)) / 0.9)); if (t < q.at - 0.9) continue;
      c.save(); c.globalAlpha = clamp((t - (q.at - 0.9)) / 0.2); c.font = `700 ${120 * u}px ${NUM}`; c.fillStyle = L.col.ink; c.textAlign = 'right'; c.textBaseline = 'alphabetic';
      const numStr = (nd.prefix || '') + TY.fmt((nd.value || 0) * k, nd.decimals ?? 0) + (nd.suffix || ''), by = F.y - 62 * u;   // 落在图区上方专留的那一条（让开顶格刻度值）
      const nw = c.measureText((nd.prefix || '') + TY.fmt(nd.value || 0, nd.decimals ?? 0) + (nd.suffix || '')).width;
      TY.tabular(c, numStr, W - L.mR, by, { align: 'right' });
      const cap = q.text ?? q.sub; if (cap) { c.font = `400 ${30 * u * fs}px ${CN}`; c.fillStyle = SUB; c.textAlign = 'right'; c.fillText(cap, W - L.mR - nw - 24 * u, by - 10 * u); }
      c.restore();
    }
    c.restore();
    if (ctx.alpha) { c0.save(); c0.shadowColor = '#FFFFFF'; for (const b of [8, 3, 1.5]) { c0.shadowBlur = b * ctx.u; c0.drawImage(BUF, 0, 0); } c0.restore(); }   // 三遍由宽到窄：外圈柔光＋贴字的实边
  },
};
function C(...a) { return a.find(v => v != null && v !== ''); }
// [article-to-video 內嵌修改] 折線的標註與線尾數值擺哪（真實試跑 #9）。原版標註固定在點的右上（或左上）140、線尾數值固定寫在線頭左上，
// 常壓到零軸、線本身和格線（線最後往下掉時，線尾數值正好被線穿過）。改成從幾個候選位置挑分數最低的：
// 壓到線、壓到線尾數值＝重罰；壓到零軸＝中罰；引線穿過折線＝中罰；壓到格線＝輕罰（字另外描白邊，格線碰不到字）；
// 離原本的位置越遠、放在點的下方，各加一點分。只看資料與版面、init 一次算好，確定性。
function placeLineText(ctx, c, hiI, qh1, H1) {
  const { u, data: d } = ctx, { F, S, xs, yS } = L, n = S.length, fs = ctx.portrait ? 1.2 : 1;
  const pts = S.map((r, i) => [xs[i], yS(r.value)]);
  const right = F.x + F.w - 80 * u;                                     // 右邊那一欄是刻度值，不放字
  const grow = (R, m) => ({ x0: R.x0 - m, y0: R.y0 - m, x1: R.x1 + m, y1: R.y1 + m });
  const hitsSeg = (a, b, R) => { const k = Math.max(2, Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / (3 * u)));
    for (let j = 0; j <= k; j++) { const x = lerp(a[0], b[0], j / k), y = lerp(a[1], b[1], j / k); if (x >= R.x0 && x <= R.x1 && y >= R.y0 && y <= R.y1) return true; } return false; };
  const lineHits = R => { const G = grow(R, 8 * u); let k = 0; for (let i = 1; i < n; i++) if (hitsSeg(pts[i - 1], pts[i], G)) k++; return k; };
  const zeroY = L.zeroBased && L.ticks[0] < 0 ? yS(0) : null;           // 零軸（黑色粗線）在圖區中間時
  const zeroHit = R => zeroY != null && R.y0 - 12 * u < zeroY && R.y1 + 12 * u > zeroY;
  const gridHits = R => L.ticks.filter(v => zeroY == null || Math.abs(v) > 1e-9).filter(v => { const y = yS(v); return y > R.y0 - 2 * u && y < R.y1 + 2 * u; }).length;
  const overlap = (A, B) => !!(A && B && A.x0 < B.x1 && B.x0 < A.x1 && A.y0 < B.y1 && B.y0 < A.y1);
  // 線尾數值（36 號粗體、右對齊在最後一點的 x）：上方（原版）、下方、左邊
  c.font = `700 ${36 * u}px ${NUM}`;
  const lab = fmtv(d, S[n - 1].value), lw = [...lab].reduce((s, ch) => s + c.measureText(/[0-9]/.test(ch) ? '0' : ch).width, 0);
  const [ex, ey] = pts[n - 1], asc = 27 * u, desc = 6 * u;
  const endScore = k => (k.R.x0 < F.x || k.R.y0 < F.y - 30 * u || k.R.y1 > F.y + F.h ? 1e6 : 0) + 1000 * lineHits(k.R) + 400 * zeroHit(k.R) + 20 * gridHits(k.R) + k.pref;
  L.endLab = [{ x: ex, y: ey - 24 * u, pref: 0 }, { x: ex, y: ey + 24 * u + asc, pref: 6 }, { x: ex, y: ey - 60 * u, pref: 8 },
    { x: ex - 22 * u, y: ey + asc / 2, pref: 12 }, { x: ex, y: ey + 60 * u + asc, pref: 14 }]
    .map(k => ({ ...k, R: { x0: k.x - lw, x1: k.x, y0: k.y - asc, y1: k.y + desc } }))
    .reduce((b, k) => endScore(k) < endScore(b) ? k : b);
  // 標註（CH.callout：第一行 50 號粗體、第二行 28 號；字從 (tx±12, ty+8) 起、行距 40）
  if (hiI < 0 || !S[hiI]) return;
  const lines = [C(qh1 && qh1.text, H1.text), C(qh1 && qh1.sub, H1.sub)].filter(Boolean);
  c.font = `700 ${50 * u}px ${NUM}`; let tw = lines[0] ? c.measureText(lines[0]).width : 0;
  c.font = `400 ${28 * u * fs}px ${CN}`; if (lines[1]) tw = Math.max(tw, c.measureText(lines[1]).width);
  const [ax, ay] = pts[hiI], toRight = !(ax > F.x + F.w * 0.55), endR = hiI === n - 1 ? null : L.endLab.R;   // 標的就是最後一點時線尾數值會淡出讓位
  const crossings = (A, T) => { let k = 0; for (let i = 1; i < n; i++) if (i !== hiI && i !== hiI + 1 && segX(A, T, pts[i - 1], pts[i])) k++; return k; };
  const segDist = (p, a, b) => { const vx = b[0] - a[0], vy = b[1] - a[1], q = clamp(((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / (vx * vx + vy * vy || 1)); return Math.hypot(p[0] - a[0] - q * vx, p[1] - a[1] - q * vy); };
  const hugs = (A, T) => { const len = Math.hypot(T[0] - A[0], T[1] - A[1]); let k = 0;   // 引線離開圓點 24 之後還貼著折線走（看起來像線的一部分）
    for (let s = 24 * u; s < len; s += 6 * u) { const p = [lerp(A[0], T[0], s / len), lerp(A[1], T[1], s / len)]; for (let i = 1; i < n; i++) if (segDist(p, pts[i - 1], pts[i]) < 12 * u) { k++; break; } } return k; };
  let best = null;
  for (const dy of [-140, -190, -240, -100, 110, 160, 210]) for (const dx of [60, 120, 200, -60, -120, -200]) {
    const tx = ax + dx * u, ty = ay + dy * u, align = dx > 0 ? 'left' : 'right';
    const x0 = align === 'left' ? tx + 12 : tx - 12 - tw, R = { x0, x1: x0 + tw, y0: ty + 8 - 46 * u, y1: ty + 8 + (lines.length - 1) * 40 + 10 * u };
    if (R.x0 < F.x || R.x1 > right || R.y0 < F.y - 10 * u || R.y1 > F.y + F.h) continue;
    const s = 1000 * lineHits(R) + 1000 * overlap(grow(R, 6 * u), endR) + 400 * zeroHit(R) + 300 * crossings([ax, ay], [tx, ty]) + 40 * hugs([ax, ay], [tx, ty]) + 20 * gridHits(R)
      + 0.3 * (Math.abs(dy + 140) + Math.abs(dx - (toRight ? 60 : -60))) + (dy > 0 ? 40 : 0);
    if (!best || s < best.s) best = { s, tx, ty, align };
  }
  L.lineCo = best;                                                       // 圖太小、一個都放不下：照原版的位置
}
function segX(a, b, p, q) { const o = (r, s, v) => Math.sign((s[0] - r[0]) * (v[1] - r[1]) - (s[1] - r[1]) * (v[0] - r[0])); return o(a, b, p) !== o(a, b, q) && o(p, q, a) !== o(p, q, b); }
// [article-to-video 內嵌修改] 等寬數字（同 TY.tabular）＋白邊：先描一圈白、再填字，格線、面積底色碰不到字
function haloTabular(c, str, x, y, { align = 'left' } = {}, w = 8) {
  const chs = [...str], ws = chs.map(ch => c.measureText(/[0-9]/.test(ch) ? '0' : ch).width), total = ws.reduce((s, v) => s + v, 0);
  let cx = align === 'right' ? x - total : align === 'center' ? x - total / 2 : x; const pos = ws.map(wi => { const p = cx + wi / 2; cx += wi; return p; });
  const ta = c.textAlign; c.textAlign = 'center';
  c.save(); c.strokeStyle = '#FFFFFF'; c.lineWidth = w; c.lineJoin = 'round'; chs.forEach((ch, i) => c.strokeText(ch, pos[i], y)); c.restore();
  chs.forEach((ch, i) => c.fillText(ch, pos[i], y)); c.textAlign = ta; return total;
}
function haloText(c, s, x, y, w) { c.save(); c.strokeStyle = '#FFFFFF'; c.lineWidth = w; c.lineJoin = 'round'; c.strokeText(s, x, y); c.restore(); c.fillText(s, x, y); }
})();
