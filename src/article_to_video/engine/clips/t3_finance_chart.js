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
// [article-to-video 內嵌修改] 柱正上方、負數柱底下的標註：圓點（半徑 DOT_R ×u）放在引線靠柱子那一頭（數值標籤外側、柱的中線上），
// 引線從圓點拉到文字。原版圓點在柱角、引線從柱中間的數值標籤外拉出去，兩個沒接上，看起來像多出一個點（計時實測 R2-7）。
// 負數柱的文字塊離數值標籤至少 NEG_DOT（×u×字級倍數）：圓點＋露出的一截引線
const DOT_R = 9, NEG_DOT = 2 * DOT_R + 16;
// [article-to-video 內嵌修改] 柱正上方的標註：引線至少露出這麼長（×u×字級倍數），不會被柱上數值標籤蓋得只剩一小截
const LEADER_MIN = 40;
// [article-to-video 內嵌修改] 品牌色：data.colors.brand 取代「當品牌用的紅」（頂端色條、小旗、角標、標註圓點、大數字、折線預設色）；
// 沒寫就是原本的紅。K 線的漲跌紅綠是語意色，不跟品牌走。#RRGGBB／#RGB 換成 rgba（折線下方漸層、角標底色用）。
// [article-to-video 內嵌修改] 快推（data.camera，建置給的）：曲線＝Emil Kowalski 的強 ease-out cubic-bezier(0.23, 1, 0.32, 1)。
// 鏡頭是在回應「標註出現」這個事件：ease-out 一開始就動（觀眾眼睛正看著圓點彈出的那一刻），後段慢慢停穩；
// ease-in-out 前幾幀幾乎不動，看起來像鏡頭遲到了。時長由 spec 給（建置的 SNAP_DUR）。
const SNAP_EASE = MO.bezier(0.23, 1, 0.32, 1);
const CAM_SIDE = 12;                                                      // 快推推近後，字離畫面左右邊至少這麼多（×u）
// [article-to-video 內嵌修改] 柱寬：一格的 BAR_FILL（原版 0.62），最寬 BAR_MAX（×u；原版 150）。見 init 的 xs
const BAR_FILL = 0.62, BAR_MAX = 240;
// [article-to-video 內嵌修改] 大數字卡、兩數對比的動畫時間與顏色（說明在 cardInit 上面）
const ROLL = 0.9, ENTER = 0.45, STAGGER = 0.06, FLOAT = 18;
const CARD_DIM = '#98A4AA';                                               // 被比下去的那個數字與說明（還要讀得到，所以比淡掉的柱 #C6D2D8 深）
const unitScale = s => /[\u2E80-\u9FFF\uFF00-\uFFEF]/.test(s) ? 0.36 : 0.62;   // 中文單位小一點、符號大一點
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
    if (kind === 'number' || kind === 'compare') { cardInit(ctx, c, kind, mL, mR, tall); return; }   // [article-to-video 內嵌修改] 圖表版面：大數字卡、兩數對比（見 cardInit）
    const S = d.series || [];
    const vals = kind === 'candle' ? S.flatMap(r => [r.h, r.l]) : S.map(r => r.value);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    // [article-to-video 內嵌修改] data.yRange [最小, 最大]：縱軸照寫的畫（格線是範圍內的整刻度），不自動留白、不加刻度。建置已經查過它包住所有數字
    const yr = Array.isArray(d.yRange) && d.yRange.length === 2 && d.yRange[0] < d.yRange[1] ? d.yRange : null;
    let zeroBased = yr ? yr[0] <= 0 && yr[1] >= 0 : kind === 'bar' || lo <= 0 || (hi - lo) / hi > 0.6;
    if (zeroBased && !yr) lo = Math.min(0, lo);
    const pad = (hi - lo) * 0.08, ticks = yr ? CH.ticks(yr[0], yr[1], tall ? 5 : 4) : CH.ticks(zeroBased ? lo : lo - pad, hi + pad, tall ? 5 : 4);
    if (yr && ticks.length < 2) ticks.splice(0, ticks.length, ...yr);
    const step = ticks[1] - ticks[0];                                     // 刻度要把数据整个包住（CH.ticks 只给区间内的整刻度）
    while (!yr && ticks[ticks.length - 1] < hi + pad * 0.5) ticks.push(+(ticks[ticks.length - 1] + step).toFixed(10));
    while (!yr && ticks[0] > (zeroBased ? lo : lo - pad * 0.5)) ticks.unshift(+(ticks[0] - step).toFixed(10));
    const dom = () => yr || [ticks[0], ticks[ticks.length - 1]];          // 縱軸畫的範圍：自己寫的 yRange，或第一格到最後一格刻度
    const titleSz = (ctx.portrait ? 64 : 58) * u, t = TY.fit(c, d.title || '', W - mL - mR, titleSz, 'PuHui-Bold', { maxLines: tall ? 3 : 2, weight: '700 ' });
    const sub = (d.unit || '') + (!zeroBased ? (d.unit ? '　' : '') + (d.axisNote || '纵轴未从 0 开始') : '');
    // 副标题、来源行：放不下先缩一点，再折两行（竖屏长口径会出画）；来源行要让开右下角标
    const fsz = ctx.portrait ? 1.2 : 1, subF = TY.fit(c, sub, W - mL - mR, 32 * u * fsz, 'PuHui-Medium', { maxLines: 2, min: 0.8 });
    c.font = `700 ${28 * u}px "PuHui-Medium"`; const badgeW = d.badge ? c.measureText(d.badge).width + 28 + 30 * u : 0;
    const srcF = TY.fit(c, d.source || '', W - mL - mR - badgeW, 26 * u * fsz, 'PuHui-Medium', { maxLines: 2, min: 0.8 });
    const top = ctx.safe.top + 80 * u + t.lines.length * t.size * 1.25 + 60 * u + (subF.lines.length - 1) * subF.size * 1.3;
    // [article-to-video 內嵌修改] 一串接續分鏡的版面一次算好：data.chainLayout（建置給的）列出整串每個分鏡的標註、整串有沒有大數字，
    // 留白照整串的需要留，每個分鏡的刻度、圖區都一樣（原版只看自己這個分鏡的第一個標註，標到負數柱時底下多一格、切點刻度跳）
    // chainLayout 的標註：{index, lines（幾行）}，折線另外帶 text、sub（擺位置要量字寬）
    const CL = d.chainLayout || {}, hls = [...ctx.of('highlight').map(q => hlOf(q, d, S.length)),
      ...(CL.highlights || []).map(h => ({ index: h.index != null ? h.index : S.length - 1, lines: h.text || h.sub ? [h.text, h.sub].filter(Boolean) : Array(h.lines || 0).fill('') }))];
    const band = ctx.of('number').length || CL.number ? 150 * u : 0;     // 有 number：给大数字单独留一条，不压在图区里（折线一上涨，右上角正好是线头）
    const F = { x: mL + 20 * u, y: top + 40 * u + band, w: W - mL - mR - 40 * u, h: H - top - 40 * u - band - (tall ? 260 : 190) * u - ctx.safe.bottom - (srcF.lines.length - 1) * srcF.size * 1.3 };   // safe.bottom：字幕带整条让出来，来源行落在它上面
    // 右边留 80px 给刻度值，别压在最后一根柱上。[article-to-video 內嵌修改] 柱寬隨根數：柱寬＝一格的 BAR_FILL（原版 0.62），最寬 BAR_MAX；
    // 根數少、柱寬碰到上限時，格子跟著縮成「柱寬 ÷ BAR_FILL」（柱與柱的間距跟根數多時同一個比例），整組柱放在圖區正中間。
    // 原版上限 150 而格子照樣撐滿圖區：兩三根柱時柱子細、彼此離很遠、圖區空一大片
    const n = S.length, area = F.w - 80 * u, bw = kind === 'bar' ? Math.min(BAR_MAX * u, area / n * BAR_FILL) : 0;
    const slot = kind === 'bar' ? Math.min(area / n, bw / BAR_FILL) : area / n, x0 = F.x + (area - slot * n) / 2;
    const xs = S.map((_, i) => x0 + slot * (i + 0.5));
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
    // [article-to-video 內嵌修改] 柱狀圖上下要留的白（下面三項）一起算到都夠：原版照順序各算一次，後面往下加的刻度把前面在上面留的白壓扁
    // （同時標正數柱、負數柱的一串：標最高那根的斜向標註被夾在圖區上緣，壓到柱上的數值）。只有一項時跟原版一樣
    const span = () => ticks[ticks.length - 1] - ticks[0], rooms = [];
    const below = need => { let k = false; while ((Math.min(...vals) - ticks[0]) / span() * F.h < need) { ticks.unshift(+(ticks[0] - step).toFixed(10)); k = true; } return k; };
    const above = need => { let k = false; while ((ticks[ticks.length - 1] - Math.max(...vals)) / span() * F.h < need) { ticks.push(+(ticks[ticks.length - 1] + step).toFixed(10)); k = true; } return k; };
    if (!yr && kind === 'bar' && Math.min(...vals) < 0) {                // 负数柱的数值写在柱底下方：最低那根下面留出一行，不然压到类目名
      const need = 56 * u * fs0; roomFor(need); rooms.push(() => below(need));
    }
    const posL = hls.filter(h => S[h.index] && S[h.index].value >= 0), negL = hls.filter(h => S[h.index] && S[h.index].value < 0);   // [article-to-video 內嵌修改] 整串的標註（見上面 chainLayout）
    if (!yr && kind === 'bar' && posL.length) {   // 标注的柱子上方要留够文字块（[Danny 本机改] 原本只管「不是最高那根」；标最高那根时斜向标注被 ty 夹在 F.y+60，文字压回柱子上）
      const nL = Math.max(...posL.map(h => h.lines.length)), need = (52 * nL + 44 + 30) * u * fs0; roomFor(need); rooms.push(() => above(need));
    }
    if (!yr && kind === 'bar' && negL.length) {    // [article-to-video 內嵌修改] 負數柱的標註放在數值標籤正下方：最低那根柱下面再留出標註文字的高度
      const nL = Math.max(...negL.map(h => h.lines.length));
      const need = (NEG_LABEL + NEG_DOT + NEG_LINE1 + NEG_LINE2 * (nL - 1) + 12) * u * fs0; roomFor(need); rooms.push(() => below(need));
    }
    for (let k = 0; k < 20 && rooms.map(f => f()).some(Boolean); k++);
    if (!yr && kind === 'line' && hls.length) {                           // [Danny 本机改] 折线标注：线的最高点之上留够文字块，斜向标注才不会压在线上
      // [article-to-video 內嵌修改] 先不留白，每個標註（含整串的）都放得到不壓線、不壓線尾數值、不壓零軸、引線不穿過折線的位置就好；
      // 放不下才往上加一格刻度再試，最多加到原版的「最高點上方 170」。原版一律留 170：圖區被大數字那一條吃掉時，縱軸被撐到資料的兩三倍高（−0.93～1.5 畫成 −1～4）
      const vmax = Math.max(...vals), need = 170 * u * fs0;
      for (;;) {
        L = { S, F, xs, slot, zeroBased, ticks, yS: CH.lin(...dom(), F.y + F.h, F.y) }; placeLineText(ctx, c);
        if (hls.filter(h => S[h.index]).every(h => { const co = placeLineLabel(ctx, c, h); return co && co.clean; })) break;
        if ((ticks[ticks.length - 1] - vmax) / (ticks[ticks.length - 1] - ticks[0]) * F.h >= need) break;
        roomFor(need); ticks.push(+(ticks[ticks.length - 1] + step).toFixed(10));
      }
    }
    L = { subF, srcF, m: mL, mR, kind, S, zeroBased, ticks, tdec, yS: CH.lin(...dom(), F.y + F.h, F.y), F, xs, slot, bw, t, sub, band, category, xFont: xf * xScale, stagger,
      every: category ? 1 : Math.max(1, Math.ceil(n / (ctx.portrait ? 6 : 10))) };
    const brand = (d.colors && d.colors.brand) || RED;                    // [article-to-video 內嵌修改] 品牌色
    const main = (d.colors && d.colors.main) || (kind === 'bar' ? '#006BA2' : brand);
    const ink = (d.colors && d.colors.ink) || brand;                      // [article-to-video 內嵌修改] 品牌色太淺時，要讀的字與記號用加深的 ink（色條、小旗照用原色）
    L.col = { main, accent: (d.colors && d.colors.accent) || main, dim: (d.colors && d.colors.dim) || '#C6D2D8', brand, ink };
    L.updown = d.upDown === 'us' ? ['#1B9E5A', RED] : [RED, '#1B9E5A'];
    if (!category) {   // [article-to-video 內嵌] 時間軸的名字互相重疊就不寫：直式畫面窄、字又放大兩成，原版的抽稀間隔常讓相鄰名字疊在一起。照原本的規則（含 xTicks）從左到右、放得下才寫；沒有重疊時跟原版一樣
      c.font = `400 ${L.xFont}px ${NUM}`;
      const half = i => c.measureText(String(S[i].label ?? '')).width / 2;
      const base = i => d.xTicks ? d.xTicks.includes(i) : i % L.every === 0 || (i === n - 1 && (n - 1) % L.every >= L.every / 2);
      // [article-to-video 內嵌修改] 被標註的那一點不再硬塞名字：換標註（接續分鏡）時 x 軸名字會跟著變，硬塞的還會擠掉旁邊原本的名字。標註的字自己寫日期
      const kept = [];
      for (let i = 0; i < n; i++) if (base(i) && kept.every(j => Math.abs(xs[i] - xs[j]) >= half(i) + half(j) + 16 * u)) kept.push(i);
      L.xKeep = new Set(kept);
    }
    c.font = `400 ${28 * u * fs0}px ${NUM}`;                               // [article-to-video 內嵌修改] 右側刻度值最寬到哪：線頭跟著跑的數值不能超過這裡
    L.axisX0 = F.x + F.w - Math.max(...ticks.map(v => c.measureText(TY.fmt(v, tdec)).width)) - 10 * u;
    // [article-to-video 內嵌修改] 標註照時間一個換一個（接續分鏡：建置把上一個分鏡最後的標註沿用過來、時間寫成負的，第一幀就在）。
    // 跟前一個一模一樣（同一點、同樣的字）的標註不重來一次：start 沿用前一個的。位置（折線挑的位置、柱的三種擺法）每個標註 init 算好
    L.hl = ctx.of('highlight').map(q => hlOf(q, d, n)).filter(h => S[h.index]);
    L.hl.forEach((h, j) => { const p = L.hl[j - 1]; h.start = p && p.index === h.index && p.lines.join('\n') === h.lines.join('\n') ? p.start : h.q.at; });
    if (kind === 'line') { placeLineText(ctx, c); L.hl.forEach(h => { h.co = placeLineLabel(ctx, c, h); }); }
    if (kind === 'bar') L.hl.forEach(h => { h.mode = barMode(ctx, c, h); });
    L.cam = d.camera ? cameraKeys(ctx, c, d.camera) : null;
  },
  draw(c0, t, ctx) {
    // alpha：图是白纸黑字，叠在深色画面上墨色看不见。先画进缓冲，再带一圈白色光晕合成（和 t1 同一招）
    const c = ctx.alpha ? (BUF = BUF || document.createElement('canvas'), BUF.width = ctx.W, BUF.height = ctx.H, BUF.getContext('2d')) : c0;
    const { W, H, u, data: d } = ctx, { m, F, yS, xs, S, kind } = L, fs = ctx.portrait ? 1.2 : 1;   // 竖屏在手机上看：小字放大两成
    const qt = ctx.of('title')[0] || { at: 0 }, lt = ctx.lt(t, qt.at);
    if (!ctx.alpha) { c.fillStyle = '#FFFFFF'; c.fillRect(0, 0, W, H); }
    c.save();
    if (L.cam) { const [s, tx, ty] = camAt(ctx, t); c.translate(tx, ty); c.scale(s, s); }   // [article-to-video 內嵌修改] 快推：停住，標註出現時推一下
    else if (d.push) { const s = 1 + (d.pushFrom || 0) + d.push * clamp(t / ctx.dur); c.translate(W / 2, H / 2); c.scale(s, s); c.translate(-W / 2, -H / 2); }   // [Danny 本机改] data.push：整镜匀速矢量慢推（语法卡 ③「图表镜头匀速慢推 1→1.03」），默认 0 不推
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
    if (L.card) { drawCard(c, t, ctx); c.restore(); glow(c0, ctx); return; }   // [article-to-video 內嵌修改] 大數字卡、兩數對比：版式一樣，沒有坐標系
    // ---- 坐标系（版式之后 0.25s） ----
    const qd = ctx.of('bar', 'line', 'candle')[0] || { at: qt.at + 0.75 }, gStart = Math.min(qt.at + 0.25, qd.at - 0.4);
    const gp = MO.seg(ctx.lt(t, gStart), 0, 0.6);
    const fmtTick = v => TY.fmt(v, L.tdec);
    CH.grid(c, F, L.ticks, yS, gp, { col: GRID, zeroCol: L.zeroBased ? INK : GRID, font: `400 ${28 * u * fs}px ${NUM}`, labelCol: '#5b666b', side: 'right', lw: 1.5 * u, zeroLw: 3 * u, fmt: fmtTick, labelDy: -10 * u });
    // [article-to-video 內嵌修改] 原版「被标注的那根永远有名字」拿掉（類目軸本來每根都有名字；寫了 xTicks 就只寫 xTicks），換標註時 x 軸不跳
    const keep = L.xKeep ? (i => L.xKeep.has(i)) : i => d.xTicks ? d.xTicks.includes(i) : i % L.every === 0 ||   // [Danny 本机改] data.xTicks：自己指定要写名字的索引（时间轴按月/按年对齐）
      (i === S.length - 1 && (S.length - 1) % L.every >= L.every / 2);   // 末尾标签离上一个太近就不硬塞
    const xp = MO.seg(ctx.lt(t, gStart + 0.15), 0, 0.5), xy = F.y + F.h + 46 * u, xo = { font: `400 ${L.xFont}px ${NUM}`, col: '#3a4246' };
    if (!L.stagger) CH.xLabels(c, S.map((r, i) => ({ x: xs[i], label: keep(i) ? String(r.label ?? '') : '' })), xy, xp, xo);
    else { CH.xLabels(c, S.map((r, i) => ({ x: xs[i], label: i % 2 ? '' : String(r.label ?? '') })), xy, xp, xo); CH.xLabels(c, S.map((r, i) => ({ x: xs[i], label: i % 2 ? String(r.label ?? '') : '' })), xy + L.xFont * 1.15, xp, xo); }
    // ---- 数据 ----
    const n = S.length, ddur = qd.dur || Math.min(2.2, Math.max(0.9, n * (kind === 'bar' ? 0.22 : 0.06)));
    const dp = ctx.p(t, qd.at, ddur);
    // [article-to-video 內嵌修改] 標註一個換一個（init 的 L.hl）：現在是哪一個（cur）、淡掉的程度（dimA）、線尾數值的透明度（endA）
    // 都照順序疊上去，換標註時只有變了的東西在動（原版只認第一個 highlight cue；接續分鏡的切點標註消失、淡掉的柱閃回原色、線尾數值跳出來）
    const hs = L.hl, dimA = S.map(() => 0);
    let curI = -1, endA = 1;
    for (const h of hs) {
      const k = ctx.lt(t, h.start); if (k <= 0) continue;
      const w = MO.smooth(clamp(k / 0.4));
      for (let i = 0; i < n; i++) dimA[i] = lerp(dimA[i], i === h.index ? 0 : 1, w);
      endA = lerp(endA, h.index === n - 1 ? 0 : 1, clamp(k / 0.25));   // [Danny 本机改] 标注就是最后一点时，线头数值让位，免得两个数字叠在一起
      curI = h.index;
    }
    let tops = null, pts = null;
    if (kind === 'bar') {
      const bw = L.bw;
      const opt = { dimCol: L.col.dim, fmt: v => fmtv(d, v), font: `600 ${Math.max(24 * u * fs, Math.min(34 * u * fs, L.slot * 0.3))}px ${NUM}`, labelCol: INK, label: n <= 14 };
      const own = {};                                                      // 带 data.index 的 bar cue：这几根柱子从它的 at 起单独长
      for (const q of ctx.of('bar')) if (q.data && q.data.index != null) [].concat(q.data.index).forEach(i => { own[i] = q; });
      // [article-to-video 內嵌修改] 沒有自己時刻的柱跟著「沒帶 index 的 bar cue」長（原版跟著時間最早的資料 cue：先寫一根帶 index 的、
      // 後面再寫一個沒帶 index 的「其他一起長」，後面那個沒作用、其他柱跟第一根一起出來）。沒有沒帶 index 的 bar cue 時照原版
      const qb = ctx.of('bar').find(q => !(q.data && q.data.index != null)), bp = qb ? ctx.p(t, qb.at, qb.dur || ddur) : dp;
      const perBar = Object.keys(own).length > 0 || S.some(r => r.color);   // 原版「逐根長出」的路徑（series[i].text 只在這條路徑生效）
      // [article-to-video 內嵌修改] data.barsLater（建置給的）：這一串後面的分鏡才長的柱（bar cue 寫在接續分鏡），這個片段不畫
      const later = new Set(d.barsLater || []);
      // [article-to-video 內嵌修改] data.barsNoRoll（建置給的）：柱上數字不從 0 往上滾、長出來時就寫最後的數（比左邊那根低的正數柱：
      // 「降到 3.8」配往上數會打架，滾動中又是一串假數字；規則見 timeline._bars_no_roll）。柱照樣從 0 長、數字照樣淡入
      const noRoll = new Set(d.barsNoRoll || []);
      // [article-to-video 內嵌修改] 一律逐根畫（跟原版整排一起畫的錯開時間一樣：MO.lagged），每根柱自己的淡掉程度 dimA[i]
      tops = S.map((r, i) => {
        const q = own[i], p = later.has(i) ? 0 : q ? ctx.p(t, q.at, q.dur || 0.7) : MO.lagged(i, n, bp, Math.min(0.22, 2 / n));
        const base = r.color || L.col.main, col = i === curI ? (perBar ? base : L.col.accent) : base;
        const dim = r.color ? 0 : dimA[i];   // [article-to-video 內嵌修改] 自己寫了 color 的柱不淡掉：顏色是作者指定的（跟片裡別處同一個量的配色對上），不是強調；原版標註一出現就跟著淡成灰
        return CH.bars(c, [{ x: xs[i], v: r.value }], yS, bw, p, { ...opt, ...(noRoll.has(i) ? { fmt: () => fmtv(d, r.value) } : {}), ...(perBar && r.text != null ? { fmt: () => r.text } : {}), lag: 0, col, hi: dim > 0 ? 1 : -1, hiP: dim })[0];   // [Danny 本机改] series 项可写 text 覆盖柱上数值（如「＞50%」；只在逐根长出的路径生效：有 data.index 的 bar cue 或 color）
      });
    } else if (kind === 'line') {
      pts = S.map((r, i) => [xs[i], yS(r.value)]);
      const lp = MO.smooth(dp);
      const hp = CH.line(c, pts, lp, { col: L.col.main, lw: 5 * u, headR: 9 * u, area: { top: F.y, base: F.y + F.h, c0: rgba(L.col.main, 0.12, 'rgba(227,18,11,0.12)'), c1: rgba(L.col.main, 0, 'rgba(227,18,11,0)') } });
      if (hp && lp < 1) {   // [article-to-video 內嵌修改] 描白邊；快到最後一點時滑到線尾數值的位置（原版一直寫在線頭右邊，最後幾幀壓到右側刻度值，長完再跳到線尾數值的位置）
        const v = yS.inv(hp[1]), txt = fmtv(d, v), [ex, ey] = pts[n - 1]; c.save(); c.font = `700 ${36 * u}px ${NUM}`; c.fillStyle = L.col.main; c.textAlign = 'left';
        c.textBaseline = 'middle'; const am = c.measureText('0').actualBoundingBoxAscent; c.textBaseline = 'alphabetic'; const aa = c.measureText('0').actualBoundingBoxAscent;
        const tw = [...txt].reduce((s, ch) => s + c.measureText(/[0-9]/.test(ch) ? '0' : ch).width, 0);
        const R = Math.max(tw + 40 * u, ex + 20 * u + tw - L.axisX0 + 10 * u), w = clamp((hp[0] - (ex - R)) / R);   // 線頭離最後一點 R 以內開始滑
        const x = Math.min(lerp(hp[0] + 20 * u, hp[0] + L.endLab.x - ex - tw, w), L.axisX0 - tw), y = lerp(hp[1] - 4 * u + aa - am, hp[1] + L.endLab.y - ey, w);
        haloTabular(c, txt, x, y, {}, 8 * u); c.restore(); }
      else if (lp >= 1 && endA > 0) { const v = S[n - 1].value; c.save(); c.globalAlpha = endA;
        c.font = `700 ${36 * u}px ${NUM}`; c.fillStyle = L.col.main; c.textAlign = 'right'; c.textBaseline = 'alphabetic'; haloTabular(c, fmtv(d, v), L.endLab.x, L.endLab.y, { align: 'right' }, 8 * u); c.restore(); }   // [article-to-video 內嵌修改] 位置挑不被線穿過的（placeLineText）、描白邊
    } else {
      const cw = Math.min(30 * u, L.slot * 0.6);
      CH.candles(c, S, xs, yS, cw, dp, { up: L.updown[0], down: L.updown[1], lag: Math.min(0.06, 1.5 / n) });
    }
    // ---- 标注：一镜只标一件事 ----
    // [article-to-video 內嵌修改] 接續分鏡：上一個分鏡的標註（沿用過來）一直留著，到這個分鏡自己的標註才換。
    // 同一點：圓點不動、舊字淡出、新的引線和字長出來；換到別點：舊的整個淡出（0.25 秒）、新的圓點彈出來。一模一樣的標註不重來
    let ci = -1; hs.forEach((h, j) => { if (ctx.lt(t, h.start) > 0) ci = j; });
    if (ci >= 0) {
      const cur = hs[ci], kc = ctx.lt(t, cur.start), anchorOf = i => kind === 'bar' ? [xs[i], tops[i][1]] : kind === 'line' ? pts[i] : [xs[i], yS(S[i].c)];
      let pj = ci - 1; while (pj >= 0 && hs[pj].start === cur.start) pj--;
      const prev = pj >= 0 ? hs[pj] : null, same = !!prev && prev.index === cur.index;
      if (prev) { const out = 1 - MO.seg(kc, 0, 0.25); if (out > 0) drawLabel(c, ctx, prev, ctx.lt(t, prev.start), out, same ? 'none' : 'spring', anchorOf(prev.index), tops); }
      drawLabel(c, ctx, cur, kc, 1, same ? 'full' : 'spring', anchorOf(cur.index), tops);
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
    glow(c0, ctx);
  },
};
function glow(c0, ctx) {
  if (ctx.alpha) { c0.save(); c0.shadowColor = '#FFFFFF'; for (const b of [8, 3, 1.5]) { c0.shadowBlur = b * ctx.u; c0.drawImage(BUF, 0, 0); } c0.restore(); }   // 三遍由宽到窄：外圈柔光＋贴字的实边
}
function C(...a) { return a.find(v => v != null && v !== ''); }
// [article-to-video 內嵌修改] 圖表版面：大數字卡（chart: 'number'）、兩數對比（chart: 'compare'）。版式（色條、小旗、標題、副標、
// 來源行、角標、白邊描字、字型、品牌色）跟柱狀圖、折線一樣，只是內容區不畫坐標系，改放大數字。
// 內容區＝副標下面到來源行上面；數字一律靠左對齊標題（經濟學人式版面從左邊界往右排），整塊在內容區裡垂直置中（偏上一點）。
// 單位（prefix、suffix）比數字小：中文單位（「天」「個百分點」）0.36 倍、符號（%、$）0.62 倍，基線對齊。
// 動畫（理由見 NOTICE.md）：
// - 大數字卡的數字照原版大數字（number cue）的滾法：at 前 0.9 秒開始從 0 滾、expoOut（強 ease-out：先快後慢、最後一位落定）、
//   前 0.2 秒淡入；等寬數字，右緣固定（位數變多時往左長，單位不跟著跑）。說明晚 STAGGER 秒、淡入＋往上浮 ENTER 秒。
// - 兩數對比的數字不滾：淡入＋往上浮 FLOAT，ENTER 秒、quintOut（強 ease-out），at 那一刻落定。比較要讀的是兩個確切的數，
//   從 0 滾上去會經過一串假的數字，也會把「從 4.58 降到 3.81」演成往上數。
// - 強調（highlight）：0.4 秒（smooth）另一個數字和它的說明淡成灰、被強調的數字換成品牌色；差距（gap）在 at 落定：
//   橫線從左畫出、字淡入上浮。
// 一個數字（前綴＋數字＋後綴）在 sz 號字的排法：各段寬度、數字的字高（量 '0' 的上緣）
function numParts(c, pre, num, suf, sz) {
  c.save(); c.font = `700 ${sz}px ${NUM}`; const dw = c.measureText('0').width, capH = c.measureText('0').actualBoundingBoxAscent;
  const numW = [...num].reduce((s, ch) => s + (/[0-9]/.test(ch) ? dw : c.measureText(ch).width), 0);
  const uf = s => `700 ${sz * unitScale(s)}px ${NUM}`;
  c.font = uf(pre); const preW = pre ? c.measureText(pre).width + sz * 0.04 : 0;
  c.font = uf(suf); const sufW = suf ? c.measureText(suf).width + sz * 0.02 : 0;
  // 字的墨從哪裡開始（光學對齊）：等寬數字的「1」在格子正中，左邊空一截；整個數字往左移這麼多，墨才對齊標題的左邊界
  let lead = 0;
  if (pre) { c.font = uf(pre); c.textAlign = 'left'; lead = -c.measureText(pre).actualBoundingBoxLeft; }
  else if (num) { c.font = `700 ${sz}px ${NUM}`; c.textAlign = 'center'; const ch = [...num][0], m = c.measureText(ch);
    lead = /[0-9]/.test(ch) ? dw / 2 - m.actualBoundingBoxLeft : m.width / 2 - m.actualBoundingBoxLeft; }
  c.restore();
  return { sz, dw, capH, preW, numW, sufW, lead, w: preW + numW + sufW - lead, uf };
}
// 畫一個數字：x 是左緣、by 是基線；str 是現在要寫的數字（滾動中位數會變少），右緣固定在 x + preW + numW
function drawNum(c, P, pre, str, suf, x, by) {
  c.textBaseline = 'alphabetic'; x -= P.lead;
  if (pre) { c.font = P.uf(pre); c.textAlign = 'left'; c.fillText(pre, x, by); }
  c.font = `700 ${P.sz}px ${NUM}`; TY.tabular(c, str, x + P.preW + P.numW, by, { align: 'right', digitW: P.dw });
  if (suf) { c.font = P.uf(suf); c.textAlign = 'left'; c.fillText(suf, x + P.preW + P.numW + P.sz * 0.02, by); }
}
function cardInit(ctx, c, kind, mL, mR, tall) {
  const { W, H, u, data: d } = ctx, fsz = ctx.portrait ? 1.2 : 1, cw = W - mL - mR;
  const t = TY.fit(c, d.title || '', cw, (ctx.portrait ? 64 : 58) * u, 'PuHui-Bold', { maxLines: tall ? 3 : 2, weight: '700 ' });
  const subF = TY.fit(c, d.unit || '', cw, 32 * u * fsz, 'PuHui-Medium', { maxLines: 2, min: 0.8 });
  c.font = `700 ${28 * u}px "PuHui-Medium"`; const badgeW = d.badge ? c.measureText(d.badge).width + 28 + 30 * u : 0;
  const srcF = TY.fit(c, d.source || '', cw - badgeW, 26 * u * fsz, 'PuHui-Medium', { maxLines: 2, min: 0.8 });
  const top = ctx.safe.top + 80 * u + t.lines.length * t.size * 1.25 + 60 * u + (subF.lines.length - 1) * subF.size * 1.3;
  const y0 = top + 40 * u, y1 = H - 40 * u - ctx.safe.bottom - (srcF.lines.length - 1) * srcF.size * 1.3 - srcF.size - 60 * u;   // 內容區：副標下面到來源行上面
  const brand = (d.colors && d.colors.brand) || RED, ink = (d.colors && d.colors.ink) || brand;
  L = { card: kind, t, subF, srcF, m: mL, mR, S: d.series || [], col: { main: brand, accent: brand, dim: CARD_DIM, brand, ink } };
  if (kind === 'number') {
    const q = ctx.of('number')[0], nd = (q && q.data) || {}, dec = nd.decimals ?? 0, pre = nd.prefix || '', suf = nd.suffix || '';
    let P = numParts(c, pre, TY.fmt(nd.value || 0, dec), suf, (ctx.portrait ? 250 : 300) * u);
    if (P.w > cw) P = numParts(c, pre, TY.fmt(nd.value || 0, dec), suf, P.sz * cw / P.w);
    const capF = TY.fit(c, (q && (q.text ?? q.sub)) || '', cw, 44 * u * fsz, 'PuHui-Medium', { maxLines: 2, min: 0.7 });
    const gap = 0.3 * P.capH, blockH = P.capH + gap + capF.lines.length * capF.size * 1.3;
    if (blockH > y1 - y0) throw new Error(`[article-to-video 內嵌] 大數字卡放不下：標題、副標、來源行之間只剩 ${Math.round(y1 - y0)}px，數字和說明要 ${Math.round(blockH)}px。請縮短標題或說明`);
    const by = y0 + (y1 - y0 - blockH) * 0.45 + P.capH;
    L.num = { q, nd, dec, pre, suf, P, capF, x: mL, by, capY: by + gap + capF.size };
  } else compareInit(ctx, c, cw, y0, y1);
  L.cam = d.camera ? cameraKeys(ctx, c, d.camera) : null;
}
// 兩數對比：兩格（16:9 左右並排、9:16 上下疊），每格一個數字＋說明（series[i].label），兩個數字同一個字級（比較才公平）。
// 差距（gap cue）放在兩格下面：先一條細橫線（像直式算術的那條線：上面是兩個數，下面是結果），再一行「動詞 數字 單位」。
// 整串（接續分鏡）裡有差距就先留好位置（chainLayout.gap），切點時兩個數字不會跳。放不下就整組縮小，字級最小到原本的一半。
function compareInit(ctx, c, cw, y0, y1) {
  const { W, u, data: d } = ctx, fsz = ctx.portrait ? 1.2 : 1, side = !ctx.portrait, S = L.S, mL = L.m;
  const dec = d.decimals ?? 1, pre = d.prefix || '', suf = d.suffix || '', strs = S.map(r => TY.fmt(r.value, dec));
  const gutter = 120 * u, cellW = side ? (cw - gutter) / 2 : cw;
  const caps0 = S.map(r => TY.fit(c, String(r.label ?? ''), cellW, 40 * u * fsz, 'PuHui-Medium', { maxLines: 2, min: 0.75 }));
  const capSz = Math.min(...caps0.map(f => f.size)), caps = S.map(r => TY.fit(c, String(r.label ?? ''), cellW, capSz, 'PuHui-Medium', { maxLines: 2, min: 1 }));
  const capLines = Math.max(...caps.map(f => f.lines.length));
  const gq = ctx.of('gap')[0], hasGap = !!gq || !!(d.chainLayout && d.chainLayout.gap);
  const gd = (gq && gq.data) || {}, gsuf = gd.suffix || '', gpre = gd.prefix || '', verb = gq ? C(gq.text, '差') : '差';
  const r = v => Math.sign(v) * +Math.abs(v).toFixed(dec);              // 畫面上寫的數（四捨五入到 decimals）：差距照畫面上的兩個數算，觀眾自己減得出來
  const gStr = S.length === 2 ? TY.fmt(Math.abs(r(S[1].value) - r(S[0].value)), dec) : '';
  const gWord = 44 * u * fsz;
  let sz = (side ? 210 : 180) * u, lay;
  for (let k = 0; k < 12; k++) {
    let Ps = strs.map(s => numParts(c, pre, s, suf, sz));
    const wMax = Math.max(...Ps.map(P => P.w));
    if (wMax > cellW) { sz *= cellW / wMax; Ps = strs.map(s => numParts(c, pre, s, suf, sz)); }
    const capH = Ps[0].capH, gapNC = 0.3 * capH, cellH = capH + gapNC + capLines * capSz * 1.3, rowGap = 0.45 * capH;
    const G = numParts(c, gpre, gStr, gsuf, Math.max(60 * u * fsz, 0.4 * sz)), ruleGap = 0.32 * capH;
    const gapH = hasGap ? ruleGap + 2 * u + 0.28 * capH + G.capH : 0;
    const blockH = (side ? cellH : 2 * cellH + rowGap) + gapH;
    lay = { Ps, capH, gapNC, cellH, rowGap, G, ruleGap, blockH };
    if (blockH <= y1 - y0 || k === 11) break;
    sz *= 0.92;
  }
  if (lay.blockH > y1 - y0) throw new Error(`[article-to-video 內嵌] 兩數對比放不下：標題、副標、來源行之間只剩 ${Math.round(y1 - y0)}px，兩個數字和說明要 ${Math.round(lay.blockH)}px。請縮短標題或說明`);
  const top = y0 + (y1 - y0 - lay.blockH) * 0.45, { Ps, capH, gapNC, cellH, rowGap, G, ruleGap } = lay;
  const cells = S.map((_, i) => { const x = side ? mL + i * (cellW + gutter) : mL, by = top + capH + (side ? 0 : i * (cellH + rowGap));
    c.save(); c.font = `500 ${capSz}px ${CN}`; const capW = Math.max(...caps[i].lines.map(l => c.measureText(l).width)); c.restore();
    return { x, by, P: Ps[i], str: strs[i], cap: caps[i], capY: by + gapNC + capSz, right: x + Math.max(Ps[i].w, capW) }; });
  const ruleY = top + (side ? cellH : 2 * cellH + rowGap) + ruleGap;
  // 每個數字什麼時候落定：number cue 的 data.index（一個或清單）；同一個數字寫兩次以第一次為準
  const at = S.map(() => null);
  for (const q of ctx.of('number')) [].concat((q.data || {}).index ?? []).forEach(i => { if (at[i] == null) at[i] = q.at; });
  L.cmp = { cells, at, pre, suf, capSz, hl: ctx.of('highlight').map(q => hlOf(q, d, S.length)).filter(h => S[h.index]),
    gap: gq && { at: gq.at, verb, pre: gpre, str: gStr, suf: gsuf, G, x: mL, x1: Math.max(...cells.map(e => e.right)), ruleY, by: ruleY + 2 * u + 0.28 * capH + G.capH, word: gWord } };
  L.focus = i => { const e = cells[i]; return [e.x + e.P.w / 2, e.by - capH / 2]; };   // 快推時不動的點：被強調的那個數字中間
}
function drawCard(c, t, ctx) {
  const { u } = ctx;
  if (L.card === 'number' && L.num.q) {
    const { q, nd, dec, pre, suf, P, capF, x, by, capY } = L.num, s = t - (q.at - ROLL);
    if (s < 0) return;
    c.save(); c.globalAlpha = clamp(s / 0.2); c.fillStyle = L.col.ink;
    drawNum(c, P, pre, TY.fmt((nd.value || 0) * MO.expoOut(clamp(s / ROLL)), dec), suf, x, by);
    const cp = MO.quintOut(clamp((s - STAGGER) / ENTER));
    if (cp > 0) { c.globalAlpha = cp; c.fillStyle = INK; c.textAlign = 'left'; c.font = `500 ${capF.size}px ${CN}`;
      capF.lines.forEach((ln, k) => c.fillText(ln, x, capY + k * capF.size * 1.3 + (1 - cp) * 12 * u)); }
    c.restore();
  } else if (L.card === 'compare') drawCompare(c, t, ctx);
}
// 字的顏色：base 往 to 混 k（強調：黑 → 品牌色），再往 CARD_DIM 混 dim（被比下去：淡成灰）
function tone(base, to, k, dim) {
  const A = PAINT.hex(base), B = PAINT.hex(to), D = PAINT.hex(CARD_DIM);
  return `rgb(${[0, 1, 2].map(j => Math.round(lerp(lerp(A[j], B[j], k), D[j], dim))).join(',')})`;
}
function drawCompare(c, t, ctx) {
  const { u } = ctx, { cells, at, pre, suf, capSz, hl, gap } = L.cmp;
  const emph = cells.map(() => 0), dim = cells.map(() => 0);           // 強調照時間一個換一個（接續分鏡沿用的強調在負的時間，第一幀就在）
  for (const h of hl) { const k = ctx.lt(t, h.q.at); if (k <= 0) continue; const w = MO.smooth(clamp(k / 0.4));
    cells.forEach((_, i) => { emph[i] = lerp(emph[i], i === h.index ? 1 : 0, w); dim[i] = lerp(dim[i], i === h.index ? 0 : 1, w); }); }
  cells.forEach((e, i) => {
    if (at[i] == null) return;
    const s = t - (at[i] - ENTER); if (s < 0) return;
    const p = MO.quintOut(clamp(s / ENTER)), cp = MO.quintOut(clamp((s - STAGGER) / ENTER));
    c.save(); c.globalAlpha = clamp(s / ENTER * 1.5);
    c.fillStyle = tone(INK, L.col.ink, emph[i], dim[i]);
    drawNum(c, e.P, pre, e.str, suf, e.x, e.by + (1 - p) * FLOAT * u);
    if (cp > 0) { c.globalAlpha = cp; c.fillStyle = tone(INK, INK, 0, dim[i]); c.textAlign = 'left'; c.font = `500 ${capSz}px ${CN}`;
      e.cap.lines.forEach((ln, k) => c.fillText(ln, e.x, e.capY + k * capSz * 1.3 + (1 - cp) * 12 * u)); }
    c.restore();
  });
  if (gap) {
    const s = t - (gap.at - ENTER - 0.1); if (s < 0) return;
    const rp = MO.quintOut(clamp(s / ENTER)), p = MO.quintOut(clamp((s - 0.1) / ENTER));
    c.save(); c.strokeStyle = INK; c.lineWidth = 2 * u; c.globalAlpha = 0.5; c.beginPath(); c.moveTo(gap.x, gap.ruleY); c.lineTo(lerp(gap.x, gap.x1, rp), gap.ruleY); c.stroke();
    if (p > 0) {
      const y = gap.by + (1 - p) * 12 * u; c.globalAlpha = clamp(s / ENTER * 1.5); c.textBaseline = 'alphabetic'; c.textAlign = 'left';
      c.font = `500 ${gap.word}px ${CN}`; c.fillStyle = INK; c.fillText(gap.verb, gap.x, y); const vw = c.measureText(gap.verb + ' ').width;
      c.fillStyle = L.col.ink; drawNum(c, gap.G, gap.pre, gap.str, '', gap.x + vw + gap.G.lead, y);   // 句中的數字不做光學對齊
      c.font = `500 ${gap.word}px ${CN}`; c.fillStyle = INK; c.fillText(gap.suf, gap.x + vw + gap.G.preW + gap.G.numW + gap.G.sz * 0.02, y);
    }
    c.restore();
  }
}
// [article-to-video 內嵌修改] 快推的關鍵幀（init 算好）。cam = {dur, box: [左, 上, 右, 下]（佔畫面比例：內容推近後可以到的範圍，
// 畫面扣掉平台介面區與字幕帶）, snaps: [{at, zoom（推到多大，1.04＝推近 4%）, index（標哪一點）}]}：整串到這個片段為止的每一下，
// 之前分鏡的 at 是負的，所以接續分鏡從同一個鏡頭接著走。鏡頭＝畫面座標 = s × 原座標 + (tx, ty)：
// 每一下以被標註的點為固定點（它在畫面上不動、其他東西往外推開）；放大量夾在「點不動、內容框推近後還在 box 裡」以內
// （推不到要的量就少推，不平移）。內容框＝標題上的小旗到來源行（有角標時到角標底）、左右邊距之間，再加上超出邊距的
// x 軸名字（以點為中心，點多時最左、最右的名字會超出邊距一截）；頂端色條是裝飾、不算。box 左右再各內縮 CAM_SIDE，字不貼著畫面邊。
function cameraKeys(ctx, c, cam) {
  const { W, H, u, safe, data: d } = ctx, { S, xs, yS, kind } = L, b = cam.box || [0, 0, 1, 1];
  const A = [b[0] * W + CAM_SIDE * u, b[1] * H, b[2] * W - CAM_SIDE * u, b[3] * H];
  let x0 = L.m, x1 = W - L.mR;
  c.save(); c.font = `400 ${L.xFont}px ${NUM}`;
  if (xs) S.forEach((r, i) => { if (L.xKeep && !L.xKeep.has(i)) return; const hw = c.measureText(String(r.label ?? '')).width / 2; x0 = Math.min(x0, xs[i] - hw); x1 = Math.max(x1, xs[i] + hw); });   // 大數字卡、兩數對比沒有 x 軸
  c.restore();
  const C = [x0, safe.top + 52 * u, x1, Math.max(H - safe.bottom - 24 * u, d.badge ? H - safe.bottom - 50 * u + 24 : 0)];
  const fit = Math.min((A[2] - A[0]) / (C[2] - C[0]), (A[3] - A[1]) / (C[3] - C[1]));
  const keys = []; let s = 1, tx = 0, ty = 0;
  for (const q of cam.snaps || []) {
    const i = q.index != null && S[q.index] ? q.index : S.length - 1, P = L.focus ? L.focus(i) : [xs[i], yS(kind === 'candle' ? S[i].c : S[i].value)];
    // 被標註的點現在在畫面上的位置 p0 不動：畫面座標 = p0 + s1 × (原座標 − P)。內容框不出 box 的條件換成 s1 的上限
    // （每個方向兩邊各一個）；推不到要的量就少推（甚至不推），不平移整張圖——平移會把被標註的點往畫面邊推（方向反了）
    const p0 = [s * P[0] + tx, s * P[1] + ty];
    let s1 = clamp(+q.zoom || 1, s, Math.max(s, fit));
    for (const k of [0, 1]) {
      if (P[k] > C[k]) s1 = Math.min(s1, (p0[k] - A[k]) / (P[k] - C[k]));
      if (C[k + 2] > P[k]) s1 = Math.min(s1, (A[k + 2] - p0[k]) / (C[k + 2] - P[k]));
    }
    s1 = Math.max(s, s1);
    const nx = p0[0] - s1 * P[0], ny = p0[1] - s1 * P[1];
    keys.push({ at: +q.at || 0, dur: cam.dur || 0.3, d: [s1 - s, nx - tx, ny - ty] }); s = s1; tx = nx; ty = ny;
  }
  return keys;
}
// t 時的鏡頭 [s, tx, ty]：每一下從上一下的終點出發（at 這一幀第一次動，跟 cue 的對齊約定一樣），幾下疊在一起也連續
function camAt(ctx, t) {
  const v = [1, 0, 0];
  for (const k of L.cam) { const e = SNAP_EASE(clamp(ctx.lt(t, k.at) / k.dur)); if (e > 0) for (let j = 0; j < 3; j++) v[j] += k.d[j] * e; }
  return v;
}
// [article-to-video 內嵌修改] 一個 highlight cue 標哪一點、寫哪幾行：cue 自己寫的優先，沒寫用 data.highlight，index 再沒有就是最後一點。
// data.carried（建置從上一個分鏡沿用過來的標註、chainLayout 列的標註）：字和 index 都已經寫死，不看這個分鏡的 data.highlight
function hlOf(q, d, n) {
  const H = d.highlight || {}, fixed = !!(q.data && q.data.carried), qi = q.data && q.data.index;
  const index = qi != null ? qi : !fixed && H.index != null ? H.index : n - 1;
  const lines = fixed ? [q.text, q.sub].filter(Boolean) : [C(q.text, H.text), C(q.sub, H.sub)].filter(Boolean);
  return { index, lines, q };
}
// [article-to-video 內嵌修改] 折線的標註與線尾數值擺哪（真實試跑 #9）。原版標註固定在點的右上（或左上）140、線尾數值固定寫在線頭左上，
// 常壓到零軸、線本身和格線（線最後往下掉時，線尾數值正好被線穿過）。改成從幾個候選位置挑分數最低的：
// 壓到線、壓到線尾數值＝重罰；壓到零軸＝中罰；引線穿過折線＝中罰；壓到格線＝輕罰（字另外描白邊，格線碰不到字）；
// 離原本的位置越遠、放在點的下方，各加一點分。只看資料與版面、init 一次算好，確定性。
function lineGeom(ctx) {
  const { u } = ctx, { F, S, xs, yS } = L, n = S.length;
  const pts = S.map((r, i) => [xs[i], yS(r.value)]);
  const grow = (R, m) => ({ x0: R.x0 - m, y0: R.y0 - m, x1: R.x1 + m, y1: R.y1 + m });
  const hitsSeg = (a, b, R) => { const k = Math.max(2, Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / (3 * u)));
    for (let j = 0; j <= k; j++) { const x = lerp(a[0], b[0], j / k), y = lerp(a[1], b[1], j / k); if (x >= R.x0 && x <= R.x1 && y >= R.y0 && y <= R.y1) return true; } return false; };
  const zeroY = L.zeroBased && L.ticks[0] < 0 ? yS(0) : null;           // 零軸（黑色粗線）在圖區中間時
  return {
    pts, n, F, grow, right: F.x + F.w - 80 * u,                          // 右邊那一欄是刻度值，不放字
    lineHits: R => { const G = grow(R, 8 * u); let k = 0; for (let i = 1; i < n; i++) if (hitsSeg(pts[i - 1], pts[i], G)) k++; return k; },
    zeroHit: R => zeroY != null && R.y0 - 12 * u < zeroY && R.y1 + 12 * u > zeroY,
    gridHits: R => L.ticks.filter(v => zeroY == null || Math.abs(v) > 1e-9).filter(v => { const y = yS(v); return y > R.y0 - 2 * u && y < R.y1 + 2 * u; }).length,
    overlap: (A, B) => !!(A && B && A.x0 < B.x1 && B.x0 < A.x1 && A.y0 < B.y1 && B.y0 < A.y1),
  };
}
// 線尾數值（36 號粗體、右對齊在最後一點的 x）：上方（原版）、下方、左邊
function placeLineText(ctx, c) {
  const { u, data: d } = ctx, { S } = L, g = lineGeom(ctx), { F, n } = g;
  c.font = `700 ${36 * u}px ${NUM}`;
  const lab = fmtv(d, S[n - 1].value), lw = [...lab].reduce((s, ch) => s + c.measureText(/[0-9]/.test(ch) ? '0' : ch).width, 0);
  const [ex, ey] = g.pts[n - 1], asc = 27 * u, desc = 6 * u;
  const endScore = k => (k.R.x0 < F.x || k.R.y0 < F.y - 30 * u || k.R.y1 > F.y + F.h ? 1e6 : 0) + 1000 * g.lineHits(k.R) + 400 * g.zeroHit(k.R) + 20 * g.gridHits(k.R) + k.pref;
  L.endLab = [{ x: ex, y: ey - 24 * u, pref: 0 }, { x: ex, y: ey + 24 * u + asc, pref: 6 }, { x: ex, y: ey - 60 * u, pref: 8 },
    { x: ex - 22 * u, y: ey + asc / 2, pref: 12 }, { x: ex, y: ey + 60 * u + asc, pref: 14 }]
    .map(k => ({ ...k, R: { x0: k.x - lw, x1: k.x, y0: k.y - asc, y1: k.y + desc } }))
    .reduce((b, k) => endScore(k) < endScore(b) ? k : b);
}
// 一個標註（CH.callout：第一行 50 號粗體、第二行 28 號；字從 (tx±12, ty+8) 起、行距 40）擺哪；一個都放不下回傳 null（照原版的位置）
function placeLineLabel(ctx, c, h) {
  const { u } = ctx, g = lineGeom(ctx), { F, n, pts } = g, fs = ctx.portrait ? 1.2 : 1, hiI = h.index, lines = h.lines;
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
    if (R.x0 < F.x || R.x1 > g.right || R.y0 < F.y - 10 * u || R.y1 > F.y + F.h) continue;
    const lh = g.lineHits(R), eo = g.overlap(g.grow(R, 6 * u), endR), zh = g.zeroHit(R), cr = crossings([ax, ay], [tx, ty]);
    const s = 1000 * lh + 1000 * eo + 400 * zh + 300 * cr + 40 * hugs([ax, ay], [tx, ty]) + 20 * g.gridHits(R)
      + 0.3 * (Math.abs(dy + 140) + Math.abs(dx - (toRight ? 60 : -60))) + (dy > 0 ? 40 : 0);
    if (!best || s < best.s) best = { s, tx, ty, align, clean: !lh && !eo && !zh && !cr };   // clean：不壓線、線尾數值、零軸，引線不穿過折線（init 決定要不要多留白）
  }
  return best;
}
// [article-to-video 內嵌修改] 柱狀圖的標註擺法（原版每一幀在 draw 裡決定，這裡用柱長完的位置 init 算好）：
// 負數柱 'neg'（數值標籤正下方）；旁邊有高 1.5 倍以上的柱、或預演的斜向文字框會壓到別根柱或它的數值 'above'（這根柱正上方）；其他 'diag'（斜向）
function barMode(ctx, c, h) {
  const { u } = ctx, { S, xs, yS, F } = L, fs = ctx.portrait ? 1.2 : 1, hi = h.index, v = S[hi].value, ls = h.lines;
  if (v < 0) return 'neg';
  if (S.some((r, j) => j !== hi && r.value > v * 1.5)) return 'above';   // 有更高的柱子挡在旁边：标注放在这根柱子正上方，别压进别的柱子
  const tops = S.map((r, i) => [xs[i], yS(r.value)]), anchor = tops[hi];   // 预演斜向标注的文字框：压到别的柱子或它的数值，就改放这根柱子正上方
  const ax = anchor[0] - L.bw / 2, right = ax > F.x + F.w * 0.55, tx = right ? ax - 60 * u : ax + 60 * u;
  c.save(); c.font = `700 ${50 * u}px ${NUM}`; let tw = c.measureText(ls[0] || '').width; c.font = `400 ${28 * u * fs}px ${CN}`; if (ls[1]) tw = Math.max(tw, c.measureText(ls[1]).width); c.restore();
  const drop = (ls.length > 1 ? 56 : 16) * u, bx0 = right ? tx - 12 - tw : tx + 12, bx1 = bx0 + tw;
  let ty = Math.max(F.y + 60 * u, anchor[1] - 140 * u);
  // [article-to-video 內嵌修改] 斜向文字也不能壓到這根柱自己的數值（柱頂正中）：柱寬隨根數變寬之後，數值會落到文字底下
  // （標最高那根、文字被夾在圖區上緣 +60 時最明顯）。橫向疊到時，文字往上抬到數值上面（最多抬到圖區上緣 +20）
  const lf = Math.max(24 * u * fs, Math.min(34 * u * fs, L.slot * 0.3)); c.save(); c.font = `600 ${lf}px ${NUM}`; const lw = c.measureText(fmtv(ctx.data, v)).width;
  // [article-to-video 內嵌修改] 再照量到的字算一次（數值標籤的字頂、標註最後一行的字底；CH.bars、CH.callout 的 14、8、40 是像素，不跟 u 縮），
  // 取抬得比較高的：畫面縮小渲（qa、測試的 640×360）時，原本的算法只剩 1～2 像素，標註的字會壓到數值
  const labTop = anchor[1] - 14 - c.measureText('0').actualBoundingBoxAscent, last = ls.length - 1;
  c.font = last > 0 ? `400 ${28 * u * fs}px ${CN}` : `700 ${50 * u}px ${NUM}`; const desc = c.measureText(ls[last] || '').actualBoundingBoxDescent; c.restore();
  const fit = labTop - 4 * u - 2 - desc - 8 - 40 * last;
  if (bx0 < anchor[0] + lw / 2 + 6 * u && bx1 > anchor[0] - lw / 2 - 6 * u) ty = Math.max(F.y + 20 * u, Math.min(ty, anchor[1] - 14 - lf - drop - 4 * u, fit));
  h.ty = ty;
  const by1 = ty + drop;
  return tops.some(([bx, by], j) => j !== hi && bx + L.slot * 0.45 > bx0 && bx - L.slot * 0.45 < bx1 && by - 44 * u * fs < by1) ? 'above' : 'diag';
}
// [article-to-video 內嵌修改] 畫一個標註（原版寫在 draw 裡、只畫第一個 highlight cue）。k：這個標註出現了幾秒；alpha：整個標註的透明度（換標註時舊的淡出）；
// dot：'spring'＝圓點彈出（原版）、'full'＝圓點已經在（同一點換字）、'none'＝不畫圓點（同一點換字時舊的那個）。anchor：點（柱頂／柱底、折線上的點）；tops：現在的柱頂
function drawLabel(c, ctx, h, k, alpha, dot, anchor, tops) {
  if (k <= 0 || alpha <= 0) return;
  const { u } = ctx, { F, yS, kind } = L, fs = ctx.portrait ? 1.2 : 1, lines = h.lines, bw = L.bw;
  const s = dot === 'spring' ? MO.spring(k, { duration: 0.45, bounce: 0.25 }) : 1, lp = MO.quintOut(MO.seg(k, 0.12, 0.45)), fp = MO.quintOut(MO.seg(k, 0.3, 0.7));
  const [x, y] = anchor;
  if (kind === 'bar' && h.mode === 'neg') {
    // [article-to-video 內嵌修改] 負數柱：數值標籤寫在柱底下方（CH.bars），原版的斜向標註往下拉會壓在標籤上（「-2.6%」被「跌最深」蓋住）。
    // 改成文字塊放在這根柱的數值標籤正下方（也讓開它橫跨到的其他負數柱與它們的標籤）。[計時實測 R2-7] 圓點在數值標籤正下方
    // （柱的中線上），引線從圓點垂直拉到文字（原版圓點在柱底左角，跟引線沒接上）
    const vlab = NEG_LABEL * u * fs;
    c.save(); c.globalAlpha = alpha; c.font = `700 ${50 * u * fs}px ${NUM}`; let tw = c.measureText(lines[0] || '').width; c.font = `400 ${28 * u * fs}px ${CN}`; if (lines[1]) tw = Math.max(tw, c.measureText(lines[1]).width);
    const cx = clamp(x, F.x + tw / 2, F.x + F.w - 80 * u - tw / 2);
    const cover = tops.filter(([bx]) => bx > cx - tw / 2 - L.slot / 2 && bx < cx + tw / 2 + L.slot / 2).map(([, by]) => by > yS(0) ? by + vlab : yS(0));
    const y0 = y + vlab, dy = y0 + DOT_R * u;                          // 數值標籤下緣、圓點中心
    const top = Math.max(y0 + NEG_DOT * u * fs, Math.max(...cover) + NEG_GAP * u * fs);   // 文字塊上緣
    const y1 = top - 6 * u;
    if (y1 - dy > 6 * u) { c.strokeStyle = INK; c.lineWidth = 2 * u; c.beginPath(); c.moveTo(x, dy); c.lineTo(lerp(x, cx, lp), lerp(dy, y1, lp)); c.stroke(); }
    if (dot !== 'none') { c.fillStyle = L.col.ink; c.beginPath(); c.arc(x, dy, DOT_R * u * Math.max(0, s), 0, 7); c.fill(); }
    c.globalAlpha = alpha * fp; c.fillStyle = INK; c.textAlign = 'center'; c.textBaseline = 'alphabetic';
    lines.forEach((ln, i) => { c.font = i === 0 ? `700 ${50 * u * fs}px ${NUM}` : `400 ${28 * u * fs}px ${CN}`; haloText(c, ln, cx, top + (NEG_LINE1 + i * NEG_LINE2) * u * fs + (1 - fp) * 12 * u, 10 * u); });   // [article-to-video 內嵌修改] 描白邊（試跑 #9）
    c.restore();
  } else if (kind === 'bar' && h.mode === 'above') {
    // 文字块放在这根柱子正上方：先量宽，横向夹在图区里；再抬到它横跨的所有柱子（含数值标签）之上，引线从本柱数值标签上沿连上去
    const lh = 52 * u * fs, vlab = 44 * u * fs;
    c.save(); c.globalAlpha = alpha; c.font = `700 ${50 * u * fs}px ${NUM}`; let tw = c.measureText(lines[0] || '').width; c.font = `400 ${28 * u * fs}px ${CN}`; if (lines[1]) tw = Math.max(tw, c.measureText(lines[1]).width);
    const cx = clamp(x, F.x + tw / 2, F.x + F.w - 80 * u - tw / 2);
    const cover = tops.filter(([bx]) => bx > cx - tw / 2 - L.slot / 2 && bx < cx + tw / 2 + L.slot / 2).map(([, by]) => by);
    // [article-to-video 內嵌修改] 引線至少露出 LEADER_MIN：原版文字塊緊貼數值標籤（只隔 18），引線只剩 10 像素、看起來被標籤蓋掉（短柱最明顯）
    // [計時實測 R2-7] 圓點在數值標籤正上方（柱的中線上），引線從圓點往上拉到文字。[Danny 本机改] 原本圓點在柱頂左角（放柱頂正中會壓到
    // 柱上數值的字底），但引線從數值標籤上面拉出去，兩個沒接上；現在圓點跟引線一起放到數值標籤外側，一樣不壓字
    const y0 = y - vlab - 4 * u, dy = y0 - DOT_R * u;                 // 數值標籤上緣、圓點中心
    const bottom = Math.max(F.y + lh * lines.length, Math.min(Math.min(y, ...cover) - vlab - 18 * u, dy - DOT_R * u - LEADER_MIN * u * fs - 10 * u));   // 文字块底边
    const y1 = bottom + 10 * u;
    if (dy - y1 > 6 * u) { c.strokeStyle = INK; c.lineWidth = 2 * u; c.beginPath(); c.moveTo(x, dy); c.lineTo(lerp(x, cx, lp), lerp(dy, y1, lp)); c.stroke(); }
    if (dot !== 'none') { c.fillStyle = L.col.ink; c.beginPath(); c.arc(x, dy, DOT_R * u * Math.max(0, s), 0, 7); c.fill(); }
    c.globalAlpha = alpha * fp; c.fillStyle = INK; c.textAlign = 'center'; c.textBaseline = 'alphabetic';
    lines.forEach((ln, i) => { c.font = i === 0 ? `700 ${50 * u * fs}px ${NUM}` : `400 ${28 * u * fs}px ${CN}`; haloText(c, ln, cx, bottom - (lines.length - 1 - i) * lh * (i === 0 ? 1 : 0.62) + (1 - fp) * 12 * u, 10 * u); });   // [article-to-video 內嵌修改] 描白邊（試跑 #9）
    c.restore();
  } else {
    const a = kind === 'bar' ? [x - bw / 2, y] : [x, y];
    const right = a[0] > F.x + F.w * 0.55, co = kind === 'line' ? h.co : null;   // [article-to-video 內嵌修改] 折線：位置照 placeLineLabel 挑的（不壓線、零軸）
    const tx = co ? co.tx : right ? a[0] - 60 * u : a[0] + 60 * u;
    const ty = co ? co.ty : kind === 'bar' && h.ty != null ? h.ty : Math.max(F.y + 60 * u, a[1] - 140 * u);   // [article-to-video 內嵌修改] 負數柱另外處理（上面那段），這裡只剩正數柱與折線、K 線；柱的高度 barMode 算好（讓開自己的數值）
    CH.callout(c, k, 0, { x: a[0], y: a[1], tx, ty, lines, col: INK, dotCol: L.col.ink, font: `700 ${50 * u}px ${NUM}`, font2: `400 ${28 * u * fs}px ${CN}`, align: co ? co.align : right ? 'right' : 'left', r: 9 * u, halo: 10 * u, alpha, dot });
  }
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
