/*
 * Review surface for the D slice: name → Aid decisions over the `_unidentified`
 * shelf.  Split out of resolve.html so the page stays markup.
 *
 * Hard boundary: nothing in this file promotes a book.  It records, overrides or
 * clears a *decision*; moving an archive lives in the governance page behind its
 * own scoped token.  That is why every affordance here writes to /api/resolve/*
 * and never to /api/governance/*.
 */
(() => {
  const $ = (id) => document.getElementById(id);
  const main = $("main");
  const statusEl = $("status");

  const LOAD_TIMEOUT_MS = 15000;
  const PAGE_SIZE = 60;

  let data = null;
  let shown = PAGE_SIZE;
  let loadTimer = null;
  let busy = false;
  let filters = { state: "", generation: "", q: "" };

  function esc(v) {
    return String(v === null || v === undefined ? "" : v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function setStatus(msg, cls) {
    statusEl.textContent = msg;
    statusEl.className = cls || "";
  }

  function lock(on) {
    busy = on;
    main.querySelectorAll("button").forEach((b) => { b.disabled = on; });
    if (!on) syncPager();
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    }).then(async (r) => {
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || d.error || "HTTP " + r.status);
      return d;
    });
  }

  function loadFailed(msg) {
    main.innerHTML = '<div class="empty">裁决记录载入失败：' + esc(msg) +
      '<br><button id="retry" type="button">重试</button></div>';
    main.setAttribute("aria-busy", "false");
    $("retry").addEventListener("click", () => load(true));
    setStatus("裁决记录载入失败：" + msg, "error");
  }

  async function load(isRetry, successMessage) {
    if (loadTimer) clearTimeout(loadTimer);
    lock(true);
    main.setAttribute("aria-busy", "true");
    main.innerHTML = "";
    const controller = new AbortController();
    loadTimer = setTimeout(() => {
      loadTimer = null;
      controller.abort();
      loadFailed("请求超时，请点「重试」");
    }, LOAD_TIMEOUT_MS);

    const qs = new URLSearchParams();
    if (filters.state) qs.set("state", filters.state);
    if (filters.generation) qs.set("generation", filters.generation);
    if (filters.q) qs.set("q", filters.q);

    try {
      const r = await fetch("/api/resolve?" + qs.toString(), { signal: controller.signal });
      if (!r.ok) throw new Error("HTTP " + r.status);
      data = await r.json();
      if (loadTimer) { clearTimeout(loadTimer); loadTimer = null; }
      if (!isRetry || !successMessage) shown = PAGE_SIZE;
      render();
      const note = successMessage
        || ("共 " + data.total + " 个书单位 · " + data.zips + " 个包 · 已裁决 " + data.promotable +
            " 个（" + data.promotableZips + " 个包）· 只读，未移动任何文件。");
      setStatus(note, successMessage ? "ok" : "");
    } catch (err) {
      if (err.name === "AbortError") return;
      if (loadTimer) { clearTimeout(loadTimer); loadTimer = null; }
      loadFailed(err.message);
    } finally {
      lock(false);
    }
  }

  function summaryChips(d) {
    const c = d.counts || {};
    const parts = [
      ["书单位", d.total],
      ["包", d.zips],
      ["已定案", d.promotable],
      ["待定", c.pending || 0],
      ["跳过", c.skipped || 0],
      ["未处理", c.unresolved || 0],
    ];
    return parts.map(([k, v]) => '<span class="chip">' + esc(v) + " <b>" + esc(k) + "</b></span>").join("");
  }

  function candidateLine(m) {
    const cands = (m.candidates || []).slice(0, 3);
    if (!cands.length) return "";
    return '<div class="cands">候选：' + cands.map((c) =>
      esc(c.id) + "（" + (typeof c.score === "number" ? c.score.toFixed(3) : esc(c.score)) + "）" +
      esc(String(c.name || "").slice(0, 42))).join(" · ") + "</div>";
  }

  function row(m) {
    const stateLabel = { chosen: "已定案", pending: "待定", skipped: "已跳过", unresolved: "未处理" }[m.state] || m.state;
    const tags = [];
    if (m.aidHint) tags.push("文件名带车号");
    if (m.conflictingAids && m.conflictingAids.length) tags.push("车号冲突");
    if (m.wouldCreateBook) tags.push("新建书");
    else if (m.chosen) tags.push("并入已有书");

    const acts = [];
    acts.push('<button type="button" data-act="decide" data-key="' + esc(m.key) +
      '" data-aid="' + esc(m.chosen || "") + '">' + (m.chosen ? "改判" : "指定车号") + "</button>");
    if (m.state !== "skipped") {
      acts.push('<button type="button" data-act="skip" data-key="' + esc(m.key) + '">跳过</button>');
    }
    if (m.state !== "unresolved") {
      acts.push('<button type="button" data-act="clear" data-key="' + esc(m.key) + '">清除裁决</button>');
    }

    return "<tr>" +
      '<td data-k="书名"><div class="name">' + esc(m.name) + "</div>" + candidateLine(m) +
        (m.note ? '<div class="note">' + esc(m.note) + "</div>" : "") +
        (tags.length ? '<div class="cands">' + esc(tags.join(" · ")) + "</div>" : "") + "</td>" +
      '<td data-k="世代">' + esc(m.generation) + "</td>" +
      '<td data-k="包">' + esc(m.zipCount) + "</td>" +
      '<td data-k="车号"><span class="aid">' + esc(m.chosen || "—") + "</span></td>" +
      '<td data-k="状态"><span class="state" data-s="' + esc(m.state) + '">' + esc(stateLabel) + "</span></td>" +
      '<td data-k="操作"><div class="rowacts">' + acts.join("") + "</div></td>" +
      "</tr>";
  }

  function render() {
    const d = data || {};
    const items = d.items || [];
    const slice = items.slice(0, shown);
    const states = (d.states || []).map((s) =>
      '<option value="' + esc(s) + '"' + (filters.state === s ? " selected" : "") + ">" + esc(s) + "</option>").join("");
    const gens = Object.keys(d.byGeneration || {}).sort().map((g) =>
      '<option value="' + esc(g) + '"' + (filters.generation === g ? " selected" : "") + ">" + esc(g) +
      "（" + esc(d.byGeneration[g]) + "）</option>").join("");

    const head =
      '<div class="filters">' +
        '<input id="q" type="search" value="' + esc(filters.q) + '" placeholder="搜索书名、目录或车号" aria-label="搜索">' +
        '<select id="state" aria-label="按状态筛选"><option value="">全部状态</option>' + states + "</select>" +
        '<select id="gen" aria-label="按世代筛选"><option value="">全部世代</option>' + gens + "</select>" +
        '<button id="clearf" type="button">清掉筛选</button>' +
      "</div>" +
      '<div class="summary">' + summaryChips(d) + "</div>" +
      '<p class="note">' + esc(d.note || "") + "</p>";

    const body = items.length
      ? '<table><thead><tr><th>书名</th><th>世代</th><th>包</th><th>车号</th><th>状态</th><th>操作</th></tr></thead><tbody>' +
        slice.map(row).join("") + "</tbody></table>"
      : '<div class="empty">' + (d.total === 0
          ? "没有符合当前筛选的书单位。清掉筛选再试。"
          : "还没有裁决记录：先在命令行跑 <code>python -m jmlib resolve --apply</code>。") + "</div>";

    const more = items.length > shown
      ? '<div class="rowacts"><button id="more" type="button">再显示 ' + Math.min(PAGE_SIZE, items.length - shown) +
        " 个（还有 " + (items.length - shown) + " 个）</button></div>"
      : "";

    main.innerHTML = head + body + more;
    main.setAttribute("aria-busy", "false");
    wire();
    lock(busy);
  }

  function syncPager() {
    const more = $("more");
    if (more) more.disabled = busy;
  }

  function wire() {
    const q = $("q");
    if (q) {
      let t = null;
      q.addEventListener("input", () => {
        clearTimeout(t);
        t = setTimeout(() => { filters.q = q.value.trim(); load(true); }, 280);
      });
    }
    const st = $("state");
    if (st) st.addEventListener("change", () => { filters.state = st.value; load(true); });
    const gen = $("gen");
    if (gen) gen.addEventListener("change", () => { filters.generation = gen.value; load(true); });
    const cf = $("clearf");
    if (cf) cf.addEventListener("click", () => {
      filters = { state: "", generation: "", q: "" };
      load(true);
    });
    const more = $("more");
    if (more) more.addEventListener("click", () => { shown += PAGE_SIZE; render(); });

    main.querySelectorAll("button[data-act]").forEach((b) => {
      b.addEventListener("click", () => act(b.dataset.act, b.dataset.key, b.dataset.aid));
    });
  }

  async function act(kind, key, currentAid) {
    if (kind === "skip") {
      try {
        const d = await post("/api/resolve/skip", { key: key });
        await load(true, d.message);
      } catch (err) { setStatus("跳过失败：" + err.message, "error"); }
      return;
    }
    if (kind === "clear") {
      try {
        const d = await post("/api/resolve/clear", { key: key });
        await load(true, d.message);
      } catch (err) { setStatus("清除失败：" + err.message, "error"); }
      return;
    }

    const answer = window.prompt("输入这本书的车号（1–7 位数字）", currentAid || "");
    if (answer === null) return;
    const aid = answer.trim();
    if (!/^\d{1,7}$/.test(aid)) {
      setStatus("车号必须是 1–7 位数字。", "error");
      return;
    }
    try {
      const d = await post("/api/resolve/decide", { key: key, aid: aid });
      await load(true, d.message);
    } catch (err) { setStatus("记录失败：" + err.message, "error"); }
  }

  $("refresh").addEventListener("click", () => load(true));
  load();
})();
