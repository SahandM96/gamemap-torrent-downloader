(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const LS = { lastDir: "td.lastDir" };

  // ------------------------------------------------------------------ api
  const T = window.__TAURI__ || {};
  const invoke = (cmd, args) => T.core.invoke(cmd, args);
  const errMsg = (err) => (typeof err === "string" ? err : (err && err.message) || String(err));

  async function api(path, opts = {}) {
    try {
      const body = await invoke("desk_api", {
        req: { path, method: opts.method || "GET", body: opts.body || {} },
      });
      if (body && body.success === false) throw new Error(body.error || "خطا");
      return body ? body.data : null;
    } catch (err) {
      throw new Error(errMsg(err));
    }
  }
  const post = (path, payload) => api(path, { method: "POST", body: payload || {} });
  const openUrl = (url) => invoke("open_url", { url });

  let osPaths = { downloads: "", home: "", os: "" };

  /** OS-native folder dialog; returns absolute path or "". */
  async function pickFolder(start, title) {
    const picked = await invoke("pick_folder", {
      start: start || osPaths.downloads || osPaths.home || null,
      title: title || "انتخاب پوشه",
    });
    return picked || "";
  }

  /** OS-native yes/no dialog (falls back to window.confirm outside Tauri). */
  async function askNative(message, opts = {}) {
    const d = T.dialog;
    if (d && typeof d.ask === "function") {
      return d.ask(message, {
        title: opts.title || "GameMap Torrent Desk",
        kind: opts.kind || "warning",
        okLabel: opts.okLabel || "بله",
        cancelLabel: opts.cancelLabel || "خیر",
      });
    }
    return window.confirm(message);
  }

  // ------------------------------------------------------------ feedback
  let toastTimer = null;
  function toast(message, isError = false) {
    const el = $("#toast");
    el.textContent = message;
    el.classList.toggle("err", !!isError);
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, isError ? 6500 : 3200);
  }

  let busyDepth = 0;
  async function withBusy(text, fn) {
    busyDepth += 1;
    $("#busyText").textContent = text;
    $("#busy").hidden = false;
    try {
      return await fn();
    } finally {
      busyDepth -= 1;
      if (busyDepth <= 0) { busyDepth = 0; $("#busy").hidden = true; }
    }
  }

  function setBtnBusy(btn, busy, busyLabel) {
    if (!btn) return;
    btn.disabled = busy;
    btn.classList.toggle("is-busy", busy);
    const label = btn.querySelector(".btn-label");
    const spin = btn.querySelector(".btn-spin");
    if (label) {
      if (!btn.dataset.idle) btn.dataset.idle = label.textContent;
      label.textContent = busy ? (busyLabel || btn.dataset.idle) : btn.dataset.idle;
    }
    if (spin) spin.hidden = !busy;
  }

  function fmtBytes(n) {
    n = Number(n) || 0;
    if (!n) return "-";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
    return `${i === 0 ? n : n.toFixed(2)} ${units[i]}`;
  }

  function fmtEta(sec) {
    sec = Number(sec) || 0;
    if (!sec) return "-";
    if (sec < 60) return `${sec} ثانیه`;
    if (sec < 3600) return `${Math.floor(sec / 60)} دقیقه`;
    return `${Math.floor(sec / 3600)} ساعت و ${Math.floor((sec % 3600) / 60)} دقیقه`;
  }

  const esc = (value) => String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

  // ------------------------------------------------------------------ tabs
  let pollTimer = null;
  function showTab(name) {
    $$(".tab").forEach((btn) => btn.classList.toggle("active", btn.dataset.tab === name));
    $$(".panel").forEach((panel) => panel.classList.toggle("active", panel.id === `tab-${name}`));
    clearInterval(pollTimer);
    pollTimer = null;
    if (name === "downloads") { loadDownloads(true); pollTimer = setInterval(() => loadDownloads(false), 1800); }
    if (name === "settings") loadSettings();
    if (name === "partials" && !$("#partialList").dataset.scanned) scanPartials();
    if (name === "support") loadSupport();
  }
  $$(".tab").forEach((btn) => btn.addEventListener("click", () => showTab(btn.dataset.tab)));

  // --------------------------------------------------------------- sources
  // Catalog = built-in defaults (always present). The search list = catalog minus prefs.sources_disabled.
  let catalog = [];
  let disabled = new Set();

  const enabledSources = () => catalog.filter((s) => !disabled.has(s.id));
  const selectedSources = () => $$("#sourceChips .chip.on").map((chip) => chip.dataset.id);
  const blockedHint = (s) => /مسدود|Cloudflare/i.test(s.note || "");

  async function saveDisabled() {
    await post("/api/settings", { sources_disabled: Array.from(disabled) });
  }

  async function setInList(id, inList) {
    if (!inList && enabledSources().length <= 1 && !disabled.has(id)) {
      toast("حداقل یک سایت باید در لیست جست‌وجو بماند", true);
      return false;
    }
    if (inList) disabled.delete(id); else disabled.add(id);
    try {
      await saveDisabled();
    } catch (err) {
      if (inList) disabled.add(id); else disabled.delete(id);
      toast(err.message, true);
      return false;
    }
    renderSourcesEverywhere();
    return true;
  }

  function renderSourceChips() {
    const list = enabledSources();
    $("#sourceChips").innerHTML = list.length
      ? list.map((s) => `
        <span class="chip on" data-id="${esc(s.id)}" title="${esc(s.note || "")}" tabindex="0">${esc(s.label)}</span>`).join("")
      : '<span class="muted tiny">هیچ سایتی در لیست نیست — «مدیریت لیست…» را بزنید.</span>';
    // chip click = include/exclude for *this* search only (list membership lives in the catalog)
    $$("#sourceChips .chip").forEach((chip) => {
      const toggle = () => {
        chip.classList.toggle("on");
        $("#resCount").textContent = `${selectedSources().length} سایت برای این جست‌وجو`;
      };
      chip.addEventListener("click", toggle);
      chip.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); toggle(); } });
    });
    $("#resCount").textContent = `${list.length} سایت در لیست جست‌وجو`;
  }

  function catalogHtml() {
    return catalog.map((s) => {
      const inList = !disabled.has(s.id);
      return `
        <div class="source-row ${inList ? "in-list" : ""}">
          <div class="meta-col">
            <div class="label">${esc(s.label)} <span class="group">${esc(s.group || "")}</span></div>
            <div class="note">${esc(s.note || s.homepage || "")}</div>
          </div>
          <button type="button" class="${inList ? "ghost" : "primary"} sm src-toggle" data-id="${esc(s.id)}" data-in="${inList ? "1" : "0"}">
            ${inList ? "حذف از لیست" : "افزودن به لیست"}
          </button>
        </div>`;
    }).join("");
  }

  function bindCatalog(root) {
    $$(".src-toggle", root).forEach((btn) => {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        await setInList(btn.dataset.id, btn.dataset.in !== "1");
        btn.disabled = false;
      });
    });
  }

  function renderSourcesEverywhere() {
    renderSourceChips();
    for (const id of ["sourceCatalog", "sourceCatalogDlg"]) {
      const root = document.getElementById(id);
      if (!root) continue;
      root.innerHTML = catalogHtml();
      bindCatalog(root);
    }
  }

  $("#manageSourcesBtn").addEventListener("click", () => {
    renderSourcesEverywhere();
    $("#sourcesDlg").showModal();
  });
  $("#sourcesDlgClose").addEventListener("click", () => $("#sourcesDlg").close());
  $("#enableAllSources").addEventListener("click", async () => {
    const prev = new Set(disabled);
    disabled.clear();
    try { await saveDisabled(); renderSourcesEverywhere(); toast("همهٔ سایت‌های پیش‌فرض به لیست برگشتند"); }
    catch (err) { disabled = prev; toast(err.message, true); }
  });
  $("#disableBlockedHint").addEventListener("click", async () => {
    const prev = new Set(disabled);
    const keep = catalog.filter((s) => !blockedHint(s));
    if (!keep.length) return toast("همهٔ سایت‌ها هشدار مسدودی دارند", true);
    disabled = new Set(catalog.filter(blockedHint).map((s) => s.id));
    try { await saveDisabled(); renderSourcesEverywhere(); toast(`${keep.length} سایت بدون هشدار در لیست ماند`); }
    catch (err) { disabled = prev; toast(err.message, true); }
  });

  function renderStatus(list, note) {
    const byId = {};
    list.forEach((item) => { byId[item.id] = item; });
    $("#sourceStatus").innerHTML = catalog.filter((s) => byId[s.id]).map((s) => {
      const st = byId[s.id];
      const cls = st.ok ? "ok" : "bad";
      let tail;
      if (st.ok) {
        tail = st.count != null ? `${st.count} نتیجه` : "در دسترس";
      } else {
        const raw = String(st.error || "خطا");
        if (/تایم‌?اوت|timeout/i.test(raw)) tail = "تایم‌اوت";
        else if (/HTTP\s*403|Cloudflare|چلنج/i.test(raw)) tail = "بلاک/Cloudflare";
        else if (/HTTP\s*5\d\d/i.test(raw)) tail = "خطای سرور";
        else if (/DNS|فیلتر|پروکسی/i.test(raw)) tail = "نیاز به پروکسی";
        else tail = raw.slice(0, 40);
      }
      return `<span class="chip ${cls}" title="${esc(st.error || st.url || "")}">
                <span class="dot"></span>${esc(s.label)} · <span class="chip-err">${esc(tail)}</span></span>`;
    }).join("");
    if (note) $("#probeInfo").textContent = note;
  }

  // ---------------------------------------------------------------- search
  let results = [];

  $("#searchForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const query = $("#q").value.trim();
    if (query.length < 2) return toast("عبارت جست‌وجو خیلی کوتاه است", true);
    const ids = selectedSources();
    if (!ids.length) return toast("حداقل یک سایت را برای جست‌وجو انتخاب کنید", true);
    const btn = $("#searchBtn");
    setBtnBusy(btn, true, "در حال جست‌وجو…");
    $("#searchLoading").hidden = false;
    try {
      const data = await post("/api/search", {
        query, sources: ids, limit: Number($("#limit").value) || 20,
      });
      results = data.results || [];
      renderStatus(data.sources || []);
      renderResults(data.query);
      if (!results.length) {
        const fails = (data.sources || []).filter((s) => !s.ok);
        const proxyish = fails.filter((s) => /پروکسی|proxy|اتصال|شبکه|تایم‌?اوت/i.test(s.error || ""));
        if (fails.length && proxyish.length === fails.length) {
          toast("همهٔ سایت‌ها قطع شدند — پروکسی را با «تست» چک کنید یا ✕ کنید", true);
        }
      }
    } catch (err) {
      toast(`جست‌وجو ناموفق: ${err.message}`, true);
    } finally {
      $("#searchLoading").hidden = true;
      setBtnBusy(btn, false);
    }
  });

  function labelOf(id) {
    const hit = catalog.find((s) => s.id === id);
    return hit ? hit.label : id;
  }

  function renderResults(query) {
    const body = $("#resultBody");
    if (!results.length) {
      body.innerHTML = `<div class="empty">برای «${esc(query)}» نتیجه‌ای نبود.
        وضعیت سایت‌ها را ببینید یا در تنظیمات پروکسی بگذارید.</div>`;
      $("#resCount").textContent = "۰ نتیجه";
      $("#bulkBtn").disabled = true;
      return;
    }
    const sort = ($("#sortResults") && $("#sortResults").value) || "seeds";
    const ordered = results.map((r, i) => ({ r, i })).sort((a, b) => {
      if (sort === "size") return (b.r.size_bytes || 0) - (a.r.size_bytes || 0);
      if (sort === "name") return String(a.r.name || "").localeCompare(String(b.r.name || ""), "en");
      return (b.r.seeds || 0) - (a.r.seeds || 0);
    });
    body.innerHTML = ordered.map(({ r, i }, pos) => {
      const seeds = r.seeds == null ? "—" : String(r.seeds);
      const leech = r.leechers == null ? "—" : String(r.leechers);
      const size = fmtBytes(r.size_bytes);
      const src = labelOf(r.source);
      return `
      <div class="result-row" data-i="${i}" style="--i:${Math.min(pos, 20)}">
        <input type="checkbox" class="rowchk" data-i="${i}" aria-label="انتخاب">
        <div class="result-main">
          <div class="result-title">
            ${r.page_url ? `<a href="#" data-url="${esc(r.page_url)}" class="ext">${esc(r.name)}</a>` : esc(r.name)}
          </div>
          <div class="result-sub">
            <span class="stat seeds" title="سیچر">⬆ ${esc(seeds)}</span>
            <span class="stat leech" title="لینچر">⬇ ${esc(leech)}</span>
            <span class="stat" title="حجم">${esc(size)}</span>
            <span class="stat">${esc(src)}</span>
            ${r.category ? `<span class="badge-cat">${esc(r.category)}</span>` : ""}
          </div>
        </div>
        <div class="result-src hide-sm">${esc(src)}</div>
        <div class="result-cell">${esc(size)}</div>
        <div class="result-cell seeds" title="سیچر">${esc(seeds)}</div>
        <div class="result-cell leech hide-sm" title="لینچر">${esc(leech)}</div>
        <button class="primary sm dl-one" data-i="${i}">دانلود…</button>
      </div>`;
    }).join("");
    $("#resCount").textContent = `${results.length} نتیجه از ${new Set(results.map((r) => r.source)).size} سایت`;
    $$(".dl-one", body).forEach((btn) => btn.addEventListener("click", () => {
      openDest([payloadOf(results[Number(btn.dataset.i)])]);
    }));
    $$("a.ext", body).forEach((a) => a.addEventListener("click", (ev) => {
      ev.preventDefault();
      openUrl(a.dataset.url).catch((e) => toast(errMsg(e), true));
    }));
    $$(".rowchk", body).forEach((chk) => chk.addEventListener("change", syncBulk));
    syncBulk();
  }

  $("#sortResults")?.addEventListener("change", () => {
    if (results.length) renderResults($("#q").value.trim() || "");
  });

  function syncBulk() {
    const total = $$(".rowchk").length;
    const picked = $$(".rowchk").filter((c) => c.checked).length;
    $("#bulkBtn").disabled = picked === 0;
    $("#bulkBtn").textContent = picked ? `دانلود ${picked} مورد…` : "دانلود انتخاب‌شده‌ها…";
    $("#selAll").checked = total > 0 && picked === total;
  }

  $("#selAll").addEventListener("change", (ev) => {
    $$(".rowchk").forEach((chk) => { chk.checked = ev.target.checked; });
    syncBulk();
  });

  $("#bulkBtn").addEventListener("click", () => {
    const picked = $$(".rowchk").filter((c) => c.checked)
      .map((c) => payloadOf(results[Number(c.dataset.i)]));
    if (picked.length) openDest(picked);
  });

  const payloadOf = (r) => ({
    name: r.name, magnet: r.magnet || "", torrent_url: r.torrent_url || "",
    source: r.source, source_label: labelOf(r.source),
    page_url: r.page_url || "", size_bytes: r.size_bytes || 0,
  });

  async function probeSources() {
    const btns = [$("#probeBtn"), $("#probeBtn2")];
    btns.forEach((b) => { if (b) b.disabled = true; });
    $("#probeInfo").textContent = "در حال بررسی سایت‌ها…";
    try {
      const data = await withBusy("در حال بررسی دسترسی سایت‌ها…", () => post("/api/sources/probe", {}));
      renderStatus(data.sources, `بررسی شد: ${new Date(data.probed_at * 1000).toLocaleTimeString("fa-IR")}`);
      $("#probeList").innerHTML = $("#sourceStatus").innerHTML;
      toast("وضعیت سایت‌ها به‌روز شد");
    } catch (err) {
      toast(`بررسی ناموفق: ${err.message}`, true);
    } finally {
      btns.forEach((b) => { if (b) b.disabled = false; });
    }
  }
  $("#probeBtn").addEventListener("click", probeSources);
  $("#probeBtn2").addEventListener("click", probeSources);

  // ---------------------------------------------------------------- proxy
  function showProxy(proxy) {
    const value = (proxy || "").trim();
    const quick = $("#quickProxy");
    const settings = $("#setProxy");
    const state = $("#quickProxyState");
    if (quick) quick.value = value;
    if (settings) settings.value = value;
    if (!state) return;
    state.classList.toggle("on", !!value);
    if (!value) state.textContent = "خاموش — اتصال مستقیم";
    else if (/^https?:\/\//i.test(value)) state.textContent = "فعال برای جست‌وجو و aria2";
    else state.textContent = "فعال برای جست‌وجو (aria2 فقط پروکسی HTTP می‌پذیرد)";
  }

  async function saveProxy(value) {
    const btns = [$("#quickProxySave"), $("#quickProxyClear"), $("#quickProxyTest")].filter(Boolean);
    btns.forEach((b) => { b.disabled = true; });
    try {
      if (value) {
        await withBusy("در حال تست پروکسی…", () => post("/api/proxy/test", { proxy: value }));
      }
      const data = await post("/api/settings", { proxy: value });
      showProxy((data.prefs || {}).proxy);
      if (catalog.length) renderSourcesEverywhere();
      toast(value ? "پروکسی ذخیره و تأیید شد" : "پروکسی حذف شد — اتصال مستقیم");
    } catch (err) {
      toast(err.message, true);
    } finally {
      btns.forEach((b) => { b.disabled = false; });
    }
  }

  async function suggestProxy() {
    try {
      const data = await api("/api/proxy/detect");
      const list = (data && data.proxies) || [];
      if (!list.length) return toast("پروکسی محلیِ باز پیدا نشد (xray/v2ray را چک کنید)", true);
      const pick = list[0];
      $("#quickProxy").value = pick;
      toast(`پیدا شد: ${pick} — ذخیره را بزن`);
    } catch (err) {
      toast(err.message, true);
    }
  }

  $("#quickProxySave")?.addEventListener("click", () => saveProxy($("#quickProxy").value.trim()));
  $("#quickProxyClear")?.addEventListener("click", () => saveProxy(""));
  $("#quickProxy")?.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); saveProxy($("#quickProxy").value.trim()); }
  });
  $("#quickProxyTest")?.addEventListener("click", async () => {
    const value = ($("#quickProxy").value || "").trim();
    try {
      await withBusy("تست پروکسی…", () => post("/api/proxy/test", { proxy: value }));
      toast(value ? "پروکسی وصل است" : "اتصال مستقیم OK است");
    } catch (err) {
      toast(err.message, true);
    }
  });
  $("#quickProxyDetect")?.addEventListener("click", suggestProxy);

  // -------------------------------------------------- destination dialog
  let destState = null;
  let defaultDir = "";

  const bestStartDir = (preset) =>
    preset || localStorage.getItem(LS.lastDir) || defaultDir || osPaths.downloads || "";

  async function pickInto(input, title) {
    try {
      const picked = await pickFolder(input.value || bestStartDir(""), title);
      if (picked) input.value = picked;
      return picked;
    } catch (err) {
      toast(errMsg(err), true);
      return "";
    }
  }

  function openDest(items, mode = "add", id = null, preset = "") {
    destState = { items, mode, id };
    $("#destTitle").textContent = mode === "redir"
      ? "تغییر مقصد دانلود"
      : items.length > 1 ? `دانلود ${items.length} مورد — مقصد مشترک` : "مقصد دانلود";
    $("#destName").value = items.length === 1 ? (items[0].name || "") : "";
    $("#destName").disabled = items.length !== 1;
    $("#destRemember").disabled = items.length !== 1;
    $("#destPath").value = bestStartDir(preset);
    $("#destDlg").showModal();
  }

  $("#destPick").addEventListener("click", () => pickInto($("#destPath"), "انتخاب مقصد دانلود"));
  $("#destCancel").addEventListener("click", () => $("#destDlg").close());

  $("#destGo").addEventListener("click", async () => {
    if (!destState) return;
    let path = ($("#destPath").value || "").trim();
    if (!path) path = await pickInto($("#destPath"), "انتخاب مقصد دانلود");
    if (!path) return toast("مقصد را از دیالوگ سیستم انتخاب کنید", true);
    const btn = $("#destGo");
    btn.disabled = true;
    try {
      if (destState.mode === "redir") {
        await withBusy("در حال جابه‌جایی فایل‌ها…", () => post(`/api/downloads/${destState.id}/redir`,
          { dir: path, create_dir: $("#destCreate").checked }));
        toast("مقصد عوض شد و فایل‌های ناقص جابه‌جا شدند");
        $("#destDlg").close();
        loadDownloads(false);
      } else {
        const common = {
          dir: path,
          create_dir: $("#destCreate").checked,
          remember: $("#destRemember").checked,
          paused: $("#destPaused").checked,
        };
        let ok = 0;
        const errors = [];
        await withBusy(destState.items.length > 1 ? `در حال افزودن ${destState.items.length} دانلود…` : "در حال افزودن دانلود…", async () => {
          for (const item of destState.items) {
            const payload = Object.assign({}, item, common);
            if (destState.items.length === 1 && $("#destName").value) payload.name = $("#destName").value;
            try {
              await post("/api/download", payload);
              ok += 1;
            } catch (err) {
              errors.push(`${item.name || payload.name}: ${err.message}`);
            }
          }
        });
        if (ok) {
          localStorage.setItem(LS.lastDir, path);
          $("#destDlg").close();
          toast(errors.length ? `${ok} شروع شد، ${errors.length} خطا` : `${ok} دانلود در صف قرار گرفت`);
          showTab("downloads");
        } else {
          toast(errors[0] || "شروع ناموفق", true);
        }
      }
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  });

  $("#manualBtn").addEventListener("click", () => {
    const value = ($("#manualUrl").value || "").trim();
    if (!value) return;
    const magnet = value.startsWith("magnet:") ? value : "";
    const torrentUrl = !magnet && /^https?:\/\//i.test(value) ? value : "";
    if (!magnet && !torrentUrl) return toast("مغناطیس یا لینک .torrent معتبر نیست", true);
    let name = value.split("/").pop();
    if (magnet) {
      const m = magnet.match(/[?&]dn=([^&]+)/);
      try { name = m ? decodeURIComponent(m[1].replace(/\+/g, " ")) : ""; } catch (e) { name = m ? m[1] : ""; }
    }
    openDest([{
      name, magnet, torrent_url: torrentUrl, source: "manual", source_label: "دستی",
      page_url: "", size_bytes: 0,
    }]);
    $("#manualUrl").value = "";
  });

  // ------------------------------------------------------------- downloads
  const STATUS_FA = {
    downloading: "در حال دانلود", metadata: "دریافت اطلاعات تورنت", queued: "در صف", paused: "متوقف",
    done: "کامل", error: "خطا", missing: "فراموش شده", removed: "حذف‌شده",
  };
  const LIVE_STATUSES = new Set(["downloading", "queued", "metadata"]);
  let downloadsData = { aria2: {}, items: [] };
  let downloadsLoadedOnce = false;
  let dlFetchBusy = false;
  let dlFetchAgain = false;

  function updateAriaStatus(ariaInfo) {
    if (!ariaInfo) return;
    const el = $("#ariaStatus");
    el.textContent = ariaInfo.up ? `aria2 ${ariaInfo.version} · فعال` : "aria2 خاموش است";
    el.classList.toggle("up", !!ariaInfo.up);
    el.classList.toggle("down", !ariaInfo.up);
    const g = ariaInfo.global || {};
    $("#globalStat").textContent = ariaInfo.up
      ? `سرعت کل: ${fmtBytes(g.downloadSpeed)}/s · فعال: ${g.numActive || 0} · صف: ${g.numWaiting || 0} · متوقف: ${g.numStopped || 0}`
      : "";
  }

  async function loadDownloads(showLoader) {
    if (dlFetchBusy) { dlFetchAgain = true; return; }
    dlFetchBusy = true;
    if (showLoader && !downloadsLoadedOnce) {
      $("#dlList").innerHTML = `<div class="empty-card glass dl-skeleton" aria-busy="true">
        <div class="skeleton-rows"><i></i><i></i><i></i><i></i></div>
        <p>در حال بارگذاری دانلودها…</p>
      </div>`;
    }
    try {
      const data = await api("/api/downloads");
      downloadsData = data;
      downloadsLoadedOnce = true;
      renderDownloads(data);
      updateAriaStatus(data.aria2);
    } catch (err) {
      if (!downloadsLoadedOnce) {
        $("#dlList").innerHTML = `<div class="empty-card glass">${esc(err.message)}</div>`;
      }
    } finally {
      dlFetchBusy = false;
      if (dlFetchAgain) {
        dlFetchAgain = false;
        loadDownloads(false);
      }
    }
  }

  function actionBtn(id, act, label, cls) {
    return `<button class="${cls} act" type="button" data-id="${esc(id)}" data-act="${act}">${label}</button>`;
  }

  function actionsHtml(it) {
    const st = it.status;
    const live = LIVE_STATUSES.has(st);
    const stuck = st === "paused" || st === "missing" || st === "error";
    const acts = [];
    if (live) acts.push(actionBtn(it.id, "pause", "توقف موقت", "ghost sm"));
    if (stuck) acts.push(actionBtn(it.id, "resume", "ادامه", "primary sm"));
    if (live || stuck) acts.push(actionBtn(it.id, "stop", "توقف", "ghost sm"));
    acts.push(actionBtn(it.id, "redir", "تغییر مقصد", "ghost sm"));
    acts.push(actionBtn(it.id, "files", "فایل‌ها", "ghost sm"));
    acts.push(actionBtn(it.id, "reveal", "نمایش در فایل‌منیجر", "ghost sm"));
    acts.push(actionBtn(it.id, "delete", "حذف", "danger sm"));
    return acts.join("");
  }

  function pctOf(it) {
    return Math.max(0, Math.min(100, Number(it.progress) || 0));
  }

  function metaHtml(it) {
    const pct = pctOf(it);
    return `
      <span data-k="size"><b>${esc(it.done_human)}</b> / ${esc(it.total_human)}</span>
      <span data-k="speed">${it.speed_human ? `سرعت: <b>${esc(it.speed_human)}</b>` : ""}</span>
      <span data-k="eta">${it.eta_seconds ? `تا پایان: <b>${fmtEta(it.eta_seconds)}</b>` : ""}</span>
      <span data-k="seeds">${it.seeds != null ? `سیچر: <b>${esc(it.seeds)}</b>` : ""}</span>
      <span data-k="conns">${it.connections != null ? `اتصال: <b>${esc(it.connections)}</b>` : ""}</span>
      <span data-k="pct">${pct ? `<b>${pct}%</b>` : ""}</span>`;
  }

  function buildCard(it, isNew) {
    const st = it.status;
    const pct = pctOf(it);
    const el = document.createElement("div");
    el.className = `card ${st}${isNew ? " is-new" : ""}`;
    el.dataset.id = it.id;
    el.dataset.status = st;
    el.innerHTML = `
      <div class="card-head">
        <div class="card-title"></div>
        <span class="pill pill-status ${esc(st)}"></span>
        <span class="pill pill-source"></span>
      </div>
      <div class="meta">${metaHtml(it)}</div>
      <div class="bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}">
        <i style="width:${pct}%"></i>
      </div>
      <div class="path"></div>
      <div class="err" hidden></div>
      <div class="card-actions">${actionsHtml(it)}</div>`;
    el.querySelector(".card-title").textContent = it.name || "(بدون نام)";
    el.querySelector(".pill-status").textContent = STATUS_FA[st] || st;
    el.querySelector(".pill-source").textContent = it.source_label || it.source || "";
    el.querySelector(".path").textContent = it.dir || "";
    const err = el.querySelector(".err");
    if (it.error) { err.hidden = false; err.textContent = `⚠ ${it.error}`; }
    if (isNew) {
      el.addEventListener("animationend", () => el.classList.remove("is-new"), { once: true });
    }
    return el;
  }

  function patchCard(el, it) {
    const st = it.status;
    const pct = pctOf(it);
    if (el.dataset.status !== st) {
      el.className = `card ${st}`;
      el.dataset.status = st;
      const pill = el.querySelector(".pill-status");
      pill.className = `pill pill-status ${st}`;
      pill.textContent = STATUS_FA[st] || st;
      el.querySelector(".card-actions").innerHTML = actionsHtml(it);
    }
    const title = el.querySelector(".card-title");
    if (title.textContent !== (it.name || "(بدون نام)")) title.textContent = it.name || "(بدون نام)";
    const src = el.querySelector(".pill-source");
    const srcText = it.source_label || it.source || "";
    if (src.textContent !== srcText) src.textContent = srcText;
    const meta = el.querySelector(".meta");
    const nextMeta = metaHtml(it);
    if (meta.dataset.sig !== nextMeta) {
      meta.innerHTML = nextMeta;
      meta.dataset.sig = nextMeta;
    }
    const bar = el.querySelector(".bar");
    const fill = bar.querySelector("i");
    const width = `${pct}%`;
    if (fill.style.width !== width) fill.style.width = width;
    bar.setAttribute("aria-valuenow", String(pct));
    const path = el.querySelector(".path");
    if (path.textContent !== (it.dir || "")) path.textContent = it.dir || "";
    const err = el.querySelector(".err");
    if (it.error) {
      err.hidden = false;
      const msg = `⚠ ${it.error}`;
      if (err.textContent !== msg) err.textContent = msg;
    } else if (!err.hidden) {
      err.hidden = true;
      err.textContent = "";
    }
  }

  function sortedDownloads(data) {
    const items = (data.items || []).slice();
    const sort = $("#sortDl").value;
    items.sort((a, b) => (sort === "old"
      ? (a.added_at || 0) - (b.added_at || 0)
      : (b.added_at || 0) - (a.added_at || 0)));
    return items;
  }

  function renderDownloads(data) {
    const items = sortedDownloads(data);
    const liveN = items.filter((i) => LIVE_STATUSES.has(i.status)).length;
    const badge = $("#dlBadge");
    if (badge.textContent !== String(liveN)) badge.textContent = String(liveN);

    const list = $("#dlList");
    if (!items.length) {
      if (!list.querySelector(".empty-card:not(.dl-skeleton)") || list.querySelector(".card,.dl-skeleton")) {
        list.innerHTML = '<div class="empty-card glass">لیست دانلود خالی است.</div>';
      }
      return;
    }

    // Drop placeholder / skeleton without wiping existing cards.
    list.querySelectorAll(".empty-card,.dl-skeleton").forEach((n) => n.remove());

    const byId = new Map();
    $$("#dlList .card[data-id]").forEach((el) => byId.set(el.dataset.id, el));
    const keep = new Set(items.map((it) => it.id));
    byId.forEach((el, id) => { if (!keep.has(id)) el.remove(); });

    let next = list.firstChild;
    for (const it of items) {
      let el = byId.get(it.id);
      if (!el) {
        el = buildCard(it, true);
        byId.set(it.id, el);
      } else {
        patchCard(el, it);
      }
      if (el !== next) list.insertBefore(el, next);
      next = el.nextSibling;
    }
  }

  // One listener for the whole list — no rebind on each poll.
  $("#dlList").addEventListener("click", (ev) => {
    const btn = ev.target.closest(".act");
    if (!btn || !btn.closest("#dlList")) return;
    onDownloadAction(btn);
  });

  async function onDownloadAction(btn) {
    const id = btn.dataset.id;
    const act = btn.dataset.act;
    btn.disabled = true;
    try {
      if (act === "delete") {
        const removeFiles = await askNative(
          "فایل‌های دانلودشده/ناقص هم از دیسک حذف شوند؟\n«خیر» = فقط از لیست حذف می‌شود.",
          { title: "حذف دانلود", okLabel: "حذف فایل‌ها هم", cancelLabel: "فقط از لیست" },
        );
        await post(`/api/downloads/${id}/delete`, { remove_files: !!removeFiles });
        toast(removeFiles ? "مورد و فایل‌هایش حذف شد" : "مورد از لیست حذف شد");
      } else if (act === "redir") {
        const item = (downloadsData.items || []).find((x) => x.id === id);
        openDest([], "redir", id, item ? item.dir : "");
        return;
      } else if (act === "files") {
        showFiles((downloadsData.items || []).find((x) => x.id === id));
        return;
      } else if (act === "reveal") {
        await post(`/api/downloads/${id}/reveal`, {});
        toast("پوشه در فایل‌منیجر سیستم باز شد");
      } else {
        await post(`/api/downloads/${id}/${act}`, {});
        toast({ pause: "توقف موقت", resume: "ادامه داده شد", stop: "توقف کامل" }[act] || "انجام شد");
      }
      await loadDownloads(false);
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  function showFiles(item) {
    if (!item) return;
    $("#filesTitle").textContent = item.name || "فایل‌ها";
    const files = item.files || [];
    $("#filesList").innerHTML = files.length
      ? files.map((f) => {
          const pct = f.length ? Math.round((f.completed / f.length) * 100) : 0;
          return `<div class="file"><b>${esc(f.name)}</b> — ${fmtBytes(f.completed)} / ${fmtBytes(f.length)}
                    ${pct ? `(${pct}%)` : ""}
                    <div class="path">${esc(f.path)}</div></div>`;
        }).join("")
      : '<div class="empty-card">لیست فایلی ثبت نشده (متادیتا هنوز نیامده یا مورد حذف شده).</div>';
    $("#filesDlg").showModal();
  }

  $("#filesClose").addEventListener("click", () => $("#filesDlg").close());
  $("#refreshDl").addEventListener("click", () => loadDownloads(false));
  $("#sortDl").addEventListener("change", () => renderDownloads(downloadsData));
  $("#saveSessionBtn").addEventListener("click", async () => {
    try {
      await post("/api/daemon", { action: "save" });
      toast("صف در aria2.session ذخیره شد");
    } catch (err) {
      toast(err.message, true);
    }
  });
  $("#daemonBtn").addEventListener("click", async () => {
    const btn = $("#daemonBtn");
    btn.disabled = true;
    try {
      const info = await withBusy("در حال راه‌اندازی aria2…", () => post("/api/daemon", { action: "start" }));
      toast(`aria2 ${info.version} راه‌اندازی شد`);
      await loadDownloads(false);
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  });

  // ------------------------------------------------------------- partials
  async function scanPartials() {
    const btn = $("#scanBtn");
    btn.disabled = true;
    $("#scanInfo").textContent = "";
    $("#partialList").innerHTML = `<div class="empty-card glass"><div class="spinner" style="margin:0 auto 10px"></div>در حال اسکن پوشه‌ها…</div>`;
    try {
      const data = await api(`/api/partials?root=${encodeURIComponent(($("#scanRoot").value || "").trim())}`);
      $("#partialList").dataset.scanned = "1";
      renderPartials(data);
      $("#scanInfo").textContent = `${(data.items || []).length} مورد نیمه‌کاره در ${(data.roots || []).length} پوشه`;
      $("#ptBadge").textContent = (data.items || []).length;
    } catch (err) {
      $("#partialList").innerHTML = `<div class="empty-card glass">${esc(err.message)}</div>`;
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  function renderPartials(data) {
    const items = data.items || [];
    $("#partialList").innerHTML = items.length ? items.map((p) => `
      <div class="card paused" data-control="${esc(p.control)}">
        <div class="card-head">
          <div class="card-title">${esc(p.name)}</div>
          <span class="pill paused">${esc(p.partial_human)} دانلود شده</span>
          ${p.resumable ? '<span class="pill">قابل ادامه</span>' : '<span class="pill">مغناطیسی ندارد</span>'}
        </div>
        <div class="meta">
          <span>دریافتی: <b>${esc(p.partial_human)}</b>${p.exists ? "" : " (فایل هنوز ساخته نشده)"}</span>
          <span>تغییر: ${new Date(p.modified * 1000).toLocaleString("fa-IR")}</span>
        </div>
        <div class="path">${esc(p.target)}</div>
        <div class="card-actions">
          ${p.resumable ? `<button class="primary sm p-act" data-act="resume" data-id="${esc(p.download_id)}">ادامه دانلود</button>` : ""}
          ${p.resumable ? "" : `<button class="ghost sm p-act" data-act="reveal" data-id="${esc(p.download_id)}">در لیست دانلودها</button>`}
          <button class="danger sm p-act" data-act="delete" data-control="${esc(p.control)}">حذف ناقص‌ها</button>
        </div>
      </div>`).join("")
      : '<div class="empty-card glass">هیچ فایل نیمه‌کاره‌ای پیدا نشد.</div>';
    $$("#partialList .p-act").forEach((btn) => btn.addEventListener("click", async () => {
      const act = btn.dataset.act;
      btn.disabled = true;
      try {
        if (act === "delete") {
          const yes = await askNative("فایل ناقص و فایل کنترل .aria2 حذف شود؟", { title: "حذف فایل ناقص", okLabel: "حذف", cancelLabel: "انصراف" });
          if (!yes) { btn.disabled = false; return; }
          const data2 = await post("/api/partials/delete", { control: btn.dataset.control, remove_target: true });
          toast(`${data2.deleted.length} فایل حذف شد`);
        } else if (act === "resume") {
          await post(`/api/downloads/${btn.dataset.id}/resume`, {});
          toast("از سرگیری از همان فایل ناقص");
          showTab("downloads");
          return;
        } else if (act === "reveal") {
          showTab("downloads");
          return;
        }
        await scanPartials();
      } catch (err) {
        toast(err.message, true);
        btn.disabled = false;
      }
    }));
  }
  $("#scanBtn").addEventListener("click", scanPartials);
  $("#scanBrowse").addEventListener("click", async () => {
    if (await pickInto($("#scanRoot"), "پوشه برای اسکن فایل‌های نیمه‌کاره")) scanPartials();
  });
  $("#scanClear").addEventListener("click", () => { $("#scanRoot").value = ""; });

  // --------------------------------------------------------------- support
  function fmtIrr(n) {
    return Number(n || 0).toLocaleString("fa-IR");
  }

  function syncDonateHint() {
    const irr = Number($("#donateAmount").value) || 0;
    $("#donateTomanHint").textContent = `≈ ${fmtIrr(Math.floor(irr / 10))} تومان`;
  }

  async function loadSupport() {
    try {
      const d = await invoke("donate_info");
      const presets = d.presets_irr || [100000, 200000, 500000, 1000000];
      $("#donatePresets").innerHTML = presets.map((n) =>
        `<button type="button" class="ghost sm preset" data-irr="${Number(n)}">${fmtIrr(n)}</button>`
      ).join("");
      $$("#donatePresets .preset").forEach((btn) => {
        btn.addEventListener("click", () => {
          $("#donateAmount").value = String(btn.dataset.irr);
          syncDonateHint();
        });
      });
      $("#donateAmount").min = String(d.min_irr || 100000);
      const addr = d.tron || d.tron_usdt || "";
      $("#tronAddress").textContent = addr;
      renderTronQr(d.tron_qr_svg || "");
      syncDonateHint();
    } catch (err) {
      toast(errMsg(err), true);
    }
  }

  // SVG is generated locally in Rust (qrcode crate) — trusted, no user input.
  function renderTronQr(svg) {
    const box = $("#tronQr");
    if (!box) return;
    box.innerHTML = svg || '<p class="muted tiny">QR در دسترس نیست</p>';
  }

  $("#tronCopyBtn").addEventListener("click", async () => {
    const addr = ($("#tronAddress").textContent || "").trim();
    if (!addr) return;
    try {
      await navigator.clipboard.writeText(addr);
      toast("آدرس TRX کپی شد");
    } catch (err) {
      toast("کپی ناموفق بود", true);
    }
  });

  $("#donateAmount").addEventListener("input", syncDonateHint);
  $("#donatePayBtn").addEventListener("click", async () => {
    const btn = $("#donatePayBtn");
    const errEl = $("#donateErr");
    errEl.hidden = true;
    const amount = Number($("#donateAmount").value) || 0;
    btn.disabled = true;
    try {
      const data = await withBusy("در حال ساخت لینک پرداخت…", () => invoke("create_donation", {
        amountIrr: amount,
        description: "حمایت از GameMap Torrent Desk",
      }));
      await openUrl(data.payment_url);
      toast("درگاه زرین‌پال در مرورگر سیستم باز شد");
    } catch (err) {
      const msg = errMsg(err);
      errEl.textContent = msg;
      errEl.hidden = false;
      toast(msg, true);
    } finally {
      btn.disabled = false;
    }
  });

  // -------------------------------------------------------------- settings
  const kv = (pairs) => pairs
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v || "-")}</dd>`).join("");

  async function loadSettings() {
    try {
      if (!catalog.length) {
        try {
          const meta = await api("/api/sources");
          catalog = (meta && meta.sources) || [];
        } catch (err) { /* boot may retry */ }
      }
      const data = await api("/api/settings");
      const p = data.prefs || {};
      $("#setDir").value = p.default_dir || "";
      showProxy(p.proxy);
      $("#setMax").value = String(p.max_concurrent || 3);
      $("#setAlloc").value = p.file_allocation || "none";
      $("#setPaused").checked = !!p.start_paused;
      $("#setCreate").checked = p.create_dir !== false;
      defaultDir = p.default_dir || defaultDir;
      const known = new Set(catalog.map((s) => s.id));
      disabled = new Set((p.sources_disabled || []).filter((id) => known.has(id)));
      if (catalog.length && enabledSources().length === 0) disabled.clear();
      renderSourcesEverywhere();
      const env = data.env || {};
      const paths = data.paths || {};
      $("#pathInfo").innerHTML = kv([
        ["state.json", paths.state], ["aria2.session", paths.session], ["aria2.log", paths.log],
        ["سیستم‌عامل", osPaths.os], ["Downloads سیستم", osPaths.downloads],
        ["تایم‌اوت / صفحات جزئیات / صفحات eztv",
         `${env.timeout}s · ${env.detail_limit} · ${env.eztv_pages}`],
      ]);
    } catch (err) {
      toast(err.message, true);
    }
    try {
      const health = await api("/api/health");
      $("#ariaInfo").innerHTML = kv([
        ["وضعیت", health.aria2.up ? `فعال — ${health.aria2.version}` : "خاموش"],
        ["RPC", health.aria2.endpoint], ["باینری", health.aria2.binary],
        ["مقصد پیش‌فرض", health.default_dir], ["تعداد منابع", String(catalog.length)],
      ]);
      updateAriaStatus(health.aria2);
    } catch (err) {
      /* health is best-effort */
    }
  }

  $("#saveSettings").addEventListener("click", async () => {
    const btn = $("#saveSettings");
    btn.disabled = true;
    try {
      const data = await post("/api/settings", {
        default_dir: $("#setDir").value.trim(),
        proxy: $("#setProxy").value.trim(),
        max_concurrent: Number($("#setMax").value) || 3,
        file_allocation: $("#setAlloc").value,
        start_paused: $("#setPaused").checked,
        create_dir: $("#setCreate").checked,
      });
      defaultDir = (data.prefs || {}).default_dir || defaultDir;
      showProxy((data.prefs || {}).proxy);
      toast("تنظیمات ذخیره شد");
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  });

  $$("[data-browse]").forEach((btn) => {
    btn.addEventListener("click", () => pickInto($(`#${btn.dataset.browse}`), "انتخاب مقصد پیش‌فرض"));
  });

  $$("[data-daemon]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const action = btn.dataset.daemon;
      btn.disabled = true;
      try {
        if (action === "log") {
          const data = await post("/api/daemon", { action: "log" });
          const log = $("#ariaLog");
          log.hidden = !log.hidden;
          log.textContent = data.tail || "(لاگ خالی است)";
          return;
        }
        const label = { start: "راه‌اندازی aria2…", stop: "توقف aria2…", save: "ذخیرهٔ صف…", purge: "پاک‌سازی…" }[action];
        const data = await withBusy(label || "لطفاً صبر کنید…", () => post("/api/daemon", { action }));
        toast(action === "start" ? `aria2 ${data.version} راه‌اندازی شد` : "انجام شد");
        await loadSettings();
      } catch (err) {
        toast(err.message, true);
      } finally {
        btn.disabled = false;
      }
    });
  });

  // ------------------------------------------------------------------ boot
  async function boot() {
    try { osPaths = await invoke("os_paths"); } catch (err) { /* outside Tauri */ }
    await withBusy("در حال آماده‌سازی…", async () => {
      try {
        const [meta, settings] = await Promise.all([api("/api/sources"), api("/api/settings")]);
        catalog = (meta && meta.sources) || [];
        const p = (settings && settings.prefs) || {};
        defaultDir = p.default_dir || "";
        // ignore stale ids that are no longer in the built-in catalog
        const known = new Set(catalog.map((s) => s.id));
        disabled = new Set((p.sources_disabled || []).filter((id) => known.has(id)));
        if (catalog.length && enabledSources().length === 0) disabled.clear();
        renderSourcesEverywhere();
        showProxy(p.proxy);
      } catch (err) {
        toast(`منابع بارگذاری نشد: ${err.message}`, true);
        // Still paint whatever we have so the chip bar isn't blank.
        if (catalog.length) renderSourcesEverywhere();
      }
    });
    // aria2 may still be auto-starting — poll a few times
    const refreshAria = async () => {
      try { updateAriaStatus((await api("/api/health")).aria2); } catch (err) { /* ignore */ }
    };
    refreshAria();
    setTimeout(refreshAria, 1500);
    setTimeout(refreshAria, 4000);
    loadDownloads(false);
  }

  boot();
})();
