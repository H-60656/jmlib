/* jmlib reader (C.2).
 *
 * Split out of read.html so the page can stay markup and this file can grow
 * without the HTML becoming unreadable.  Pure static file: no build step, no
 * framework, no third-party code.
 *
 * The contract this file has to keep:
 *   - one image at a time is streamed from /api/page and never a whole chapter
 *     unpacked to disk;
 *   - the resume order stays explicit-params > memory > chapter one;
 *   - every failed state (book, page, image, fullscreen) offers a way forward
 *     instead of a dead end.
 *
 * Reading preferences are *presentation only*: they are read once at start,
 * written only when the user changes a control, and never touch the memory
 * pointer — that still means "the page I stopped on".
 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const viewer = $("viewer");
  const pagesBox = $("pages");
  const slots = [pagesBox.children[0], pagesBox.children[1]];  const placeholder = $("placeholder");
  const statusEl = $("status"), memEl = $("mem");
  const chapterSel = $("chapter");
  const prevCh = $("prev-ch"), nextCh = $("next-ch");
  const dirSel = $("dir"), layoutSel = $("layout"), fitSel = $("fit");
  const fsBtn = $("fs");
  const bmAdd = $("bm-add"), bmList = $("bm-list");
  const prev = $("prev"), next = $("next");
  const progress = $("progress"), jump = $("jump"), go = $("go");
  const scrollBox = $("scroll");

  const params = new URLSearchParams(location.search);
  const aid = params.get("aid") || "";
  const forceP = parseInt(params.get("p") || "", 10);
  const forceI = parseInt(params.get("i") || "", 10);

  const PREFETCH_LIMIT = 2;              // plan: at most two pages ahead
  const SWIPE_MIN = 45;                  // px before a drag counts as a swipe
  const SCROLL_SAVE_MS = 2000;           // plan: throttle scroll-position memory writes
  const EAGER_PAGES = 3;                 // pages loaded without waiting for the observer
  const SCROLL_LOAD_TIMEOUT_MS = 12000;   // a stuck column page must fail, not hang
  const RESTORE_WINDOW_MS = 1500;        // hold a restored position while pages decode
  const REANCHOR_HOLD_MS = 400;          // suppress sampling after one correction

  let book = null;
  let chapters = [];
  let pindex = 1, imageIndex = 1, pageCount = 0;
  let prefs = { direction: "ltr", layout: "single", fit: "height", fullscreen: false };
  let objectUrls = [], controller = null, seq = 0;
  let touchX = null, touchY = null;
  let scrollObserver = null, scrollSaveTimer = null, scrollDirty = false, reanchorUntil = 0;
  // A restore holds the requested page for a bounded window.  Sampling is
  // suppressed for its whole length rather than for a fixed delay after each
  // correction: the column reflows every time a neighbour decodes, and letting
  // the sampler in between two of those reflows records (and saves) a position
  // the user never chose — which is how a restored page 155 came back as 158.
  let restoreUntil = 0;
  // The element a restore is holding in place.  Keeping the *node* rather than its
  // page number matters: the sampler rewrites ``imageIndex`` while the neighbours
  // decode, and anchoring on the number would chase the drift it is meant to stop.
  let anchorImg = null;

  /* ---------------------------------------------------------------- text */

  function setStatus(msg, isError = false) {
    statusEl.textContent = msg;
    statusEl.classList.toggle("error", isError);
  }
  function setMem(msg, isError = false) {
    memEl.textContent = msg;
    memEl.classList.toggle("error", isError);
  }
  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  /* ------------------------------------------------------------ geometry */

  function chapterOf(p) {
    return chapters.find((c) => c.pindex === p) || null;
  }
  function step() {
    return prefs.layout === "double" ? 2 : 1;
  }
  function visiblePages() {
    const out = [imageIndex];
    if (prefs.layout === "double" && imageIndex + 1 <= pageCount) out.push(imageIndex + 1);
    return out;
  }
  function positionText() {
    const pages = visiblePages();
    const span = pages.length > 1 ? `${pages[0]}–${pages[pages.length - 1]}` : String(pages[0]);
    return `第 ${pindex} 章 · 第 ${span} / ${pageCount} 页`;
  }
  function hasPrevChapter() {
    return chapters.findIndex((c) => c.pindex === pindex) > 0;
  }
  function hasNextChapter() {
    const at = chapters.findIndex((c) => c.pindex === pindex);
    return at >= 0 && at < chapters.length - 1;
  }

  /* -------------------------------------------------------------- states */

  function showPlaceholder(title, detail, retryLabel) {
    placeholder.hidden = false;
    placeholder.innerHTML = `<strong>${esc(title)}</strong><span>${esc(detail)}</span>` +
      (retryLabel ? `<button id="retry" type="button">${esc(retryLabel)}</button>` : "");
    if (retryLabel) {
      $("retry").addEventListener("click", () => loadPage(pindex, imageIndex, { retry: true }));
    }
  }
  function hidePlaceholder() { placeholder.hidden = true; placeholder.innerHTML = ""; }

  function setBusy(on) {
    viewer.setAttribute("aria-busy", on ? "true" : "false");
    chapterSel.disabled = on || chapters.length === 0;
    prevCh.disabled = on || !hasPrevChapter();
    nextCh.disabled = on || !hasNextChapter();
    prev.disabled = on || (imageIndex <= 1 && !hasPrevChapter());
    next.disabled = on || (imageIndex + step() - 1 >= pageCount && !hasNextChapter());
    jump.disabled = on || pageCount === 0;
    go.disabled = on || pageCount === 0;
    progress.disabled = on || pageCount === 0;
    bmAdd.disabled = on || pageCount === 0;
  }
  function syncControls() {
    chapterSel.value = String(pindex);
    jump.max = String(pageCount);
    jump.value = String(imageIndex);
    progress.max = String(pageCount || 1);
    progress.value = String(imageIndex);
    prevCh.disabled = !hasPrevChapter();
    nextCh.disabled = !hasNextChapter();
    prev.disabled = imageIndex <= 1 && !hasPrevChapter();
    next.disabled = (imageIndex + step() - 1 >= pageCount) && !hasNextChapter();
  }

  function applyPrefs() {
    dirSel.value = prefs.direction;
    layoutSel.value = prefs.layout;
    fitSel.value = prefs.fit;
    viewer.dataset.dir = prefs.direction;
    viewer.dataset.layout = prefs.layout;
    viewer.classList.remove("fit-height", "fit-width", "fit-original");
    viewer.classList.add("fit-" + prefs.fit);
    scrollBox.hidden = prefs.layout !== "scroll";
    fsBtn.setAttribute("aria-pressed", document.fullscreenElement ? "true" : "false");
  }

  /* -------------------------------------------------------------- memory */

  async function saveMemory(note = "") {
    try {
      const r = await fetch("/api/memory/" + encodeURIComponent(aid), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pindex, image_index: imageIndex }),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      setMem(note || "记忆点已记录：" + positionText());
    } catch (e) {
      setMem("记忆点保存失败：" + e.message + "（仍可继续阅读，下一页会重试）", true);
    }
  }

  /* --------------------------------------------------------------- prefs */

  async function loadPrefs() {
    try {
      const r = await fetch("/api/prefs/" + encodeURIComponent(aid));
      if (!r.ok) return;
      const d = await r.json();
      prefs.direction = d.direction || "ltr";
      prefs.layout = d.layout || "single";
      prefs.fit = d.fit || "height";
      prefs.fullscreen = d.fullscreen === true;
    } catch (_) {
      /* Preferences are a convenience: a failure here must not stop reading. */
    }
  }

  async function savePref(field, value) {
    try {
      const r = await fetch("/api/prefs/" + encodeURIComponent(aid), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [field]: value }),
      });
      if (!r.ok) {
        let detail = "HTTP " + r.status;
        try { const d = await r.json(); if (d.detail) detail += "：" + d.detail; } catch (_) {}
        throw new Error(detail);
      }
      setMem("阅读设置已保存。");
    } catch (e) {
      // The control keeps its new value: the reader already behaves that way, and
      // silently reverting a control the user just moved is worse than forgetting it.
      setMem("阅读设置保存失败：" + e.message + "（本次仍然生效）", true);
    }
  }

  /* ----------------------------------------------------------- bookmarks */

  async function loadBookmarks() {
    try {
      const r = await fetch("/api/bookmarks?aid=" + encodeURIComponent(aid));
      if (!r.ok) return;
      const d = await r.json();
      const items = d.items || [];
      bmList.innerHTML = `<option value="">书签（${items.length}）</option>` + items.map((bm) => {
        const label = `第 ${bm.pindex} 章 · 第 ${bm.image_index} 页` +
          (bm.note ? " · " + bm.note : "");
        return `<option value="${bm.id}" data-p="${bm.pindex}" data-i="${bm.image_index}">` +
          `${esc(label)}</option>`;
      }).join("");
      bmList.disabled = items.length === 0;
    } catch (_) {
      bmList.disabled = true;
    }
  }

  async function addBookmark() {
    try {
      const r = await fetch("/api/bookmarks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ aid, pindex, image_index: imageIndex, note: "" }),
      });
      if (!r.ok) {
        let detail = "HTTP " + r.status;
        try { const d = await r.json(); if (d.detail) detail += "：" + d.detail; } catch (_) {}
        throw new Error(detail);
      }
      setMem("已加书签：" + positionText());
      loadBookmarks();
    } catch (e) {
      setMem("加书签失败：" + e.message, true);
    }
  }

  /* -------------------------------------------------------------- viewer */

  function releaseUrls() {
    for (const url of objectUrls) URL.revokeObjectURL(url);
    objectUrls = [];
  }
  function cancelInFlight() {
    if (controller) { controller.abort(); controller = null; }
  }

  async function fetchPageBlob(p, i) {
    const url = "/api/page?aid=" + encodeURIComponent(aid) +
      "&pindex=" + encodeURIComponent(p) + "&i=" + encodeURIComponent(i);
    const r = await fetch(url, { signal: controller.signal });
    if (!r.ok) {
      let detail = "HTTP " + r.status + " " + r.statusText;
      try { const d = await r.json(); if (d.detail) detail += "：" + d.detail; } catch (_) {}
      throw new Error(detail);
    }
    return r.blob();
  }

  function prefetchFrom(p, i) {
    const chapter = chapterOf(p);
    if (!chapter) return;
    for (let k = 1; k <= PREFETCH_LIMIT; k += 1) {
      const at = i + k * step();
      if (at > chapter.page_count) break;
      // Same URL the reader would request next; the HTTP cache does the rest, so
      // the Image object itself can be dropped immediately.
      new Image().src = "/api/page?aid=" + encodeURIComponent(aid) +
        "&pindex=" + encodeURIComponent(p) + "&i=" + encodeURIComponent(at);
    }
  }

  async function loadPage(p, i, opts = {}) {
    const isRetry = !!opts.retry;
    const memoryNote = opts.memoryNote || "";
    cancelInFlight();
    if (prefs.layout === "scroll") {
      // One entry point: every caller (chapter select, jump, bookmark, keyboard)
      // keeps working in either mode without knowing which one is active.
      buildScroll(p, i, memoryNote);
      saveMemory(memoryNote);
      return;
    }
    const mySeq = ++seq;
    pindex = p;
    imageIndex = i;
    pageCount = (chapterOf(p) || {}).page_count || 0;
    const pages = visiblePages();
    syncControls();
    setBusy(true);
    showPlaceholder("正在载入页面…", positionText(), "");
    setStatus("正在载入 " + positionText() + (isRetry ? "（重试）" : "") + "…");

    controller = new AbortController();
    try {
      const blobs = await Promise.all(pages.map((at) => fetchPageBlob(p, at)));
      if (mySeq !== seq) return;                        // a newer turn superseded this one
      releaseUrls();
      slots.forEach((img, at) => {
        const blob = blobs[at];
        if (!blob) { img.hidden = true; img.removeAttribute("src"); return; }
        const url = URL.createObjectURL(blob);
        objectUrls.push(url);
        img.src = url;
        img.hidden = false;
        img.alt = `${book.title || aid} 第 ${p} 章 第 ${pages[at]} 页`;
      });
      hidePlaceholder();
      setStatus(positionText() + " · " + (book.title || "（标题待补）"));
      if (!memoryNote) setMem("正在记录记忆点…");
      saveMemory(memoryNote);
      prefetchFrom(p, pages[pages.length - 1]);
    } catch (e) {
      if (e.name === "AbortError" || mySeq !== seq) {
        setMem("已取消上一页加载。", false);
        return;
      }
      slots.forEach((img) => { img.hidden = true; });
      hidePlaceholder();
      showPlaceholder("这一页载入失败",
        e.message + " · 可重试，或换一章 / 返回书库。", "重试当前页");
      setStatus("页面载入失败：" + e.message, true);
    } finally {
      if (mySeq === seq) { controller = null; setBusy(false); syncControls(); }
    }
  }

  /* ------------------------------------------------------ continuous mode */

  function scrollImages() {
    return Array.from(scrollBox.querySelectorAll("img"));
  }

  function releaseScroll() {
    if (scrollObserver) { scrollObserver.disconnect(); scrollObserver = null; }
    for (const img of scrollImages()) img.removeAttribute("src");
    scrollBox.innerHTML = "";
    scrollBox.hidden = prefs.layout !== "scroll";
  }

  // The column holds one <img> per page with no src yet.  An observer loads each
  // one only when it comes near the viewport, so a 177-page chapter costs a few
  // requests instead of 177 — the same "never unpack the whole chapter" rule the
  // paged view keeps, expressed as lazy loading instead of one-at-a-time.
  //
  // Load the pages around ``center``.  This is the load contract, and the observer
  // is only an optimisation on top of it: IntersectionObserver does not fire in a
  // hidden tab, in an older browser, or while the page is not rendered, and a
  // reader that shows nothing in those cases is broken rather than thrifty.
  function ensureLoaded(center, before = 1, after = 2) {
    const imgs = scrollImages();
    const from = Math.max(1, center - before);
    const to = Math.min(imgs.length, center + after);
    for (let at = from; at <= to; at += 1) {
      const img = imgs[at - 1];
      if (!img || img.getAttribute("src") || img.dataset.loading) continue;
      loadScrollImage(img);
    }
  }

  function buildScroll(p, startAt, note) {
    releaseScroll();
    const chapter = chapterOf(p);
    if (!chapter) return;
    pindex = p;
    imageIndex = Math.min(Math.max(1, startAt), chapter.page_count);
    pageCount = chapter.page_count;
    anchorImg = null;
    scrollBox.hidden = false;
    scrollBox.innerHTML = Array.from({ length: pageCount }, (_, i) =>
      `<img alt="${esc((book && book.title) || aid)} 第 ${p} 章 第 ${i + 1} 页" data-i="${i + 1}">`,
    ).join("");
    syncControls();
    setBusy(false);
    hidePlaceholder();
    setScrollStatus();
    if (note) setMem(note);

    const order = scrollImages();
    // Right-to-left in a column does not reorder the images: page 1 still sits at
    // the top, because a vertical layout has no left/right axis to flip.  The
    // direction only matters here for the keyboard and swipe mapping below.

    scrollObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const img = entry.target;
        if (img.getAttribute("src")) continue;
        loadScrollImage(img);
      }
    }, { root: scrollBox, rootMargin: "600px 0px" });

    for (const img of order) scrollObserver.observe(img);

    // The first screenful is fetched eagerly: the observer only fires for what is
    // already near the viewport, and until a few images have real height the column
    // is short, so waiting for it would leave the reader on a blank screen.  This
    // is still bounded — at most EAGER_PAGES requests up front, never the chapter.
    for (let k = 0; k < EAGER_PAGES; k += 1) {
      const img = order[imageIndex - 1 + k];
      if (!img || img.getAttribute("src") || img.dataset.loading) break;
      loadScrollImage(img);
    }

    const first = order[imageIndex - 1];
    // The restore target: held by node identity until the first screenful settles.
    anchorImg = first || null;
    // Open the restore window *before* the first scroll, so no sample taken while
    // the column reflows can overwrite the page we were asked to restore.
    if (imageIndex > 1) restoreUntil = Date.now() + RESTORE_WINDOW_MS;
    if (first && scrollBox.scrollHeight > scrollBox.clientHeight) {
      // Position by element, not by a computed offset: the images have no height
      // yet, so arithmetic on page numbers would be a guess.
      first.scrollIntoView({ block: "start" });
    }
    // Hold the requested position while the first images decode: their heights
    // arrive a moment later, and sampling in between would report (and save) a
    // position a few pages off the memory point we were asked to restore.
    // Whatever happened above, the column must show the current page: re-run the
    // contract after the scroll so a browser that skipped the observer still paints.
    ensureLoaded(imageIndex, 1, PREFETCH_LIMIT);
    // The scroll may also have been clamped while every image was still a
    // placeholder, so pin the target once more after layout has settled.
    //
    // One correction is not enough: setting ``src`` removes the placeholder's
    // ``min-height`` immediately, but the real height only arrives at decode, so
    // the column collapses and regrows underneath the scroll position — which is
    // how a restored page 120 came back as 123.  Re-pin a few times instead of
    // guessing a single delay.
    if (imageIndex > 1) {
      for (const delay of [80, 240, 600, 1200]) {
        setTimeout(() => reanchorScroll(), delay);
      }
    }
    markScrollPosition();
  }

  async function loadScrollImage(img) {
    const at = parseInt(img.dataset.i, 10);
    const chapter = chapterOf(pindex);
    if (!chapter || !Number.isFinite(at)) return;
    // The URL is pointed at directly: no blob bookkeeping, no object URL to revoke
    // when the mode switches, and the HTTP cache serves a revisit for free.
    const url = "/api/page?aid=" + encodeURIComponent(aid) +
      "&pindex=" + encodeURIComponent(pindex) + "&i=" + encodeURIComponent(at);
    img.dataset.loading = String(Date.now());
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), SCROLL_LOAD_TIMEOUT_MS);
    try {
      const r = await fetch(url, { signal: ctrl.signal });
      if (!r.ok) throw new Error("HTTP " + r.status);
      // Wait for the fetch before revealing the src, so a failure is reported here
      // rather than showing the browser's broken-image glyph in a long column.
      //
      // The height only changes when the image *decodes*, which is after this
      // assignment, so the correction has to hang off the load event: a page above
      // the current one growing from a placeholder into a real image pushes
      // everything below it down, and without re-anchoring the page you were
      // reading slides out from under you — a restored position lands pages early.
      //
      // ``at <= imageIndex`` rather than ``at < imageIndex``: the anchor's *own*
      // height also changes when it decodes, and every neighbouring page that
      // resolves while you sit still is a chance for the column to shift.  Anchoring
      // on the anchor's top edge keeps the reading position fixed in both cases.
      if (at <= imageIndex) {
        img.addEventListener("load", () => reanchorScroll(), { once: true });
      }
      // A page that failed once and now loads must say so: leaving the failure
      // line up after a successful retry makes a working reader look broken.
      if (img.dataset.retryAt && at === imageIndex) {
        img.addEventListener("load", () => {
          delete img.dataset.retryAt;
          setMem("第 " + at + " 页已重新载入。", false);
        }, { once: true });
      }
      // The failure message lived in ``alt``; clear it so a successful page is not
      // described as broken to a screen reader.
      img.removeAttribute("alt");
      img.src = url;
    } catch (e) {
      // A timeout is a failure like any other: without this the page would keep
      // its "loading" marker forever and stay blank with nothing to click.
      const message = e.name === "AbortError"
        ? "载入超时（" + Math.round(SCROLL_LOAD_TIMEOUT_MS / 1000) + " 秒）"
        : e.message;
      img.dataset.error = message;
      img.alt = "这一页载入失败：" + message;
      // A failed page shows a retry affordance instead of a silent gap; the
      // observer will not fire again for it, so the click is the way back.
      img.style.minHeight = "120px";
      const retry = document.createElement("button");
      retry.type = "button";
      retry.textContent = "重试第 " + at + " 页";
      retry.addEventListener("click", () => {
        retry.remove();
        img.removeAttribute("src");
        img.style.minHeight = "";
        img.removeAttribute("alt");
        delete img.dataset.error;
        // Remember that this page is being retried: a successful load should
        // replace the failure line instead of leaving a stale complaint on screen.
        img.dataset.retryAt = String(at);
        loadScrollImage(img);
      });
      img.after(retry);
      // Only the page you are actually looking at earns the status line: in a
      // column many pages can fail at once, and shouting about page 12 while you
      // sit at page 90 would bury the position read-out under noise.
      if (at === imageIndex) {
        setMem("第 " + at + " 页载入失败：" + message + "（可点该页下方按钮重试）", true);
      }
    } finally {
      clearTimeout(timer);
      delete img.dataset.loading;
    }
  }

  // The status line is the only continuous read-out of where you are in a column
  // that has no page boundaries, so every position change goes through here.
  function setScrollStatus() {
    setStatus(positionText() + " · " + ((book && book.title) || "（标题待补）") + "（连续滚动）");
  }

  // Keep the page you are reading pinned while its neighbours finish loading.
  // Called whenever a page at or above the current one turns from a placeholder
  // into a real image.
  function reanchorScroll() {
    if (prefs.layout !== "scroll") return;
    const img = anchorImg && scrollBox.contains(anchorImg)
      ? anchorImg
      : scrollBox.querySelector('img[data-i="' + imageIndex + '"]');
    if (!img) return;
    // If the reader has scrolled well away from the anchor, the restore is over:
    // yanking them back on a late decode would be worse than a slightly loose
    // position.  The gesture handlers clear the anchor outright; this covers the
    // keyboard and programmatic cases too.
    const drift = img.getBoundingClientRect().top - scrollBox.getBoundingClientRect().top;
    if (Math.abs(drift) > scrollBox.clientHeight * 2) { anchorImg = null; return; }
    // Suppress position sampling while the correction lands: the scroll this
    // triggers arrives asynchronously, so a plain boolean would be reset before
    // the event is delivered and the sampler would rewrite the index we are
    // deliberately holding.  A real user gesture clears this at once.
    reanchorUntil = Date.now() + REANCHOR_HOLD_MS;
    img.scrollIntoView({ block: "start" });
  }

  // Which page is "current" in the column: the topmost image still on screen.
  function markScrollPosition() {
    if (Date.now() < reanchorUntil || Date.now() < restoreUntil) return;
    const imgs = scrollImages();
    if (!imgs.length) return;
    const boxTop = scrollBox.getBoundingClientRect().top;
    let current = 1;
    for (const img of imgs) {
      const rect = img.getBoundingClientRect();
      if (rect.bottom - boxTop > 8) { current = parseInt(img.dataset.i, 10) || 1; break; }
    }
    if (current === imageIndex) return;
    imageIndex = current;
    syncControls();
    setScrollStatus();
  }

  function onScroll() {
    if (prefs.layout !== "scroll") return;
    markScrollPosition();
    // Belt and braces: the observer does the steady-state loading, but it is
    // inert in a hidden tab and after a programmatic scroll in some engines, so
    // every scroll also tops up the window around the current page.
    ensureLoaded(imageIndex, 1, PREFETCH_LIMIT);
    // Throttled write: scrolling fires constantly and every write is a DB row
    // update.  The plan asks for at most one write every two seconds.
    scrollDirty = true;
    if (scrollSaveTimer) return;
    scrollSaveTimer = setTimeout(() => {
      scrollSaveTimer = null;
      if (!scrollDirty) return;
      scrollDirty = false;
      saveMemory();
    }, SCROLL_SAVE_MS);
  }

  function flushScrollMemory() {
    if (!scrollDirty) return;
    scrollDirty = false;
    if (scrollSaveTimer) { clearTimeout(scrollSaveTimer); scrollSaveTimer = null; }
    saveMemory();
  }



  /* ---------------------------------------------------------- navigation */

  function scrollToPage(at) {
    const img = scrollBox.querySelector('img[data-i="' + at + '"]');
    if (!img) return false;
    // A jump is a decision: stop holding the old restore target.
    anchorImg = null;
    reanchorUntil = 0;
    restoreUntil = 0;
    // Jumping is the case where the observer is least likely to help: the target
    // may be far outside the rootMargin, and a programmatic scroll need not fire
    // it at all.  Load around the destination explicitly.
    ensureLoaded(at, 1, PREFETCH_LIMIT);
    img.scrollIntoView({ block: "start" });
    imageIndex = at;
    syncControls();
    setScrollStatus();
    return true;
  }

  function scrollFlip(delta) {
    // One "page turn" in the column is roughly one screenful.  Using the real
    // client height keeps the step honest whatever the fit mode is.
    const span = Math.max(200, Math.round(scrollBox.clientHeight * 0.92));
    scrollBox.scrollBy({ top: delta > 0 ? span : -span, behavior: "auto" });
  }

  function goto(delta) {
    if (prefs.layout === "scroll") {
      const atEnd = scrollBox.scrollTop + scrollBox.clientHeight >= scrollBox.scrollHeight - 8;
      const atTop = scrollBox.scrollTop <= 8;
      if (delta > 0 && atEnd && hasNextChapter()) {
        const at = chapters.findIndex((c) => c.pindex === pindex);
        gotoChapter(chapters[at + 1].pindex, 1, "已进入下一章。");
        return;
      }
      if (delta < 0 && atTop && hasPrevChapter()) {
        const at = chapters.findIndex((c) => c.pindex === pindex);
        const prevIndex = chapters[at - 1];
        gotoChapter(prevIndex.pindex, prevIndex.page_count, "已回到上一章。");
        return;
      }
      scrollFlip(delta);
      return;
    }
    if (delta > 0) {
      const target = imageIndex + step();
      if (target <= pageCount) { loadPage(pindex, target); return; }
      if (hasNextChapter()) {
        const at = chapters.findIndex((c) => c.pindex === pindex);
        gotoChapter(chapters[at + 1].pindex, 1, "已进入下一章。");
      }
      return;
    }
    if (imageIndex > 1) { loadPage(pindex, Math.max(1, imageIndex - step())); return; }
    if (hasPrevChapter()) {
      const at = chapters.findIndex((c) => c.pindex === pindex);
      const prevIndex = chapters[at - 1];
      // Land on the last spread of the previous chapter, not on a half spread.
      const size = step();
      const span = Math.max(1, Math.ceil(prevIndex.page_count / size));
      const first = (span - 1) * size + 1;
      gotoChapter(prevIndex.pindex, Math.min(first, prevIndex.page_count), "已回到上一章。");
    }
  }

  function gotoChapter(p, i, note) {
    const chapter = chapterOf(p);
    if (!chapter) return;
    const clamped = Math.min(Math.max(1, i), chapter.page_count);
    loadPage(p, clamped, note ? { memoryNote: note } : {});
  }

  function jumpTo(value) {
    const v = parseInt(value, 10);
    if (!Number.isFinite(v) || v < 1 || v > pageCount) {
      setStatus(`请输入 1 – ${pageCount} 之间的页码。`, true);
      jump.focus();
      return;
    }
    // In the column the page already exists as an element, so jumping is a scroll
    // rather than a rebuild — the loaded images stay loaded.
    if (prefs.layout === "scroll" && scrollToPage(v)) {
      setMem("已跳到第 " + v + " 页。");
      return;
    }
    loadPage(pindex, v);
  }

  /* ------------------------------------------------------------ keyboard */

  function forwardKey() { return prefs.direction === "rtl" ? "ArrowLeft" : "ArrowRight"; }
  function backwardKey() { return prefs.direction === "rtl" ? "ArrowRight" : "ArrowLeft"; }

  function onKey(e) {
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA")) return;
    if (e.key === forwardKey()) { e.preventDefault(); goto(1); return; }
    if (e.key === backwardKey()) { e.preventDefault(); goto(-1); return; }
    if (e.key === " ") { e.preventDefault(); goto(1); return; }
    if (e.key === "Home") { e.preventDefault(); loadPage(pindex, 1); return; }
    if (e.key === "End") { e.preventDefault(); loadPage(pindex, Math.max(1, pageCount)); return; }
    if (e.key === "Escape") {
      // Escape leaves fullscreen first, so the key never teleports the reader
      // out of the book while the browser is still occupying the screen.
      if (document.fullscreenElement) { document.exitFullscreen().catch(() => {}); return; }
      location.href = "/";
    }
  }

  /* -------------------------------------------------------------- events */

  function wire() {
    prev.addEventListener("click", () => goto(-1));
    next.addEventListener("click", () => goto(1));
    chapterSel.addEventListener("change", () => gotoChapter(parseInt(chapterSel.value, 10), 1));
    prevCh.addEventListener("click", () => {
      const at = chapters.findIndex((c) => c.pindex === pindex);
      if (at > 0) gotoChapter(chapters[at - 1].pindex, 1);
    });
    nextCh.addEventListener("click", () => {
      const at = chapters.findIndex((c) => c.pindex === pindex);
      if (at >= 0 && at < chapters.length - 1) gotoChapter(chapters[at + 1].pindex, 1);
    });

    go.addEventListener("click", () => jumpTo(jump.value));
    jump.addEventListener("keydown", (e) => { if (e.key === "Enter") jumpTo(jump.value); });

    progress.addEventListener("input", () => {
      // Live label while dragging; the page is only fetched when the drag ends,
      // otherwise a single sweep would queue dozens of abandoned requests.
      const v = parseInt(progress.value, 10);
      if (Number.isFinite(v)) setStatus(`第 ${pindex} 章 · 第 ${v} / ${pageCount} 页（松手跳转）`);
    });
    progress.addEventListener("change", () => {
      const v = parseInt(progress.value, 10);
      if (Number.isFinite(v) && v !== imageIndex) loadPage(pindex, v);
    });

    dirSel.addEventListener("change", () => {
      prefs.direction = dirSel.value;
      applyPrefs();
      savePref("direction", prefs.direction);
    });
    layoutSel.addEventListener("change", () => {
      const wasScroll = prefs.layout === "scroll";
      prefs.layout = layoutSel.value;
      applyPrefs();
      savePref("layout", prefs.layout);
      // Both directions keep the page the reader is looking at: leaving the column
      // must not dump them at page one, and entering it must not either.
      loadPage(pindex, imageIndex);
      if (wasScroll && prefs.layout !== "scroll") releaseScroll();
    });
    fitSel.addEventListener("change", () => {
      prefs.fit = fitSel.value;
      applyPrefs();
      savePref("fit", prefs.fit);
    });

    fsBtn.addEventListener("click", toggleFullscreen);
    document.addEventListener("fullscreenchange", () => {
      fsBtn.setAttribute("aria-pressed", document.fullscreenElement ? "true" : "false");
    });

    bmAdd.addEventListener("click", addBookmark);
    bmList.addEventListener("change", () => {
      const opt = bmList.options[bmList.selectedIndex];
      if (!opt || !opt.value) return;
      const p = parseInt(opt.dataset.p, 10);
      const i = parseInt(opt.dataset.i, 10);
      bmList.selectedIndex = 0;
      gotoChapter(p, i, "已跳到书签位置。");
    });

    document.addEventListener("keydown", onKey);

    scrollBox.addEventListener("scroll", onScroll, { passive: true });
    // A deliberate gesture ends the restore window immediately: whatever the
    // decoder is still doing, the user has chosen where to look, and holding the
    // old anchor would fight them for up to 400 ms.
    const endReanchor = () => { reanchorUntil = 0; restoreUntil = 0; anchorImg = null; };
    scrollBox.addEventListener("wheel", endReanchor, { passive: true });
    scrollBox.addEventListener("touchstart", endReanchor, { passive: true });
    scrollBox.addEventListener("pointerdown", endReanchor, { passive: true });
    // Leaving the page must not lose up to two seconds of reading position.
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") flushScrollMemory();
    });
    window.addEventListener("pagehide", flushScrollMemory);

    viewer.addEventListener("touchstart", (e) => {
      if (e.touches.length !== 1) { touchX = null; return; }
      touchX = e.touches[0].clientX;
      touchY = e.touches[0].clientY;
    }, { passive: true });
    viewer.addEventListener("touchend", (e) => {
      if (touchX === null || !e.changedTouches.length) return;
      const dx = e.changedTouches[0].clientX - touchX;
      const dy = e.changedTouches[0].clientY - touchY;
      touchX = null;
      if (Math.abs(dx) <= SWIPE_MIN || Math.abs(dx) <= Math.abs(dy) * 1.4) return;
      // A leftward drag means "forward" only when the book reads left to right;
      // in RTL the same gesture has to go back, or swipes fight the arrows.
      const forward = prefs.direction === "rtl" ? dx > 0 : dx < 0;
      goto(forward ? 1 : -1);
    }, { passive: true });

    slots.forEach((img) => {
      img.addEventListener("error", () => {
        img.hidden = true;
        showPlaceholder("图片显示失败", "浏览器无法解码这一页。可重试，或换一章。", "重试当前页");
        setStatus("图片显示失败。", true);
      });
    });
  }

  async function toggleFullscreen() {
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
        prefs.fullscreen = false;
        savePref("fullscreen", false);
      } else {
        await document.documentElement.requestFullscreen();
        prefs.fullscreen = true;
        savePref("fullscreen", true);
      }
    } catch (e) {
      // Sandboxed frames and headless tabs refuse fullscreen.  Say so and keep the
      // preference untouched: recording "on" for a mode that never engaged would
      // make the next visit try again for no reason.
      setMem("这个浏览器不允许全屏：" + e.message + "（其余功能不受影响）", true);
    }
  }

  /* ---------------------------------------------------------------- init */

  async function init() {
    if (!/^\d{1,7}$/.test(aid)) {
      slots.forEach((img) => { img.hidden = true; });
      setBusy(false);
      hidePlaceholder();
      showPlaceholder("书号无效", "请从书库打开阅读器（/read?aid=车号）。", "");
      setStatus("书号无效，无法打开阅读器。", true);
      return;
    }
    $("book-title").textContent = "载入中…";
    setStatus("正在载入书本信息…");

    let memory = null;
    try {
      const [br, mr] = await Promise.all([
        fetch("/api/books/" + encodeURIComponent(aid)),
        fetch("/api/memory/" + encodeURIComponent(aid)),
      ]);
      if (!br.ok) throw new Error("书本信息 HTTP " + br.status);
      book = await br.json();
      if (mr.ok) memory = await mr.json();
    } catch (e) {
      slots.forEach((img) => { img.hidden = true; });
      setBusy(false);
      placeholder.hidden = false;
      placeholder.innerHTML = `<strong>书本信息载入失败</strong><span>${esc(e.message)}</span>` +
        `<button id="retry" type="button">重试</button>`;
      $("retry").addEventListener("click", init);
      setStatus("书本信息载入失败：" + e.message, true);
      return;
    }

    $("book-title").textContent = book.title || `#${aid}（标题待补）`;
    chapters = book.chapters || [];
    chapterSel.innerHTML = chapters.map((c) =>
      `<option value="${c.pindex}">第 ${c.pindex} 章 · ${c.page_count} 页</option>`).join("");
    if (!chapters.length) {
      slots.forEach((img) => { img.hidden = true; });
      setBusy(false);
      hidePlaceholder();
      showPlaceholder("这本书没有章节", "返回书库，或先运行 jmlib index 建立索引。", "");
      setStatus("没有章节记录。", true);
      return;
    }

    await loadPrefs();
    applyPrefs();
    if (prefs.fullscreen) {
      // Honor a stored preference once, and only as a best-effort: browsers
      // require a user gesture, so a refusal here is normal, not an error.
      document.documentElement.requestFullscreen()
        .then(() => fsBtn.setAttribute("aria-pressed", "true"))
        .catch(() => {});
    }

    let p = chapters[0].pindex, i = 1, resumedNote = "", staleNote = "";
    if (Number.isFinite(forceP) && chapterOf(forceP)) {
      p = forceP;
      const target = chapterOf(p);
      if (Number.isFinite(forceI) && forceI >= 1 && forceI <= target.page_count) i = forceI;
    } else if (memory && memory.has_memory) {
      const c = chapterOf(memory.pindex);
      if (c && memory.image_index >= 1 && memory.image_index <= c.page_count) {
        p = memory.pindex;
        i = memory.image_index;
        resumedNote = "已从记忆点续读：第 " + p + " 章 · 第 " + i + " 页";
      } else {
        staleNote = "原记忆点已失效（章节或页不存在），已从第一章第 1 页开始。";
      }
    }
    setMem(resumedNote || staleNote || "从头开始阅读。", !!staleNote);
    loadBookmarks();
    loadPage(p, i, resumedNote ? { memoryNote: resumedNote } : {});
  }

  wire();
  init();
})();
