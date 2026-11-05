// GZW Material Finder - search UI. Data: window.GZW_DATA (app/data/gzw_data.js), or newer data the desktop shell
// downloaded (handed over as a parsed object by api.get_data() - never loaded as a script).
// Runs standalone in a browser (finds -> localStorage) or inside the pywebview shell (finds -> data/finds.jsonl).
// The public edition's data has no TAC map spots (D.places empty): no crate lines or crate guesses, area-level links only.
(() => {
  'use strict';
  let D = window.GZW_DATA;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const norm = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  const api = () => (window.pywebview && window.pywebview.api) || null;

  // indexes over the current data, rebuilt by useData() when the shell hands over a newer download
  let placeById, poiTier, matById, matByNorm, POI_LINK, PLACE_INDEX, hasPlaces, recById, devById, devByNorm;
  let dataInfo = { source: 'bundled', version: null, booted: false };
  function useData(d) {
    D = d;
    const places = D.places || [];
    hasPlaces = places.length > 0;
    placeById = new Map(places.map((p) => [p.id, p]));
    poiTier = Object.fromEntries((D.pois || []).map((p) => [p.name, p.tier]));
    matById = new Map(D.materials.map((m) => [m.id, m]));
    matByNorm = new Map();
    for (const m of D.materials) for (const a of [m.name, ...m.aliases]) matByNorm.set(norm(a), m);
    recById = new Map((D.recipes || []).map((r) => [r.id, r])); // older feed data has no recipes: crafting shows "coming soon"
    devById = new Map((D.devices || []).map((v) => [v.id, v])); // older feed data has no devices: disassembly shows "coming soon"
    devByNorm = new Map();
    for (const v of D.devices || []) for (const a of [v.name, ...v.aliases]) devByNorm.set(norm(a), v);
    // TAC map link for each area: the data's own area link, else the centre of its named spots
    POI_LINK = {};
    const by = {};
    for (const p of places) (by[p.poi] ||= []).push(p);
    for (const [poi, ps] of Object.entries(by)) {
      const lat = ps.reduce((s, p) => s + p.lat, 0) / ps.length, lng = ps.reduce((s, p) => s + p.lng, 0) / ps.length;
      POI_LINK[poi] = `https://gzwtacmap.com/maps/lamang#m=15.50/${lng.toFixed(2)}/${lat.toFixed(2)}`;
    }
    for (const p of D.pois || []) if (p.link) POI_LINK[p.name] = p.link;
    // log-a-find picker: whole areas, plus named spots when the data has them
    PLACE_INDEX = [
      ...(D.pois || []).map((p) => ({ kind: 'poi', id: null, poi: p.name, name: p.name, text: [p.name] })),
      ...places.map((p) => ({ kind: 'place', id: p.id, poi: p.poi, name: p.name, crates: p.crates, text: [p.name + ' ' + p.poi, p.name] })),
    ];
  }
  useData(D);

  let meta = { build: null, hotkey: null, shell: false };

  // ---------- finds (your own logged finds; they outrank everything) ----------
  const Finds = {
    list: [],
    async load() {
      if (api()) { try { this.list = await api().get_finds(); } catch (e) { console.error(e); } return; }
      try { this.list = JSON.parse(localStorage.getItem('gzw_finds') || '[]'); } catch { this.list = []; }
    },
    async add(f) {
      this.list.push(f);
      if (api()) return api().log_find(f);
      try { localStorage.setItem('gzw_finds', JSON.stringify(this.list)); } catch { /* private mode: keep in memory */ }
    },
    forMaterial(id) { return this.list.filter((f) => f.material === id); },
  };

  // ---------- checklist: what you have vs what your planned upgrades need ----------
  const CL = {
    have: {}, plan: [],
    async load() {
      let c = null;
      if (api()) { try { c = await api().get_checklist(); } catch (e) { console.error(e); } }
      else { try { c = JSON.parse(localStorage.getItem('gzw_checklist') || 'null'); } catch { c = null; } }
      this.have = (c && c.have) || {};
      this.plan = (c && c.plan) || [];
      this.craft = { have: (c && c.craft && c.craft.have) || {}, plan: (c && c.craft && c.craft.plan) || [] };
      this.dis = { have: (c && c.disassembly && c.disassembly.have) || {}, plan: (c && c.disassembly && c.disassembly.plan) || [] };
    },
    save() {
      clearTimeout(this._t);
      this._t = setTimeout(() => {
        const data = { have: this.have, plan: this.plan, craft: this.craft, disassembly: this.dis };
        if (api()) api().save_checklist(data).catch((e) => toast('Could not save checklist: ' + e));
        else { try { localStorage.setItem('gzw_checklist', JSON.stringify(data)); } catch { /* private mode */ } }
      }, 250);
    },
    needs() { // material id -> qty needed by the planned upgrades, plus their cash cost
      const need = {}; let cash = 0;
      for (const key of this.plan) {
        const [st, lv] = key.split('|');
        const s = D.stations.find((x) => x.station === st && String(x.level ?? 1) === lv);
        if (!s) continue;
        cash += Number(s.cash) || 0;
        for (const c of s.cost || []) {
          const m = matByNorm.get(norm(c.material));
          if (m) need[m.id] = (need[m.id] || 0) + (Number(c.qty) || 1);
        }
      }
      return { need, cash };
    },
    status(id) { const n = this.needs().need[id] || 0, h = this.have[id] || 0; return { have: h, need: n, short: Math.max(0, n - h) }; },
    togglePlan(key) { const i = this.plan.indexOf(key); if (i >= 0) this.plan.splice(i, 1); else this.plan.push(key); this.save(); },
    setHave(id, v) { v = Math.max(0, Math.min(999, Math.round(Number(v) || 0))); if (v) this.have[id] = v; else delete this.have[id]; this.save(); },
    // craft checklist: its own have counts and planned recipes ("craft" section of data/checklist.json)
    craft: { have: {}, plan: [] },
    craftNeeds() {
      const need = {};
      for (const id of this.craft.plan) {
        for (const c of (recById.get(id) || {}).ingredients || []) {
          const m = matByNorm.get(norm(c.material));
          if (m) need[m.id] = (need[m.id] || 0) + (Number(c.qty) || 1);
        }
      }
      return need;
    },
    craftStatus(id) { const n = this.craftNeeds()[id] || 0, h = this.craft.have[id] || 0; return { have: h, need: n, short: Math.max(0, n - h) }; },
    toggleCraft(id) { const p = this.craft.plan, i = p.indexOf(id); if (i >= 0) p.splice(i, 1); else p.push(id); this.save(); },
    setCraftHave(id, v) { v = Math.max(0, Math.min(999, Math.round(Number(v) || 0))); if (v) this.craft.have[id] = v; else delete this.craft.have[id]; this.save(); },
    // disassembly checklist: the parts you want (1 each) and how many you have ("disassembly" section of data/checklist.json)
    dis: { have: {}, plan: [] },
    disNeeds() { const need = {}; for (const id of this.dis.plan) if (matById.has(id)) need[id] = 1; return need; },
    disStatus(id) { const n = this.disNeeds()[id] || 0, h = this.dis.have[id] || 0; return { have: h, need: n, short: Math.max(0, n - h) }; },
    toggleDis(id) { const p = this.dis.plan, i = p.indexOf(id); if (i >= 0) p.splice(i, 1); else p.push(id); this.save(); },
    setDisHave(id, v) { v = Math.max(0, Math.min(999, Math.round(Number(v) || 0))); if (v) this.dis.have[id] = v; else delete this.dis.have[id]; this.save(); },
  };
  const crafts = (m) => m.used_in.filter((u) => u.craft);
  const isCraftView = () => S.view === 'crafting' || S.view === 'craftlist';
  const isDisView = () => S.view === 'disassembly' || S.view === 'dislist';
  // ---------- disassembly: devices you take apart in the Electronics Workshop, and the parts they give ----------
  const isPart = (m) => !!(m.dismantle_from && m.dismantle_from.length);
  const devOfItem = (item) => devByNorm.get(norm(item)) || null;
  // TAC map spots of a device, per area, most spots first (maintainer data only; the public data has none)
  function devSpots(dev) {
    const by = new Map();
    for (const x of D.disassembly || []) {
      if (x.device ? x.device !== dev.id : devOfItem(x.name) !== dev) continue;
      if (!by.has(x.poi)) by.set(x.poi, { poi: x.poi, n: 0, spots: [] });
      const a = by.get(x.poi); a.n++; a.spots.push(x);
    }
    return [...by.values()].sort((a, b) => b.n - a.n || a.poi.localeCompare(b.poi));
  }
  // one line: where to look for a device (TAC counts, else the areas research/players name, else "no data")
  function devWhere(dev, max = 3) {
    const sp = devSpots(dev);
    const more = (n) => (n > max ? ` <span class="muted">+${n - max}</span>` : '');
    if (sp.length) return sp.slice(0, max).map((a) => `<a class="spot" data-url="${esc(a.spots[0].link)}" title="Open on GZW Tac Map">${esc(a.poi)}</a> <span class="tag2">×${esc(a.n)}</span>`).join(' · ') + more(sp.length);
    const pois = [...new Set((dev.hints || []).map((h) => h.poi).filter(Boolean))];
    if (pois.length) return pois.slice(0, max).map((p) => (POI_LINK[p] ? `<a class="spot" data-url="${esc(POI_LINK[p])}" title="Open on GZW Tac Map">${esc(p)}</a>` : esc(p))).join(' · ') + more(pois.length) + ' <span class="muted">(reported)</span>';
    return '<span class="muted">No location data yet</span>';
  }
  const disSoon = () => '<div class="empty">Coming soon: disassembly data is being added.</div>';

  // ---------- fuzzy matching ----------
  function lev(a, b) {
    const dp = Array.from({ length: a.length + 1 }, (_, i) => [i, ...Array(b.length).fill(0)]);
    for (let j = 1; j <= b.length; j++) dp[0][j] = j;
    for (let i = 1; i <= a.length; i++)
      for (let j = 1; j <= b.length; j++)
        dp[i][j] = Math.min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    return dp[a.length][b.length];
  }
  function scoreText(q, text) {
    const n = norm(text);
    if (!q || !n) return 0;
    if (n === q) return 100;
    if (n.startsWith(q)) return 85 - Math.min(10, (n.length - q.length) * 0.2);
    const words = String(text).toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
    if (words.some((w) => w.startsWith(q))) return 70;
    if (n.includes(q)) return 55;
    if (q.length >= 4 && lev(q, n.slice(0, q.length)) <= 1) return 45;
    let i = 0;
    for (const c of n) { if (c === q[i]) i++; if (i === q.length) break; }
    if (i === q.length && q.length >= 3) return 30 - Math.min(15, (n.length - q.length) * 0.3);
    return 0;
  }
  const bestScore = (q, texts) => Math.max(0, ...texts.map((t) => scoreText(q, t)));
  // Multi-word queries ("construction pha") - every word must start a word in the text.
  function tokenScore(query, text) {
    const toks = String(query).toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
    if (toks.length < 2) return 0;
    const words = String(text).toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
    return toks.every((t) => words.some((w) => w.startsWith(t))) ? 75 - Math.min(10, words.length) : 0;
  }

  function search(query) {
    const q = norm(query);
    if (!q) return [];
    const out = [];
    if (isCraftView()) { // crafting: recipes + their ingredients (upgrade materials too, when a recipe uses them)
      for (const m of D.materials) { const s = crafts(m).length && bestScore(q, [m.name, ...m.aliases]); if (s) out.push({ kind: 'material', id: m.id, label: m.name, s: s + 1 }); }
      for (const r of D.recipes || []) { const s = Math.max(scoreText(q, r.output), r.category ? scoreText(q, r.category) - 10 : 0); if (s > 0) out.push({ kind: 'recipe', id: r.id, label: r.output, s: s + 1 }); }
      return out.sort((a, b) => b.s - a.s || a.label.localeCompare(b.label)).slice(0, 8);
    }
    if (isDisView()) { // disassembly: devices + the parts they give
      for (const v of D.devices || []) { const s = bestScore(q, [v.name, ...v.aliases]); if (s) out.push({ kind: 'device', id: v.id, label: v.name, s: s + 1 }); }
      for (const m of D.materials) { const s = isPart(m) && bestScore(q, [m.name, ...m.aliases]); if (s) out.push({ kind: 'part', id: m.id, label: m.name, s }); }
      return out.sort((a, b) => b.s - a.s || a.label.localeCompare(b.label)).slice(0, 8);
    }
    for (const m of D.materials) { const s = !m.craft_only && bestScore(q, [m.name, ...m.aliases]); if (s) out.push({ kind: 'material', id: m.id, label: m.name, s: s + 1 }); }
    const seenSt = new Set();
    for (const st of D.stations) {
      // match the slot ("Heat Source") or the device people actually see ("Open Fire Pit")
      const s = Math.max(scoreText(q, st.station), st.device ? scoreText(q, st.device) : 0);
      if (s && !seenSt.has(st.station)) { seenSt.add(st.station); out.push({ kind: 'station', id: st.station + '|' + (st.level ?? 1), label: st.station, s }); }
    }
    return out.sort((a, b) => b.s - a.s || a.label.localeCompare(b.label)).slice(0, 8);
  }

  // ---------- ranking: where to find a material ----------
  // a player naming an area for THIS material beats general "this kind of place / has crates" evidence
  const SCORE = { mine: 8, named: 6, hint: 1, crate: 1.5 };
  function rank(m) {
    const places = new Map(); // place id -> {score, mine, hint, crates}
    const touch = (id) => { if (!places.has(id)) places.set(id, { score: 0, mine: 0, hint: 0, crate: 0 }); return places.get(id); };
    // per area: who (independent origin = a player, or a page) said what. Scored per PERSON, not per post.
    const poiOf = (name) => {
      if (!pois.has(name)) pois.set(name, { poi: name, score: 0, mine: 0, found: new Map(), tips: new Set(), negs: new Set(), namedSrc: new Set(), spots: new Map(), places: [] });
      return pois.get(name);
    };
    const pois = new Map();
    for (const h of m.location_hints) {
      const rep = h.report;
      if (h.kind === 'named' && h.poi) {
        const a = poiOf(h.poi), who = h.indep || (D.sources[h.src] || {}).indep || h.src;
        if (rep && rep.type === 'advice') a.tips.add(who);
        else {
          const w = rep && rep.preview ? SCORE.named / 2 : SCORE.named; // preview-era (pre-launch) finds count half
          a.found.set(who, Math.max(a.found.get(who) || 0, w));
          if (rep && h.text && h.text !== h.poi) {
            // near-identical wording from different players = the same spot
            const k = h.text.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim().slice(0, 40);
            if (!a.spots.has(k)) a.spots.set(k, { text: h.text.trim(), who: new Set(), link: null });
            const s = a.spots.get(k);
            if (!s.link && h.places && h.places.length && placeById.has(h.places[0])) s.link = placeById.get(h.places[0]).link;
            if (h.text.trim().length < s.text.length) s.text = h.text.trim();
            s.who.add(who);
          }
        }
        a.namedSrc.add(h.src);
      }
      // a spot matching "industrial / warehouse / depot" five ways is still one industrial spot: cap generic matches
      for (const id of h.places || []) { const e = touch(id); e.hint++; if (e.hint <= 2) e.score += SCORE.hint; }
    }
    for (const n of m.negatives || []) if (n.poi) poiOf(n.poi).negs.add(n.indep || n.src);
    if (m.crate && m.crate.length)
      for (const p of D.places || []) { const c = p.crates.t2 + p.crates.t3; if (c) { const e = touch(p.id); e.score += SCORE.crate * c; e.crate = c; } }
    for (const f of Finds.forMaterial(m.id)) {
      if (f.place && placeById.has(f.place)) { const e = touch(f.place); e.score += SCORE.mine; e.mine++; }
      else if (f.poi) { const a = poiOf(f.poi); a.score += SCORE.mine; a.mine++; }
    }
    for (const [id, e] of places) { const p = placeById.get(id); poiOf(p.poi).places.push({ p, ...e }); }
    const list = [...pois.values()].map((a) => {
      a.places.sort((x, y) => y.score - x.score);
      // top 5 spots count fully, the rest at 30% (so 40 field shelters can't swamp real reports)
      a.score += a.places.reduce((s, x, i) => s + x.score * (i < 5 ? 1 : 0.3), 0);
      a.mine += a.places.reduce((s, x) => s + x.mine, 0);
      a.crates = a.places.reduce((s, x) => ({ t2: s.t2 + x.p.crates.t2, t3: s.t3 + x.p.crates.t3 }), { t2: 0, t3: 0 });
      a.score += [...a.found.values()].reduce((s, w) => s + w, 0) + a.tips.size * 2 - a.negs.size * 3;
      a.named = a.found; // kept for older callers
      a.players = a.found.size;
      a.label = a.mine ? 'mine' : a.players >= 2 ? 'confirmed' : a.players === 1 ? 'reported' : a.tips.size ? 'tip' : 'inferred';
      return a;
    });
    const strength = { mine: 5, confirmed: 4, reported: 3, tip: 2, inferred: 1 };
    return list.filter((a) => a.score > 0).sort((x, y) => y.score - x.score || strength[y.label] - strength[x.label] || x.poi.localeCompare(y.poi));
  }

  // Fallback when nothing is known: POIs with the most material crates (only when the data has TAC map spots).
  function crateGuess() {
    const by = new Map();
    for (const p of D.places || []) {
      const c = p.crates.t2 + p.crates.t3;
      if (!c) continue;
      if (!by.has(p.poi)) by.set(p.poi, { poi: p.poi, score: 0, places: [], label: 'guess', named: new Set(), mine: 0, crates: { t2: 0, t3: 0 } });
      const a = by.get(p.poi);
      a.score += c; a.crates.t2 += p.crates.t2; a.crates.t3 += p.crates.t3;
      a.places.push({ p, score: c, crate: c });
    }
    return [...by.values()].map((a) => (a.places.sort((x, y) => y.score - x.score), a)).sort((x, y) => y.score - x.score);
  }

  // ---------- rendering ----------
  const LABEL = { mine: 'Mine', confirmed: 'Confirmed', reported: 'Reported', tip: 'Tip', inferred: 'Inferred', guess: 'Guess' };
  const LABEL_TIP = {
    mine: 'You logged a find here', confirmed: '2+ different players/sources found it here (the number = how many)',
    reported: 'One player/source found it here', tip: 'Recommended by players, but nobody said they found it here',
    inferred: 'Matches the type of place sources describe, plus TAC map crate spots', guess: 'No reports yet - just where material crates are',
  };
  const badge = (l, extra = '') => `<span class="badge b-${l}" title="${esc(LABEL_TIP[l])}">${LABEL[l]}${extra}</span>`;
  // [source] tags open the source page; the Gemini summary has no page worth opening, so it opens the spot on TAC map
  const srcLink = (id, tacUrl) => {
    const s = D.sources[id];
    if (!s) return '';
    const label = id.startsWith('P:') ? (s.kind ? s.kind.replace('reddit-snippet', 'reddit') : 'post') : id.replace(/^R:/, '');
    if (s.kind === 'ai-summary')
      return tacUrl ? `<span class="src" data-url="${esc(tacUrl)}" title="Gemini AI summary (unverified) - click to open this spot on GZW Tac Map">[ai tip]</span>` : '<span class="src muted" title="Gemini AI summary (unverified)">[ai tip]</span>';
    return `<span class="src" data-url="${esc(s.url)}" title="${esc(s.title)} - ${esc(s.reliability || '')}">[${esc(label)}]</span>`;
  };
  const crateTxt = (c) => [c.t3 ? `<span class="tag3">${esc(c.t3)}×T3</span>` : '', c.t2 ? `<span class="tag2">${esc(c.t2)}×T2</span>` : ''].filter(Boolean).join(' ');

  function poiRow(a, showCrates) {
    const spots = a.places.slice(0, 3).map(({ p, mine, crate }) => {
      const bits = [mine ? `${mine} found` : '', showCrates && crate ? `${esc(crate)} crate${crate > 1 ? 's' : ''}` : ''].filter(Boolean).join(', ');
      return `<a class="spot" data-url="${esc(p.link)}" title="Open on GZW Tac Map">${esc(p.name)}</a>${bits ? ` <span class="muted">(${bits})</span>` : ''}`;
    }).join(' · ');
    const tier = poiTier[a.poi] ? `<span class="ptier">T${esc(poiTier[a.poi])}</span>` : '';
    const areaLink = (a.places[0] && a.places[0].p.link) || POI_LINK[a.poi] || '';
    const named = a.namedSrc && a.namedSrc.size ? ' ' + [...a.namedSrc].slice(0, 4).map((id) => srcLink(id, areaLink)).join('') : '';
    const crates = showCrates && a.crates && (a.crates.t2 || a.crates.t3) ? `<span class="crates" title="Building Material Crates (TAC map)">${crateTxt(a.crates)}</span>` : '';
    // what players actually said ("generator room hallway"), most-repeated first - click opens the spot on TAC map
    const said = a.spots ? [...a.spots.values()].sort((x, y) => y.who.size - x.who.size).slice(0, 2)
      .map(({ text: t, who, link }) => `<a class="said" data-url="${esc(link || areaLink)}" title="Open on GZW Tac Map">“${esc(t)}”</a>${who.size > 1 ? ` <span class="tag2">×${who.size}</span>` : ''}`).join(' · ') : '';
    const negs = a.negs && a.negs.size ? ` <span class="neg" title="Players who searched here and found none">✗ ${a.negs.size} found none</span>` : '';
    const extra = a.mine ? ' ×' + a.mine : a.label === 'confirmed' ? ' · ' + a.players : '';
    const poiName = areaLink ? `<a class="poi spot" data-url="${esc(areaLink)}" title="Open on GZW Tac Map">${esc(a.poi)}</a>` : `<span class="poi">${esc(a.poi)}</span>`;
    return `<div class="row"><div class="top">${badge(a.label, extra)}${poiName}${tier}${named}${crates}${negs}</div>${said ? `<div class="spots">${said}</div>` : ''}${spots ? `<div class="spots">${spots}</div>` : ''}</div>`;
  }

  function usedIn(m, craft) {
    const uses = craft ? crafts(m) : m.used_in;
    if (!uses.length) return '<div class="used">Used in: <span class="muted">unknown yet</span></div>';
    const parts = uses.map((u) => u.craft // a crafting recipe: "craft: Morphine  Laboratory ×1", clickable to the recipe
      ? `<a class="spot" data-rec="${esc(u.craft)}"><b>${esc(u.device)}</b></a> <span class="muted">${esc(u.station)}</span>${u.qty ? ' ×' + esc(u.qty) : ''}`
      : `<b>${esc(u.device || u.station)}</b> <span class="muted">${esc(u.device ? u.station + ' ' : '')}L${esc(u.level || 1)}</span>${u.qty ? ' ×' + esc(u.qty) : ''}`);
    return `<div class="used" data-fitdrop="6">Used in: ${parts.join(' · ')}</div>`;
  }
  // Fact-check marks from data/seed/verification.json
  const vmark = (o) => !o || !o.verified ? ''
    : o.verified === 'supported' ? ` <span class="vok" title="Independently checked against the source: ${esc(o.vnote || 'supported')}">✓</span>`
    : o.verified === 'partial' ? ` <span class="vpart" title="Checked: partly right - ${esc(o.vnote || '')}">≈</span>`
    : '';

  function renderMaterial(m, craft) {
    const ranked = rank(m);
    let html = `<div class="card-h"><span class="name">${esc(m.name)}</span><span class="cat">${esc(m.category)}</span></div>`;
    const cs = craft ? CL.craftStatus(m.id) : CL.status(m.id);
    if (cs.need || cs.have) // checklist line stays visible when pinned: tells you whether to pick it up
      html += `<div class="clline">${craft ? 'Craft checklist' : 'Checklist'}: have <b>${cs.have}</b>${cs.need ? ` / need <b>${cs.need}</b> · ${cs.short ? `<span class="need">pick up ${cs.short} more</span>` : '<span class="ok">✓ enough</span>'}` : ` · not needed by your planned ${craft ? 'crafts' : 'upgrades'}`}</div>`;
    html += usedIn(m, craft);
    if (m.crate_seen && D.meta.crate_reports)
      html += `<div class="used" data-fitdrop="5">In crates: <b class="tag2">${esc(m.crate_seen)} of ${esc(D.meta.crate_reports)}</b> reported Building Material Crate openings</div>`;
    // Only show crate counts when crates are a known source for this material (or as the no-data fallback).
    const fromCrates = !!(m.crate && m.crate.length);
    // data-fitdrop: when the pinned HUD can't fit, the highest numbers are hidden first (never the top row)
    const row = (a, drop) => poiRow(a, fromCrates).replace('<div class="row"', `<div class="row"${drop ? ` data-fitdrop="${drop}"` : ''}`);
    if (ranked.length) {
      html += `<section><h3>Most common</h3>${ranked.slice(0, 3).map((a, i) => row(a, i ? 3 - i : 0)).join('')}</section>`;
      if (ranked.length > 3) html += `<section><h3>Less common</h3>${ranked.slice(3, 7).map((a, i) => row(a, 7 + i)).join('')}</section>`;
    } else if (m.craft_only) { // a crafting ingredient: crate guesses and crate tips are about building materials
      html += `<section><h3>No reports yet</h3><div class="note">Nobody has named an area for ${esc(m.name)} yet. Press Ctrl+L when you find one.</div></section>`;
    } else if (!hasPlaces) {
      // no map spots in this data (public edition): no crate guesses, just the general tips
      const tips = (D.general_tips || []).slice(0, 4).map((t, i) => `<li data-fitdrop="${4 + i}">${esc(t.text)}${srcLink(t.src)}</li>`).join('');
      html += `<section><h3>No reports yet</h3><div class="note">Nobody has named an area for ${esc(m.name)} yet. Press Ctrl+L when you find one.</div>${tips ? `<ul class="says">${tips}</ul>` : ''}</section>`;
    } else {
      html += `<section><h3>No reports yet</h3><div class="note">Nobody has reported where ${esc(m.name)} spawns. Best guess: Building Material Crates. Press Ctrl+L when you find one.</div>${crateGuess().slice(0, 4).map((a, i) => poiRow(a, true).replace('<div class="row"', `<div class="row"${i ? ` data-fitdrop="${4 + i}"` : ''}`)).join('')}</section>`;
      const tips = (D.general_tips || []).slice(0, 4).map((t) => `<li>${esc(t.text)}${srcLink(t.src)}</li>`).join('');
      if (tips) html += `<section class="hud-hide"><h3>Where crates turn up</h3><ul class="says">${tips}</ul></section>`;
    }
    // "Dismantle: <device>" - click the device for its card (Disassembly view); areas are TAC counts or reported areas
    const dis = (m.dismantle_from || []).map((d) => {
      const dev = devOfItem(d.item);
      return `<li>Dismantle: ${dev ? `<a class="spot" data-dev="${esc(dev.id)}" title="Open in Disassembly"><b>${esc(d.item)}</b></a> @ ${devWhere(dev)}` : `<b>${esc(d.item)}</b>`}${srcLink(d.src)}</li>`;
    });
    if (dis.length) html += `<section data-fitdrop="3"><h3>Dismantle from</h3><ul class="says">${dis.join('')}</ul></section>`;
    const says = m.location_hints.filter((h) => !h.report).map((h) => `<li>${esc(h.text)}${srcLink(h.src)}${vmark(h)}</li>`);
    // player reports, grouped by place + spot: "YBL-1 — kitchen · found by A, B · tip from C"
    const groups = new Map();
    for (const h of m.location_hints.filter((x) => x.report)) {
      const k = `${h.poi || '?'}|${h.text.trim().toLowerCase()}`;
      if (!groups.has(k)) groups.set(k, { poi: h.poi, text: h.text, found: new Map(), tips: new Map() });
      const g = groups.get(k), who = h.report.author || D.sources[h.src]?.kind || 'someone';
      (h.report.type === 'advice' ? g.tips : g.found).set(who, h);
    }
    const names = (mp) => [...mp.keys()].slice(0, 4).join(', ') + (mp.size > 4 ? ` +${mp.size - 4}` : '');
    const repLines = [...groups.values()].sort((a, b) => b.found.size - a.found.size).map((g) => {
      const hs = [...g.found.values(), ...g.tips.values()];
      const spotPlace = hs.map((h) => h.places && h.places[0]).find((id) => id && placeById.has(id));
      const tac = (spotPlace && placeById.get(spotPlace).link) || POI_LINK[g.poi] || '';
      const links = [...new Set(hs.map((h) => h.src))].slice(0, 3).map((id) => srcLink(id, tac)).join('');
      const bits = [g.found.size ? `<b class="tag2">found by ${g.found.size}</b> (${esc(names(g.found))})` : '', g.tips.size ? `tip from ${esc(names(g.tips))}` : ''].filter(Boolean).join(' · ');
      const where = `${esc(g.poi || '')}${g.poi && g.text !== g.poi ? ' — ' : ''}${g.text !== g.poi ? esc(g.text) : ''}`;
      return `<li>${tac ? `<a class="spot" data-url="${esc(tac)}" title="Open on GZW Tac Map">${where}</a>` : where} · ${bits}${links}</li>`;
    });
    for (const n of m.negatives || []) repLines.push(`<li><span class="neg">✗</span> ${esc(n.poi || '')} ${esc(n.spot || '')} · searched, found none (${esc(n.report.author || 'a player')})${srcLink(n.src)}</li>`);
    if (repLines.length) html += `<section class="hud-hide"><h3>Player reports</h3><ul class="says">${repLines.slice(0, 14).join('')}${repLines.length > 14 ? `<li class="muted">+${repLines.length - 14} more</li>` : ''}</ul></section>`;
    const crateSrcs = [...new Set((m.crate || []).map((c) => c.src))];
    if (crateSrcs.length) says.push(`<li>Found in Building Material Crates${crateSrcs.slice(0, 6).map((id) => srcLink(id)).join('')}${crateSrcs.length > 6 ? ` +${crateSrcs.length - 6}` : ''}</li>`);
    if (says.length) html += `<section class="hud-hide"><h3>What sources say</h3><ul class="says">${says.join('')}</ul></section>`;
    if (m.notes) html += `<div class="note hud-hide">${esc(m.notes)}</div>`;
    return html;
  }

  function renderStation(key) {
    const [name, lvl] = key.split('|');
    const levels = D.stations.filter((s) => s.station === name).sort((a, b) => (a.level ?? 1) - (b.level ?? 1));
    let html = `<div class="card-h"><span class="name">${esc(name)}</span><span class="cat">${esc(levels[0]?.room || '')}</span></div>`;
    for (const s of levels) {
      const cost = (s.cost || []).map((c) => {
        const m = matByNorm.get(norm(c.material));
        return `<li>${c.qty ? esc(c.qty) + '× ' : ''}${m ? `<a class="spot" data-mat="${esc(m.id)}">${esc(c.material)}</a>` : esc(c.material)}</li>`;
      }).join('');
      const cash = s.cash ? `<li>$${Number(s.cash).toLocaleString('en-US')} cash</li>` : '';
      const extra = [s.effect, s.power_kw ? `${s.power_kw} kW` : ''].filter(Boolean).map(esc).join(' · ');
      const conflicts = (s.conflicts || []).map((c) => typeof c === 'string'
        ? `<div class="warn">⚠ Sources disagree: ${esc(c)}</div>`
        : `<div class="warn">⚠ Another source says (${esc(c.field || 'detail')}): ${esc(c.value || '')}${srcLink(c.src)}</div>`).join('');
      const key = `${s.station}|${s.level ?? 1}`, planned = CL.plan.includes(key);
      const planBtn = `<span class="lvl${planned ? ' on' : ''}" data-act="plan" data-key="${esc(key)}" title="Add this upgrade's materials to your checklist">${planned ? '☑ building' : '☐ plan this'}</span>`;
      html += `<section><h3>Level ${esc(s.level ?? 1)}${s.device ? ' · ' + esc(s.device) : ''}${srcLink(s.src)}${vmark(s)}</h3>${planBtn}${extra ? `<div class="note">${extra}</div>` : ''}${s.requires ? `<div class="note">Requires ${esc(s.requires)}</div>` : ''}<ul class="says">${cost}${cash}</ul>${conflicts}</section>`;
    }
    return html;
  }

  function renderRecipe(id) {
    const r = recById.get(id);
    const ings = r.ingredients.map((c) => {
      const m = matByNorm.get(norm(c.material));
      return `<li>${c.qty ? esc(c.qty) + '× ' : ''}${m ? `<a class="spot" data-mat="${esc(m.id)}">${esc(c.material)}</a>` : esc(c.material)}</li>`;
    }).join('');
    const reqs = r.requires.map((q) => `${esc(q.cap)}${q.n ? ' ' + esc(q.n) : ''}`).join(' · ');
    const planned = CL.craft.plan.includes(r.id);
    const planBtn = `<span class="lvl${planned ? ' on' : ''}" data-act="cplan" data-key="${esc(r.id)}" title="Add this recipe's ingredients to your craft checklist">${planned ? '☑ crafting' : '☐ plan this'}</span>`;
    return `<div class="card-h"><span class="name">${esc(r.output)}</span><span class="cat">${esc(r.category || 'craft')}</span></div>`
      + `<section><h3>${esc(r.room || 'Hideout')}${r.time ? ' · ' + esc(r.time) : ''}${srcLink(r.src)}</h3>${planBtn}`
      + `<div class="note">${reqs ? 'Needs hideout capability: ' + reqs : 'No capability requirement listed'}</div><ul class="says">${ings}</ul>`
      + `${r.notes ? `<div class="note hud-hide">${esc(r.notes)}</div>` : ''}</section>`;
  }
  const craftSoon = () => '<div class="empty">Coming soon: crafting data is being added.</div>';

  // a device: the parts it gives (click = that material's card), then where to find it - TAC map spots per area, best
  // areas first (maintainer data), and what research / players say (both editions)
  const disBtn = (id) => { const on = CL.dis.plan.includes(id); return `<span class="lvl${on ? ' on' : ''}" data-act="dplan" data-key="${esc(id)}" title="Add this part to your disassembly checklist">${on ? '☑ want' : '☐ want'}</span>`; };
  function renderDevice(id) {
    const v = devById.get(id);
    let html = `<div class="card-h"><span class="name">${esc(v.name)}</span><span class="cat">${esc(v.category || 'device')}</span></div>`;
    const parts = v.parts.map((p) => {
      const m = matByNorm.get(norm(p.material)), st = m ? CL.disStatus(m.id) : null;
      const have = st && (st.need || st.have) ? ` <span class="muted">have ${esc(st.have)}${st.need ? ' / need ' + esc(st.need) : ''}</span>` : '';
      return `<li>${p.qty ? esc(p.qty) + '× ' : ''}${m ? `<a class="spot" data-mat="${esc(m.id)}" title="Open its card">${esc(p.material)}</a>` : esc(p.material)}${have}${srcLink(p.src)} ${m ? disBtn(m.id) : ''}</li>`;
    }).join('');
    html += `<section><h3>Gives${v.time ? ' · ' + esc(v.time) : ''}${srcLink(v.src)}</h3>${parts ? `<ul class="says">${parts}</ul>` : '<div class="note">Parts unknown yet.</div>'}</section>`;
    const sp = devSpots(v);
    if (sp.length) {
      const total = sp.reduce((s, a) => s + a.n, 0);
      const rows = sp.map((a, i) => {
        const spots = a.spots.slice(0, 4).map((x, j) => {
          const pl = placeById.get(x.place);
          const label = `${pl ? pl.name : 'spot ' + (j + 1)}${x.floor != null && x.floor !== 0 ? ` (floor ${x.floor})` : ''}`;
          return `<a class="spot" data-url="${esc(x.link)}" title="Open this spot on GZW Tac Map">${esc(label)}</a>`;
        }).join(' · ');
        const area = POI_LINK[a.poi] || a.spots[0].link;
        return `<div class="row"${i ? ` data-fitdrop="${Math.min(9, 2 + i)}"` : ''}><div class="top"><a class="poi spot" data-url="${esc(area)}" title="Open on GZW Tac Map">${esc(a.poi)}</a><span class="tag2">×${esc(a.n)}</span></div><div class="spots">${spots}${a.spots.length > 4 ? ` +${a.spots.length - 4}` : ''}</div></div>`;
      }).join('');
      html += `<section><h3>Where (GZW Tac Map · ${esc(total)} spot${total > 1 ? 's' : ''})</h3>${rows}</section>`;
    }
    const hints = (v.hints || []).map((h) => {
      const where = h.poi ? (POI_LINK[h.poi] ? `<a class="spot" data-url="${esc(POI_LINK[h.poi])}" title="Open on GZW Tac Map">${esc(h.poi)}</a>` : esc(h.poi)) : '';
      return `<li>${where}${where && h.text !== h.poi ? ' — ' : ''}${h.text !== h.poi ? esc(h.text) : ''}${h.author ? ` <span class="muted">(${esc(h.author)})</span>` : ''}${srcLink(h.src)}</li>`;
    });
    if (hints.length) html += `<section${sp.length ? ' class="hud-hide"' : ''}><h3>${sp.length ? 'What research / players say' : 'Where (research / players)'}</h3><ul class="says">${hints.join('')}</ul></section>`;
    else if (!sp.length) html += `<section><h3>Where</h3><div class="note">No location data yet.</div></section>`;
    if (v.notes) html += `<div class="note hud-hide">${esc(v.notes)}</div>`;
    return html;
  }
  // a part: which devices give it (and where they are), what it's used for
  function renderPart(id) {
    const m = matById.get(id), cs = CL.disStatus(id);
    let html = `<div class="card-h"><span class="name">${esc(m.name)}</span><span class="cat">${esc(m.category)}</span></div>`;
    if (cs.need || cs.have)
      html += `<div class="clline">Disassembly checklist: have <b>${cs.have}</b>${cs.need ? ` / need <b>${cs.need}</b> · ${cs.short ? `<span class="need">dismantle ${cs.short} more</span>` : '<span class="ok">✓ enough</span>'}` : ''}</div>`;
    const rows = m.dismantle_from.map((d) => {
      const dev = devOfItem(d.item);
      return `<div class="row"><div class="top">${dev ? `<a class="poi spot" data-dev="${esc(dev.id)}" title="Open the device">${esc(d.item)}</a>${dev.time ? `<span class="muted">${esc(dev.time)}</span>` : ''}` : `<span class="poi">${esc(d.item)}</span>`}${srcLink(d.src)}</div>${dev ? `<div class="spots">${devWhere(dev, 4)}</div>` : ''}</div>`;
    }).join('');
    html += `<section><h3>Dismantle from</h3>${disBtn(m.id)}${rows}</section>`;
    if (crafts(m).length) html += usedIn(m, true);
    if (m.notes) html += `<div class="note hud-hide">${esc(m.notes)}</div>`;
    return html;
  }

  // ---------- state + UI ----------
  const S = { results: [], sel: 0, mode: 'search', view: 'finder', logMat: null, logPlaces: [], logSel: 0, logPick: null };
  const input = $('q');

  // ---------- checklist view ----------
  function renderChecklist() {
    const { need, cash } = CL.needs();
    const f = norm(input.value);
    const match = (s) => !f || norm(s).includes(f);
    const byStation = new Map();
    for (const s of D.stations) {
      if (!byStation.has(s.station)) byStation.set(s.station, { room: s.room, levels: [] });
      byStation.get(s.station).levels.push(s);
    }
    const planRows = [...byStation.entries()].filter(([st, info]) => match(st) || info.levels.some((s) => s.device && match(s.device))).map(([st, info]) => {
      const chips = info.levels.slice().sort((a, b) => (a.level ?? 1) - (b.level ?? 1)).map((s) => {
        const key = `${st}|${s.level ?? 1}`, on = CL.plan.includes(key);
        return `<span class="lvl${on ? ' on' : ''}" data-act="plan" data-key="${esc(key)}" title="${esc(s.device || st)}${s.cash ? ' · $' + Number(s.cash).toLocaleString('en-US') : ''}">${on ? '☑' : '☐'} L${esc(s.level ?? 1)}</span>`;
      }).join('');
      return `<div class="pl-row"><a class="pl-name spot" data-st="${esc(st)}">${esc(st)}</a><span class="muted">${esc(info.room || '')}</span><span class="pl-lvls">${chips}</span></div>`;
    }).join('');
    const mats = D.materials.filter((m) => !m.craft_only && (match(m.name) || m.aliases.some(match)));
    const short = (m) => Math.max(0, (need[m.id] || 0) - (CL.have[m.id] || 0));
    const row = (m) => {
      const h = CL.have[m.id] || 0, n = need[m.id] || 0, s = short(m);
      const st = n ? (s ? `<span class="need">need ${s}</span>` : '<span class="ok">✓</span>') : '';
      return `<div class="cl-row${n && !s ? ' done' : ''}"${n && !s ? ' data-fitdrop="4"' : ''}><a class="spot cl-name" data-mat="${esc(m.id)}" title="Where to find it">${esc(m.name)}</a>`
        + `<span class="cl-have"><button class="cbtn" data-act="dec" data-id="${esc(m.id)}" title="One less">−</button><input class="cnum" data-id="${esc(m.id)}" value="${h}" inputmode="numeric" aria-label="have" /><button class="cbtn" data-act="inc" data-id="${esc(m.id)}" title="One more">+</button></span>`
        + `<span class="cl-need">${n ? '/ ' + n : ''}</span><span class="cl-st">${st}</span></div>`;
    };
    // alphabetical, so rows stay put while you tick them off instead of jumping as shortfalls change
    const abc = (a, b) => a.name.localeCompare(b.name, 'en', { sensitivity: 'base' });
    const needed = mats.filter((m) => need[m.id]).sort(abc);
    const others = mats.filter((m) => !need[m.id]).sort(abc);
    const totalShort = needed.reduce((s, m) => s + short(m), 0);
    const summary = CL.plan.length
      ? `${CL.plan.length} upgrade${CL.plan.length > 1 ? 's' : ''} planned · ${totalShort ? `<span class="need">${totalShort} item${totalShort > 1 ? 's' : ''} still to find</span>` : '<span class="ok">you have everything ✓</span>'}${cash ? ` · $${cash.toLocaleString('en-US')} cash` : ''}`
      : 'Tick the upgrades you\'re building below, then set how many of each material you have.';
    $('card').innerHTML = `<div id="cardInner"><div class="card-h"><span class="name">Checklist</span><span class="cat">have / need</span></div><div class="used">${summary}</div>`
      + `<section><h3>Needed for your plan</h3>${needed.length ? needed.map(row).join('') : '<div class="note">Nothing planned yet.</div>'}</section>`
      + `<section class="hud-hide"><h3>Upgrades you're building</h3>${planRows || '<div class="note">No match.</div>'}</section>`
      + `<section class="hud-hide"><h3>Everything else you have</h3>${others.map(row).join('') || '<div class="note">No match.</div>'}</section></div>`;
    fit();
  }
  // ---------- craft checklist view (same layout; planned recipes instead of upgrades) ----------
  function renderCraftList() {
    if (!(D.recipes || []).length) { $('card').innerHTML = `<div id="cardInner">${craftSoon()}</div>`; fit(); return; }
    const need = CL.craftNeeds(), have = CL.craft.have;
    const f = norm(input.value);
    const match = (s) => !f || norm(s).includes(f);
    const abc = (a, b) => a.localeCompare(b, 'en', { sensitivity: 'base' });
    const recs = D.recipes.filter((r) => match(r.output) || match(r.category || '') || r.ingredients.some((c) => match(c.material)))
      .sort((a, b) => abc(a.output, b.output));
    const planRows = recs.map((r) => {
      const on = CL.craft.plan.includes(r.id);
      return `<div class="pl-row"><a class="pl-name spot" data-rec="${esc(r.id)}">${esc(r.output)}</a><span class="muted">${esc(r.room || '')}</span><span class="pl-lvls"><span class="lvl${on ? ' on' : ''}" data-act="cplan" data-key="${esc(r.id)}" title="${esc(r.category || '')}">${on ? '☑' : '☐'} craft</span></span></div>`;
    }).join('');
    const mats = D.materials.filter((m) => crafts(m).length && (match(m.name) || m.aliases.some(match)));
    const short = (m) => Math.max(0, (need[m.id] || 0) - (have[m.id] || 0));
    const row = (m) => {
      const h = have[m.id] || 0, n = need[m.id] || 0, s = short(m);
      const st = n ? (s ? `<span class="need">need ${s}</span>` : '<span class="ok">✓</span>') : '';
      return `<div class="cl-row${n && !s ? ' done' : ''}"${n && !s ? ' data-fitdrop="4"' : ''}><a class="spot cl-name" data-mat="${esc(m.id)}" title="Where to find it">${esc(m.name)}</a>`
        + `<span class="cl-have"><button class="cbtn" data-act="cdec" data-id="${esc(m.id)}" title="One less">−</button><input class="cnum" data-craft="1" data-id="${esc(m.id)}" value="${h}" inputmode="numeric" aria-label="have" /><button class="cbtn" data-act="cinc" data-id="${esc(m.id)}" title="One more">+</button></span>`
        + `<span class="cl-need">${n ? '/ ' + n : ''}</span><span class="cl-st">${st}</span></div>`;
    };
    // alphabetical, so rows stay put while you tick them off
    const byName = (a, b) => abc(a.name, b.name);
    const needed = mats.filter((m) => need[m.id]).sort(byName);
    const others = mats.filter((m) => !need[m.id]).sort(byName);
    const totalShort = needed.reduce((s, m) => s + short(m), 0);
    const n = CL.craft.plan.length;
    const summary = n
      ? `${n} recipe${n > 1 ? 's' : ''} planned · ${totalShort ? `<span class="need">${totalShort} item${totalShort > 1 ? 's' : ''} still to find</span>` : '<span class="ok">you have everything ✓</span>'}`
      : 'Tick the recipes you plan to craft below, then set how many of each ingredient you have.';
    $('card').innerHTML = `<div id="cardInner"><div class="card-h"><span class="name">Craft checklist</span><span class="cat">have / need</span></div><div class="used">${summary}</div>`
      + `<section><h3>Needed for your crafts</h3>${needed.length ? needed.map(row).join('') : '<div class="note">Nothing planned yet.</div>'}</section>`
      + `<section class="hud-hide"><h3>Recipes you're crafting</h3>${planRows || '<div class="note">No match.</div>'}</section>`
      + `<section class="hud-hide"><h3>Other crafting ingredients</h3>${others.map(row).join('') || '<div class="note">No match.</div>'}</section></div>`;
    fit();
  }
  // ---------- disassembly checklist view (same layout; the parts you want instead of upgrades) ----------
  function renderDisList() {
    if (!(D.devices || []).length) { $('card').innerHTML = `<div id="cardInner">${disSoon()}</div>`; fit(); return; }
    const need = CL.disNeeds(), have = CL.dis.have;
    const f = norm(input.value);
    const match = (s) => !f || norm(s).includes(f);
    const abc = (a, b) => a.localeCompare(b, 'en', { sensitivity: 'base' });
    const byName = (a, b) => abc(a.name, b.name);
    const parts = D.materials.filter((m) => isPart(m) && (match(m.name) || m.aliases.some(match) || m.dismantle_from.some((d) => match(d.item)))).sort(byName);
    const short = (m) => Math.max(0, (need[m.id] || 0) - (have[m.id] || 0));
    const row = (m) => {
      const h = have[m.id] || 0, n = need[m.id] || 0, s = short(m);
      const st = n ? (s ? `<span class="need">need ${s}</span>` : '<span class="ok">✓</span>') : '';
      return `<div class="cl-row${n && !s ? ' done' : ''}"${n && !s ? ' data-fitdrop="4"' : ''}><a class="spot cl-name" data-part="${esc(m.id)}" title="Which device gives it">${esc(m.name)}</a>`
        + `<span class="cl-have"><button class="cbtn" data-act="ddec" data-id="${esc(m.id)}" title="One less">−</button><input class="cnum" data-dis="1" data-id="${esc(m.id)}" value="${h}" inputmode="numeric" aria-label="have" /><button class="cbtn" data-act="dinc" data-id="${esc(m.id)}" title="One more">+</button></span>`
        + `<span class="cl-need">${n ? '/ ' + n : ''}</span><span class="cl-st">${st}</span></div>`;
    };
    // alphabetical, so rows stay put while you tick them off
    const wanted = parts.filter((m) => need[m.id]);
    const others = parts.filter((m) => !need[m.id]);
    // devices still to dismantle (parts you want but don't have), alphabetical, with where to find each one
    const todo = new Map();
    for (const m of wanted) {
      if (!short(m)) continue;
      for (const d of m.dismantle_from) {
        const dev = devOfItem(d.item), k = dev ? dev.id : norm(d.item);
        if (!todo.has(k)) todo.set(k, { name: dev ? dev.name : d.item, dev, parts: [] });
        todo.get(k).parts.push(m.name);
      }
    }
    const devRows = [...todo.values()].sort(byName).map((t) => `<div class="row"><div class="top">${t.dev ? `<a class="poi spot" data-dev="${esc(t.dev.id)}" title="Open the device">${esc(t.name)}</a>` : `<span class="poi">${esc(t.name)}</span>`}<span class="muted">→ ${esc(t.parts.join(', '))}</span></div>`
      + `<div class="spots">${t.dev ? devWhere(t.dev, 4) : '<span class="muted">No location data yet</span>'}</div></div>`).join('');
    const pick = parts.map((m) => `<div class="pl-row"><a class="pl-name spot" data-part="${esc(m.id)}">${esc(m.name)}</a><span class="muted">${esc(m.dismantle_from.map((d) => d.item).join(', '))}</span><span class="pl-lvls">${disBtn(m.id)}</span></div>`).join('');
    const totalShort = wanted.reduce((s, m) => s + short(m), 0);
    const summary = wanted.length
      ? `${wanted.length} part${wanted.length > 1 ? 's' : ''} wanted · ${totalShort ? `<span class="need">${totalShort} device${totalShort > 1 ? 's' : ''} still to dismantle</span>` : '<span class="ok">you have everything ✓</span>'}`
      : 'Tick the parts you want below; it shows which devices to dismantle and where to find them.';
    $('card').innerHTML = `<div id="cardInner"><div class="card-h"><span class="name">Disassembly checklist</span><span class="cat">have / need</span></div><div class="used">${summary}</div>`
      + `<section><h3>Devices to dismantle</h3>${devRows || `<div class="note">${wanted.length ? 'Nothing left to dismantle.' : 'Nothing planned yet.'}</div>`}</section>`
      + `<section><h3>Parts you want</h3>${wanted.length ? wanted.map(row).join('') : '<div class="note">Nothing planned yet.</div>'}</section>`
      + `<section class="hud-hide"><h3>Pick the parts you want</h3>${pick || '<div class="note">No match.</div>'}</section>`
      + `<section class="hud-hide"><h3>Other parts you have</h3>${others.map(row).join('') || '<div class="note">No match.</div>'}</section></div>`;
    fit();
  }
  const isList = (v) => v === 'checklist' || v === 'craftlist' || v === 'dislist';
  const PLACEHOLDER = { finder: 'Search a material or station…', checklist: 'Filter the checklist…', crafting: 'Search a recipe or ingredient…', craftlist: 'Filter the craft checklist…',
    disassembly: 'Search a device or part…', dislist: 'Filter the disassembly checklist…' };
  function rerender() {
    const top = $('card').scrollTop;
    if (S.view === 'checklist') renderChecklist(); else if (S.view === 'craftlist') renderCraftList(); else if (S.view === 'dislist') renderDisList(); else renderCard();
    $('card').scrollTop = top;
  }
  function setView(v) {
    if (S.mode === 'log') endLog();
    S.view = v;
    $('frame').classList.toggle('cl', isList(v));
    input.placeholder = PLACEHOLDER[v];
    input.value = '';
    if (isList(v)) { $('results').innerHTML = ''; rerender(); } else update();
    input.focus();
  }

  function renderResults() {
    $('results').innerHTML = S.results.map((r, i) => `<span class="chip${i === S.sel ? ' sel' : ''}" data-i="${i}">${esc(r.label)}${['station', 'recipe', 'device', 'part'].includes(r.kind) ? `<span class="k">${r.kind}</span>` : ''}</span>`).join('');
  }
  function renderCard() {
    const r = S.results[S.sel], craft = S.view === 'crafting';
    if (craft && !(D.recipes || []).length) { $('card').innerHTML = craftSoon(); return; }
    if (S.view === 'disassembly' && !(D.devices || []).length) { $('card').innerHTML = disSoon(); return; }
    if (!r) {
      // disassembly with an empty box: every device, so you can browse them
      const devList = () => `<ul class="says">${(D.devices || []).map((v) => `<li><a class="spot" data-dev="${esc(v.id)}">${esc(v.name)}</a> <span class="muted">→ ${esc(v.parts.map((p) => p.material).join(', ') || '?')}</span></li>`).join('')}</ul>`;
      $('card').innerHTML = input.value.trim()
        ? `<div class="empty">No match for "${esc(input.value)}".</div>`
        : craft ? `<div class="empty">Crafting: type a recipe (e.g. <b>morphine</b>, <b>hacking tool</b>) or an ingredient (<b>saline</b>).</div>`
        : S.view === 'disassembly' ? `<div id="cardInner"><div class="empty">Disassembly: type a device (e.g. <b>imsi</b>, <b>radio</b>) or a part (<b>cpu card</b>).</div>${devList()}</div>`
        : `<div class="empty">Type a material (e.g. <b>dynamo</b>, <b>steel</b>, <b>sold</b>) or a station (<b>generator</b>).</div>`;
      if (S.view === 'disassembly') fit();
      return;
    }
    $('card').innerHTML = `<div id="cardInner">${r.kind === 'material' ? renderMaterial(matById.get(r.id), craft) : r.kind === 'recipe' ? renderRecipe(r.id)
      : r.kind === 'device' ? renderDevice(r.id) : r.kind === 'part' ? renderPart(r.id) : renderStation(r.id)}</div>`;
    $('card').scrollTop = 0;
    fit();
  }

  // ---------- fit: the pinned HUD can't scroll (clicks pass through), so it must fit the window ----------
  function fit() {
    const main = $('card'), inner = $('cardInner'), more = $('fitmore');
    more.classList.remove('on');
    if (!inner) return;
    inner.style.zoom = '';
    inner.querySelectorAll('.fit-dropped').forEach((el) => el.classList.remove('fit-dropped'));
    if (!document.body.classList.contains('hud')) return; // search view scrolls normally
    const over = () => inner.getBoundingClientRect().height > main.clientHeight + 1;
    let z = 1;
    while (over() && z > 0.52) { z = Math.round((z - 0.04) * 100) / 100; inner.style.zoom = String(z); }
    let dropped = 0;
    const drops = [...inner.querySelectorAll('[data-fitdrop]')].sort((a, b) => b.dataset.fitdrop - a.dataset.fitdrop);
    for (const el of drops) {
      if (!over()) break;
      el.classList.add('fit-dropped');
      dropped++;
      const sec = el.closest('section');
      if (sec && !sec.querySelector('.row:not(.fit-dropped), li')) sec.classList.add('fit-dropped');
    }
    if (dropped) { more.textContent = `+${dropped} more · ${meta.hotkey || 'hotkey'} to see all`; more.classList.add('on'); }
    // last resort: scale to exactly the space left (a tiny window with huge text still ends up ~normal size)
    for (let i = 0; i < 6 && over() && z > 0.3; i++) {
      const h = inner.getBoundingClientRect().height;
      z = Math.max(0.3, Math.floor(z * (main.clientHeight / h) * 100) / 100 - (i ? 0.02 : 0));
      inner.style.zoom = String(z);
    }
  }
  window.addEventListener('resize', () => fit());
  function update() {
    if (isList(S.view)) { rerender(); return; } // the search box filters the checklist
    S.results = search(input.value); S.sel = 0; renderResults(); renderCard();
  }
  function select(i) { if (!S.results.length) return; S.sel = (i + S.results.length) % S.results.length; renderResults(); renderCard(); }
  function toView(v) { if (S.view !== v) { S.view = v; $('frame').classList.remove('cl'); input.placeholder = PLACEHOLDER[v]; } }
  const toFinder = () => toView('finder');
  function openMaterial(id) { // crafting pages stay in Crafting; a crafting-only ingredient always opens there
    const m = matById.get(id);
    if (m.craft_only && !crafts(m).length && isPart(m)) { openPart(id); return; } // a part nothing crafts with yet
    toView(isCraftView() || m.craft_only ? 'crafting' : 'finder');
    input.value = m.name; update();
    const i = S.results.findIndex((r) => r.kind === 'material' && r.id === id);
    if (i > 0) select(i);
    input.focus();
  }
  function openRecipe(id) {
    const r = recById.get(id); if (!r) return;
    toView('crafting'); input.value = r.output; update();
    const i = S.results.findIndex((x) => x.kind === 'recipe' && x.id === id);
    if (i >= 0) select(i);
    input.focus();
  }
  function openDis(kind, id, label) {
    toView('disassembly'); input.value = label; update();
    const i = S.results.findIndex((x) => x.kind === kind && x.id === id);
    if (i >= 0) select(i);
    input.focus();
  }
  function openDevice(id) { const v = devById.get(id); if (v) openDis('device', id, v.name); }
  function openPart(id) { const m = matById.get(id); if (m) openDis('part', id, m.name); }
  function openStation(name) {
    toFinder(); input.value = name; update();
    const i = S.results.findIndex((r) => r.kind === 'station' && r.label === name);
    if (i >= 0) select(i);
    input.focus();
  }

  // --- log-a-find mode (picks from PLACE_INDEX, built in useData) ---
  function startLog() {
    const r = S.results[S.sel];
    if (!r || r.kind !== 'material') { toast('Pick a material first'); return; }
    S.mode = 'log'; S.logMat = matById.get(r.id); S.logPick = null;
    $('frame').classList.add('logging');
    S.savedQuery = input.value; input.value = ''; input.placeholder = `Where did you find ${S.logMat.name}? (spot or area)`;
    $('results').innerHTML = '';
    logUpdate();
  }
  function logUpdate() {
    const q = norm(input.value);
    if (S.logPick) {
      $('card').innerHTML = `<div class="log-prompt">Logging <b>${esc(S.logMat.name)}</b> @ ${esc(S.logPick.name)}${S.logPick.kind === 'place' ? ' (' + esc(S.logPick.poi) + ')' : ''}</div>
        <div class="note">Was it in a Building Material Crate? Press <b>2</b> = T2 crate, <b>3</b> = T3 crate, <b>Enter</b> = no / not sure.</div>`;
      return;
    }
    const score = (p) => Math.max(bestScore(q, p.text), tokenScore(input.value, p.text[0]));
    S.logPlaces = q ? PLACE_INDEX.map((p) => ({ p, s: score(p) + (p.crates && p.crates.t2 + p.crates.t3 ? 2 : 0) })).filter((x) => x.s > 2).sort((a, b) => b.s - a.s).slice(0, 12).map((x) => x.p) : [];
    S.logSel = 0;
    renderLogPlaces();
  }
  function renderLogPlaces() {
    const opts = S.logPlaces.map((p, i) => `<div class="place-opt${i === S.logSel ? ' sel' : ''}" data-i="${i}"><span>${esc(p.name)}</span><span class="pp">${p.kind === 'place' ? esc(p.poi) : 'whole area'}${p.crates && (p.crates.t2 + p.crates.t3) ? ' · ' + crateTxt(p.crates) : ''}</span></div>`).join('');
    const how = hasPlaces ? 'Type the spot (e.g. "construction pha" or "warehouse sawmill")' : 'Type the area (e.g. "sawmill" or "ybl")';
    $('card').innerHTML = `<div class="log-prompt">Logging a find of <b>${esc(S.logMat.name)}</b>. ${how} and press Enter. Esc cancels.</div>${opts || '<div class="empty">Start typing a place…</div>'}`;
  }
  async function saveLog(tier) {
    const p = S.logPick, m = S.logMat;
    const f = { ts: new Date().toISOString(), material: m.id, material_name: m.name, place: p.id, place_name: p.name, poi: p.poi, crate_tier: tier || null, build: meta.build };
    try { await Finds.add(f); toast(`Logged ${m.name} @ ${p.name}${meta.share_url ? " - right-click › Share my finds to send it to everyone" : ""}`); } catch (e) { toast('Could not save: ' + e); }
    endLog(m.id);
  }
  function endLog(openId) {
    S.mode = 'search'; S.logPick = null;
    $('frame').classList.remove('logging');
    input.placeholder = PLACEHOLDER[S.view] || PLACEHOLDER.finder;
    input.value = S.savedQuery || '';
    update();
    if (openId) { const i = S.results.findIndex((r) => r.id === openId); if (i >= 0) select(i); }
    footer();
  }

  function toast(msg) { const t = $('toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove('show'), 2200); }
  function openUrl(url) { if (api()) api().open_url(url); else window.open(url, '_blank', 'noopener'); }
  function hide() { if (api()) api().hide(); }
  // HUD = pinned, see-through, click-through. In a plain browser it just previews the look.
  function pin() { if (api()) api().pin(); else window.gzwMode('hud'); }
  function quit() { if (api()) api().quit(); else toast('Close the tab to quit'); }
  function setTextScale(v) {
    document.documentElement.style.setProperty('--fs', String(v));
    setTimeout(fit, 30);
  }
  const KEY_HELP = [
    ['toggle', 'Show · pin over the game (see-through) · back to search'],
    ['hide', 'Hide'],
    ['quit', 'Shut down the finder'],
    ['taller', 'Taller window'],
    ['shorter', 'Shorter window'],
    ['wider', 'Wider window'],
    ['narrower', 'Narrower window'],
    ['less_see_through', 'Less see-through (more solid)'],
    ['more_see_through', 'More see-through'],
    ['bigger_text', 'Bigger text'],
    ['smaller_text', 'Smaller text'],
  ];
  function showHelp() {
    const keys = meta.keys || {};
    const rows = KEY_HELP.map(([a, label]) => `<tr><td class="k">${esc(keys[a] || '—')}</td><td>${esc(label)}</td></tr>`).join('');
    const errs = (meta.hotkey_errors || []).map((e) => `<div class="warn">⚠ ${esc(e)}</div>`).join('');
    $('card').innerHTML = `<div id="cardInner" class="help"><div class="card-h"><span class="name">Hotkeys</span><span class="cat">work anywhere, even in game</span></div>
      <table>${rows}</table>${errs}
      <section><h3>In the search box</h3><table>
        <tr><td class="k">↑ ↓</td><td>Pick another match</td></tr>
        <tr><td class="k">Enter</td><td>Pin the card over the game</td></tr>
        <tr><td class="k">Ctrl+L</td><td>Log a find (your finds rank first)</td></tr>
        <tr><td class="k">Right-click</td><td>Share my finds: sends them to everyone through GitHub</td></tr>
        <tr><td class="k">Esc</td><td>Clear, then hide</td></tr>
        <tr><td class="k">F1</td><td>This help</td></tr>
        <tr><td class="k">Right-click</td><td>Menu: Finder · Checklist (what you have vs what your upgrades need) · Crafting (recipes + ingredients) · Craft checklist · Disassembly (devices + the parts they give) · Disassembly checklist · Hide · Exit</td></tr>
        <tr><td class="k">Click a name</td><td>${hasPlaces ? "Areas, spots and players' quotes open that spot on GZW Tac Map" : "Area names and players' quotes open that area on GZW Tac Map (in your browser)"}</td></tr></table></section>
      <div class="note">Change any key in config.json, then restart the finder.</div>${dataNote()}</div>`;
  }
  function dataNote() {
    const v = (D.meta.verification || {}).summary;
    const checked = v ? Object.values(v).reduce((a, b) => a + b, 0) : 0;
    const map = hasPlaces ? `map spots from GZW Tac Map (${esc((D.meta.tacmap_fetched || '').slice(0, 10))})` : 'area links open GZW Tac Map';
    const from = dataInfo.source === 'feed' ? ` Data: downloaded update v${esc(dataInfo.version)}.` : '';
    return `<section><h3>About the data</h3><div class="note">${D.materials.length} materials · ${D.stations.length} station recipes · ${(D.recipes || []).length} crafting recipes · ${(D.devices || []).length} devices to dismantle${(D.disassembly || []).length ? ` (${esc(D.disassembly.length)} map spots)` : ''} ·${esc(D.meta.crate_reports || 0)} crate openings reported · ${map}.${
      checked ? ` An independent fact-check of ${esc(checked)} sampled claims found ${esc(v.supported)} supported, ${esc(v.partial)} partly right (≈) and ${esc(v.unsupported)} wrong (removed). ✓ marks checked claims.` : ''}${from}${
      meta.app_version ? ` App ${esc(meta.app_version)}${meta.edition === 'public' ? '' : ' (' + esc(meta.edition) + ')'}.` : ''}</div></section>`;
  }
  function footer() {
    const tac = hasPlaces ? (D.meta.tacmap_fetched || '').slice(0, 10) : '';
    const changed = (D.meta.sources_changed || []).length;
    const warn = (meta.hotkey_errors || []).length ? ` · <span class="tag3" title="${esc(meta.hotkey_errors.join('\n'))}">⚠ ${meta.hotkey_errors.length} hotkey(s) unavailable (F1)</span>` : '';
    const stale = changed ? ` · <span class="tag2" title="${esc(D.meta.sources_changed.map((c) => c.title).join('\n'))}">⚠ ${changed} source(s) updated since this data</span>` : '';
    const patched = meta.build && D.meta.game_build && meta.build !== D.meta.game_build
      ? ` · <span class="tag2" title="This data was built for game build ${esc(D.meta.game_build)}; you're on ${esc(meta.build)}. Loot may have moved - your own finds (Ctrl+L) catch it first.">⚠ game updated since this data</span>` : '';
    const src = tac ? ` · TAC ${esc(tac)}` : dataInfo.source === 'feed' ? ` · data v${esc(dataInfo.version)}` : '';
    // unobtrusive: only in the search view's footer (hidden while pinned) and the right-click menu
    const upd = meta.update ? ` · <a class="upd" data-url="${esc(meta.update.url)}" title="Version ${esc(meta.update.latest)} is out (you have ${esc(meta.app_version)}) - opens the download page">⬆ Update available</a>` : '';
    const ver = meta.app_version ? ` · v${esc(meta.app_version)}` : '';
    $('foot').innerHTML = `finds: <b>${Finds.list.length}</b>${ver}${src}${meta.build ? ' · build ' + esc(meta.build) : ''}${upd}${warn}${stale}${patched}`;
    $('hk').textContent = meta.hotkey ? meta.hotkey : '';
    $('hudtag').textContent = `◆ PINNED · ${meta.hotkey || 'hotkey'} to search`;
    const k = meta.keys || {};
    const short = (s) => String(s || '').replace(/^Ctrl\+Alt\+/i, '');
    $('keys').innerHTML = meta.shell
      ? `Ctrl+Alt + <b>${esc(short(k.toggle))}</b> open/pin · <b>${esc(short(k.hide))}</b> hide · <b>${esc(short(k.quit))}</b> exit · <b>↑↓</b> height · <b>←→</b> width · <b>+/-</b> text · Alt+Shift + <b>+/-</b> see-through · <b>right-click</b> menu · <b>F1</b> help`
      : '<b>Enter</b> pin · <b>Ctrl+L</b> log · <b>Esc</b> clear · <b>right-click</b> menu · <b>F1</b> help';
  }

  input.addEventListener('input', () => (S.mode === 'log' ? logUpdate() : update()));
  // checklist "have" boxes: commit on Enter / leaving the box (not every keystroke, so typing isn't interrupted)
  document.addEventListener('change', (e) => {
    if (e.target.matches('.cnum')) { (e.target.dataset.dis ? CL.setDisHave : e.target.dataset.craft ? CL.setCraftHave : CL.setHave).call(CL, e.target.dataset.id, e.target.value); rerender(); }
  });

  // ---------- right-click menu (search view only; the pinned HUD lets clicks through to the game) ----------
  const CTX_ITEMS = [['finder', '⌕  Finder'], ['checklist', '☑  Checklist'], ['crafting', '⚗  Crafting'], ['craftlist', '☑  Craft checklist'],
    ['disassembly', '⚙  Disassembly'], ['dislist', '☑  Disassembly checklist'], ['-'], ['help', '?  Hotkeys'], ['hide', '_  Hide'], ['quit', '✕  Exit']];
  const CTX_VIEWS = CTX_ITEMS.findIndex(([k]) => k === '-'); // "Update available" goes after the views
  function openCtx(x, y) {
    const m = $('ctx');
    const extra = [...(meta.update ? [['update', `⬆  Update available (${meta.update.latest})`]] : []),
      ...(meta.share_url && Finds.list.length ? [['share', `⇪  Share my finds (${Finds.list.length})`]] : [])];
    const items = [...CTX_ITEMS.slice(0, CTX_VIEWS), ...extra, ...CTX_ITEMS.slice(CTX_VIEWS)];
    m.innerHTML = items.map(([k, l]) => k === '-' ? '<div class="ctx-sep"></div>' : `<div class="ctx-item${k === S.view ? ' cur' : ''}${k === 'update' ? ' upd' : ''}" data-ctx="${k}">${esc(l)}</div>`).join('');
    m.classList.add('open');
    const r = m.getBoundingClientRect();
    m.style.left = Math.max(2, Math.min(x, innerWidth - r.width - 4)) + 'px';
    m.style.top = Math.max(2, Math.min(y, innerHeight - r.height - 4)) + 'px';
  }
  const closeCtx = () => $('ctx').classList.remove('open');
  // Opens a pre-filled GitHub issue with your finds (material | area | day - nothing else). The maintainer's copy
  // reads these, adds them to the shared data and closes the issue. Each GitHub account counts as one reporter.
  function shareFinds() {
    if (!meta.share_url) return;
    const lines = [...new Set(Finds.list.filter((f) => f.material && f.poi && f.ts)
      .map((f) => `- ${f.material} | ${f.poi} | ${String(f.ts).slice(0, 10)}`.replace(/[\r\n]/g, ' ')))].slice(-120);
    if (!lines.length) { toast('No finds to share yet - press Ctrl+L when you find something'); return; }
    const body = `Finds logged in game with GZW Material Finder. Just press "Create" (or "Submit new issue"); they're added to everyone's data within a day.\n\ngzw-finds v1\n${lines.join('\n')}\n`;
    openUrl(`${meta.share_url}?title=${encodeURIComponent(`[finds] ${lines.length} find${lines.length > 1 ? 's' : ''}`)}&body=${encodeURIComponent(body)}`);
    toast('Opened GitHub in your browser - sign in and press Create');
  }
  function doCtx(k) {
    if (PLACEHOLDER[k]) setView(k);
    else if (k === 'help') showHelp();
    else if (k === 'update' && meta.update) openUrl(meta.update.url); // the shell only opens https pages
    else if (k === 'share') shareFinds();
    else if (k === 'hide') hide();
    else if (k === 'quit') quit();
  }
  document.addEventListener('contextmenu', (e) => {
    if (e.target.matches('input')) return; // keep copy/paste on the text boxes
    e.preventDefault();
    if (!document.body.classList.contains('hud')) openCtx(e.clientX, e.clientY);
  });

  document.addEventListener('keydown', (e) => {
    if ($('ctx').classList.contains('open') && e.key === 'Escape') { e.preventDefault(); closeCtx(); return; }
    if (e.target.matches && e.target.matches('.cnum')) { // typing a "have" number
      if (e.key === 'Enter' || e.key === 'Escape') { e.preventDefault(); e.target.blur(); input.focus(); }
      return;
    }
    if (e.ctrlKey && (e.key === 'l' || e.key === 'L')) { e.preventDefault(); if (S.mode === 'search' && (S.view === 'finder' || S.view === 'crafting')) startLog(); return; }
    if (e.ctrlKey && (e.key === 'q' || e.key === 'Q')) { e.preventDefault(); quit(); return; }
    if (e.key === 'F1') { e.preventDefault(); showHelp(); return; }
    // In the desktop app these are global hotkeys handled by the shell; in a plain browser, emulate text size.
    if (!api() && e.ctrlKey && e.altKey && (e.key === '=' || e.key === '-')) {
      e.preventDefault();
      const cur = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--fs')) || 1;
      const v = Math.round(Math.min(2, Math.max(0.7, cur + (e.key === '=' ? 0.1 : -0.1))) * 10) / 10;
      setTextScale(v); toast(`Text ${Math.round(v * 100)}%`);
      return;
    }
    if (S.mode === 'log') {
      if (e.key === 'Escape') { e.preventDefault(); endLog(); return; }
      if (S.logPick) {
        if (e.key === '2' || e.key === '3') { e.preventDefault(); saveLog(Number(e.key)); }
        else if (e.key === 'Enter') { e.preventDefault(); saveLog(null); }
        return;
      }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); if (S.logPlaces.length) { S.logSel = (S.logSel + (e.key === 'ArrowDown' ? 1 : -1) + S.logPlaces.length) % S.logPlaces.length; renderLogPlaces(); } return; }
      if (e.key === 'Enter' && S.logPlaces[S.logSel]) { e.preventDefault(); S.logPick = S.logPlaces[S.logSel]; logUpdate(); }
      return;
    }
    if (e.key === 'ArrowDown' && !isList(S.view)) { e.preventDefault(); select(S.sel + 1); }
    else if (e.key === 'ArrowUp' && !isList(S.view)) { e.preventDefault(); select(S.sel - 1); }
    else if (e.key === 'Enter') { e.preventDefault(); if (S.results.length || isList(S.view)) pin(); }
    else if (e.key === 'Escape') { e.preventDefault(); if (input.value) { input.value = ''; update(); } else hide(); }
  });
  document.addEventListener('click', (e) => {
    const ci = e.target.closest('[data-ctx]'); if (ci) { closeCtx(); doCtx(ci.dataset.ctx); return; }
    if (!e.target.closest('#ctx')) closeCtx();
    const act = e.target.closest('[data-act]');
    if (act) {
      const a = act.dataset.act;
      if (a === 'plan') CL.togglePlan(act.dataset.key);
      else if (a === 'cplan') CL.toggleCraft(act.dataset.key);
      else if (a === 'cinc' || a === 'cdec') CL.setCraftHave(act.dataset.id, (CL.craft.have[act.dataset.id] || 0) + (a === 'cinc' ? 1 : -1));
      else if (a === 'inc' || a === 'dec') CL.setHave(act.dataset.id, (CL.have[act.dataset.id] || 0) + (a === 'inc' ? 1 : -1));
      else if (a === 'dplan') CL.toggleDis(act.dataset.key);
      else if (a === 'dinc' || a === 'ddec') CL.setDisHave(act.dataset.id, (CL.dis.have[act.dataset.id] || 0) + (a === 'dinc' ? 1 : -1));
      rerender();
      return;
    }
    const dev = e.target.closest('[data-dev]'); if (dev) { openDevice(dev.dataset.dev); return; }
    const part = e.target.closest('[data-part]'); if (part) { openPart(part.dataset.part); return; }
    const st = e.target.closest('[data-st]'); if (st) { openStation(st.dataset.st); return; }
    const rec = e.target.closest('[data-rec]'); if (rec) { openRecipe(rec.dataset.rec); return; }
    const chip = e.target.closest('.chip'); if (chip) { select(Number(chip.dataset.i)); input.focus(); return; }
    const opt = e.target.closest('.place-opt'); if (opt) { S.logPick = S.logPlaces[Number(opt.dataset.i)]; logUpdate(); input.focus(); return; }
    const mat = e.target.closest('[data-mat]'); if (mat) { openMaterial(mat.dataset.mat); return; }
    const link = e.target.closest('[data-url]'); if (link) { openUrl(link.dataset.url); return; }
    if (e.target.closest('#b-help')) { showHelp(); input.focus(); return; }
    if (e.target.closest('#b-hide')) { hide(); return; }
    if (e.target.closest('#b-quit')) { quit(); }
  });

  // ---------- called by the desktop shell ----------
  window.gzwFocus = () => { input.focus(); input.select(); };
  // Mode change: 'active' (searching) or 'hud' (pinned over the game). Doesn't move focus or reset the card:
  // coming back from the HUD you keep what you were looking at (the shell focuses search only on a fresh open).
  window.gzwMode = (mode) => {
    document.body.classList.toggle('hud', mode === 'hud');
    closeCtx();
    if (mode === 'hud') $('card').scrollTop = 0;
    setTimeout(fit, 30);
  };
  window.gzwToast = (msg) => toast(msg);
  window.gzwTextScale = (v) => setTextScale(v);
  window.gzwFit = () => fit();
  // the shell found something new (e.g. "update available"): re-read meta, redraw the footer
  window.gzwMeta = async () => {
    if (!api()) return;
    try { meta = { ...meta, ...(await api().get_meta()), shell: true }; } catch (e) { console.error(e); }
    footer();
  };
  // In a plain browser, clicking the pinned card returns to search mode (the shell uses the hotkey instead).
  document.addEventListener('mousedown', () => { if (!api() && document.body.classList.contains('hud')) window.gzwMode('active'); });

  async function boot() {
    await Finds.load();
    await CL.load();
    if (api()) {
      try { meta = { ...meta, ...(await api().get_meta()), shell: true }; } catch (e) { console.error(e); }
      // newest valid data: a verified download arrives as a parsed object (JSON through the bridge), never as script
      try {
        const r = await api().get_data();
        if (r && r.data) { useData(r.data); dataInfo = { ...dataInfo, source: r.source, version: r.data_version }; }
      } catch (e) { console.error(e); useData(window.GZW_DATA); dataInfo = { ...dataInfo, source: 'bundled', version: null }; }
      if (meta.text_scale) setTextScale(meta.text_scale);
      if (meta.mode === 'hud') document.body.classList.add('hud'); // page was reloaded while pinned
    }
    // Preview/screenshot hook for a plain browser: index.html#q=steel&mode=hud&fs=1.3
    const h = new URLSearchParams(location.hash.slice(1));
    if (!api() && h.has('q')) input.value = h.get('q');
    if (!api() && h.get('fs')) setTextScale(parseFloat(h.get('fs')) || 1);
    footer(); update(); input.focus();
    if (!api() && h.get('mode') === 'hud') window.gzwMode('hud');
    dataInfo.booted = !!api() || !window.pywebview; // in the shell: only once the bridge answered
  }
  window.addEventListener('pywebviewready', boot);
  boot();

  // Exposed for automated tests.
  window.GZW = {
    search, rank, get matById() { return matById; }, Finds, crateGuess, select: (i) => select(i), state: S, fit, setTextScale, showHelp, CL, setView, openCtx, rerender,
    openDevice, openPart, openMaterial, devSpots, data: () => D, dataInfo: () => ({ ...dataInfo, materials: D.materials.length, places: (D.places || []).length }), startLog, renderMaterial,
  };
})();
