/* openbasket page logic that needs no DOM: identifiers, routing, fund totals,
   breakdown entries and CSV cells. app.js uses it as window.OpenbasketCore;
   the tests load it with node (module.exports). */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.OpenbasketCore = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const UNRESOLVED = "Unresolved";
  /** Breakdown label for rows whose source gives no value (data-api.md v1.1). */
  const UNKNOWN = "Unknown";
  const ISIN_RE = /^[A-Z]{2}[A-Z0-9]{9}[0-9]$/;
  const SERIES_RE = /^S\d{9}$/;

  /* matched_via, data-api.md v1.1. Any other value is an issuer adapter name
     (ishares, spdr, xtrackers, amundi, ...): the fund's ISIN was in that issuer's list. */
  const MATCHED_VIA = {
    nport_lei: "its ISIN, which other funds' SEC N-PORT filings list under this fund's LEI",
    openfigi_ticker: "its ticker from OpenFIGI, found in SEC's fund ticker list",
    sec_ticker: "its ticker, found in SEC's fund ticker list",
    series_id: "its SEC series ID",
    issuer_id: "the issuer's own fund ID",
    proxy: "a configured proxy fund that tracks the same index",
    security_master: "the security master: a single security, its own constituent",
    directory: "a fund directory listing",
  };

  const compact = (x) => (x || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  const sum = (xs) => xs.reduce((a, b) => a + b, 0);

  function isValidIsin(x) {
    if (!ISIN_RE.test(x)) return false;
    const digits = x.replace(/[A-Z]/g, (c) => String(c.charCodeAt(0) - 55));
    let total = 0;
    for (let i = 0; i < digits.length; i++) {
      let n = Number(digits[digits.length - 1 - i]);
      if (i % 2 === 1) {
        n *= 2;
        if (n > 9) n -= 9;
      }
      total += n;
    }
    return total % 10 === 0;
  }

  /** The ISIN or series ID in a location hash, else null: #api, #top and the like are not routes. */
  function idFromHash(hash) {
    let raw = (hash || "").replace(/^#/, "");
    try {
      raw = decodeURIComponent(raw);
    } catch (e) {
      return null;
    }
    const id = compact(raw);
    return ISIN_RE.test(id) || SERIES_RE.test(id) ? id : null;
  }

  /** The fund a URL names: ?isin= (or ?id=) first, then a #ISIN hash. */
  function idFromUrl(search, hash) {
    const p = new URLSearchParams(search || "");
    return compact(p.get("isin") || p.get("id") || "") || idFromHash(hash);
  }

  /** Whether a hashchange should open a fund: only for an ISIN or series hash not already shown. */
  function hashRoute(hash, shownId) {
    const id = idFromHash(hash);
    return id && id !== shownId ? id : null;
  }

  function matchedVia(v, label) {
    if (!v) return "–";
    if (MATCHED_VIA[v]) return MATCHED_VIA[v];
    return `the fund's ISIN in the ${label ? label(v) : v} fund list`;
  }

  /** Read a fund file's rows by field name, so added fields never shift the columns. */
  function rowsOf(f) {
    const ix = Object.fromEntries((f.fields || []).map((n, i) => [n, i]));
    const get = (r, k) => (ix[k] == null ? null : r[ix[k]]);
    return (f.constituents || []).map((r, i) => ({
      i, isin: get(r, "isin"), name: get(r, "name") || "", weight: Number(get(r, "weight_pct")), asset: get(r, "asset_class") || "",
      source: get(r, "source") || f.source, as_of: get(r, "as_of") || f.as_of, depth: Number(get(r, "depth") || 0),
      via: get(r, "via") || "", country: get(r, "country") || "", sector: get(r, "sector") || "",
    }));
  }

  /**
   * The fund's weights: resolved, unresolved, the listed rows and, for a truncated
   * file, the resolved weight of the rows that were cut (in the full Parquet only).
   * Uses the v1.1 keys (n_rows, listed_pct) where present, else the listed rows.
   */
  function totals(f, rows) {
    const listed = f.listed_pct != null ? Number(f.listed_pct) : sum(rows.map((r) => r.weight));
    const nRows = f.n_rows != null ? Number(f.n_rows) : f.truncated ? null : rows.length;
    const truncated = f.truncated === true || (nRows != null && nRows > rows.length);
    const unresolved = f.unresolved && f.unresolved.weight_pct != null ? Number(f.unresolved.weight_pct) : Number(f.unresolved_pct);
    const resolved = f.resolved_pct != null ? Number(f.resolved_pct) : 100 - unresolved;
    const noHoldings = rows.length === 0 && !nRows && !(resolved > 0);
    return { listed, nRows, truncated, unresolved, resolved, notListed: truncated ? resolved - listed : 0, noHoldings };
  }

  /**
   * One breakdown's entries, largest first, with Unresolved last and never dropped.
   * More than maxBars + 1 labels are shown as maxBars plus one "others" entry;
   * `all` keeps every label for the table alternative.
   */
  function chartEntries(list, unresolved, label, maxBars) {
    const lbl = label || ((x) => x);
    const cap = maxBars || 8;
    const items = [];
    let un = null;
    for (const pair of list || []) {
      if (!Array.isArray(pair)) continue;
      const v = Number(pair[1]);
      if (!Number.isFinite(v)) continue;
      if (pair[0] === UNRESOLVED) un = (un || 0) + v;
      else items.push({ key: pair[0], label: lbl(pair[0]), value: v, rank: items.length });
    }
    items.sort((a, b) => b.value - a.value || a.rank - b.rank);
    let shown = items;
    if (items.length > cap + 1) {
      const rest = items.slice(cap);
      shown = items.slice(0, cap).concat({ key: "__other", label: `${rest.length} others`, value: sum(rest.map((x) => x.value)), other: true });
    }
    const unEntry = { key: UNRESOLVED, label: UNRESOLVED, value: un != null ? un : Number(unresolved), unresolved: true };
    return { all: items.concat(unEntry), shown: shown.concat(unEntry) };
  }

  /** The ten largest holdings by ISIN, adding up an ISIN's rows from different sources or sub-funds. */
  function topHoldings(rows, n) {
    const m = new Map();
    for (const r of rows) {
      if (!r.isin) continue;
      const x = m.get(r.isin) || { isin: r.isin, label: r.name || r.isin, value: 0 };
      x.value += r.weight;
      m.set(r.isin, x);
    }
    return [...m.values()].sort((a, b) => Math.abs(b.value) - Math.abs(a.value)).slice(0, n || 10);
  }

  /** Breakdowns from the listed rows, for an untruncated file written before v1.1. */
  function localBreakdowns(rows, unresolved) {
    const by = (k) => {
      const m = new Map();
      for (const r of rows) m.set(r[k] || UNKNOWN, (m.get(r[k] || UNKNOWN) || 0) + r.weight);
      return [...m.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1)).concat([[UNRESOLVED, unresolved]]);
    };
    return { asset_class: by("asset"), country: by("country"), sector: by("sector"), source: by("source"),
      top10_pct: sum(topHoldings(rows).map((x) => x.value)) };
  }

  /** The rows a download adds after the constituents: the cut rows of a truncated file, then Unresolved. */
  function closingRows(f, rows, t) {
    const out = [];
    if (t.truncated) {
      out.push({ isin: null, name: `Not listed in this file: ${t.nRows - rows.length} more rows, in the full Parquet file`,
        weight: t.notListed, asset: "", country: "", sector: "", source: f.source, as_of: f.as_of, depth: null, via: "" });
    }
    const un = f.unresolved || {};
    out.push({ isin: null, name: "Unresolved weight", weight: t.unresolved, asset: "", country: "", sector: "",
      source: un.source || f.source, as_of: f.as_of, depth: 0, via: f.name });
    return out;
  }

  function csvCell(v) {
    if (v == null) return "";
    if (typeof v === "number") return Number.isFinite(v) ? String(Math.round(v * 1e6) / 1e6) : "";
    if (typeof v === "boolean") return v ? "true" : "false";
    let x = String(v);
    if (/^[=+\-@\t\r]/.test(x)) x = `'${x}`;
    return /[",\r\n]/.test(x) ? `"${x.replace(/"/g, '""')}"` : x;
  }

  /** Split text into plain parts and http(s) links, trailing punctuation left out of the link. */
  function linkParts(text) {
    if (!text) return [];
    const out = [];
    const re = /https?:\/\/[^\s<>"']+/g;
    let last = 0;
    let m;
    while ((m = re.exec(text))) {
      let url = m[0];
      const trail = url.match(/[).,;:]+$/);
      if (trail) url = url.slice(0, -trail[0].length);
      if (m.index > last) out.push({ text: text.slice(last, m.index) });
      out.push({ url });
      last = m.index + url.length;
      re.lastIndex = last;
    }
    if (last < text.length) out.push({ text: text.slice(last) });
    return out;
  }

  return {
    UNRESOLVED, UNKNOWN, ISIN_RE, SERIES_RE, MATCHED_VIA, compact, sum, isValidIsin, idFromHash, idFromUrl, hashRoute,
    matchedVia, rowsOf, totals, chartEntries, topHoldings, localBreakdowns, closingRows, csvCell, linkParts,
  };
});
