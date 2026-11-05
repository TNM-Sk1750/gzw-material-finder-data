"""Data sync for GZW Material Finder: pulls GZW Tac Map markers, merges seed research, writes app/data/gzw_data.js.

Polite by design: TAC map is checked at most once per `refresh_hours` (default 24), compressed, and only
when something changed is the data rebuilt. Source pages behind the seed research are re-checked weekly
(one request each, 2 s apart) so the app can say when the research might be out of date.
Maintainer edition only: the public edition never calls gzwtacmap.com or the source pages (see edition()).

CLI:  python shell/datasync.py                 # refresh if due, rebuild if changed
      python shell/datasync.py --force         # refresh now
      python shell/datasync.py --build         # rebuild only (no network)
      python shell/datasync.py --build-public  # shareable build, TAC data left out -> data/public/gzw_data.json
"""
import gzip
import hashlib
import html
import json
import math
import re
import sys
import time
import urllib.request
import winreg
import zlib
from datetime import datetime, timezone
from pathlib import Path

APP_VERSION = '1.3.0'  # bump on every release, and before publishing changed seed data (see publish.py)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
TAC_DIR = DATA / 'tacmap'
SEED_DIR = DATA / 'seed'
OUT_JS = ROOT / 'app' / 'data' / 'gzw_data.js'
STATE = DATA / 'sync_state.json'
EDITION_FILE = ROOT / 'edition.json'      # written by tools/package.py; absent in the source tree
POI_CENTRES = SEED_DIR / 'poi_centres.json'
CONTRIB_CACHE = DATA / 'contrib_cache'    # other maintainers' finds, downloaded by publish.py
PUBLIC_JSON = DATA / 'public' / 'gzw_data.json'

SITE = 'https://gzwtacmap.com'  # links only here; the marker download lives in tacmap.py (maintainer edition)
WEEK = 7 * 24 * 3600
GZW_APPID = '2479810'

# Location-type hint text -> which TAC sub-POI names count as that type of place.
TYPE_RULES = [
    ('construction', r'construct|concrete|cement|building site', r'construction site|cement|lumberyard'),
    ('lumber', r'sawmill|lumber|logging|wood ?shed', r'lumber|logging|woodshed|\bsaw\b|sawmill'),
    ('industrial', r'industrial|factor|warehouse|depot|storage', r'warehouse|depot|factory|storage|supply yard|wreck(ing)? ?yard'),
    ('garage', r'garage|workshop|tool ?shed|mechanic|car repair', r'garage|car repair|toolshed|workshop|wreck(ing)? ?yard'),
    ('farm', r'\bfarms?\b|\bbarns?\b|farm shed', r'farm|woodshed|shed'),
    ('office', r'office|server|comms|communication|radio', r'office|radio|headquarters|town hall|main building'),
    ('medical', r'clinic|pharmac|hospital|medical|infirmar|doctor|laborator', r'infirmary|doctor|hospital|clinic|pharmacy|research facility'),
    ('druglab', r'drug lab|poppy', r'drug lab|poppy'),
    ('military', r'military|barracks|bunker|base\b', r'military|barracks|bunker|outpost|headquarters|shooting range|mortar nest|training course|air control'),
    ('camp', r'\bcamps?\b|campsite|shelter|shack|\bhuts?\b|woodland|forest', r'campsite|shelter|shack|\bhut\b'),
    ('gas', r'gas station|fuel|petrol', r'gas station|fuel'),
    ('housing', r'\bhouses?\b|\bhomes?\b|stilt|village|apartment', r'house|homestead|dwelling|villa|apartment|housing|living quarters'),
]
TYPE_RULES = [(k, re.compile(h, re.I), re.compile(p, re.I)) for k, h, p in TYPE_RULES]
# Hint types matched by what TAC map knows is there, not by place name.
CONTAINER_RULES = [
    ('toolbox', re.compile(r'tool ?box', re.I), lambda pl: pl['toolBoxes'] > 0),
    ('crate', re.compile(r'building materials? crate|material crate|blue (crate|box)', re.I), lambda pl: pl['crates']['t2'] + pl['crates']['t3'] > 0),
]
# How people write area names -> TAC's area (longest first so "Pha Lang Airfield" beats "Pha Lang").
POI_ALIASES = [
    ('pha lang airfield', 'Pha Lang Airfield'), ('airfield', 'Pha Lang Airfield'), (r'\bpla\b', 'Pha Lang Airfield'),
    ('ground zero', 'Ground Zero'), (r'\bgz\b', 'Ground Zero'), ('northern reach', 'Ground Zero'),
    ('lakevale', 'Ground Zero'), (r'r[ls]{2}s?-?1\b', 'Ground Zero'), ('radio observatory', 'Ground Zero'),
    ("hunter'?s paradise", "Hunter's Paradise"), ('midnight sapphire', 'Midnight Sapphire'), ('tiger bay', 'Tiger Bay'),
    ('blue lagoon', 'Blue Lagoon'), ('fort narith', 'Fort Narith'), (r'\bybl\b|ybl-1', 'YBL-1'), ('sawmill', 'Sawmill'),
    ('ban pa', 'Ban Pa'), ('kiu vongsa', 'Kiu Vongsa'), ('nam thaven', 'Nam Thaven'), (r'pha lang(?! airfield)', 'Pha Lang'),
]
POI_ALIASES = [(re.compile(p, re.I), poi) for p, poi in POI_ALIASES]
# Place names too generic to pin a claim to one spot ("a farm", "the warehouse").
GENERIC_PLACE = re.compile(r'^(farm|garage|warehouse|office|offices|shed|house|checkpoint|campsite|construction site|'
                           r'gas station|small marketplace|marketplace|remote house|farm house|main entrance|field shelter)$', re.I)


