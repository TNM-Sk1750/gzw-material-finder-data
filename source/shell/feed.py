"""Data feed client (both editions): newer shared data from the public GitHub repo, as plain JSON.

At startup and at most every 6 h: GET {feed_base}/version.json (tiny). If its data_version is newer than the copy
we have, GET {feed_base}/gzw_data.json, check its sha256, size (5 MB cap) and shape, and save it atomically to
data/feed/. Downloaded data is only ever parsed as JSON and handed to the page as an object (Api.get_data):
it is never run as script. Failures are silent (logged to data/finder.log) and back off; the bundled data
always keeps working. No feed_base in shell/feed.json = feed off, nothing is fetched.
"""
import hashlib
import json
import re
import time
from urllib.parse import urlparse

import datasync
from datasync import APP_VERSION, read_json, version_tuple, write_json

CONFIG = datasync.ROOT / 'shell' / 'feed.json'
FEED_DIR = datasync.DATA / 'feed'
DATA_FILE = FEED_DIR / 'gzw_data.json'
VERSION_FILE = FEED_DIR / 'version.json'   # the version.json that DATA_FILE was verified against
STATE = FEED_DIR / 'state.json'
MAX_DATA = 5 * 1024 * 1024
MAX_VERSION = 16 * 1024
CHECK_EVERY = 6 * 3600
TIMEOUT = 20


def log(msg):
    datasync.log(msg, 'feed')


def valid_base(url):
    """https only - plain http is allowed for a local test server and nothing else."""
    if not isinstance(url, str) or len(url) > 300:
        return None
    u = urlparse(url)
    local = u.scheme == 'http' and u.hostname in ('127.0.0.1', 'localhost')
    return url.rstrip('/') if (u.scheme == 'https' or local) and u.hostname else None


def https_url(url):
    return url if isinstance(url, str) and url.startswith('https://') and len(url) <= 300 else None


def load_config(override=None):
    """shell/feed.json: {"feed_base": null, "release_page": null}. override = --feed-base (testing)."""
    cfg = read_json(CONFIG, {}) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    base = override if override is not None else cfg.get('feed_base')
    release = https_url(cfg.get('release_page'))
    repo = re.fullmatch(r'https://github\.com/([A-Za-z0-9-]+/[A-Za-z0-9_.-]+)/releases', release or '')
    return {'feed_base': valid_base(base), 'release_page': release,
            'share_url': f'https://github.com/{repo.group(1)}/issues/new' if repo else None}  # "Share my finds"


# ---------- shape checks: the page trusts this data, so anything unexpected is rejected ----------
DATA_KEYS = {'meta': dict, 'sources': dict, 'pois': list, 'places': list, 'crates': list, 'disassembly': list,
             'materials': list, 'stations': list, 'recipes': list, 'devices': list, 'crateFacts': list, 'general_tips': list}


