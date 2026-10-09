/* 審片頁的互動（內嵌進頁面，不連網）。
 * - 留言格、整體意見：打字就存進瀏覽器（localStorage，鍵含專案），重新整理不會不見。
 *   重建後版本變了：上一版還沒複製出去的留言會問要不要放回來；已經複製出去的就從空白開始。
 * - 「複製給 Claude」：組成固定格式的文字（格式見 article_to_video/feedback.py），
 *   先試 Clipboard API，不行就選取文字用 execCommand('copy')，再不行就把文字選好請作者按 ⌘C。
 *   不管哪一種，文字都會顯示在下方，可以自己選取複製。
 * - 「能加什麼」的範例：點一下放進整體意見。
 * - 有成片時：點卡片上的時間，影片跳到那一段。
 */
(function () {
  'use strict';

  var data = JSON.parse(document.getElementById('review-data').textContent);
  var KEY = 'atv-review:' + data.storageKey;
  var boxes = Array.prototype.slice.call(document.querySelectorAll('textarea.comment'));
  var overall = document.getElementById('overall');
  var statusEl = document.getElementById('copy-status');
  var panel = document.getElementById('copy-panel');
  var out = document.getElementById('copy-out');
  var shotById = {};
  data.shots.forEach(function (s) { shotById[s.id] = s; });

  // ------------------------------------------------------------ 存取（瀏覽器不給存也照常能用）

  function readStore() {
    try {
      var raw = window.localStorage.getItem(KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  function writeStore(obj) {
    try {
      if (obj) window.localStorage.setItem(KEY, JSON.stringify(obj));
      else window.localStorage.removeItem(KEY);
    } catch (e) { /* 私密瀏覽或被擋：只是不能保存，不影響複製 */ }
  }

  function snapshot(sent) {
    var comments = {};
    boxes.forEach(function (b) { if (b.value.trim()) comments[b.dataset.shotId] = b.value; });
    return { version: data.version, comments: comments, overall: overall.value, sent: !!sent };
  }

  function hasAny(s) {
    return !!s && ((s.overall && s.overall.trim()) || Object.keys(s.comments || {}).length > 0);
  }

  function fill(s) {
    var leftover = [];
    Object.keys(s.comments || {}).forEach(function (id) {
      var box = document.querySelector('textarea.comment[data-shot-id="' + id + '"]');
      if (box) box.value = s.comments[id];
      else leftover.push('（原本寫給 ' + id + '，這版已經沒有這個分鏡）' + s.comments[id]);
    });
    var parts = [];
    if (s.overall && s.overall.trim()) parts.push(s.overall.trim());
    parts = parts.concat(leftover);
    if (parts.length) overall.value = parts.join('\n');
  }

  // ------------------------------------------------------------ 留言數

  function lines(text) {
    return text.split(/\r?\n/).map(function (s) { return s.trim(); }).filter(Boolean);
  }

  function item(head, ls) {
    return head + ls[0] + ls.slice(1).map(function (l) { return '\n  ' + l; }).join('');
  }

  function buildText() {
    var rows = ['審片留言｜' + data.project + '｜第 ' + data.version + ' 版'];
    var count = 0;
    boxes.forEach(function (b) {
      var ls = lines(b.value);
      if (!ls.length) return;
      var s = shotById[b.dataset.shotId];
      rows.push(item('分鏡 ' + s.number + '（' + s.id + '）：', ls));
      count += 1;
    });
    var ov = lines(overall.value);
    if (ov.length) {
      rows.push(item('整體意見：', ov));
      count += 1;
    }
    return { text: rows.join('\n') + '\n', count: count };
  }

  function setStatus(msg, tone) {
    statusEl.textContent = msg;
    statusEl.dataset.tone = tone || '';
  }

  function showCount() {
    var n = buildText().count;
    setStatus(n ? '已寫 ' + n + ' 則留言。寫完按「複製給 Claude」。' : '在卡片下方的格子寫想改的地方，寫完按「複製給 Claude」。');
  }

  function onEdit() {
    writeStore(snapshot(false));
    showCount();
  }

  // ------------------------------------------------------------ 複製

  function selectOut() {
    out.focus();
    out.select();
    try { out.setSelectionRange(0, out.value.length); } catch (e) { /* 舊瀏覽器 */ }
  }

  function legacyCopy() {
    selectOut();
    try {
      return document.execCommand('copy') === true;
    } catch (e) {
      return false;
    }
  }

  function copy() {
    var r = buildText();
    if (!r.count) {
      panel.hidden = true;
      setStatus('還沒寫任何留言。沒有要改的話，直接跟 Claude 說「可以出片」就好。', 'warn');
      return;
    }
    out.value = r.text;
    panel.hidden = false;
    writeStore(snapshot(true));
    var done = function (ok) {
      if (ok) {
        selectOut();
        setStatus('已複製 ' + r.count + ' 則留言。回到 Claude 的對話框，按 ⌘V 貼上送出。', 'ok');
      } else {
        selectOut();
        setStatus('這個瀏覽器不讓網頁直接複製。下面的文字已經幫你選好了：按 ⌘C 複製，再到 Claude 的對話框按 ⌘V 貼上。', 'warn');
      }
    };
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      navigator.clipboard.writeText(r.text).then(function () { done(true); }, function () { done(legacyCopy()); });
    } else {
      done(legacyCopy());
    }
  }

  // ------------------------------------------------------------ 開頁：放回留言

  var stored = readStore();
  if (stored && stored.version === data.version) {
    fill(stored);
  } else if (stored && !stored.sent && hasAny(stored)) {
    var carry = document.getElementById('carry');
    var n = Object.keys(stored.comments || {}).length + (stored.overall && stored.overall.trim() ? 1 : 0);
    document.getElementById('carry-text').textContent =
      '你在第 ' + stored.version + ' 版寫的 ' + n + ' 則留言還沒複製給 Claude。要放回這一版的卡片上嗎？';
    carry.hidden = false;
    document.getElementById('carry-yes').addEventListener('click', function () {
      fill(stored);
      carry.hidden = true;
      onEdit();
    });
    document.getElementById('carry-no').addEventListener('click', function () {
      carry.hidden = true;
      writeStore(null);
    });
  } else if (stored) {
    writeStore(null); // 上一版的留言已經複製出去了
  }

  boxes.concat([overall]).forEach(function (b) { b.addEventListener('input', onEdit); });
  document.getElementById('copy-btn').addEventListener('click', copy);
  document.getElementById('copy-close').addEventListener('click', function () { panel.hidden = true; showCount(); });

  Array.prototype.forEach.call(document.querySelectorAll('button.example'), function (btn) {
    btn.addEventListener('click', function () {
      var text = btn.textContent.trim();
      overall.value = overall.value.trim() ? overall.value.replace(/\s+$/, '') + '\n' + text : text;
      overall.focus();
      onEdit();
    });
  });

  var video = document.querySelector('video.film-video');
  if (video) {
    Array.prototype.forEach.call(document.querySelectorAll('button.seek'), function (btn) {
      btn.addEventListener('click', function () {
        video.currentTime = parseFloat(btn.dataset.start) || 0;
        video.scrollIntoView({ block: 'center' });
        var p = video.play();
        if (p && p.catch) p.catch(function () { /* 瀏覽器不讓自動播放：停在那一格就好 */ });
      });
    });
  }

  showCount();
})();
