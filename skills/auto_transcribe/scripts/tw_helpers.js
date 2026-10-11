// 通义听悟页面操作helper —— 独立 JS 文件，避免 Python 字符串转义地狱。
// 由 auto_transcribe.py 读取本文件并注入执行，函数挂到 window.__tw 上。
(() => {
  const norm = (s) => String(s || '').replace(/\s+/g, '');

  function visible(n) {
    const st = getComputedStyle(n);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
    const r = n.getBoundingClientRect();
    return r.width >= 4 && r.height >= 4;
  }

  // 找元素：exact=精确相等，contains=包含
  // 关键经验：首页「上传音视频」卡片真正可点的是整张卡片（class含 ButtonList，
  // 中心约 578,323），而标题文字节点（578,275）点了没反应。
  // 故对包含匹配：优先选「同时含特征词 + 面积在中等区间」的卡片容器。
  function locate(txt, exact) {
    const target = norm(txt);
    const nodes = Array.from(
      document.querySelectorAll('button,a,div,span,label,li,[role=button]')
    );
    const vw = window.innerWidth * window.innerHeight;
    const all = [];
    const exactHits = [];

    for (const n of nodes) {
      const t = norm(n.innerText || n.textContent || '');
      if (!t) continue;
      if (!visible(n)) continue;
      const r = n.getBoundingClientRect();
      const item = { n, t, area: Math.round(r.width * r.height), tag: n.tagName };
      all.push(item);
      if (t === target) exactHits.push(item);
    }

    const inter = (c) =>
      ['BUTTON', 'A'].includes(c.tag) || c.n.getAttribute('role') === 'button';

    function finish(c) {
      const hit = c.n;
      hit.scrollIntoView({ block: 'center' });
      const r = hit.getBoundingClientRect();
      return {
        x: Math.round(r.x + r.width / 2),
        y: Math.round(r.y + r.height / 2),
        text: norm(hit.innerText || '').slice(0, 40),
        area: c.area,
      };
    }

    if (exact) {
      if (!exactHits.length) return null;
      const pool = exactHits.filter(inter);
      return finish((pool.length ? pool : exactHits).slice().sort((a, b) => a.area - b.area)[0]);
    }

    // 包含匹配
    const hits = all.filter((c) => c.t.includes(target));
    if (!hits.length) return null;

    // 卡片类特征词：同时含多个短词时更像可点卡片
    const feature = (t) => {
      let s = 0;
      if (t.includes('音视频转文字')) s += 2;
      if (t.includes('区分发言人')) s += 2;
      if (t.includes('上传本地')) s += 2;
      if (t.includes('导入阿里云盘')) s += 2;
      if (t.includes('点击') && t.includes('拖拽')) s += 2;
      return s;
    };

    // 评分：特征词多的优先，其次排除巨容器，再取面积较小
    const scored = hits
      .filter((c) => c.area < vw * 0.5)
      .map((c) => ({ ...c, score: feature(c.t) + (inter(c) ? 3 : 0) }));

    if (!scored.length) return null;
    scored.sort((a, b) => (b.score - a.score) || (a.area - b.area));
    return finish(scored[0]);
  }

  // 点击元素。
  // 重要教训：CDP 的 Input.dispatchMouseEvent 合成点击在听悟首页卡片上**无效**
  // （click() 返回 true 但弹层不开），必须用 DOM 原生 el.click()，
  // 因为 React 的事件监听挂在元素本身。
  window.__twClick = null;

  function nativeClick(kw, opts) {
    const o = opts || {};
    const target = norm(kw);
    const nodes = Array.from(
      document.querySelectorAll('button,a,div,span,label,li,[role=button]')
    );
    const cands = [];
    for (const n of nodes) {
      const t = norm(n.innerText || n.textContent || '');
      if (!t || !visible(n)) continue;
      if (o.requireAll) {
        // 要求同时包含多个特征词（定位卡片容器）
        if (!o.requireAll.every((k) => t.includes(k))) continue;
      } else if (o.exact ? t !== target : !t.includes(target)) {
        continue;
      }
      const r = n.getBoundingClientRect();
      if (o.maxWidth && r.width > o.maxWidth) continue;
      if (o.minWidth && r.width < o.minWidth) continue;
      cands.push({ n, t, area: Math.round(r.width * r.height), tag: n.tagName });
    }
    if (!cands.length) return { ok: false, reason: 'no-match' };

    let pool = cands;
    if (o.requireAll) {
      // 卡片：取面积最小的完整卡片
      pool = cands.slice().sort((a, b) => a.area - b.area);
    } else {
      const inter = cands.filter(
        (c) => ['BUTTON', 'A'].includes(c.tag) || c.n.getAttribute('role') === 'button'
      );
      const use = inter.length ? inter : cands;
      pool = use.slice().sort((a, b) => a.area - b.area);
    }
    const hit = pool[0].n;
    hit.scrollIntoView({ block: 'center' });
    hit.click();
    const r = hit.getBoundingClientRect();
    return {
      ok: true,
      text: norm(hit.innerText || '').slice(0, 40),
      x: Math.round(r.x + r.width / 2),
      y: Math.round(r.y + r.height / 2),
    };
  }

  window.__tw = {
    locate,
    nativeClick,

    // 真实鼠标点击（弹层类元素需要真实事件）
    clickAt: (x, y) => { window.__tw._x = x; window.__tw._y = y; return { x, y }; },

    // DOM 原生 click
    domClick: (txt, exact) => {
      const p = locate(txt, exact !== false);
      if (!p) return false;
      // 重新定位一次拿元素引用并点
      const target = norm(txt);
      const nodes = Array.from(
        document.querySelectorAll('button,a,div,span,label,li,[role=button]')
      );
      const hit = nodes
        .filter((n) => {
          const t = norm(n.innerText || n.textContent || '');
          const ok = exact !== false ? t === target : t.includes(target);
          return ok && visible(n);
        })
        .sort((a, b) => {
          const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
          return ra.width * ra.height - rb.width * rb.height;
        })[0];
      if (!hit) return false;
      hit.click();
      return true;
    },

    countFiles: () => document.querySelectorAll('input[type=file]').length,

    // 选中「多人讨论」（区分发言人下的第四个选项）
    pickMultiDiscussion: () => {
      const row = Array.from(document.querySelectorAll('div')).find(
        (n) =>
          String(n.className || '').includes('sc-hTUWRQ') &&
          norm(n.innerText || '').includes('多人讨论')
      );
      if (!row) return 'no-row';
      const btn = Array.from(row.children).find((c) => norm(c.innerText || '') === '多人讨论');
      if (!btn) return 'no-btn';
      if (String(btn.className || '').includes('btnActive')) return 'already';
      btn.click();
      return 'clicked';
    },

    isMultiDiscussionOn: () => {
      const row = Array.from(document.querySelectorAll('div')).find(
        (n) =>
          String(n.className || '').includes('sc-hTUWRQ') &&
          norm(n.innerText || '').includes('多人讨论')
      );
      if (!row) return false;
      const btn = Array.from(row.children).find((c) => norm(c.innerText || '') === '多人讨论');
      return !!btn && String(btn.className || '').includes('btnActive');
    },

    text: () => document.body.innerText,
    url: () => location.href,

    // 扫描当前页面，抽取**真正失败**的文件名。
    // 关键区分（踩过的坑）：
    //   「已上传(0%)」是**正在排队上传**的正常状态，不是失败！
    //   「上传中 / 转写中 / 排队中」也都是正常态。
    //   只有「转写失败 / 解析失败 / 文件损坏 / 不支持的格式」才是失败。
    scanFailed: () => {
      const norm = (s) => String(s || '').replace(/\s+/g, '');
      const FAIL_RE = /转写失败|解析失败|文件损坏|格式不支持|不支持的格式|上传失败|转写异常/;
      const lines = (document.body.innerText || '').split('\n');
      const fails = [];
      const seen = new Set();
      for (let i = 0; i < lines.length; i++) {
        const raw = (lines[i] || '').trim();
        const ln = norm(raw);
        if (!ln || !FAIL_RE.test(ln)) continue;

        let name = null;
        for (let k = i; k >= Math.max(0, i - 4); k--) {
          const m = (lines[k] || '').match(/\d{8}\s+\d{6}-[A-Za-z0-9]+/);
          if (m) { name = m[0].trim(); break; }
        }
        // 同一文件名只记一次，保留首个原因
        if (name && !seen.has(name)) {
          seen.add(name);
          // 原因可能在本行或紧邻行
          let reason = raw;
          for (let d = 1; d <= 2; d++) {
            const nb = (lines[i + d] || '').trim();
            if (nb && nb.length < 50 && FAIL_RE.test(norm(nb))) { reason += ' | ' + nb; break; }
          }
          fails.push({ name: name, reason: reason.slice(0, 80), status: raw.slice(0, 30) });
        }
      }
      return fails;
    },

    // 统计页面上的队列状态（用于人工判断是否还在推进）
    queueStats: () => {
      const t = document.body.innerText || '';
      const count = (re) => (t.match(re) || []).length;
      return {
        排队中: count(/排队中/g),
        上传中: count(/上传中/g),
        已上传0: count(/已上传\(0%\)/g),
        转写中: count(/转写中/g),
        失败: count(/转写失败|解析失败|文件损坏|格式不支持/g),
        文件条目: count(/\d{8}\s+\d{6}-[A-Za-z0-9]+/g),
      };
    },

    // 关闭右上角全局通知（antd notification）。
    // 陷阱：真实类名是 `.ant-notification`（不是 -notice），关闭按钮是 `.ant-notification-close`。
    // 该toast 会浮在页面右上角，虽不挡上传卡片，但点击行为会被它吞掉。
    closeNotices: () => {
      let n = 0;
      document.querySelectorAll('.ant-notification').forEach((el) => {
        const r = el.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return;
        const btn = el.querySelector('.ant-notification-close');
        if (btn) btn.click();
        else el.remove();
        n++;
      });
      return n;
    },

    // 诊断：列出所有包含关键词的可点击元素
    probe: (kw) => {
      const target = norm(kw);
      return Array.from(
        document.querySelectorAll('button,a,div,span,li,[role=button]')
      )
        .filter((n) => norm(n.innerText || '').includes(target))
        .slice(0, 6)
        .map((n) => {
          const r = n.getBoundingClientRect();
          return {
            tag: n.tagName,
            text: norm(n.innerText || '').slice(0, 30),
            cls: String(n.className || '').slice(0, 40),
            box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
          };
        });
    },

    // 一站式：首页 → 打开上传面板（两层弹层），返回状态串
    openUploadPanel: () => {
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const clickContains = (kw) => {
        const p = window.__tw.locate(kw, false);
        if (!p) return false;
        const el = document.elementFromPoint(p.x, p.y);
        if (el) {
          // 向上找到带 onClick 的祖先（React 事件挂在容器上）
          let n = el;
          for (let i = 0; i < 5 && n; i++) {
            n.click();
            n = n.parentElement;
            if (i >= 1) break;
          }
        }
        return true;
      };
      return (async () => {
        window.__tw.locate('上传音视频', false);
        const p1 = window.__tw.locate('上传音视频', false);
        if (p1) {
          const el = document.elementFromPoint(p1.x, p1.y);
          if (el) { el.click(); if (el.parentElement) el.parentElement.click(); }
        }
        await sleep(2500);
        if (!document.querySelector('input[type=file]')) {
          const p2 = window.__tw.locate('上传本地', false);
          if (p2) {
            const el2 = document.elementFromPoint(p2.x, p2.y);
            if (el2) { el2.click(); if (el2.parentElement) el2.parentElement.click(); }
          }
          await sleep(2500);
        }
        return {
          ready: document.querySelectorAll('input[type=file]').length > 0,
          hasLocalText: document.body.innerText.includes('上传本地'),
        };
      })();
    },

    // 上传面板是否就绪
    panelReady: () => document.querySelectorAll('input[type=file]').length > 0,

    // 面板里已选文件数（听悟在标题显示「文件数量：50/50」）
    fileCountText: () => {
      const t = document.body.innerText;
      const m = t.match(/文件数量[：:]\s*(\d+)\s*\/\s*(\d+)/);
      return m ? { chosen: +m[1], max: +m[2] } : null;
    },

    // 读取面板里「已选文件列表」的文件名（去重）。
    // 只抓 span.upload-filename —— 这是上传面板专属的已选文件项，
    // 不能用 body.innerText 扫（默认文件夹页面本身会列出已上传文件名，会误判）。
    // 返回的是裸名（去掉扩展名），与本地清单格式一致，可直接比对。
    panelNames: () => {
      const stripExt = (t) => {
        const dot = t.lastIndexOf('.');
        return dot > 0 ? t.slice(0, dot) : t;
      };
      const set = new Set();
      document.querySelectorAll('span.upload-filename').forEach((s) => {
        const t = (s.innerText || '').trim();
        if (t) set.add(stripExt(t));
      });
      return Array.from(set);
    },
  };
  return 'tw-ready';
})();