def log(msg, tag='sync'):
    DATA.mkdir(parents=True, exist_ok=True)
    with open(DATA / 'finder.log', 'a', encoding='utf-8') as f:
        f.write(f'{datetime.now():%Y-%m-%d %H:%M:%S} {tag}: {msg}\n')


def edition():
    """'maintainer' (TAC map, source checks, publisher) or 'public' (bundled data + the GitHub feed only).
    No edition.json = running from the source tree = maintainer. A broken one counts as public: the safe side."""
    try:
        ed = json.loads(EDITION_FILE.read_text(encoding='utf-8')).get('edition')
    except FileNotFoundError:
        return 'maintainer'
    except (OSError, ValueError, AttributeError):
        return 'public'
    return ed if ed in ('maintainer', 'public') else 'public'


def version_tuple(v):
    """'1.10.2' -> (1, 10, 2, 0) so versions compare as numbers; anything else -> None."""
    if not isinstance(v, str) or not re.fullmatch(r'\d{1,4}(\.\d{1,4}){0,3}', v):
        return None
    t = tuple(int(x) for x in v.split('.'))
    return t + (0,) * (4 - len(t))


def norm(s):
    return re.sub(r'[^a-z0-9]', '', str(s or '').lower())


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def write_json(path, obj, indent=None):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(json.dumps(obj, indent=indent, ensure_ascii=False), encoding='utf-8')
    tmp.replace(path)  # atomic: a crash never leaves half a file


