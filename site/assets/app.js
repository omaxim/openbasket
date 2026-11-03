/* openbasket landing page: fund search, fund view, release and API sections.
   Reads the static JSON API described in docs/data-api.md; the API base comes
   from <body data-api>, so the same page runs on fixtures locally. */
(() => {
  "use strict";

  const body = document.body;
  const API = (body.dataset.api || "api/v1").replace(/\/+$/, "");
  const DOCS = (body.dataset.docs || "docs/").replace(/\/?$/, "/");
  const FIRST_ROWS = 100;
  const MAX_SUGGESTIONS = 8;
  const ISIN_RE = /^[A-Z]{2}[A-Z0-9]{9}[0-9]$/;
  const SERIES_RE = /^S\d{9}$/;
  const SOURCE_LABEL = {
    ishares: "iShares",
    spdr: "SPDR",
    tickerinside: "TickerInside",
    nport: "SEC N-PORT",
    security_master: "Security master",
  };
  const MATCHED_VIA = {
    ishares: "the fund's ISIN in the iShares fund list",
    spdr: "the fund's ISIN in the SPDR fund list",
    nport: "an SEC N-PORT filing for the fund's series",
    openfigi: "its US ticker (from OpenFIGI) in SEC's fund ticker list",
    proxy: "a configured proxy fund on the same index",
    security_master: "the security master: a single security, its own constituent",
  };

  const state = { meta: null, sources: {}, index: null, indexPromise: null, fund: null, token: 0 };

  /* ---------- small helpers ---------- */

  const $ = (sel, el = document) => el.querySelector(sel);

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

  const ext = (href, text) => h("a", { href, rel: "noopener" }, text);

  function fmtPct(v, dp) {
    if (v == null || Number.isNaN(v)) return "–";
    const a = Math.abs(v);
    const d = dp != null ? dp : a >= 0.1 || a === 0 ? 2 : a >= 0.001 ? 3 : 5;
    const s = v.toLocaleString("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d });
    return /^-[0.]+$/.test(s) ? s.slice(1) : s;
  }
  const fmtInt = (n) => (n == null ? "–" : Number(n).toLocaleString("en-GB"));

  function parseDate(s) {
    if (!s) return null;
    const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(s) ? s + "T00:00:00Z" : s);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  function fmtDate(s) {
    const d = parseDate(s);
    return d ? d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }) : s || "–";
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

  function isValidIsin(s) {
    if (!ISIN_RE.test(s)) return false;
    const digits = s.replace(/[A-Z]/g, (c) => String(c.charCodeAt(0) - 55));
    let sum = 0;
    for (let i = 0; i < digits.length; i++) {
      let n = Number(digits[digits.length - 1 - i]);
      if (i % 2 === 1) {
        n *= 2;
        if (n > 9) n -= 9;
      }
      sum += n;
    }
    return sum % 10 === 0;
  }

  const LICENCE_URL = { "CC BY 4.0": "https://creativecommons.org/licenses/by/4.0/" };
  const licence = (text) => (LICENCE_URL[text] ? ext(LICENCE_URL[text], text) : text || "–");
  const sourceLabel = (id) => SOURCE_LABEL[id] || id || "unknown";
  const sourceName = (id) => (state.sources[id] && state.sources[id].name) || sourceLabel(id);
  const badge = (id, main) => h("span", { class: main ? "badge badge-main" : "badge", title: sourceName(id) }, sourceLabel(id));
  const fundUrl = (id) => `${API}/funds/${encodeURIComponent(id)}.json`;

  async function getJSON(path) {
    const res = await fetch(path, { headers: { Accept: "application/json" } });
    if (res.status === 404) return { __notFound: true };
    if (!res.ok) throw new Error(`HTTP ${res.status} for ${path}`);
    return res.json();
  }

  /* ---------- meta, release and API sections ---------- */

  async function loadMeta() {
    const view = $("#release-view");
    try {
      const meta = await getJSON(`${API}/meta.json`);
      if (meta.__notFound) throw new Error("meta.json not found");
      state.meta = meta;
      for (const s of meta.sources || []) state.sources[s.id] = s;
      view.replaceChildren(renderRelease(meta));
    } catch (e) {
      view.replaceChildren(
        h("p", { class: "notice" }, "The release information could not be loaded. The releases are listed on ",
          ext("https://github.com/omaxim/openbasket/releases", "GitHub"), ".")
      );
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

  function sourceCard(s) {
    const range = s.as_of_min && s.as_of_max
      ? s.as_of_min === s.as_of_max ? fmtDate(s.as_of_min) : `${fmtDate(s.as_of_min)} to ${fmtDate(s.as_of_max)}`
      : "–";
    return h("article", { class: "card" },
      h("h3", {}, safeUrl(s.url) ? ext(s.url, s.name || sourceLabel(s.id)) : s.name || sourceLabel(s.id)),
      h("dl", { class: "kv" },
        h("dt", {}, "Licence"), h("dd", {}, licence(s.licence)),
        h("dt", {}, "Credit"), h("dd", {}, s.credit || "–"),
        h("dt", {}, "Holdings as of"), h("dd", {}, range),
        h("dt", {}, "Main source of"), h("dd", {}, `${fmtInt(s.n_funds)} funds`)),
      h("p", { class: "tags" }, badge(s.id)));
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

  const norm = (s) =>
    (s || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[®™]/g, "")
      .replace(/&/g, " and ").replace(/[^a-z0-9]+/g, " ").trim();
  const compact = (s) => (s || "").toUpperCase().replace(/[^A-Z0-9]/g, "");

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

  /** Characters of q in order inside s; higher for tighter matches, 0 if absent. */
  function subsequence(q, s) {
    let qi = 0, start = -1, last = -1, runs = 0;
    for (let i = 0; i < s.length && qi < q.length; i++) {
      if (s[i] === q[qi]) {
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
        const s = 900 + c.length - (k === e.id || k === e.isin ? 0 : 1);
        if (s > best) { best = s; via = k === e.id || k === e.isin ? null : k; }
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
    openFund(r.via || r.e.id);
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
      return openFund(c);
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
        openFund(a.dataset.isin);
      }));
  }

  /* ---------- routing ---------- */

  function idFromLocation() {
    const p = new URLSearchParams(location.search);
    const q = compact(p.get("isin") || p.get("id") || "");
    if (q) return q;
    const hash = compact(decodeURIComponent(location.hash.slice(1)));
    return ISIN_RE.test(hash) || SERIES_RE.test(hash) ? hash : null;
  }

  function pushId(id) {
    const url = new URL(location.href);
    url.search = "";
    url.searchParams.set("isin", id);
    url.hash = "";
    if (url.href !== location.href) history.pushState({ id }, "", url.href);
  }

  /* ---------- fund view ---------- */

  const section = $("#fund");
  const view = $("#fund-view");

  function show(...nodes) {
    section.hidden = false;
    view.replaceChildren(...nodes.flat(Infinity).filter(Boolean));
  }

  async function openFund(raw, opts = {}) {
    const requested = compact(raw);
    if (!requested) return;
    if (opts.push !== false) pushId(requested);
    const token = ++state.token;
    section.hidden = false;
    if (opts.scroll !== false) section.scrollIntoView({ behavior: "smooth", block: "start" });

    if (!ISIN_RE.test(requested) && !SERIES_RE.test(requested)) return renderInvalid(requested, "is not an ISIN or an SEC series ID");
    if (ISIN_RE.test(requested) && !isValidIsin(requested)) return renderInvalid(requested, "has a wrong check digit, so it is not a valid ISIN");

    show(h("div", { class: "state", role: "status" }, h("p", { class: "eyebrow" }, "Looking through"), h("h2", {}, requested),
      h("p", {}, "Loading holdings…")));
    try {
      let id = requested;
      if (state.index && state.index.aliases[id]) id = state.index.aliases[id];
      let data = await getJSON(fundUrl(id));
      if (data.__notFound && !state.index) {
        const idx = await loadIndex().catch(() => null);
        if (idx && idx.aliases[requested] && idx.aliases[requested] !== id) data = await getJSON(fundUrl((id = idx.aliases[requested])));
      }
      if (data && data.alias_of) data = await getJSON(fundUrl((id = compact(data.alias_of))));
      if (token !== state.token) return;
      if (data.__notFound) return renderNotCovered(requested);
      if (!Array.isArray(data.constituents)) throw new Error("This file is not a fund file.");
      state.fund = data;
      renderFund(data, requested);
    } catch (err) {
      if (token !== state.token) return;
      renderError(requested, err);
    }
  }

  function renderInvalid(id, why) {
    document.title = "openbasket";
    show(h("div", { class: "state" }, h("p", { class: "eyebrow" }, "Not an identifier"), h("h2", {}, id),
      h("p", {}, `“${id}” ${why}. Search by fund name or ticker instead, or check the ISIN: twelve characters, two letters, nine letters or digits and a check digit.`)));
  }

  function renderNotCovered(id) {
    document.title = `${id} not covered · openbasket`;
    show(h("div", { class: "state" }, h("p", { class: "eyebrow" }, "Not covered"), h("h2", {}, id),
      h("p", {}, "This build has no fund file for this identifier. It may be a single stock or bond (which is its own constituent), a fund that none of the sources covers yet, or a share class whose ISIN no source reports."),
      h("p", {}, "The sources used are listed under ", h("a", { href: "#release" }, "Latest data release"), "; what is and is not covered is described in the ",
        h("a", { href: `${DOCS}sources.html` }, "sources documentation"), ".")));
  }

  function renderError(id, err) {
    document.title = "openbasket";
    const retry = h("button", { class: "btn btn-sm", type: "button" }, "Try again");
    retry.addEventListener("click", () => openFund(id, { push: false }));
    show(h("div", { class: "state" }, h("p", { class: "eyebrow" }, "Something went wrong"), h("h2", {}, id),
      h("p", {}, `The fund file could not be loaded (${err && err.message ? err.message : "network error"}).`), retry));
  }

  function rowsOf(f) {
    const ix = Object.fromEntries((f.fields || []).map((n, i) => [n, i]));
    return f.constituents.map((r, i) => ({
      i, isin: r[ix.isin], name: r[ix.name] || "", weight: Number(r[ix.weight_pct]), asset: r[ix.asset_class] || "",
      source: r[ix.source] || f.source, as_of: r[ix.as_of] || f.as_of, depth: Number(r[ix.depth] || 0), via: r[ix.via] || "",
    }));
  }

  function rowSourceSummary(rows) {
    const m = new Map();
    for (const r of rows) {
      const s = m.get(r.source) || { id: r.source, weight: 0, n: 0, min: r.as_of, max: r.as_of };
      s.weight += r.weight;
      s.n++;
      if (r.as_of && (!s.min || r.as_of < s.min)) s.min = r.as_of;
      if (r.as_of && (!s.max || r.as_of > s.max)) s.max = r.as_of;
      m.set(r.source, s);
    }
    return [...m.values()].sort((a, b) => b.weight - a.weight);
  }

  function renderFund(f, requested) {
    const rows = rowsOf(f);
    const fof = rows.some((r) => r.depth > 0);
    const src = state.sources[f.source] || {};
    const label = f.isin || f.id;
    document.title = `${f.name} (${f.ticker || label}) · openbasket`;

    const otherIsins = (f.isins || []).filter((x) => x !== f.isin);
    const head = h("div", { class: "fund-head" },
      h("p", { class: "eyebrow" }, `Fund look-through${f.issuer ? ` · ${f.issuer}` : ""}`),
      h("h2", {}, f.name || label),
      h("ul", { class: "ids" },
        h("li", {}, h("b", {}, "ISIN"), f.isin ? h("code", {}, f.isin) : "not known"),
        f.ticker ? h("li", {}, h("b", {}, "Ticker"), f.ticker) : null,
        f.issuer ? h("li", {}, h("b", {}, "Issuer"), f.issuer) : null,
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
      f.credit ? h("p", { class: "credit" }, f.credit) : null,
      h("dl", { class: "kv" },
        h("dt", {}, "Holdings as of"), h("dd", {}, fmtDate(f.as_of), age ? h("span", { class: "muted" }, ` (${age})`) : null),
        h("dt", {}, "Matched via"), h("dd", {}, MATCHED_VIA[f.matched_via] || f.matched_via || "–"),
        src.licence ? [h("dt", {}, "Licence"), h("dd", {}, licence(src.licence))] : null),
      f.note ? h("p", { class: "notice notice-warn" }, h("strong", {}, "Note: "), f.note) : null,
      h("p", { class: "links" },
        safeUrl(f.source_url) ? ext(f.source_url, "Check it at the source ↗") : null,
        safeUrl(src.url) ? ext(src.url, `${sourceLabel(f.source)} website ↗`) : null),
      rowSources.length
        ? h("ul", { class: "row-sources" }, h("li", { class: "head" }, "Rows in this file by source"),
          rowSources.map((s) => h("li", {}, badge(s.id, s.id === f.source), " ",
            `${fmtPct(s.weight)}% of net assets in ${fmtInt(s.n)} rows, as of ${s.min === s.max ? s.min : `${s.min} to ${s.max}`}`,
            s.id !== f.source && state.sources[s.id] ? ` · ${state.sources[s.id].credit}` : null)))
        : null);

    const unresolved = f.unresolved && f.unresolved.weight_pct != null ? f.unresolved.weight_pct : f.unresolved_pct;
    const chips = h("div", { class: "chips" },
      chip(`${fmtPct(f.resolved_pct, 2)}%`, "resolved to ISINs"),
      chip(`${fmtPct(unresolved, 2)}%`, "unresolved", unresolved < 0),
      chip(`${fmtPct(f.reported_total_pct, 2)}%`, "reported total"),
      chip(fmtInt(f.n_constituents), f.truncated ? `constituents, ${fmtInt(rows.length)} listed here` : "constituents"));

    const notices = [];
    if (f.truncated) {
      notices.push(h("p", { class: "notice notice-warn" }, h("strong", {}, "Shortened list. "),
        `This fund has ${fmtInt(f.n_constituents)} constituents; this page lists the ${fmtInt(rows.length)} largest. Every row is in the `,
        safeUrl(f.full_data_url) ? ext(f.full_data_url, "full Parquet file of this release") : "release Parquet file", "."));
    }
    if (unresolved < 0) {
      notices.push(h("p", { class: "notice" }, h("strong", {}, "Negative unresolved weight. "),
        "The listed positions add up to more than 100% of net assets, for example when the fund has liabilities or purchases not yet settled. Nothing is rescaled. ",
        h("a", { href: `${DOCS}output.html#unresolved-weight` }, "More on unresolved weight"), "."));
    }

    show(h("div", { class: "fund-grid" }, head, panel), chips, notices, renderTable(f, rows, fof, unresolved));
  }

  function chip(value, label, warn) {
    return h("div", { class: warn ? "chip chip-warn" : "chip" }, h("p", { class: "stat" }, value, h("span", {}, label)));
  }

  function renderTable(f, rows, fof, unresolved) {
    const cols = [
      { key: "isin", label: "ISIN", type: "s", cls: "col-isin" },
      { key: "name", label: "Name", type: "s" },
      { key: "weight", label: "Weight %", type: "n", num: true },
      { key: "asset", label: "Asset class", type: "s", opt: true },
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
      });
      return th;
    });

    function sorted() {
      const q = norm(t.filter);
      let out = q ? rows.filter((r) => norm(`${r.isin || ""} ${r.name} ${r.asset} ${r.via}`).includes(q)) : rows.slice();
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
      tbody.replaceChildren(...shown.map((r) => rowEl(r, cols)));
      more.hidden = out.length <= FIRST_ROWS || t.all;
      more.textContent = `Show all ${fmtInt(out.length)} rows`;
      count.textContent = `Showing ${fmtInt(shown.length)} of ${fmtInt(out.length)} rows${t.filter ? " matching the filter" : ""}` +
        (f.truncated ? `, the largest ${fmtInt(rows.length)} of ${fmtInt(f.n_constituents)} constituents.` : ".");
    }

    more.addEventListener("click", () => {
      t.all = true;
      draw();
    });

    const filter = h("input", { class: "filter", type: "search", placeholder: "Filter rows by ISIN or name", "aria-label": "Filter rows" });
    filter.addEventListener("input", () => {
      t.filter = filter.value;
      draw();
    });

    const csv = h("button", { class: "btn btn-sm", type: "button" }, "Download CSV");
    csv.addEventListener("click", () => downloadCsv(f, rows, unresolved));
    const src = state.sources[f.source] || {};
    const why = f.unresolved && f.unresolved.note;
    const foot = h("tfoot", {}, h("tr", {},
      h("td", { class: "isin col-isin" }, "—"),
      h("td", { class: "name" }, "Unresolved weight",
        h("span", { class: "why" }, why || "100 minus the sum of the rows above: what the source does not tie to an ISIN.")),
      h("td", { class: `num${unresolved < 0 ? " neg" : ""}` }, fmtPct(unresolved)),
      h("td", { class: "col-opt" }, ""),
      h("td", {}, badge(f.source, true)),
      h("td", { class: "col-opt dim" }, f.as_of || ""),
      fof ? h("td", { class: "col-opt" }, "") : null));

    const captionText = `Constituents of ${f.name}, largest first. Weights are percent of net assets, never rescaled. Source: ${src.name || sourceLabel(f.source)}, as of ${f.as_of || "unknown"}.`;
    const table = h("table", {}, h("caption", { class: "sr-only" }, captionText), h("thead", {}, h("tr", {}, ths)), tbody, foot);
    draw();

    return h("div", {},
      h("div", { class: "toolbar" }, filter,
        h("div", { class: "actions" }, csv,
          h("a", { class: "btn btn-ghost btn-sm", href: fundUrl(f.id), type: "application/json" }, "JSON"),
          safeUrl(f.source_url) ? h("a", { class: "btn btn-ghost btn-sm", href: f.source_url, rel: "noopener" }, "Source ↗") : null)),
      h("p", { class: "table-caption" }, captionText),
      h("div", { class: "table-wrap" }, table),
      h("div", { class: "table-foot" }, count, more),
      h("p", { class: "table-foot" },
        h("span", {}, "Data: ", safeUrl(f.source_url) ? ext(f.source_url, src.name || sourceLabel(f.source)) : src.name || sourceLabel(f.source),
          f.credit ? ` · ${f.credit}` : null, ` · as of ${f.as_of || "unknown"}. openbasket only gathers this data; check the source before relying on it.`)));
  }

  function rowEl(r, cols) {
    const fundLink = r.isin && state.index && state.index.aliases[r.isin];
    const isinCell = r.isin
      ? fundLink
        ? h("a", { href: `?isin=${encodeURIComponent(r.isin)}`, title: "This holding is a fund: open its look-through",
          onclick: (ev) => { if (!ev.metaKey && !ev.ctrlKey) { ev.preventDefault(); openFund(r.isin); } } }, h("code", {}, r.isin))
        : h("code", {}, r.isin)
      : "—";
    const cells = {
      isin: h("td", { class: "isin col-isin" }, isinCell),
      name: h("td", { class: "name" }, r.name, h("span", { class: "sub" }, r.isin || "no ISIN")),
      weight: h("td", { class: `num${r.weight < 0 ? " neg" : ""}` }, fmtPct(r.weight)),
      asset: h("td", { class: "col-opt dim" }, r.asset),
      source: h("td", {}, badge(r.source)),
      as_of: h("td", { class: "col-opt dim" }, r.as_of || ""),
      via: h("td", { class: "col-opt dim" }, r.depth > 0 ? r.via : "direct"),
    };
    return h("tr", {}, cols.map((c) => cells[c.key]));
  }

  /* ---------- CSV ---------- */

  function csvCell(v) {
    if (v == null) return "";
    if (typeof v === "number") return Number.isFinite(v) ? String(v) : "";
    let s = String(v);
    if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`;
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }

  function downloadCsv(f, rows, unresolved) {
    const header = ["fund_id", "fund_isin", "fund_name", "constituent_isin", "name", "weight_pct", "asset_class", "source",
      "source_name", "source_licence", "credit", "as_of", "depth", "via", "fund_source_url"];
    const credit = (s) => (s === f.source ? f.credit : state.sources[s] && state.sources[s].credit) || "";
    const line = (r) => [f.id, f.isin, f.name, r.isin, r.name, r.weight, r.asset, r.source, sourceName(r.source),
      (state.sources[r.source] || {}).licence || "", credit(r.source), r.as_of, r.depth, r.via, f.source_url].map(csvCell).join(",");
    const lines = [header.join(","), ...rows.map(line),
      line({ isin: null, name: "Unresolved weight", weight: unresolved, asset: "", source: f.source, as_of: f.as_of, depth: 0, via: f.name })];
    const blob = new Blob(["﻿" + lines.join("\r\n") + "\r\n"], { type: "text/csv;charset=utf-8" });
    const a = h("a", { download: `openbasket_${f.id}_${f.as_of || "undated"}${f.truncated ? "_largest" : ""}.csv` });
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
      if (id) metaReady.finally(() => openFund(id, { push: false, ...opts }));
      else {
        section.hidden = true;
        document.title = "openbasket — what an ETF or fund holds, by ISIN, from public sources";
      }
    };
    window.addEventListener("popstate", () => route({ scroll: true }));
    window.addEventListener("hashchange", () => idFromLocation() && route({ scroll: true }));
    route({ scroll: true });
  }

  start();
})();
