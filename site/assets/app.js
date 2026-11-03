/* openbasket landing page: fund search, fund view with its distribution
   charts, coverage, release and API sections. Reads the static JSON API
   described in docs/data-api.md (v1.1); the API base comes from
   <body data-api>, so the same page runs on fixtures locally. */
(() => {
  "use strict";

  const core = window.OpenbasketCore;
  const { UNRESOLVED, ISIN_RE, SERIES_RE, compact, sum, isValidIsin, rowsOf, totals, topHoldings } = core;
  const body = document.body;
  const API = (body.dataset.api || "api/v1").replace(/\/+$/, "");
  const DOCS = (body.dataset.docs || "docs/").replace(/\/?$/, "/");
  const FIRST_ROWS = 100;
  const MAX_SUGGESTIONS = 8;
  const MAX_BARS = 8;
  const SOURCE_LABEL = {
    ishares: "iShares",
    spdr: "SPDR",
    tickerinside: "TickerInside",
    nport: "SEC N-PORT",
    security_master: "Security master",
    openfigi: "OpenFIGI",
  };
  /* Chart colours: the Leith Research tokens and the palette of its charts. */
  const C = {
    wine: "#9c3848", slate: "#5f7d8c", sand: "#c9a96e", moss: "#7a8c5c", stone: "#b8aea3",
    other: "#e0d8cc", paper2: "#ede7de", muted: "#7d7873", line: "#d9d2c7",
  };
  /* Shares in a strip: wine for the largest entry, then wine fading into paper. Bars are all
     wine; "others" are stone and Unresolved is a dashed paper bar. */
  const SERIES = [C.wine, "#b0606d", "#c48b94", "#d6b2b5", "#e4cdcc", "#ecdcd9"];

  const state = {
    meta: null, sources: {}, index: null, indexPromise: null, fund: null, token: 0,
    shownId: null, redrawTable: null,
  };

  /* ---------- small helpers ---------- */

  const $ = (sel, el = document) => el.querySelector(sel);
  const SVG_NS = "http://www.w3.org/2000/svg";

  function safeUrl(u) {
    if (!u || typeof u !== "string") return null;
    try {
      const url = new URL(u, location.href);
      return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
    } catch (e) {
      return null;
    }
  }

  /** Build an element. Text goes in as text nodes, never as HTML. */
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k === "href") {
        const url = safeUrl(v);
        if (url) el.setAttribute("href", url);
      } else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat(Infinity)) {
      if (c == null || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }

  /** An SVG element with attributes. */
  function s(tag, attrs, ...children) {
    const el = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v != null) el.setAttribute(k, v);
    for (const c of children.flat(Infinity)) if (c != null) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    return el;
  }

  const ext = (href, text) => h("a", { href, rel: "noopener" }, text);

  /** Plain text with its http(s) links made clickable (credit lines carry licence links). */
  function linkify(text) {
    return core.linkParts(text).map((p) => (p.url ? (safeUrl(p.url) ? ext(p.url, p.url) : p.url) : p.text));
  }

  function fmtPct(v, dp) {
    if (v == null || Number.isNaN(v)) return "–";
    const a = Math.abs(v);
    const d = dp != null ? dp : a >= 0.1 || a === 0 ? 2 : a >= 0.001 ? 3 : 5;
    const out = v.toLocaleString("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d });
    return /^-[0.]+$/.test(out) ? out.slice(1) : out;
  }
  const fmtInt = (n) => (n == null ? "–" : Number(n).toLocaleString("en-GB"));

  function parseDate(x) {
    if (!x) return null;
    const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(x) ? x + "T00:00:00Z" : x);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  function fmtDate(x) {
    const d = parseDate(x);
    return d ? d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }) : x || "–";
  }
  function fmtRange(lo, hi) {
    if (!lo && !hi) return "–";
    if (!lo || !hi || lo === hi) return fmtDate(lo || hi);
    return `${fmtDate(lo)} to ${fmtDate(hi)}`;
  }
  function ageText(asOf) {
    const a = parseDate(asOf);
    const b = parseDate(state.meta && state.meta.built_at);
    if (!a || !b) return null;
    const days = Math.floor((Date.UTC(b.getUTCFullYear(), b.getUTCMonth(), b.getUTCDate()) - a) / 86400000);
    if (days < 0) return null;
    if (days <= 1) return days === 0 ? "same day as this build" : "1 day before this build";
    if (days < 60) return `${days} days before this build`;
    return `about ${Math.round(days / 30.4)} months before this build`;
  }

  let regionNames = null;
  try {
    regionNames = new Intl.DisplayNames(["en-GB"], { type: "region" });
  } catch (e) {
    regionNames = null;
  }
  function countryName(code) {
    if (!code) return null;
    if (/^[A-Z]{2}$/.test(code) && regionNames) {
      try {
        const n = regionNames.of(code);
        if (n && n !== code) return n;
      } catch (e) {
        /* not a region code: keep the source's label */
      }
    }
    return code;
  }

  const LICENCE_URL = { "CC BY 4.0": "https://creativecommons.org/licenses/by/4.0/" };
  const licence = (text) => (LICENCE_URL[text] ? ext(LICENCE_URL[text], text) : text ? linkify(text) : "–");
  const titleCase = (id) => String(id).replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  const sourceLabel = (id) => SOURCE_LABEL[id] || (id ? titleCase(id) : "unknown");
  const sourceName = (id) => (state.sources[id] && state.sources[id].name) || sourceLabel(id);
  const badge = (id, main) => h("span", { class: main ? "badge badge-main" : "badge", title: sourceName(id) }, sourceLabel(id));
  const fundUrl = (id) => `${API}/funds/${encodeURIComponent(id)}.json`;
  const matchedVia = (v) => core.matchedVia(v, sourceLabel);

  async function getJSON(path) {
    const res = await fetch(path, { headers: { Accept: "application/json" } });
    if (res.status === 404) return { __notFound: true };
    if (!res.ok) throw new Error(`HTTP ${res.status} for ${path}`);
    return res.json();
  }

  /* ---------- meta: coverage, release and API sections ---------- */

  async function loadMeta() {
    const view = $("#release-view");
    const cov = $("#coverage-view");
    try {
      const meta = await getJSON(`${API}/meta.json`);
      if (meta.__notFound) throw new Error("meta.json not found");
      state.meta = meta;
      for (const src of meta.sources || []) state.sources[src.id] = src;
      view.replaceChildren(renderRelease(meta));
      if (cov) cov.replaceChildren(renderCoverage(meta));
    } catch (e) {
      const fail = () => h("p", { class: "notice" }, "The release information could not be loaded. The releases are listed on ",
        ext("https://github.com/omaxim/openbasket/releases", "GitHub"), ".");
      view.replaceChildren(fail());
      if (cov) cov.replaceChildren(fail());
    }
  }

  function renderRelease(m) {
    const tag = m.release_tag || "latest";
    const sources = m.sources || [];
    return h("div", {},
      h("div", { class: "release-head" },
        h("div", {},
          h("h3", {}, "Release ", h("em", {}, tag)),
          h("p", {}, `Built ${fmtDate(m.built_at)}. Holdings dates differ by source and fund; each fund shows its own.`)),
        h("p", { class: "btn-links" },
          h("a", { class: "btn", href: m.release_url, rel: "noopener" }, "View the release"),
          h("a", { class: "btn btn-ghost", href: m.full_data_url, rel: "noopener" }, "Download the full Parquet"))),
      h("div", { class: "stats" },
        stat(fmtInt(m.n_funds), "funds looked through"),
        stat(fmtInt(m.n_isins), "fund ISINs recognised"),
        stat(fmtInt(sources.length), "sources")),
      h("h3", { class: "sources-title" }, "Sources in this release"),
      h("div", { class: "cards" }, sources.map(sourceCard)));
  }

  const stat = (value, label) => h("div", { class: "card" }, h("p", { class: "stat" }, value, h("span", {}, label)));

  function sourceCard(src) {
    return h("article", { class: "card" },
      h("h3", {}, safeUrl(src.url) ? ext(src.url, src.name || sourceLabel(src.id)) : src.name || sourceLabel(src.id)),
      h("dl", { class: "kv" },
        h("dt", {}, "Licence"), h("dd", {}, licence(src.licence)),
        h("dt", {}, "Credit"), h("dd", {}, src.credit ? linkify(src.credit) : "–"),
        h("dt", {}, "Holdings as of"), h("dd", {}, fmtRange(src.as_of_min, src.as_of_max)),
        h("dt", {}, "Funds"), h("dd", {}, `${fmtInt(src.n_funds)}`)),
      h("p", { class: "tags" }, badge(src.id)));
  }

  /** Funds per source and as-of ranges, as a table with an inline bar in each row. */
  function renderCoverage(m) {
    const sources = (m.sources || []).slice().sort((a, b) => (b.n_funds || 0) - (a.n_funds || 0));
    const max = Math.max(1, ...sources.map((x) => x.n_funds || 0));
    const rows = sources.map((src) => {
      const w = ((src.n_funds || 0) / max) * 100;
      return h("tr", {},
        h("th", { scope: "row" }, safeUrl(src.url) ? ext(src.url, sourceLabel(src.id)) : sourceLabel(src.id),
          h("span", { class: "sub" }, src.name && src.name !== sourceLabel(src.id) ? src.name : null)),
        h("td", { class: "num" }, fmtInt(src.n_funds)),
        h("td", { class: "cov-bar", "aria-hidden": "true" },
          s("svg", { viewBox: "0 0 100 6", preserveAspectRatio: "none", focusable: "false" },
            s("rect", { x: 0, y: 0, width: 100, height: 6, fill: C.paper2 }),
            s("rect", { x: 0, y: 0, width: w.toFixed(2), height: 6, fill: C.wine }))),
        h("td", { class: "dim" }, fmtRange(src.as_of_min, src.as_of_max)));
    });
    return h("div", {},
      h("div", { class: "table-wrap cov-wrap" },
        h("table", { class: "cov" },
          h("caption", { class: "sr-only" }, `Funds per source and as-of dates in release ${m.release_tag || ""}`),
          h("thead", {}, h("tr", {},
            h("th", { scope: "col" }, "Source"), h("th", { scope: "col", class: "num" }, "Funds"),
            h("th", { scope: "col", class: "cov-bar" }, h("span", { class: "sr-only" }, "Share")),
            h("th", { scope: "col" }, "Holdings as of"))),
          h("tbody", {}, rows))),
      h("p", { class: "table-foot" },
        h("span", {}, `${fmtInt(m.n_funds)} funds in all, built ${fmtDate(m.built_at)}. A fund counts once for each source its rows come from; OpenFIGI supplies names, security types and ticker matches, not holdings.`)));
  }

  function setupApi() {
    let base = "https://openbasket.leithresearch.org/api/v1/";
    if (location.protocol === "http:" || location.protocol === "https:") base = new URL(`${API}/`, location.href).href;
    document.querySelectorAll("code[data-curl]").forEach((code) => {
      const path = code.dataset.curl;
      code.textContent = `curl -s ${path === "index.json" ? "--compressed " : ""}${base}${path}`;
      const btn = h("button", { class: "copy", type: "button", "aria-label": "Copy command" }, "Copy");
      btn.addEventListener("click", () => copy(code.textContent, btn));
      code.parentElement.append(btn);
    });
  }

  async function copy(text, btn) {
    try {
      await navigator.clipboard.writeText(text);
      btn.textContent = "Copied";
    } catch (e) {
      const range = document.createRange();
      range.selectNodeContents(btn.previousElementSibling);
      const sel = getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      btn.textContent = "Selected";
    }
    setTimeout(() => (btn.textContent = "Copy"), 1600);
  }

  /* ---------- index and fuzzy search ---------- */

  const norm = (x) =>
    (x || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[®™]/g, "")
      .replace(/&/g, " and ").replace(/[^a-z0-9]+/g, " ").trim();

  function loadIndex() {
    if (state.indexPromise) return state.indexPromise;
    setStatus("Loading the fund list…");
    state.indexPromise = getJSON(`${API}/index.json`).then((raw) => {
      if (raw.__notFound) throw new Error("index.json not found");
      const f = Object.fromEntries((raw.fields || []).map((name, i) => [name, i]));
      const aliases = raw.aliases || {};
      const byId = {};
      const entries = (raw.rows || []).map((r) => {
        const e = {
          id: r[f.id], isin: r[f.isin], name: r[f.name] || "", ticker: r[f.ticker] || "", issuer: r[f.issuer] || "",
          kind: r[f.kind], source: r[f.source], as_of: r[f.as_of], resolved_pct: r[f.resolved_pct],
          n_constituents: r[f.n_constituents],
        };
        e.keys = new Set([e.id, e.isin].filter(Boolean));
        e.nName = norm(e.name);
        e.nIssuer = norm(e.issuer);
        e.words = `${e.nName} ${e.nIssuer}`.split(" ");
        e.tick = compact(e.ticker);
        byId[e.id] = e;
        return e;
      });
      for (const [isin, id] of Object.entries(aliases)) if (byId[id]) byId[id].keys.add(isin);
      state.index = { entries, byId, aliases };
      const nSources = new Set(entries.map((e) => e.source)).size;
      setStatus(`${fmtInt(entries.length)} funds from ${nSources} sources. Use the arrow keys to pick a match.`);
      return state.index;
    }).catch((err) => {
      state.indexPromise = null;
      setStatus("The fund list could not be loaded. You can still enter a full ISIN.");
      throw err;
    });
    return state.indexPromise;
  }

  /** Characters of q in order inside x; higher for tighter matches, 0 if absent. */
  function subsequence(q, x) {
    let qi = 0, start = -1, last = -1, runs = 0;
    for (let i = 0; i < x.length && qi < q.length; i++) {
      if (x[i] === q[qi]) {
        if (start < 0) start = i;
        if (last !== i - 1) runs++;
        last = i;
        qi++;
      }
    }
    if (qi < q.length) return 0;
    const span = last - start + 1;
    return (q.length / span) * 60 + 40 / runs;
  }

  function score(e, q) {
    const c = compact(q);
    const n = norm(q);
    if (!c) return 0;
    let best = 0;
    let via = null;
    const looksLikeId = /\d/.test(c) || c.length >= 6;
    for (const k of e.keys) {
      if (k === c) return { s: 1000 + (k === e.id ? 5 : 0), via: k === e.id || k === e.isin ? null : k };
      if (looksLikeId && c.length >= 3 && k.startsWith(c)) {
        const sc = 900 + c.length - (k === e.id || k === e.isin ? 0 : 1);
        if (sc > best) { best = sc; via = k === e.id || k === e.isin ? null : k; }
      }
    }
    if (e.tick) {
      if (e.tick === c) best = Math.max(best, 800);
      else if (e.tick.startsWith(c)) best = Math.max(best, 700 - (e.tick.length - c.length));
    }
    if (n) {
      const tokens = n.split(" ");
      if (tokens.every((t) => e.words.some((w) => w.startsWith(t)))) {
        best = Math.max(best, 500 + (e.nName.startsWith(n) ? 40 : 0) + (e.nName.includes(n) ? 20 : 0) - e.nName.length / 50);
      } else if (e.nName.includes(n)) {
        best = Math.max(best, 420);
      } else if (n.length >= 3) {
        const fz = Math.max(subsequence(n.replace(/ /g, ""), e.nName.replace(/ /g, "")), subsequence(n, e.nIssuer) * 0.8);
        if (fz >= 45) best = Math.max(best, 100 + fz);
      }
    }
    return best ? { s: best, via } : 0;
  }

  function search(q) {
    if (!state.index) return [];
    const out = [];
    for (const e of state.index.entries) {
      const r = score(e, q);
      if (r) out.push({ e, s: r.s, via: r.via });
    }
    out.sort((a, b) => b.s - a.s || a.e.name.localeCompare(b.e.name));
    return out.slice(0, MAX_SUGGESTIONS);
  }

  /* ---------- combobox ---------- */

  const input = $("#q");
  const list = $("#suggestions");
  let results = [];
  let active = -1;
  let timer = 0;

  /** The search box's live status line. The example links sit in their own element and are never touched. */
  function setStatus(text) {
    const el = $("#search-status");
    if (el) el.textContent = text;
  }

  function openList() {
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }
  function closeList() {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
  }

  function highlight(text, q) {
    const n = (q || "").trim();
    if (n.length < 2) return text;
    const i = text.toLowerCase().indexOf(n.toLowerCase());
    if (i < 0) return text;
    return [text.slice(0, i), h("mark", {}, text.slice(i, i + n.length)), text.slice(i + n.length)];
  }

  function renderList(q) {
    list.replaceChildren();
    if (!q.trim()) return closeList();
    if (!results.length) {
      const c = compact(q);
      const tip = ISIN_RE.test(c) ? "No fund with this ISIN in the list. Press Enter to look it up anyway." : "No matching fund in this build.";
      list.append(h("li", { class: "empty", role: "option", "aria-disabled": "true", id: "opt-empty" }, tip));
      return openList();
    }
    results.forEach((r, i) => {
      const e = r.e;
      const ids = [e.ticker, e.isin || e.id, e.issuer].filter(Boolean).join(" · ");
      const li = h("li", { role: "option", id: `opt-${i}`, "aria-selected": i === active ? "true" : "false" },
        h("div", { class: "opt-main" }, h("span", { class: "opt-name" }, highlight(e.name, q)), badge(e.source)),
        h("div", { class: "opt-meta" }, ids, r.via ? ` · share class ${r.via}` : null,
          e.as_of ? ` · as of ${e.as_of}` : null));
      li.addEventListener("mousedown", (ev) => ev.preventDefault());
      li.addEventListener("click", () => choose(i));
      list.append(li);
    });
    openList();
  }

  function setActive(i) {
    if (!results.length) return;
    active = (i + results.length) % results.length;
    list.querySelectorAll("li[role=option]").forEach((li, j) => li.setAttribute("aria-selected", j === active ? "true" : "false"));
    const li = $(`#opt-${active}`);
    input.setAttribute("aria-activedescendant", li.id);
    li.scrollIntoView({ block: "nearest" });
  }

  function choose(i) {
    const r = results[i];
    if (!r) return;
    input.value = r.e.name;
    closeList();
    openFund(r.via || r.e.id, { focus: true });
  }

  function update() {
    const q = input.value;
    if (!state.index) {
      loadIndex().then(update, () => {});
      return;
    }
    results = search(q);
    active = -1;
    renderList(q);
  }

  function submit() {
    const c = compact(input.value);
    if (active >= 0) return choose(active);
    if (ISIN_RE.test(c) || SERIES_RE.test(c)) {
      closeList();
      return openFund(c, { focus: true });
    }
    if (results.length) return choose(0);
    if (c) setStatus("No matching fund. Try a ticker, part of the name, or a full ISIN.");
  }

  function setupSearch() {
    input.addEventListener("focus", () => {
      loadIndex().catch(() => {});
      if (input.value.trim() && results.length) openList();
    });
    input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(update, 110);
    });
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault();
        if (list.hidden && input.value.trim()) update();
        setActive(active < 0 ? (ev.key === "ArrowDown" ? 0 : -1) : active + (ev.key === "ArrowDown" ? 1 : -1));
      } else if (ev.key === "Enter") {
        ev.preventDefault();
        clearTimeout(timer);
        if (state.index && active < 0) results = search(input.value);
        submit();
      } else if (ev.key === "Escape") {
        if (!list.hidden) closeList();
        else input.value = "";
      } else if (ev.key === "Tab") {
        closeList();
      }
    });
    input.addEventListener("blur", () => setTimeout(closeList, 120));
    $("#search-form").addEventListener("submit", (ev) => {
      ev.preventDefault();
      submit();
    });
    document.querySelectorAll("a[data-isin]").forEach((a) =>
      a.addEventListener("click", (ev) => {
        if (ev.metaKey || ev.ctrlKey || ev.shiftKey) return;
        ev.preventDefault();
        openFund(a.dataset.isin, { focus: true });
      }));
  }

  /* ---------- routing ---------- */

  const idFromLocation = () => core.idFromUrl(location.search, location.hash);

  /** Put the fund in the address: a new history entry, or with ``replace`` the current one rewritten. */
  function pushId(id, { replace = false } = {}) {
    const href = core.fundHref(location.href, id);
    if (href === location.href) return;
    if (replace) history.replaceState({ id }, "", href);
    else history.pushState({ id }, "", href);
  }

  /* ---------- fund view ---------- */

  const section = $("#fund");
  const view = $("#fund-view");

  /** One short sentence for screen readers; the fund view itself is not a live region. */
  function announce(text) {
    const el = $("#fund-status");
    if (!el) return;
    el.textContent = "";
    setTimeout(() => (el.textContent = text), 30);
  }

  function show(...nodes) {
    section.hidden = false;
    view.replaceChildren(...nodes.flat(Infinity).filter(Boolean));
  }

  function focusHeading() {
    const hd = $("#fund-view h2");
    if (hd) {
      hd.setAttribute("tabindex", "-1");
      hd.focus({ preventScroll: true });
    }
  }

  async function openFund(raw, opts = {}) {
    const requested = compact(raw);
    if (!requested) return;
    // A fund opened from a #ISIN hash moves to ?isin= in place; otherwise in-page links
    // (#api, #top) would replace the hash and lose it.
    if (opts.replace) pushId(requested, { replace: true });
    else if (opts.push !== false) pushId(requested);
    const token = ++state.token;
    state.shownId = requested;
    state.redrawTable = null;
    section.hidden = false;
    if (opts.scroll !== false) section.scrollIntoView({ behavior: "smooth", block: "start" });

    if (!ISIN_RE.test(requested) && !SERIES_RE.test(requested)) return renderInvalid(requested, "is not an ISIN or an SEC series ID");
    if (ISIN_RE.test(requested) && !isValidIsin(requested)) return renderInvalid(requested, "has a wrong check digit, so it is not a valid ISIN");

    show(h("div", { class: "state" }, h("p", { class: "eyebrow" }, "Looking through"), h("h2", {}, requested),
      h("p", {}, "Loading holdings…")));
    announce(`Loading ${requested}`);
    // The index tells which holdings are funds with their own look-through; load it alongside.
    const indexReady = loadIndex().catch(() => null);
    try {
      let id = requested;
      if (state.index && state.index.aliases[id]) id = state.index.aliases[id];
      let data = await getJSON(fundUrl(id));
      if (data.__notFound) {
        const idx = await indexReady;
        if (idx && idx.aliases[requested] && idx.aliases[requested] !== id) data = await getJSON(fundUrl((id = idx.aliases[requested])));
      }
      if (data && data.alias_of) data = await getJSON(fundUrl((id = compact(data.alias_of))));
      if (token !== state.token) return;
      if (data.__notFound) return renderNotCovered(requested, opts);
      if (!Array.isArray(data.constituents)) throw new Error("This file is not a fund file.");
      state.fund = data;
      renderFund(data, requested);
      if (opts.focus) focusHeading();
      indexReady.then((idx) => {
        if (idx && token === state.token && state.redrawTable) state.redrawTable();
      });
    } catch (err) {
      if (token !== state.token) return;
      renderError(requested, err);
    }
  }

  function renderInvalid(id, why) {
    document.title = "openbasket";
    show(h("div", { class: "state" }, h("p", { class: "eyebrow" }, "Not an identifier"), h("h2", {}, id),
      h("p", {}, `“${id}” ${why}. Search by fund name or ticker instead, or check the ISIN: twelve characters, two letters, nine letters or digits and a check digit.`)));
    announce(`${id} ${why}.`);
  }

  function renderNotCovered(id, opts) {
    document.title = `${id} not covered · openbasket`;
    show(h("div", { class: "state" }, h("p", { class: "eyebrow" }, "Not covered"), h("h2", {}, id),
      h("p", {}, "This build has no fund file for this identifier. It may be a single stock or bond (which is its own constituent), a fund that none of the sources covers yet, or a share class whose ISIN no source reports."),
      h("p", {}, "The sources used are listed under ", h("a", { href: "#coverage" }, "Coverage"), "; what is and is not covered is described in the ",
        h("a", { href: `${DOCS}sources/` }, "sources documentation"), ".")));
    announce(`${id} is not covered by this build.`);
    if (opts && opts.focus) focusHeading();
  }

  function renderError(id, err) {
    document.title = "openbasket";
    const retry = h("button", { class: "btn btn-sm", type: "button" }, "Try again");
    retry.addEventListener("click", () => openFund(id, { push: false, focus: true }));
    show(h("div", { class: "state" }, h("p", { class: "eyebrow" }, "Something went wrong"), h("h2", {}, id),
      h("p", {}, `The fund file could not be loaded (${err && err.message ? err.message : "network error"}).`), retry));
    announce(`The fund file for ${id} could not be loaded.`);
  }

  function rowSourceSummary(rows) {
    const m = new Map();
    for (const r of rows) {
      const x = m.get(r.source) || { id: r.source, weight: 0, n: 0, min: r.as_of, max: r.as_of };
      x.weight += r.weight;
      x.n++;
      if (r.as_of && (!x.min || r.as_of < x.min)) x.min = r.as_of;
      if (r.as_of && (!x.max || r.as_of > x.max)) x.max = r.as_of;
      m.set(r.source, x);
    }
    return [...m.values()].sort((a, b) => b.weight - a.weight);
  }

  function renderFund(f, requested) {
    const rows = rowsOf(f);
    const t = totals(f, rows);
    const noHoldings = t.noHoldings;
    const fof = rows.some((r) => r.depth > 0);
    const src = state.sources[f.source] || {};
    const label = f.isin || f.id;
    document.title = `${f.name} (${f.ticker || label}) · openbasket`;

    const otherIsins = (f.isins || []).filter((x) => x !== f.isin);
    const issuerTitle = f.source === "nport" ? "Registrant named in the fund's SEC Form N-PORT filing" : null;
    const head = h("div", { class: "fund-head" },
      h("p", { class: "eyebrow" }, `Fund look-through${f.issuer ? ` · ${f.issuer}` : ""}`),
      h("h2", {}, f.name || label),
      h("ul", { class: "ids" },
        h("li", {}, h("b", {}, "ISIN"), f.isin ? h("code", {}, f.isin) : "not known"),
        f.ticker ? h("li", {}, h("b", {}, "Ticker"), f.ticker) : null,
        f.issuer ? h("li", { title: issuerTitle }, h("b", {}, f.source === "nport" ? "Registrant" : "Issuer"), f.issuer) : null,
        f.id !== f.isin ? h("li", {}, h("b", {}, "SEC series"), h("code", {}, f.id)) : null),
      otherIsins.length ? h("p", { class: "also" }, `Also covers share classes: ${otherIsins.slice(0, 12).join(", ")}${otherIsins.length > 12 ? ` and ${otherIsins.length - 12} more` : ""}.`) : null,
      requested && requested !== f.id && requested !== f.isin
        ? h("p", { class: "notice" }, "You looked up ", h("code", {}, requested), ", a share class of this fund. Share classes hold the same portfolio, so these are its holdings.")
        : null);

    const age = ageText(f.as_of);
    const rowSources = rowSourceSummary(rows);
    const panel = h("aside", { class: "source-panel", "aria-label": "Source of this fund's holdings" },
      h("p", { class: "label" }, "Source"),
      h("p", { class: "source-name" }, safeUrl(f.source_url) ? ext(f.source_url, src.name || sourceLabel(f.source)) : src.name || sourceLabel(f.source)),
      f.credit ? h("p", { class: "credit" }, linkify(f.credit)) : null,
      h("dl", { class: "kv" },
        h("dt", {}, noHoldings ? "Listed on" : "Holdings as of"),
        h("dd", {}, fmtDate(f.as_of), age ? h("span", { class: "muted" }, ` (${age})`) : null),
        h("dt", {}, "Matched via"), h("dd", {}, matchedVia(f.matched_via)),
        f.unidentified_lines_pct != null
          ? [h("dt", {}, "Lines without ISIN"), h("dd", {}, `${fmtPct(f.unidentified_lines_pct, 2)}% of net assets (cash, derivatives, pools), part of the unresolved weight`)]
          : null,
        src.licence ? [h("dt", {}, "Licence"), h("dd", {}, licence(src.licence))] : null),
      f.note ? h("p", { class: "notice notice-warn" }, h("strong", {}, "Note: "), f.note) : null,
      h("p", { class: "links" },
        safeUrl(f.source_url) ? ext(f.source_url, "Check it at the source ↗") : null,
        safeUrl(src.url) ? ext(src.url, `${sourceLabel(f.source)} website ↗`) : null),
      rowSources.length
        ? h("ul", { class: "row-sources" }, h("li", { class: "head" }, t.truncated ? "Listed rows by source" : "Rows in this file by source"),
          rowSources.map((x) => h("li", {}, badge(x.id, x.id === f.source), " ",
            `${fmtPct(x.weight)}% of net assets in ${fmtInt(x.n)} rows, as of ${x.min === x.max ? x.min : `${x.min} to ${x.max}`}`,
            x.id !== f.source && state.sources[x.id] ? [" · ", linkify(state.sources[x.id].credit)] : null)))
        : null);

    const chips = h("div", { class: "chips" },
      chip(`${fmtPct(t.resolved, 2)}%`, "resolved to ISINs"),
      t.unexpanded ? chip(`${fmtPct(t.unexpanded, 2)}%`, "in funds not looked through") : null,
      chip(`${fmtPct(t.unresolved, 2)}%`, "unresolved", t.unresolved < 0),
      chip(f.reported_total_pct != null ? `${fmtPct(f.reported_total_pct, 2)}%` : "–", "reported total"),
      chip(fmtInt(f.n_constituents), t.truncated ? `distinct ISINs, ${fmtInt(rows.length)} rows listed here` : "distinct ISINs"));

    const notices = [];
    if (noHoldings) {
      notices.push(h("p", { class: "notice notice-warn" }, h("strong", {}, "No holdings available. "),
        `${sourceLabel(f.source)} lists this fund, but no holdings could be read from it on ${fmtDate(f.as_of)}, so all of its weight is unresolved. `,
        safeUrl(f.source_url) ? ext(f.source_url, "See what the source shows ↗") : null));
    }
    if (t.truncated) {
      notices.push(h("p", { class: "notice notice-warn" }, h("strong", {}, "Shortened list. "),
        `This fund has ${fmtInt(t.nRows)} rows${f.n_constituents != null ? ` (${fmtInt(f.n_constituents)} distinct ISINs; an ISIN held through more than one source or sub-fund has a row for each)` : ""}. `,
        `This page lists the ${fmtInt(rows.length)} largest, ${fmtPct(t.listed, 2)}% of net assets; the other ${fmtPct(t.notListed, 2)}% is in the `,
        safeUrl(f.full_data_url) ? ext(f.full_data_url, "full Parquet file of this release") : "release Parquet file",
        ". The distribution charts below cover every row."));
    }
    if (t.unresolved < 0) {
      notices.push(h("p", { class: "notice" }, h("strong", {}, "Negative unresolved weight. "),
        "The positions add up to more than 100% of net assets, for example when the fund has liabilities or purchases not yet settled. Nothing is rescaled. ",
        h("a", { href: `${DOCS}output.html#unresolved-weight` }, "More on unresolved weight"), "."));
    }

    show(h("div", { class: "fund-grid" }, head, panel), chips, notices,
      noHoldings ? null : renderDistribution(f, rows, t),
      noHoldings ? null : renderTable(f, rows, fof, t));
    announce(noHoldings
      ? `${f.name}: no holdings available from ${sourceLabel(f.source)}.`
      : `Loaded ${f.name}: ${fmtInt(f.n_constituents)} constituents, ${fmtPct(t.resolved, 2)}% resolved, as of ${fmtDate(f.as_of)}.`);
  }

  function chip(value, label, warn) {
    return h("div", { class: warn ? "chip chip-warn" : "chip" }, h("p", { class: "stat" }, value, h("span", {}, label)));
  }

  /* ---------- distribution charts ---------- */

  const NOT_STATED = "Not stated by the source";
  /* The publish pipeline labels rows without a value "Unknown"; older files may use null. */
  const unknown = (v) => v == null || v === "" || v === "Unknown";
  const DIMENSIONS = [
    { key: "asset_class", title: "By asset class", label: (v) => (unknown(v) ? NOT_STATED : v.charAt(0).toUpperCase() + v.slice(1)) },
    { key: "country", title: "By country", label: (v) => (unknown(v) ? NOT_STATED : countryName(v)) },
    { key: "sector", title: "By sector", label: (v) => (unknown(v) ? NOT_STATED : v) },
    { key: "source", title: "By source", label: (v) => (unknown(v) ? NOT_STATED : sourceName(v)) },
  ];

  /** Entries of one breakdown (core.chartEntries), coloured: wine fading to paper, others stone. */
  function chartEntries(list, unresolved, label) {
    const { all, shown } = core.chartEntries(list, unresolved, label, MAX_BARS);
    let i = 0;
    const paint = (e) => (e.unresolved ? e : { ...e, color: e.other ? C.stone : SERIES[Math.min(i++, SERIES.length - 1)] });
    const painted = shown.map(paint);
    return { all, shown: painted };
  }

  /** A horizontal composition strip of the entries' shares (only drawn when nothing is negative). */
  function strip(entries, aria) {
    if (entries.some((e) => e.value < 0)) return null;
    const total = sum(entries.map((e) => e.value));
    if (!(total > 0)) return null;
    let x = 0;
    const parts = entries.map((e) => {
      const w = (e.value / total) * 100;
      const r = e.unresolved
        ? s("rect", { x: x.toFixed(3), y: 0.5, width: Math.max(w, 0).toFixed(3), height: 9, fill: C.paper2, stroke: C.muted,
          "stroke-width": 1, "stroke-dasharray": "3 2", "vector-effect": "non-scaling-stroke" })
        : s("rect", { x: x.toFixed(3), y: 0, width: Math.max(w, 0).toFixed(3), height: 10, fill: e.color });
      x += w;
      return r;
    });
    return s("svg", { class: "dist-strip", viewBox: "0 0 100 10", preserveAspectRatio: "none", role: "img", "aria-label": aria, focusable: "false" }, parts);
  }

  /** One bar row: label, value and an SVG bar drawn from zero (to the left for a negative value). */
  function barRow(e, lo, hi) {
    const span = hi - lo || 1;
    const zero = ((0 - lo) / span) * 100;
    const end = ((e.value - lo) / span) * 100;
    const x = Math.min(zero, end);
    const w = Math.max(Math.abs(end - zero), e.value === 0 ? 0 : 0.6);
    const bar = e.unresolved
      ? s("rect", { x: x.toFixed(3), y: 1, width: w.toFixed(3), height: 8, fill: C.paper2, stroke: C.muted, "stroke-width": 1,
        "stroke-dasharray": "3 2", "vector-effect": "non-scaling-stroke" })
      : s("rect", { x: x.toFixed(3), y: 0, width: w.toFixed(3), height: 10, fill: e.other ? C.stone : C.wine });
    const svg = s("svg", { class: "bar", viewBox: "0 0 100 10", preserveAspectRatio: "none", focusable: "false" },
      s("rect", { x: 0, y: 4.5, width: 100, height: 1, fill: C.line }),
      bar,
      lo < 0 ? s("rect", { x: (zero - 0.15).toFixed(3), y: 0, width: 0.3, height: 10, fill: C.muted }) : null);
    return h("li", { class: e.unresolved ? "bar-row is-unresolved" : e.other ? "bar-row is-other" : "bar-row" },
      h("span", { class: "bar-label" },
        h("i", { class: "sw", style: e.unresolved ? null : `background:${e.color || C.wine}` }), e.label),
      h("span", { class: `bar-val${e.value < 0 ? " neg" : ""}` }, `${fmtPct(e.value)}%`),
      svg);
  }

  function bars(entries) {
    const lo = Math.min(0, ...entries.map((e) => e.value));
    const hi = Math.max(0, ...entries.map((e) => e.value));
    return h("ol", { class: "bars", "aria-hidden": "true" }, entries.map((e) => barRow(e, lo, hi)));
  }

  function altTable(caption, head, rows) {
    return h("details", { class: "dist-table" },
      h("summary", {}, `Table: ${caption}`),
      h("div", { class: "table-wrap" },
        h("table", {},
          h("caption", { class: "sr-only" }, caption),
          h("thead", {}, h("tr", {}, head.map((x, i) => h("th", { scope: "col", class: i === head.length - 1 ? "num" : null }, x)))),
          h("tbody", {}, rows.map((r) => h("tr", { class: r.unresolved ? "is-unresolved" : null },
            r.cells.map((c, i) => (i === 0 ? h("th", { scope: "row" }, c) : h("td", { class: i === r.cells.length - 1 ? "num" : null }, c)))))))));
  }

  function provenance(f, rows) {
    const ids = ((f.breakdowns && f.breakdowns.source) || []).map((p) => p[0]).filter((x) => x && x !== UNRESOLVED && x !== "Unknown");
    const srcIds = ids.length ? [...new Set(ids)] : [f.source];
    const dates = rows.map((r) => r.as_of).filter(Boolean).sort();
    const lo = dates[0] || f.as_of;
    const hi = dates[dates.length - 1] || f.as_of;
    return h("p", { class: "dist-source" }, "Source: ",
      srcIds.map((id, i) => [i ? ", " : null,
        id === f.source && safeUrl(f.source_url) ? ext(f.source_url, sourceName(id))
          : safeUrl((state.sources[id] || {}).url) ? ext(state.sources[id].url, sourceName(id)) : sourceName(id)]),
      ` · as of ${fmtRange(lo, hi)}`);
  }

  function renderDistribution(f, rows, t) {
    const b = f.breakdowns;
    const nRows = t.nRows != null ? t.nRows : rows.length;
    let fromListed = false;
    let bd = b;
    if (!bd) {
      if (t.truncated) return null; // an older file without breakdowns: the listed rows are not the whole fund
      fromListed = true;
      bd = core.localBreakdowns(rows, t.unresolved);
    }
    const scope = fromListed ? `the ${fmtInt(rows.length)} rows of this file` : `all ${fmtInt(nRows)} rows of the fund${t.truncated ? ", not only those listed below" : ""}`;
    const cards = DIMENSIONS.map((d) => {
      const { all, shown } = chartEntries(bd[d.key], t.unresolved, d.label);
      const top = shown.filter((e) => !e.unresolved).slice(0, 3).map((e) => `${e.label} ${fmtPct(e.value)}%`).join(", ");
      const un = shown[shown.length - 1];
      const aria = `${d.title}: ${top || "no resolved holdings"}; ${UNRESOLVED} ${fmtPct(un.value)}%.`;
      const bar = strip(shown, aria);
      const resolvedEntries = all.filter((e) => !e.unresolved);
      const noLabels = resolvedEntries.length > 0 && resolvedEntries.every((e) => unknown(e.key));
      return h("figure", { class: bar ? "dist-card" : "dist-card no-strip", role: bar ? null : "group", "aria-label": bar ? null : aria },
        h("figcaption", {}, h("h4", {}, d.title),
          noLabels ? h("p", { class: "dist-sub" }, `The source gives no ${d.title.replace(/^By /, "")} for these holdings.`) : null),
        bar || h("p", { class: "dist-nostrip" }, "Some entries are negative, so no share bar is drawn; the bars start at zero."),
        bars(shown),
        altTable(`${d.title.toLowerCase()}, percent of net assets`, [d.title.replace(/^By /, "").replace(/^\w/, (c) => c.toUpperCase()), "% of net assets"],
          all.map((e) => ({ cells: [e.label, fmtPct(e.value)], unresolved: e.unresolved }))),
        provenance(f, rows));
    });
    cards.push(topTenCard(f, rows, t, bd));
    return h("section", { class: "dist", "aria-labelledby": "dist-title" },
      h("p", { class: "eyebrow" }, "Distribution"),
      h("h3", { id: "dist-title" }, "Where the ", h("em", {}, "money"), " is"),
      h("p", { class: "dist-intro" },
        `Percent of the fund's net assets over ${scope}, as the source reports them and never rescaled. `,
        "Unresolved, the weight the source does not tie to an ISIN, is always shown as its own dashed bar."),
      h("div", { class: "dist-grid" }, cards));
  }

  function topTenCard(f, rows, t, bd) {
    const tops = topHoldings(rows).map((x) => ({ ...x, color: C.wine }));
    const top10 = bd.top10_pct != null ? Number(bd.top10_pct) : sum(tops.map((x) => x.value));
    const others = t.inRows - top10;
    const summary = [
      { label: "Ten largest together", value: top10, color: C.wine },
      { label: "All other holdings", value: others, color: C.stone, other: true },
      { label: UNRESOLVED, value: t.unresolved, unresolved: true },
    ];
    const aria = `Ten largest holdings: ${fmtPct(top10)}% of net assets; all other holdings ${fmtPct(others)}%; ${UNRESOLVED} ${fmtPct(t.unresolved)}%.`;
    const bar = strip(summary, aria);
    return h("figure", { class: bar ? "dist-card dist-card-wide" : "dist-card dist-card-wide no-strip", role: bar ? null : "group", "aria-label": bar ? null : aria },
      h("figcaption", {}, h("h4", {}, "Ten largest holdings"),
        h("p", { class: "dist-sub" }, `Together ${fmtPct(top10, 2)}% of net assets.`)),
      bar || h("p", { class: "dist-nostrip" }, "Some totals are negative, so no share bar is drawn."),
      bars(tops.concat(summary.slice(1))),
      altTable("ten largest holdings, percent of net assets", ["Holding", "ISIN", "% of net assets"],
        tops.map((x) => ({ cells: [x.label, x.isin, fmtPct(x.value)] }))
          .concat(summary.map((x) => ({ cells: [x.label, "", fmtPct(x.value)], unresolved: x.unresolved })))),
      provenance(f, rows));
  }

  /* ---------- holdings table ---------- */

  function renderTable(f, rows, fof, tot) {
    const cols = [
      { key: "isin", label: "ISIN", type: "s", cls: "col-isin" },
      { key: "name", label: "Name", type: "s" },
      { key: "weight", label: "Weight %", type: "n", num: true },
      { key: "asset", label: "Asset class", type: "s", opt: true },
      { key: "country", label: "Country", type: "s", opt: true },
      { key: "sector", label: "Sector", type: "s", opt: true },
      { key: "source", label: "Source", type: "s" },
      { key: "as_of", label: "As of", type: "s", opt: true },
    ];
    if (fof) cols.push({ key: "via", label: "Via", type: "s", opt: true });

    const t = { sort: null, dir: -1, all: false, filter: "" };
    const tbody = h("tbody");
    const more = h("button", { class: "btn btn-ghost btn-sm", type: "button" });
    const count = h("p", {});
    const ths = cols.map((c) => {
      const btn = h("button", { type: "button" }, c.label);
      const th = h("th", { scope: "col", class: [c.num ? "num" : "", c.opt ? "col-opt" : "", c.cls || ""].join(" ").trim() || null,
        "aria-sort": c.key === "weight" ? "descending" : "none" }, btn);
      btn.addEventListener("click", () => {
        const current = t.sort || "weight";
        t.dir = current === c.key ? -t.dir : c.type === "n" ? -1 : 1;
        t.sort = c.key;
        ths.forEach((x) => x.setAttribute("aria-sort", "none"));
        th.setAttribute("aria-sort", t.dir > 0 ? "ascending" : "descending");
        draw();
        announce(`Sorted by ${c.label.toLowerCase()}, ${t.dir > 0 ? "ascending" : "descending"}.`);
      });
      return th;
    });

    function sorted() {
      const q = norm(t.filter);
      const out = q ? rows.filter((r) => norm(`${r.isin || ""} ${r.name} ${r.asset} ${r.via} ${r.country} ${countryName(r.country) || ""} ${r.sector}`).includes(q)) : rows.slice();
      if (t.sort) {
        const c = cols.find((x) => x.key === t.sort);
        out.sort((a, b) => {
          const x = a[c.key], y = b[c.key];
          if (x == null || x === "") return 1;
          if (y == null || y === "") return -1;
          return (c.type === "n" ? x - y : String(x).localeCompare(String(y))) * t.dir || a.i - b.i;
        });
      }
      return out;
    }

    function draw() {
      const out = sorted();
      const shown = t.all ? out : out.slice(0, FIRST_ROWS);
      tbody.replaceChildren(...shown.map((r) => rowEl(r, cols, f)));
      more.hidden = out.length <= FIRST_ROWS || t.all;
      more.textContent = `Show all ${fmtInt(out.length)} rows`;
      count.textContent = `Showing ${fmtInt(shown.length)} of ${fmtInt(out.length)} rows${t.filter ? " matching the filter" : ""}` +
        (tot.truncated ? `, the largest ${fmtInt(rows.length)} of the fund's ${fmtInt(tot.nRows)} rows.` : ".");
    }
    state.redrawTable = draw;

    more.addEventListener("click", () => {
      t.all = true;
      draw();
      announce(`Showing all ${fmtInt(rows.length)} rows.`);
    });

    const filter = h("input", { class: "filter", type: "search", placeholder: "Filter rows by ISIN, name, country…", "aria-label": "Filter rows" });
    let ft = 0;
    filter.addEventListener("input", () => {
      t.filter = filter.value;
      draw();
      clearTimeout(ft);
      ft = setTimeout(() => announce(count.textContent), 500);
    });

    const csv = h("button", { class: "btn btn-sm", type: "button" }, "Download CSV");
    csv.addEventListener("click", () => downloadCsv(f, rows, tot));
    const src = state.sources[f.source] || {};

    const footRow = (name, why, weight, srcId, asOf, cls) => {
      const cells = {
        isin: h("td", { class: "isin col-isin" }, "—"),
        name: h("td", { class: "name" }, name, h("span", { class: "why" }, why)),
        weight: h("td", { class: `num${weight < 0 ? " neg" : ""}` }, fmtPct(weight)),
        source: h("td", {}, badge(srcId, true)),
        as_of: h("td", { class: "col-opt dim" }, asOf || ""),
      };
      return h("tr", { class: cls }, cols.map((c) => cells[c.key] || h("td", { class: c.opt ? "col-opt" : null }, "")));
    };
    const un = f.unresolved || {};
    const unWhy = [un.note, `100 minus the resolved weight of all ${fmtInt(tot.nRows != null ? tot.nRows : rows.length)} rows: what the source does not tie to an ISIN.`]
      .filter(Boolean).join(" ");
    const foot = h("tfoot", {},
      tot.truncated
        ? footRow("Not listed here", h("span", {}, `The other ${fmtInt(tot.nRows - rows.length)} rows, all resolved to ISINs, are in the `,
          safeUrl(f.full_data_url) ? ext(f.full_data_url, "full Parquet file") : "full Parquet file", "."),
          tot.notListed, f.source, f.as_of, "not-listed")
        : null,
      footRow("Unresolved weight", unWhy, tot.unresolved, un.source || f.source, un.as_of || f.as_of, "unresolved"));

    const captionText = `Constituents of ${f.name}, largest first. Weights are percent of net assets, never rescaled. Source: ${src.name || sourceLabel(f.source)}, as of ${f.as_of || "unknown"}.`;
    const table = h("table", { class: "holdings" }, h("caption", { class: "sr-only" }, captionText), h("thead", {}, h("tr", {}, ths)), tbody, foot);
    draw();

    return h("section", { class: "holdings-block", "aria-label": "Holdings" },
      h("div", { class: "toolbar" }, filter,
        h("div", { class: "actions" }, csv,
          h("a", { class: "btn btn-ghost btn-sm", href: fundUrl(f.id), type: "application/json" }, "JSON"),
          safeUrl(f.source_url) ? h("a", { class: "btn btn-ghost btn-sm", href: f.source_url, rel: "noopener" }, "Source ↗") : null)),
      h("p", { class: "table-caption" }, captionText),
      h("div", { class: "table-wrap" }, table),
      h("div", { class: "table-foot" }, count, more),
      h("p", { class: "table-foot" },
        h("span", {}, "Data: ", safeUrl(f.source_url) ? ext(f.source_url, src.name || sourceLabel(f.source)) : src.name || sourceLabel(f.source),
          f.credit ? [" · ", linkify(f.credit)] : null, ` · as of ${f.as_of || "unknown"}. openbasket only gathers this data; check the source before relying on it.`)));
  }

  function rowEl(r, cols, f) {
    const fundLink = r.isin && state.index && state.index.aliases[r.isin];
    const isinCell = r.isin
      ? fundLink
        ? h("a", { href: `?isin=${encodeURIComponent(r.isin)}`, title: "This holding is a fund: open its look-through",
          onclick: (ev) => { if (!ev.metaKey && !ev.ctrlKey) { ev.preventDefault(); openFund(r.isin, { focus: true }); } } }, h("code", {}, r.isin))
        : h("code", {}, r.isin)
      : "—";
    // On phones the optional columns are hidden, so the ISIN, as-of date and route go under the name.
    const sub = [r.isin || "no ISIN", r.as_of ? `as of ${r.as_of}` : null, r.depth > 0 && r.via ? `via ${r.via}` : null].filter(Boolean).join(" · ");
    const cells = {
      isin: h("td", { class: "isin col-isin" }, isinCell),
      name: h("td", { class: "name" }, r.name, h("span", { class: "sub" }, sub)),
      weight: h("td", { class: `num${r.weight < 0 ? " neg" : ""}` }, fmtPct(r.weight)),
      asset: h("td", { class: "col-opt dim" }, r.asset),
      country: h("td", { class: "col-opt dim", title: countryName(r.country) !== r.country ? countryName(r.country) : null }, r.country),
      sector: h("td", { class: "col-opt dim" }, r.sector),
      source: h("td", {}, badge(r.source, r.source === f.source)),
      as_of: h("td", { class: "col-opt dim" }, r.as_of || ""),
      via: h("td", { class: "col-opt dim" }, r.depth > 0 ? r.via : "direct"),
    };
    return h("tr", {}, cols.map((c) => cells[c.key]));
  }

  /* ---------- CSV ---------- */

  const csvCell = core.csvCell;

  function downloadCsv(f, rows, tot) {
    const header = ["fund_id", "fund_isin", "fund_name", "constituent_isin", "name", "weight_pct", "asset_class", "country", "sector",
      "source", "source_name", "source_licence", "credit", "as_of", "depth", "via", "fund_source_url", "file_truncated", "full_data_url"];
    const credit = (x) => (x === f.source ? f.credit : state.sources[x] && state.sources[x].credit) || "";
    const line = (r) => [f.id, f.isin, f.name, r.isin, r.name, r.weight, r.asset, r.country, r.sector, r.source, sourceName(r.source),
      (state.sources[r.source] || {}).licence || "", credit(r.source), r.as_of, r.depth, r.via, f.source_url, tot.truncated,
      f.full_data_url].map(csvCell).join(",");
    const lines = [header.join(","), ...rows.map(line), ...core.closingRows(f, rows, tot).map(line)];
    const blob = new Blob(["﻿" + lines.join("\r\n") + "\r\n"], { type: "text/csv;charset=utf-8" });
    const a = h("a", { download: `openbasket_${f.id}_${f.as_of || "undated"}${tot.truncated ? "_largest" : ""}.csv` });
    a.href = URL.createObjectURL(blob);
    document.body.append(a);
    a.click();
    setTimeout(() => {
      URL.revokeObjectURL(a.href);
      a.remove();
    }, 0);
  }

  /* ---------- start ---------- */

  function start() {
    const year = $("#year");
    if (year) year.textContent = new Date().getFullYear();
    setupApi();
    setupSearch();
    const metaReady = loadMeta();
    const route = (opts) => {
      const id = idFromLocation();
      const replace = core.fundFromHash(location.search, location.hash);
      if (id) metaReady.finally(() => openFund(id, { push: false, replace, ...opts }));
      else {
        state.shownId = null;
        state.token++;
        section.hidden = true;
        document.title = "openbasket — what an ETF or fund holds, by ISIN, from public sources";
      }
    };
    // Back and forward between funds. A click on an in-page link (#api, #top) also fires popstate;
    // it leaves the fund in the address unchanged, so nothing is reloaded and the browser scrolls.
    window.addEventListener("popstate", () => {
      if (idFromLocation() !== state.shownId) route({ scroll: true });
    });
    // #ISIN deep links typed into the address bar. Other hashes are left to the browser.
    window.addEventListener("hashchange", () => {
      const id = core.hashRoute(location.hash, state.shownId);
      if (id) metaReady.finally(() => openFund(id, { push: false, replace: true, scroll: true }));
    });
    route({ scroll: true });
  }

  start();
})();