def http_get(url, timeout=30, cap=None):
    """GET, gzip-compressed. cap = max bytes (as sent and unpacked): a bigger answer raises instead of filling memory."""
    req = urllib.request.Request(url, headers={'Accept-Encoding': 'gzip', 'User-Agent': 'curl/8.9.1'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read() if cap is None else r.read(cap + 1)
        if cap is not None and len(body) > cap:
            raise ValueError(f'response over {cap} bytes')
        if r.headers.get('Content-Encoding') == 'gzip':
            if cap is None:
                body = gzip.decompress(body)
            else:  # a tiny gzip can unpack to gigabytes: stop at the cap
                d = zlib.decompressobj(16 + zlib.MAX_WBITS)
                body = d.decompress(body, cap + 1)
                if len(body) > cap or d.unconsumed_tail:
                    raise ValueError(f'unpacked response over {cap} bytes')
    return body


# ---------- game build (from Steam's manifest - Steam's file, never a game file) ----------
def find_appmanifest():
    """Locate GZW's Steam manifest on any Steam library drive."""
    roots = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as k:
            roots.append(Path(winreg.QueryValueEx(k, 'SteamPath')[0]))
    except OSError:
        pass
    roots.append(Path(r'C:\Program Files (x86)\Steam'))
    libs = []
    for r in roots:
        libs.append(r)
        try:
            vdf = (r / 'steamapps' / 'libraryfolders.vdf').read_text(encoding='utf-8', errors='ignore')
            libs += [Path(p.replace('\\\\', '\\')) for p in re.findall(r'"path"\s+"([^"]+)"', vdf)]
        except OSError:
            pass
    for lib in libs:
        f = lib / 'steamapps' / f'appmanifest_{GZW_APPID}.acf'
        if f.exists():
            return f
    return None


def steam_build_id(manifest=None):
    """Installed game build - tags finds and data so loot patches don't silently mix."""
    try:
        path = Path(manifest) if manifest else find_appmanifest()
        m = re.search(r'"buildid"\s+"(\d+)"', path.read_text(encoding='utf-8', errors='ignore'))
        return m.group(1) if m else None
    except (OSError, AttributeError, TypeError):
        return None


# ---------- build ----------
def tac_link(m):
    return f"{SITE}/maps/lamang#m=17.00/{m['lng']:.2f}/{m['lat']:.2f}"


def poi_link(c):
    """Area-level link (zoomed out on the area's centre). Only a link: it opens TAC map in the browser."""
    return f"{SITE}/maps/lamang#m=15.50/{c['lng']:.2f}/{c['lat']:.2f}"


def _dist(a, b):
    return math.hypot(a['lat'] - b['lat'], a['lng'] - b['lng'])


def nearest_place(m, places):
    same = [pl for pl in places if pl['poi'] == (m.get('poi') or None)]
    pool = same or places
    best, bd = None, math.inf
    for pl in pool:
        d = _dist(m, pl)
        if d < bd:
            best, bd = pl, d
    return best, int(math.floor(bd + 0.5)) if best else None  # JS Math.round semantics


def independence_key(url):
    """Two claims only 'confirm' each other if they come from different origins."""
    host = re.sub(r'^www\.', '', (re.match(r'https?://([^/]+)', url or '') or [None, ''])[1].lower())
    if host in ('steamcommunity.com', 'youtube.com', 'youtu.be', 'reddit.com'):
        return re.sub(r'[?#].*$', '', url).rstrip('/').lower()  # each thread / video = a different person
    return 'fandom' if host.endswith('fandom.com') else host


def canonicalize_sources(seeds):
    """One key per URL across all seeds; returns ({key: source}, {(prefix, old_key): key})."""
    by_url, sources, remap = {}, {}, {}
    for seed, prefix in seeds:
        for k, v in (seed.get('sources') or {}).items():
            url = (v.get('url') or '').rstrip('/')
            canon = by_url.setdefault(url.lower(), prefix + k) if url else prefix + k
            remap[(prefix, k)] = canon
            if canon not in sources:
                sources[canon] = {**v, 'indep': independence_key(url)}
    return sources, remap


def infer_poi(text, places):
    """Which TAC area (and spots) does a named claim point at? Returns (poi or None, [place ids])."""
    poi = next((p for rx, p in POI_ALIASES if rx.search(text)), None)
    low = text.lower()
    named = [pl for pl in places if len(pl['name']) > 4 and not GENERIC_PLACE.match(pl['name'])
             and re.search(r'\b' + re.escape(pl['name'].lower()) + r'\b', low)]
    if poi:
        named = [pl for pl in named if pl['poi'] == poi]
    elif named and len({pl['poi'] for pl in named}) == 1:
        poi = named[0]['poi']  # a distinctive spot name that exists in only one area
    return poi, [pl['id'] for pl in named]


def apply_verification(research):
    """Fold in data/seed/verification.json (an independent fact-check of sampled claims).
    'unsupported' claims are removed; the rest carry their verdict so the app can show a check mark."""
    ver = read_json(SEED_DIR / 'verification.json')
    if not ver:
        return {}
    verdicts = {r['id']: r for r in ver.get('results', [])}
    removed = 0
    for m in research.get('materials') or []:
        kept = []
        for i, h in enumerate(m.get('location_hints') or []):
            v = verdicts.get(f"hint:{m['name']}:{i}")
            if v:
                if v['verdict'] == 'unsupported':
                    removed += 1
                    continue
                h['verified'], h['vnote'] = v['verdict'], v.get('note', '')
            kept.append(h)
        m['location_hints'] = kept
    for s in research.get('stations') or []:
        v = verdicts.get(f"station:{s['station']}:{s.get('level')}")
        if v:
            s['verified'], s['vnote'] = v['verdict'], v.get('note', '')
    crates = []
    for i, c in enumerate(research.get('crate_contents') or []):
        v = verdicts.get(f'crate:{i}')
        if v and v['verdict'] == 'unsupported':
            removed += 1
            continue
        if v:
            c['verified'], c['vnote'] = v['verdict'], v.get('note', '')
        crates.append(c)
    research['crate_contents'] = crates
    return {'checked': ver.get('checked'), 'summary': ver.get('summary', {}), 'removed': removed}


LAUNCH_DATE = '2026-10-22'  # 0.5 full release; Supporter Preview posts (Oct 19-21) predate loot fixes
# Sources that copy another source count as that source (tposegaming's guide repeats Abozahra01's Oct 21 video).
INDEP_ALIASES = {
    'guide:alex march (tposegaming.com)': 'video:abozahra01',
    'guide:torin hall (tposegaming.com)': 'video:abozahra01',
}


def add_player_reports(materials, mat_by_norm, sources):
    """Fold data/seed/reports.json (one entry per player per material per spot) into location hints.
    Each hint carries its own 'indep' key = the player, so 3 people in one thread count as 3,
    and one person repeating themselves counts once. Pages harvested post-by-post replace the
    research's per-page claims (otherwise the same posts would be counted twice)."""
    reports = []  # reports.json (Steam/forums), reports_reddit.json (Reddit), ... - all one format
    for f in sorted(SEED_DIR.glob('reports*.json')):
        reports += (read_json(f) or {}).get('reports') or []
    if not reports:
        return {}
    clean = lambda u: (u or '').strip().rstrip('/').lower()
    harvested = {clean(r.get('url')) for r in reports if r.get('url')}
    for m in materials:
        m['location_hints'] = [h for h in m['location_hints']
                               if not (h.get('kind') == 'named' and clean((sources.get(h.get('src')) or {}).get('url')) in harvested)]
    url_key = {clean(v.get('url')): k for k, v in sources.items() if v.get('url')}
    used, skipped, authors = 0, 0, set()
    for i, r in enumerate(reports):
        m = mat_by_norm.get(norm(r.get('material')))
        if not m or not r.get('url'):
            skipped += 1
            continue
        u = clean(r['url'])
        key = url_key.get(u)
        if not key:
            key = url_key[u] = f'P:{len(url_key)}'
            sources[key] = {'url': r['url'], 'title': f"{r.get('platform') or 'post'}{' - ' + r['author'] if r.get('author') else ''}",
                            'kind': r.get('platform'), 'published': r.get('date'), 'indep': independence_key(r['url']),
                            'reliability': {'reddit-snippet': 'Reddit search snippet only',
                                            'ai-summary': 'AI summary (Gemini), unverified - its own sources are not visible',
                                            'guide': 'one guide author'}.get(r.get('platform'), 'player report')}
        author = (r.get('author') or '').strip()
        indep = f"{r.get('platform') or 'web'}:{author.lower()}" if author else sources[key]['indep']
        indep = INDEP_ALIASES.get(indep, indep)
        authors.add(indep)
        rtype = r.get('type') if r.get('type') in ('found', 'advice', 'negative') else 'found'
        preview = bool(r.get('date')) and r['date'] < LAUNCH_DATE
        info = {'type': rtype, 'author': author or None, 'platform': r.get('platform'), 'date': r.get('date'),
                'count': r.get('count'), 'quote': r.get('quote'), 'preview': preview}
        if rtype == 'negative':
            m.setdefault('negatives', []).append({'poi': r.get('poi'), 'spot': r.get('spot'), 'src': key, 'indep': indep, 'report': info})
        else:
            spot = (r.get('spot') or '').strip()
            m['location_hints'].append({'text': spot or (r.get('poi') or 'somewhere'), 'kind': 'named' if r.get('poi') else 'type',
                                        'poi': r.get('poi'), 'src': key, 'indep': indep, 'report': info})
        used += 1
    return {'reports': used, 'skipped': skipped, 'players': len(authors)}


# ---------- finds shared by maintainers (contrib/<name>.json in the public repo, see publish.py) ----------
CONTRIB_NAME = re.compile(r'^[A-Za-z0-9_.-]{1,40}$')


def clean_contrib(doc):
    """One contrib file -> sorted list of {material, poi, date[, place]}, or None if it isn't one. Nothing else survives."""
    if not isinstance(doc, dict) or not isinstance(doc.get('finds'), list) or len(doc['finds']) > 5000:
        return None
    out = []
    for f in doc['finds']:
        if not isinstance(f, dict):
            continue
        mat, poi, date, place = f.get('material'), f.get('poi'), f.get('date'), f.get('place')
        if not all(isinstance(x, str) and 0 < len(x) <= 60 for x in (mat, poi, date)) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
            continue
        rec = {'material': mat, 'poi': poi, 'date': date}
        if type(place) is int:  # a TAC spot id: only uploaded when tac_permission is on
            rec['place'] = place
        out.append(rec)
    return sorted(out, key=lambda r: (r['date'], r['material'], r['poi'], r.get('place', -1)))


def load_contrib_cache():
    """Other maintainers' finds as the publisher last downloaded them: ({name: finds}, repo)."""
    idx = read_json(CONTRIB_CACHE / 'index.json', {}) or {}
    out = {}
    for name in sorted(idx.get('sha') or {}):
        finds = clean_contrib({'finds': read_json(CONTRIB_CACHE / f'{name}.json')}) if CONTRIB_NAME.match(name) else None
        if finds:
            out[name] = finds
    return out, idx.get('repo')


def add_contrib_finds(materials, mat_by_norm, sources, contribs, repo, place_by_id):
    """Finds logged in game with the finder (Ctrl+L) by maintainers: player reports where each maintainer is ONE
    independent origin, however many finds they log. Same material + area + spot collapses into one hint with a count."""
    used, people = 0, 0
    for name in sorted(contribs):
        if not CONTRIB_NAME.match(name):
            continue
        key, who = f'P:finder-{name}', f'finder:{name.lower()}'
        hints = {}
        for f in contribs[name]:
            m = mat_by_norm.get(norm(f['material']))
            if not m:
                continue
            spot = place_by_id.get(f.get('place'))
            h = hints.get((id(m), f['poi'], spot and spot['id']))
            if h:
                h['report']['count'] += 1
                h['report']['date'] = max(h['report']['date'], f['date'])
                continue
            h = hints[(id(m), f['poi'], spot and spot['id'])] = {
                'text': spot['name'] if spot else f['poi'], 'kind': 'named', 'poi': f['poi'], 'src': key, 'indep': who,
                'report': {'type': 'found', 'author': name, 'platform': 'finder', 'date': f['date'], 'count': 1,
                           'quote': None, 'preview': f['date'] < LAUNCH_DATE}}
            if spot:
                h['place_id'] = spot['id']
            m['location_hints'].append(h)
            used += 1
        if hints:
            people += 1
            sources[key] = {'url': f'https://github.com/{repo}/blob/HEAD/contrib/{name}.json' if repo else '',
                            'title': f'{name} - logged in game with GZW Material Finder', 'kind': 'finder', 'published': None,
                            'indep': who, 'reliability': 'first-hand find, logged in game (Ctrl+L)'}
    return {'hints': used, 'contributors': people}


def merge_materials(seeds, remap=None):
    remap = remap or {}
    by_key, alias_index = {}, {}
    for seed, prefix in seeds:
        for m in seed.get('materials') or []:
            k = alias_index.get(norm(m['name'])) or norm(m['name'])
            for a in m.get('aliases') or []:
                if norm(a) in alias_index and k not in by_key:
                    k = alias_index[norm(a)]
            cur = by_key.get(k) or {'name': m['name'], 'category': m.get('category') or 'other', 'aliases': [],
                                    'used_in': [], 'location_hints': [], 'dismantle_from': [], 'notes': []}
            if m.get('category') and cur['category'] == 'other':
                cur['category'] = m['category']
            for a in [m['name'], *(m.get('aliases') or [])]:
                if norm(a) != norm(cur['name']) and not any(norm(x) == norm(a) for x in cur['aliases']):
                    cur['aliases'].append(a)
            src = lambda o: remap.get((prefix, o['src']), prefix + o['src']) if o.get('src') else o.get('src')
            for u in m.get('used_in') or []:
                cur['used_in'].append({**u, 'src': src(u)})
            for h in m.get('location_hints') or []:
                s = src(h)
                if not any(norm(x['text']) == norm(h['text']) and x.get('src') == s for x in cur['location_hints']):
                    cur['location_hints'].append({**h, 'src': s})
            for d in m.get('dismantle_from') or []:
                cur['dismantle_from'].append({**d, 'src': src(d)})
            if m.get('notes'):
                cur['notes'].append(m['notes'])
            by_key[k] = cur
            for a in [m['name'], *(m.get('aliases') or [])]:
                alias_index[norm(a)] = k
    return list(by_key.values())


def build_devices(dis_seed, materials, mat_by_norm, disassembly, rsrc, tac_srcs, unknown):
    """Devices you dismantle in the Electronics Workshop -> [{id, name, aliases, category, time, notes, src, parts, hints}].
    Parts come from data/seed/disassembly_seed.json plus every material's dismantle_from (both ways, so a part card and a
    device card always agree). hints = where the research / players found the device (its parts' named location hints).
    TAC map markers (disassembly, maintainer data only) get their device id here; a TAC device the research doesn't know
    yet is listed with no parts. Sorted by name."""
    by_norm, devices = {}, []

    def device(name, aliases=(), **kw):
        dev = next((by_norm[norm(a)] for a in [name, *aliases] if norm(a) in by_norm), None)
        if not dev:
            dev = {'id': norm(name), 'name': name, 'aliases': [], 'category': None, 'time': None, 'notes': '', 'src': None,
                   'parts': [], 'hints': []}
            devices.append(dev)
        for k, v in kw.items():
            if v and not dev.get(k):
                dev[k] = v
        for a in [name, *aliases]:
            if norm(a) != norm(dev['name']) and not any(norm(x) == norm(a) for x in dev['aliases']):
                dev['aliases'].append(a)
            by_norm[norm(a)] = dev
        return dev

    def add_part(dev, m, qty, src):
        if not any(p['material'] == m['name'] for p in dev['parts']):
            dev['parts'].append({'material': m['name'], 'qty': qty, 'src': src})

    for dv in (dis_seed or {}).get('devices') or []:
        src = rsrc('X:', dv.get('src'))
        if src in tac_srcs:
            continue
        dev = device(dv['name'], dv.get('aliases') or [], category=dv.get('category'), time=dv.get('time'), notes=dv.get('notes'), src=src)
        for h in dv.get('location_hints') or []:
            if rsrc('X:', h.get('src')) not in tac_srcs:
                dev['hints'].append({'text': h['text'], 'poi': h.get('poi'), 'src': rsrc('X:', h.get('src')), 'author': None})
        for p in dv.get('parts') or []:
            m = mat_by_norm.get(norm(p['material']))
            psrc = rsrc('X:', p.get('src')) or src
            if not m:
                unknown.append(f"dismantle {dv['name']}: {p['material']}")
                continue
            add_part(dev, m, p.get('qty'), psrc)
            if not any(norm(d['item']) in {norm(a) for a in [dev['name'], *dev['aliases']]} for d in m['dismantle_from']):
                m['dismantle_from'].append({'item': dev['name'], 'src': psrc})
    for m in materials:  # parts the research names that the disassembly seed doesn't list (yet)
        for d in m['dismantle_from']:
            add_part(device(d['item']), m, None, d.get('src'))
    for dev in devices:  # where the device itself was seen = its parts' named hints (tposegaming lists device spots)
        names = {norm(a) for a in [dev['name'], *dev['aliases']]}
        seen = {(h['text'], h['src']) for h in dev['hints']}
        for p in dev['parts']:
            m = mat_by_norm[norm(p['material'])]
            if not all(norm(d['item']) in names for d in m['dismantle_from']):
                continue  # a part with several devices: its hints can't say which device was found
            for h in m['location_hints']:
                if h.get('kind') == 'named' and h.get('poi') and (h['text'], h.get('src')) not in seen:
                    seen.add((h['text'], h.get('src')))
                    dev['hints'].append({'text': h['text'], 'poi': h['poi'], 'src': h.get('src'),
                                         'author': (h.get('report') or {}).get('author')})
    for x in disassembly:  # TAC map spots (maintainer data only; empty in the public build)
        x['device'] = device(x['name'])['id']
    return sorted(devices, key=lambda d: d['name'].lower())


def build_data(public=False, tac=True, contribs=None, repo=None):
    """Merge data/tacmap/markers.json + data/seed/*.json (+ maintainers' shared finds) into the app's data.
    public=True: the shareable build - nothing machine-specific and no timestamps, so the same inputs always give
    the same bytes. tac=False leaves out everything taken from TAC map (spots, crate counts, disassembly markers,
    spot links, claims citing TAC map); area links then come from the frozen data/seed/poi_centres.json.
    Returns (data, summary string)."""
    places, crates, disassembly, pois, tac_doc = [], [], [], [], None
    centres = {c['name']: c for c in (read_json(POI_CENTRES) or {}).get('pois') or []}
    if tac:
        tac_doc = read_json(TAC_DIR / 'markers.json')
        if not tac_doc:
            raise FileNotFoundError('data/tacmap/markers.json missing - run a sync first')
        g = tac_doc['groups']

        places = [{'id': m['id'], 'name': re.sub(r'\s+\d+$', '', m['name'] or ''), 'poi': m['poi'] or '?', 'lat': m['lat'], 'lng': m['lng'],
                   'link': tac_link(m), 'crates': {'t2': 0, 't3': 0}, 'toolBoxes': 0} for m in g['subPoi']]
        poi_tier = {(m['poi'] or m['name']): m['tier'] for m in g['primaryPoi']}
        pois = [{'name': n, 'tier': poi_tier.get(n)} for n in sorted({pl['poi'] for pl in places})]

        for m in g['crate']:
            pl, d = nearest_place(m, places)
            tier = m.get('tier')
            if pl and tier in (2, 3):
                pl['crates'][f't{tier}'] += 1
            crates.append({'id': m['id'], 'tier': tier, 'poi': m['poi'] or '?', 'place': pl['id'] if pl else None, 'd': d, 'link': tac_link(m)})
        for m in g['toolBox']:
            pl, d = nearest_place(m, places)
            if pl and d < 250:
                pl['toolBoxes'] += 1
        for m in g['disassembly']:  # every disassembly layer (tacmap.py merges them); the device link is set below
            pl, d = nearest_place(m, places)
            disassembly.append({'id': m['id'], 'name': m['name'] or '?', 'poi': m['poi'] or '?', 'place': pl['id'] if pl else None, 'd': d,
                                'floor': m['floor'] if type(m.get('floor')) is int else None, 'link': tac_link(m)})
    else:
        pois = [{'name': n} for n in sorted(centres)]
    for p in pois:
        if p['name'] in centres:
            p['link'] = poi_link(centres[p['name']])

    seeds = [(read_json(SEED_DIR / 'starter_seed.json'), '')]
    research_file = SEED_DIR / 'research_seed.json'
    research = read_json(research_file)
    verification = apply_verification(research) if research else {}
    if research:
        seeds.append((research, 'R:'))
    # The research covers every station at every level, so it replaces the level-1-only starter recipes.
    station_seeds = seeds[-1:] if research and research.get('stations') else seeds
    # crafting recipes (data/seed/crafting_seed.json): new ingredients become materials, known ones get craft uses
    upgrade_names = {norm(a) for seed, _ in seeds for m in seed.get('materials') or [] for a in [m['name'], *(m.get('aliases') or [])]}
    crafting = read_json(SEED_DIR / 'crafting_seed.json')
    if crafting:
        seeds.append((crafting, 'C:'))
    # disassembly (data/seed/disassembly_seed.json): devices you take apart and the part each one gives
    dis_seed = read_json(SEED_DIR / 'disassembly_seed.json')
    if dis_seed:
        seeds.append((dis_seed, 'X:'))

    sources, remap = canonicalize_sources(seeds)
    # claims whose source IS TAC map (e.g. its crate layer's search aliases) are TAC data too
    tac_srcs = set() if tac else {k for k, v in sources.items() if 'gzwtacmap.com' in (v.get('url') or '')}
    for k in tac_srcs:
        sources.pop(k)

    def rsrc(prefix, s):
        return remap.get((prefix, s), prefix + s) if s else s

    stations = []
    for seed, prefix in station_seeds:
        for s in seed.get('stations') or []:
            lvl = s.get('level') or 1
            dup = next((x for x in stations if norm(x['station']) == norm(s['station']) and (x.get('level') or 1) == lvl), None)
            entry = {**s, 'src': rsrc(prefix, s.get('src')), 'corroborated_by': [rsrc(prefix, c) for c in s.get('corroborated_by') or []],
                     'conflicts': [{**c, 'src': rsrc(prefix, c.get('src'))} if isinstance(c, dict) else c for c in s.get('conflicts') or []]}
            if dup:
                if len(s.get('cost') or []) > len(dup.get('cost') or []):
                    dup.update(entry)
                continue
            stations.append(entry)

    materials = merge_materials(seeds, remap)
    mat_by_norm = {}
    for m in materials:
        for a in [m['name'], *m['aliases']]:
            mat_by_norm[norm(a)] = m
        for key in ('location_hints', 'used_in', 'dismantle_from'):
            m[key] = [x for x in m[key] if x.get('src') not in tac_srcs]
    report_stats = add_player_reports(materials, mat_by_norm, sources)
    place_by_id = {pl['id']: pl for pl in places}
    contrib_stats = add_contrib_finds(materials, mat_by_norm, sources, contribs or {}, repo, place_by_id)
    unknown = []
    for s in stations:
        for c in s.get('cost') or []:
            m = mat_by_norm.get(norm(c['material']))
            if not m:
                unknown.append(f"{s['station']}: {c['material']}")
                continue
            lvl = s.get('level') or 1
            # material-level used_in from the research may already list it (with the device name)
            if not any(norm(u['station']) == norm(s['station']) and (u.get('level') or 1) == lvl for u in m['used_in']):
                m['used_in'].append({'room': s.get('room'), 'station': s['station'], 'level': lvl, 'device': s.get('device'),
                                     'qty': c.get('qty'), 'src': s.get('src')})
    station_keys = {(norm(s['station']), s.get('level') or 1) for s in stations}
    for m in materials:  # drop used_in rows pointing at stations we no longer list (old starter names)
        m['used_in'] = [u for u in m['used_in'] if (norm(u['station']), u.get('level') or 1) in station_keys]
    recipes = []
    for r in (crafting or {}).get('recipes') or []:
        src = rsrc('C:', r.get('src'))
        rec = {'id': norm(r['output']), 'output': r['output'], 'category': r.get('category'), 'room': r.get('room'),
               'time': r.get('time'), 'requires': [{'cap': q['cap'], 'n': q.get('n')} for q in r.get('requires') or []],
               'ingredients': [{'material': c['material'], 'qty': c.get('qty')} for c in r.get('ingredients') or []],
               'notes': r.get('notes') or '', 'src': src}
        if src in tac_srcs or any(x['id'] == rec['id'] for x in recipes):
            continue
        recipes.append(rec)
        for c in rec['ingredients']:
            m = mat_by_norm.get(norm(c['material']))
            if not m:
                unknown.append(f"craft {rec['output']}: {c['material']}")
                continue
            # shown in "Used in" next to the upgrades: station = the room, device = what you craft
            m['used_in'].append({'room': rec['room'], 'station': rec['room'] or 'Hideout', 'level': None,
                                 'device': 'craft: ' + rec['output'], 'qty': c['qty'], 'src': src, 'craft': rec['id']})
    for m in materials:  # crafting-only ingredients stay out of the hideout Finder/Checklist
        m['craft_only'] = not any(norm(a) in upgrade_names for a in [m['name'], *m['aliases']])

    crate_facts = []
    for seed, prefix in seeds:
        for c in seed.get('crate_contents') or []:
            src = rsrc(prefix, c.get('src'))
            if src in tac_srcs:
                continue
            crate_facts.append({**c, 'src': src})
            for name in c.get('contains') or []:
                m = mat_by_norm.get(norm(name))
                if m:
                    m.setdefault('crate', []).append({'tier': c.get('tier'), 'src': src})
    openings = [c for c in crate_facts if c.get('src') and 'union' not in (c.get('notes') or '').lower()]
    for m in materials:  # "seen in N of M reported crate openings" - a real frequency, not a guess
        m['crate_seen'] = sum(1 for c in openings if any(norm(x) == norm(m['name']) for x in c.get('contains') or []))

    for m in materials:
        for h in m['location_hints']:
            rules = [r for r in TYPE_RULES if r[1].search(h['text'])]
            crules = [r for r in CONTAINER_RULES if r[1].search(h['text'])]
            h['types'] = [r[0] for r in rules] + [r[0] for r in crules]
            typed = [pl for pl in places if any(r[2].search(pl['name']) for r in rules) or any(r[2](pl) for r in crules)]
            exact = place_by_id.get(h.pop('place_id', None))  # a logged find knows its exact spot
            if exact:
                matched = [exact]
            elif h.get('kind') == 'named':
                poi, spot_ids = infer_poi(h['text'], places)
                if not h.get('poi') and poi:
                    h['poi'], h['poi_inferred'] = poi, True
                spots = [place_by_id[i] for i in spot_ids if not h.get('poi') or place_by_id[i]['poi'] == h['poi']]
                in_poi = [pl for pl in typed if pl['poi'] == h['poi']] if h.get('poi') else []
                seen, matched = set(), []
                for pl in spots + in_poi:
                    if pl['id'] not in seen:
                        seen.add(pl['id'])
                        matched.append(pl)
            elif h.get('poi'):
                matched = [pl for pl in typed if pl['poi'] == h['poi']] or typed
            else:
                matched = typed
            h['places'] = [pl['id'] for pl in matched]
        m['id'] = norm(m['name'])
        m['notes'] = ' '.join(m['notes'])
    devices = build_devices(dis_seed, materials, mat_by_norm, disassembly, rsrc, tac_srcs, unknown)

    general_tips = [{'text': t['text'], 'src': rsrc(prefix, t.get('src'))} for seed, prefix in seeds
                    for key in ('general_tips', 'general_location_hints', 'crate_notes') for t in seed.get(key) or []
                    if t.get('text') and rsrc(prefix, t.get('src')) not in tac_srcs]
    if public:  # same inputs -> same bytes: no build time, no local game build, no local sync state
        meta = {'research': bool(research), 'crate_reports': len(openings), 'verification': verification,
                'player_reports': report_stats, 'contrib': contrib_stats}
        if tac:
            meta['tacmap_fetched'] = tac_doc.get('fetched')
    else:
        state = read_json(STATE, {}) or {}
        meta = {'built': now_iso(), 'tacmap_fetched': (state.get('tac') or {}).get('last_success') or tac_doc.get('fetched'),
                'game_build': steam_build_id(),
                'research': bool(research), 'sources_changed': (state.get('sources') or {}).get('changed', []),
                'crate_reports': len(openings), 'verification': verification, 'player_reports': report_stats, 'contrib': contrib_stats}
    data = {
        'meta': meta,
        'sources': sources, 'pois': pois, 'places': places, 'crates': crates, 'disassembly': disassembly,
        'materials': materials, 'stations': stations, 'recipes': recipes, 'devices': devices, 'crateFacts': crate_facts,
        'general_tips': general_tips,
    }
    no_hint = [m['name'] for m in materials if not m['location_hints'] and not m.get('crate')]
    summary = (f'built{" (public" + (", with TAC" if tac else ", no TAC") + ")" if public else ""}: {len(materials)} materials, '
               f'{len(stations)} stations, {len(recipes)} recipes, {len(devices)} devices, {len(places)} places, {len(crates)} crates, {len(disassembly)} disassembly, '
               f'research={bool(research)}, no-location={len(no_hint)}, shared finds={contrib_stats["hints"]}')
    if unknown:
        summary += f'; unknown station materials: {", ".join(unknown)}'
    return data, summary


def data_js(data):
    return '// generated by shell/datasync.py - do not edit\nwindow.GZW_DATA = ' + json.dumps(data, ensure_ascii=False) + ';\n'


def public_bytes(data):
    """The published file: canonical JSON (sorted keys, no spaces), so equal data hashes equal on every machine."""
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def build():
    """Maintainer data for this PC -> app/data/gzw_data.js (TAC spots + other maintainers' shared finds). Returns a summary."""
    contribs, repo = load_contrib_cache()
    data, summary = build_data(contribs=contribs, repo=repo)
    OUT_JS.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_JS.with_suffix('.tmp')
    tmp.write_text(data_js(data), encoding='utf-8')
    tmp.replace(OUT_JS)
    return summary


def build_public(tac=False, contribs=None, repo=None, out=PUBLIC_JSON):
    """Shareable data -> data/public/gzw_data.json. TAC data only with tac=True (the maintainer's tac_permission).
    Returns (data, bytes, summary)."""
    data, summary = build_data(public=True, tac=tac, contribs=contribs, repo=repo)
    body = public_bytes(data)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(out) + '.tmp')
        tmp.write_bytes(body)
        tmp.replace(out)
    return data, body, summary


# ---------- refresh policy ----------
def _page_fingerprint(body):
    text = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', body.decode('utf-8', 'ignore'), flags=re.S | re.I)
    text = html.unescape(re.sub(r'<[^>]+>', ' ', text))
    return hashlib.sha256(re.sub(r'\s+', ' ', text).strip().encode()).hexdigest()


def check_sources(state, max_urls=15):
    """Weekly: has any page behind the seed research changed? One request each, 2 s apart."""
    srcs, cites = {}, {}
    for name in ('starter_seed.json', 'research_seed.json', 'crafting_seed.json'):
        seed = read_json(SEED_DIR / name) or {}
        prefix = 'R:' if name.startswith('research') else ''
        blob = json.dumps(seed.get('materials', [])) + json.dumps(seed.get('stations', []))
        for k, v in (seed.get('sources') or {}).items():
            url = v.get('url', '')
            if url.startswith('https://') and not any(d in url for d in ('gzwtacmap.com', 'youtube.com', 'fandom.com')):
                srcs[url] = v.get('title') or k
                cites[url] = cites.get(url, 0) + blob.count(f'"src": "{k}"')
    st = state.setdefault('sources', {'hashes': {}, 'changed': []})
    changed = []
    # most-cited pages first; fandom blocks scripts (403) so it's skipped
    for i, url in enumerate(sorted(srcs, key=lambda u: -cites.get(u, 0))[:max_urls]):
        title = srcs[url]
        if i:
            time.sleep(2)
        try:
            fp = _page_fingerprint(http_get(url, timeout=20))
        except Exception as e:
            log(f'source check failed for {url}: {e}')
            continue
        old = st['hashes'].get(url)
        if old and old != fp:
            changed.append({'url': url, 'title': title, 'seen': now_iso()})
        st['hashes'][url] = fp
    keep = {c['url']: c for c in st.get('changed', [])}
    keep.update({c['url']: c for c in changed})
    st['changed'] = list(keep.values())
    st['last_check'] = time.time()
    return changed


def sync(force=False, refresh_hours=24, check_sources_weekly=True):
    """Refresh what's due. Returns (rebuilt: bool, message)."""
    if edition() != 'maintainer':  # the public edition never contacts TAC map or the source pages
        return False, 'public edition: no TAC map or source checks'
    state = read_json(STATE, {}) or {}
    tac = state.setdefault('tac', {})
    now = time.time()
    rebuilt, msgs = False, []
    rebuilt_done = False
    due = force or now - tac.get('last_attempt', 0) >= (refresh_hours * 3600 if not tac.get('failures') else 6 * 3600)
    if due:
        tac['last_attempt'] = now
        try:
            from tacmap import fetch_tacmap  # maintainer-only module (not in the public package)
            slim = fetch_tacmap()
            digest = hashlib.sha256(json.dumps(slim['groups'], sort_keys=True).encode()).hexdigest()
            tac['last_success'] = now_iso()
            tac['failures'] = 0
            if digest != tac.get('sha256') or not (TAC_DIR / 'markers.json').exists():
                write_json(TAC_DIR / 'markers.json', {**slim, 'fetched': tac['last_success']})
                tac['sha256'] = digest
                msgs.append('TAC map changed')
                rebuilt = True
            else:
                msgs.append('TAC map unchanged')
        except Exception as e:
            tac['failures'] = tac.get('failures', 0) + 1
            msgs.append(f'TAC fetch failed ({e}); keeping current data')
        write_json(STATE, state, indent=1)  # saved now, so quitting during the source check loses nothing
        if rebuilt:
            msgs.append(build())
            rebuilt_done = True
    else:
        msgs.append('TAC map checked recently; skipped')
    if check_sources_weekly and (force or now - (state.get('sources') or {}).get('last_check', 0) >= WEEK):
        before = len((state.get('sources') or {}).get('changed', []))
        check_sources(state)
        if len(state['sources']['changed']) != before:
            msgs.append(f"{len(state['sources']['changed'])} research source(s) changed")
            rebuilt, rebuilt_done = True, False
    write_json(STATE, state, indent=1)
    if rebuilt and not rebuilt_done:
        msgs.append(build())
    log('; '.join(msgs))
    return rebuilt, '; '.join(msgs)


if __name__ == '__main__':
    if '--build-public' in sys.argv:
        _, body, summary = build_public()
        print(f'{summary}\n-> {PUBLIC_JSON}  ({len(body)} bytes, sha256 {hashlib.sha256(body).hexdigest()})')
    elif '--build' in sys.argv:
        print(build())
    else:
        print(sync(force='--force' in sys.argv)[1])
