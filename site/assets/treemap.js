/* openbasket treemap logic that needs no DOM: the squarified layout and the
   blocks and tiles of a fund's distribution. app.js draws them as SVG; the
   tests load this file with node (module.exports). */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.OpenbasketTreemap = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const UNKNOWN = "Unknown";
  const DIRECT = "__direct";
  const NOT_LISTED = "__notlisted";
  /** Weights closer to zero than this (percent of net assets) are left out of the map. */
  const EPS = 0.0005;

  /* Group-by dimensions: how a row is keyed and which breakdown of the fund file gives block totals. */
  const DIMENSIONS = {
    sector: { key: (r) => r.sector || UNKNOWN, breakdown: "sector" },
    country: { key: (r) => r.country || UNKNOWN, breakdown: "country" },
    asset: { key: (r) => r.asset || UNKNOWN, breakdown: "asset_class" },
    source: { key: (r) => r.source || UNKNOWN, breakdown: "source" },
    route: { key: (r) => (r.depth > 0 && r.via ? r.via : DIRECT), breakdown: null },
  };

  const sum = (xs) => xs.reduce((a, b) => a + b, 0);

  /** The worst aspect ratio of a row of areas laid along a side of length `side`. */
  function worst(areas, side) {
    const s = sum(areas);
    if (!(s > 0) || !(side > 0)) return Infinity;
    const max = Math.max(...areas);
    const min = Math.min(...areas);
    const s2 = s * s;
    const w2 = side * side;
    return Math.max((w2 * max) / s2, s2 / (w2 * min));
  }

  /**
   * Squarified treemap (Bruls, Huizing and van Wijk, 2000). `items` are laid out
   * in the rectangle x, y, w, h with areas proportional to `value(item)`; items
   * with no positive value are left out. Items are taken largest first. Returns
   * [{ item, x, y, w, h }] covering the rectangle.
   */
  function squarify(items, x, y, w, h, value) {
    const val = value || ((d) => d.value);
    const list = items.map((item) => ({ item, v: Number(val(item)) })).filter((d) => d.v > 0 && Number.isFinite(d.v));
    list.sort((a, b) => b.v - a.v);
    const out = [];
    const total = sum(list.map((d) => d.v));
    if (!list.length || !(w > 0) || !(h > 0) || !(total > 0)) return out;
    const scale = (w * h) / total;
    for (const d of list) d.a = d.v * scale;

    let rect = { x, y, w, h };
    let row = [];
    const place = (cells, last) => {
      const s = sum(cells.map((d) => d.a));
      if (rect.w >= rect.h) {
        // A column on the left, as wide as the row's area needs.
        const cw = last ? rect.w : Math.min(rect.w, s / rect.h);
        let cy = rect.y;
        cells.forEach((d, i) => {
          const ch = i === cells.length - 1 ? rect.y + rect.h - cy : (d.a / s) * rect.h;
          out.push({ item: d.item, x: rect.x, y: cy, w: cw, h: ch });
          cy += ch;
        });
        rect = { x: rect.x + cw, y: rect.y, w: rect.w - cw, h: rect.h };
      } else {
        // A row along the top.
        const rh = last ? rect.h : Math.min(rect.h, s / rect.w);
        let cx = rect.x;
        cells.forEach((d, i) => {
          const cw = i === cells.length - 1 ? rect.x + rect.w - cx : (d.a / s) * rect.w;
          out.push({ item: d.item, x: cx, y: rect.y, w: cw, h: rh });
          cx += cw;
        });
        rect = { x: rect.x, y: rect.y + rh, w: rect.w, h: rect.h - rh };
      }
    };
    for (let i = 0; i < list.length; i++) {
      const d = list[i];
      const side = Math.min(rect.w, rect.h);
      const areas = row.map((c) => c.a);
      if (!row.length || worst(areas.concat(d.a), side) <= worst(areas, side)) {
        row.push(d);
      } else {
        place(row, false);
        row = [d];
      }
    }
    if (row.length) place(row, true);
    return out;
  }

  /** The breakdown totals of one dimension as a Map, without the Unresolved entry. */
  function breakdownTotals(f, dim) {
    const key = DIMENSIONS[dim] && DIMENSIONS[dim].breakdown;
    const list = key && f.breakdowns && f.breakdowns[key];
    if (!Array.isArray(list)) return null;
    const m = new Map();
    for (const pair of list) {
      if (!Array.isArray(pair) || pair[0] === "Unresolved") continue;
      const v = Number(pair[1]);
      if (Number.isFinite(v)) m.set(pair[0], (m.get(pair[0]) || 0) + v);
    }
    return m;
  }

  /**
   * The treemap of a fund: one block per group of rows, largest first, each with
   * its tiles (one per row with a positive weight), and the weights that have no
   * area. `t` is core.totals(f, rows).
   *
   * - A block's area is what its positive rows weigh. When the file is cut
   *   (`t.truncated`), block totals come from the fund's breakdowns, which cover
   *   every row; what they hold beyond the listed rows is an "others" tile, so
   *   blocks keep their full size.
   * - Negative rows (shorts, written options) are listed in `negatives`, never
   *   drawn. So is a negative unresolved weight, or a block whose rows not
   *   listed here net to less than zero.
   * - Unresolved weight is its own block (`unresolved`), never mixed with rows.
   * - Rows smaller than `minValue` are merged into one "smaller" tile per block.
   */
  function model(f, rows, t, dim, opts) {
    const o = opts || {};
    const d = DIMENSIONS[dim] || DIMENSIONS.sector;
    const minValue = o.minValue || 0;
    const groups = new Map();
    const group = (k) => {
      if (!groups.has(k)) groups.set(k, { key: k, rows: [], listedNet: 0 });
      return groups.get(k);
    };
    const negatives = [];
    for (const r of rows) {
      if (!Number.isFinite(r.weight)) continue;
      const g = group(d.key(r));
      g.listedNet += r.weight;
      if (r.weight > EPS) g.rows.push(r);
      else if (r.weight < -EPS) negatives.push({ row: r, key: g.key });
    }
    const totals = t.truncated ? breakdownTotals(f, dim) : null;
    if (totals) for (const k of totals.keys()) group(k);

    const unlisted = [];
    const blocks = [];
    for (const g of groups.values()) {
      g.rows.sort((a, b) => b.weight - a.weight || a.i - b.i);
      const tiles = [];
      let small = null;
      for (const r of g.rows) {
        if (r.weight < minValue) {
          small = small || { kind: "small", value: 0, n: 0, rows: [] };
          small.value += r.weight;
          small.n++;
          small.rows.push(r);
        } else {
          tiles.push({ kind: r.asset === "fund" ? "unexpanded" : "row", value: r.weight, row: r });
        }
      }
      if (small) tiles.push(small);
      if (totals) {
        const rest = (totals.get(g.key) || 0) - g.listedNet;
        if (rest > EPS) tiles.push({ kind: "others", value: rest });
        else if (rest < -EPS) unlisted.push({ key: g.key, value: rest });
      }
      const value = sum(tiles.map((x) => x.value));
      if (value > EPS) {
        blocks.push({ key: g.key, value, net: totals ? totals.get(g.key) || 0 : g.listedNet,
          n: g.rows.length, tiles: tiles.sort((a, b) => b.value - a.value) });
      }
    }
    if (dim === "route" && t.truncated && t.notListed > EPS) {
      blocks.push({ key: NOT_LISTED, value: t.notListed, net: t.notListed, n: 0, tiles: [{ kind: "others", value: t.notListed }] });
    }
    blocks.sort((a, b) => b.value - a.value || (a.key < b.key ? -1 : 1));
    negatives.sort((a, b) => a.row.weight - b.row.weight || a.row.i - b.row.i);
    const unresolved = Number.isFinite(t.unresolved) ? t.unresolved : 0;
    return {
      dim, blocks, negatives, unlisted,
      negativeTotal: sum(negatives.map((x) => x.row.weight)) + sum(unlisted.map((x) => x.value)),
      unresolved: unresolved > EPS ? unresolved : 0,
      unresolvedNegative: unresolved < -EPS ? unresolved : 0,
      total: sum(blocks.map((b) => b.value)) + (unresolved > EPS ? unresolved : 0),
    };
  }

  /** The share of a fund's resolved weight a dimension labels (anything but Unknown), from its breakdown. */
  function labelledShare(f, rows, dim) {
    const m = breakdownTotals(f, dim);
    let known = 0;
    let all = 0;
    if (m && m.size) {
      for (const [k, v] of m) {
        if (v <= 0) continue;
        all += v;
        if (k !== UNKNOWN) known += v;
      }
    } else {
      const key = DIMENSIONS[dim].key;
      for (const r of rows) {
        if (!(r.weight > 0)) continue;
        all += r.weight;
        if (key(r) !== UNKNOWN) known += r.weight;
      }
    }
    return all > 0 ? known / all : 0;
  }

  /** The default grouping: sector when the source labels most of the weight, else country, else asset class. */
  function defaultDimension(f, rows) {
    for (const dim of ["sector", "country"]) if (labelledShare(f, rows, dim) >= 0.5) return dim;
    return "asset";
  }

  /** Whether any row is reached through a sub-fund or derivatives, so "by route" has something to show. */
  const hasRoutes = (rows) => rows.some((r) => r.depth > 0 && r.via);

  /** Relative luminance of a #rrggbb colour (WCAG 2). */
  function luminance(hex) {
    const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || "");
    if (!m) return 1;
    const lin = (c) => {
      const s = parseInt(c, 16) / 255;
      return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * lin(m[1]) + 0.7152 * lin(m[2]) + 0.0722 * lin(m[3]);
  }

  /** Ink or paper text, whichever reads better on `fill`. */
  function textOn(fill, ink, paper) {
    const l = luminance(fill);
    const contrast = (a, b) => (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    return contrast(l, luminance(ink)) >= contrast(l, luminance(paper)) ? ink : paper;
  }

  /** A label cut to about `chars` characters, with an ellipsis. */
  function fit(text, chars) {
    const s = String(text || "");
    if (chars < 2) return "";
    return s.length <= chars ? s : `${s.slice(0, Math.max(1, chars - 1)).trimEnd()}…`;
  }

  return { UNKNOWN, DIRECT, NOT_LISTED, DIMENSIONS, squarify, worst, model, breakdownTotals, labelledShare,
    defaultDimension, hasRoutes, luminance, textOn, fit };
});