def parse_version(body):
    v = json.loads(body.decode('utf-8'))
    if not isinstance(v, dict):
        raise ValueError('version.json is not an object')
    if type(v.get('data_version')) is not int or v['data_version'] < 1:
        raise ValueError('version.json: bad data_version')
    if not isinstance(v.get('data_sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', v['data_sha256']):
        raise ValueError('version.json: bad data_sha256')
    if version_tuple(v.get('built_by_app')) is None:
        raise ValueError('version.json: bad built_by_app')
    return {'data_version': v['data_version'], 'data_sha256': v['data_sha256'], 'built_by_app': v['built_by_app'],
            'built': v['built'] if isinstance(v.get('built'), str) else None,
            'app_latest': v['app_latest'] if version_tuple(v.get('app_latest')) else None,
            'release_page': https_url(v.get('release_page'))}


def validate_data(d):
    """Exact top-level keys and types, plus every field app.js dereferences. Raises ValueError(reason)."""
    def need(ok, what):
        if not ok:
            raise ValueError(f'gzw_data.json: bad {what}')

    def dicts(x):
        return isinstance(x, list) and all(isinstance(i, dict) for i in x)

    def strs(x):
        return isinstance(x, list) and all(isinstance(i, str) for i in x)

    def opt(x, t):
        return x is None or isinstance(x, t)

    num = (int, float)
    need(isinstance(d, dict) and set(d) == set(DATA_KEYS), 'top-level keys')
    for k, t in DATA_KEYS.items():
        need(isinstance(d[k], t), k)
    for k in DATA_KEYS:
        if DATA_KEYS[k] is list:
            need(dicts(d[k]) and len(d[k]) <= 20000, k)
    meta = d['meta']
    need(opt(meta.get('crate_reports'), int) and opt(meta.get('tacmap_fetched'), str) and opt(meta.get('game_build'), str)
         and opt(meta.get('verification'), dict) and (meta.get('sources_changed') is None or dicts(meta['sources_changed'])), 'meta')
    summ = (meta.get('verification') or {}).get('summary')
    need(summ is None or (isinstance(summ, dict) and all(isinstance(x, int) for x in summ.values())), 'meta.verification')
    need(all(isinstance(s, dict) and opt(s.get('url'), str) and opt(s.get('title'), str) and opt(s.get('kind'), str)
             and opt(s.get('reliability'), str) for s in d['sources'].values()), 'sources')
    need(len(d['materials']) > 0, 'materials (empty)')
    for m in d['materials']:
        need(all(isinstance(m.get(k), str) for k in ('id', 'name', 'category')) and strs(m.get('aliases'))
             and dicts(m.get('used_in')) and dicts(m.get('location_hints')) and opt(m.get('notes'), str)
             and all(m.get(k) is None or dicts(m[k]) for k in ('dismantle_from', 'negatives', 'crate'))
             and opt(m.get('crate_seen'), int) and opt(m.get('craft_only'), bool), 'material')
        for h in m['location_hints']:
            need(isinstance(h.get('text'), str) and opt(h.get('poi'), str) and opt(h.get('src'), str)
                 and (h.get('places') is None or isinstance(h['places'], list)) and opt(h.get('report'), dict), 'location hint')
            r = h.get('report')
            need(r is None or (isinstance(r.get('type'), str) and opt(r.get('author'), str)), 'player report')
        for u in m['used_in']:
            need(isinstance(u.get('station'), str) and opt(u.get('device'), str) and opt(u.get('level'), int)
                 and opt(u.get('qty'), num) and opt(u.get('craft'), str), 'used_in')
        for d_ in m.get('dismantle_from') or []:
            need(isinstance(d_.get('item'), str), 'dismantle_from')
        for n in m.get('negatives') or []:
            need(isinstance(n.get('report'), dict) and opt(n.get('poi'), str) and opt(n.get('spot'), str), 'negative report')
    for s in d['stations']:
        need(isinstance(s.get('station'), str) and opt(s.get('room'), str) and opt(s.get('device'), str) and opt(s.get('level'), int)
             and (s.get('conflicts') is None or isinstance(s['conflicts'], list))
             and (s.get('cost') is None or (dicts(s['cost']) and all(isinstance(c.get('material'), str) and opt(c.get('qty'), num)
                                                                     for c in s['cost']))), 'station')
    for r in d['recipes']:
        need(all(isinstance(r.get(k), str) for k in ('id', 'output')) and all(opt(r.get(k), str) for k in ('category', 'room', 'time', 'notes', 'src'))
             and dicts(r.get('requires')) and all(isinstance(q.get('cap'), str) and opt(q.get('n'), int) for q in r['requires'])
             and dicts(r.get('ingredients')) and all(isinstance(c.get('material'), str) and opt(c.get('qty'), num) for c in r['ingredients']),
             'recipe')
    for p in d['pois']:
        need(isinstance(p.get('name'), str) and opt(p.get('link'), str) and opt(p.get('tier'), int), 'poi')
    for p in d['places']:
        need(isinstance(p.get('id'), int) and isinstance(p.get('name'), str) and isinstance(p.get('poi'), str)
             and isinstance(p.get('lat'), num) and isinstance(p.get('lng'), num) and isinstance(p.get('link'), str)
             and isinstance(p.get('crates'), dict) and all(isinstance(p['crates'].get(t), int) for t in ('t2', 't3')), 'place')
    for x in d['disassembly']:
        need(isinstance(x.get('name'), str) and isinstance(x.get('poi'), str) and isinstance(x.get('link'), str)
             and opt(x.get('device'), str) and opt(x.get('floor'), int) and opt(x.get('place'), int), 'disassembly')
    for v in d['devices']:
        need(all(isinstance(v.get(k), str) for k in ('id', 'name')) and strs(v.get('aliases'))
             and all(opt(v.get(k), str) for k in ('category', 'time', 'notes', 'src'))
             and dicts(v.get('parts')) and all(isinstance(p.get('material'), str) and opt(p.get('qty'), num) and opt(p.get('src'), str)
                                              for p in v['parts'])
             and dicts(v.get('hints')) and all(isinstance(h.get('text'), str) and all(opt(h.get(k), str) for k in ('poi', 'src', 'author'))
                                              for h in v['hints']), 'device')
    for t in d['general_tips']:
        need(isinstance(t.get('text'), str), 'general tip')


# ---------- what we have ----------
def local_feed():
    """(data, version) of the saved feed if it's still intact (sha + shape re-checked), else (None, None)."""
    try:
        ver = parse_version(VERSION_FILE.read_bytes())
        body = DATA_FILE.read_bytes()
        if len(body) > MAX_DATA or hashlib.sha256(body).hexdigest() != ver['data_sha256']:
            return None, None
        data = json.loads(body.decode('utf-8'))
        validate_data(data)
        return data, ver
    except (OSError, ValueError):
        return None, None


def newer_than_bundled(ver):
    """Bundled data counts as (APP_VERSION, data_version 0): a feed built by an older app has older seed research."""
    return version_tuple(ver['built_by_app']) >= version_tuple(APP_VERSION)


def pick(ed):
    """Which data the page should show: ('feed', data, version) or ('bundled', None, None).
    The maintainer edition keeps its own build (TAC spots, rebuilt locally) unless the feed carries TAC spots too."""
    data, ver = local_feed()
    if data is None or not newer_than_bundled(ver):
        return 'bundled', None, None
    if ed == 'maintainer' and not data['places']:
        return 'bundled', None, None
    return 'feed', data, ver


def update_info():
    """{'latest', 'url'} when the feed says a newer app is out (and gave an https download page), else None."""
    latest = (read_json(STATE, {}) or {}).get('latest') or {}
    lv, url = version_tuple(latest.get('app_latest')), https_url(latest.get('release_page'))
    return {'latest': latest['app_latest'], 'url': url} if lv and url and lv > version_tuple(APP_VERSION) else None


# ---------- the check ----------
def check(feed_base, startup=False, now=None):
    """One feed check, if due. Returns 'off' | 'wait' | 'current' | 'updated' | 'failed'. Never raises."""
    if not feed_base:
        return 'off'
    now = now or time.time()
    st = read_json(STATE, {}) or {}
    if now < st.get('backoff_until', 0) or (not startup and now - st.get('last_attempt', 0) < CHECK_EVERY):
        return 'wait'
    st['last_attempt'] = now
    try:
        ver = parse_version(datasync.http_get(f'{feed_base}/version.json', timeout=TIMEOUT, cap=MAX_VERSION))
        st['latest'] = {'app_latest': ver['app_latest'], 'release_page': ver['release_page']}
        _, have = local_feed()
        if have and have['data_version'] >= ver['data_version']:
            result = 'current'
        else:
            # ?v= so a CDN copy of an older file isn't served for the new version
            body = datasync.http_get(f"{feed_base}/gzw_data.json?v={ver['data_version']}", timeout=TIMEOUT, cap=MAX_DATA)
            if hashlib.sha256(body).hexdigest() != ver['data_sha256']:
                raise ValueError('gzw_data.json sha256 does not match version.json')
            validate_data(json.loads(body.decode('utf-8')))
            FEED_DIR.mkdir(parents=True, exist_ok=True)
            tmp = DATA_FILE.with_suffix('.tmp')
            tmp.write_bytes(body)
            tmp.replace(DATA_FILE)  # data first, then its version: a crash in between just fails the sha check
            write_json(VERSION_FILE, ver, indent=1)
            log(f"data v{ver['data_version']} saved ({len(body)} bytes, built by app {ver['built_by_app']})")
            result = 'updated'
        st['failures'], st['backoff_until'] = 0, 0
    except Exception as e:  # network, HTTP error, bad JSON, wrong shape, sha mismatch, too big: all the same to the user
        st['failures'] = st.get('failures', 0) + 1
        wait = min(24 * 3600, 1800 * 2 ** (st['failures'] - 1))  # 30 min, 1 h, 2 h ... at most a day
        st['backoff_until'] = now + wait
        log(f'check failed ({type(e).__name__}: {str(e)[:160]}); keeping current data, next try in {wait // 60} min')
        result = 'failed'
    write_json(STATE, st, indent=1)
    return result
