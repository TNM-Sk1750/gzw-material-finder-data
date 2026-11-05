"""GZW Material Finder - desktop shell.

A small always-on-top window (pywebview / Edge WebView2) driven by global hotkeys.
Hotkeys use the Win32 RegisterHotKey API - no keyboard hooks, never touches the game process.

Modes:  hidden -> (toggle) -> active: neon panel, you type
        active -> (toggle / Enter / click back into the game) -> hud: see-through, click-through, game keeps focus
        hud -> (toggle) -> active;   hide hotkey or Esc -> hidden;   quit hotkey -> exit

Editions (edition.json, written by tools/package.py; the source tree is "maintainer"):
        maintainer: TAC map daily + source pages weekly (datasync.py), plus the GitHub publisher (publish.py)
        public:     bundled data + the GitHub data feed (feed.py) only - no other network traffic

Run:       pythonw shell/finder.py [--config file] [--feed-base url]
Self-test: python shell/finder.py --selftest [--config file]   (hidden, never takes focus, safe while the game runs)
"""
import ctypes
import ctypes.wintypes as wt
import json
import re
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path

import webview

sys.path.insert(0, str(Path(__file__).resolve().parent))
import datasync  # noqa: E402
import feed  # noqa: E402
try:
    import publish  # noqa: E402  (maintainer edition only; the public package leaves it out)
except ImportError:
    publish = None

APP_VERSION = datasync.APP_VERSION
ROOT = Path(__file__).resolve().parent.parent
APP_HTML = ROOT / 'app' / 'index.html'
DATA = ROOT / 'data'
CONFIG = ROOT / 'config.json'
LOG = DATA / 'finder.log'
TITLE = 'GZW Material Finder'
KEY_RGB = (1, 2, 3)  # must match --key in app/style.css: pixels of this colour are see-through

DEFAULT_HOTKEYS = {
    'toggle': 'Ctrl+Alt+Z',            # show / search / pin over the game
    'hide': 'Ctrl+Alt+C',
    'quit': 'Ctrl+Alt+X',
    'taller': 'Ctrl+Alt+Up',
    'shorter': 'Ctrl+Alt+Down',
    'wider': 'Ctrl+Alt+Right',
    'narrower': 'Ctrl+Alt+Left',
    'less_see_through': ['Alt+Shift+=', 'Alt+Shift+NumPlus'],   # Alt+Shift+"+"
    'more_see_through': ['Alt+Shift+-', 'Alt+Shift+NumMinus'],  # Alt+Shift+"-" (the _ key)
    'bigger_text': ['Ctrl+Alt+=', 'Ctrl+Alt+NumPlus'],
    'smaller_text': ['Ctrl+Alt+-', 'Ctrl+Alt+NumMinus'],
}
# v2 hotkeys that v3 moved (arrows were see-through + size; now arrows = height/width, see-through = Alt+Shift+/-)
V2_MOVED = {'less_see_through': 'Ctrl+Alt+Up', 'more_see_through': 'Ctrl+Alt+Down',
            'bigger_window': 'Ctrl+Alt+Right', 'smaller_window': 'Ctrl+Alt+Left'}
CONFIG_VERSION = 3
DEFAULT_CONFIG = {
    'hotkeys': DEFAULT_HOTKEYS,
    'width': 440,
    'height': 620,
    'x': None,  # None = top-right of the primary screen
    'y': 80,
    'opacity_active': 0.9,
    'opacity_hud': 0.85,
    'text_scale': 1.0,
    'hud_click_through': True,
    'refresh_hours': 24,
    'steam_appmanifest': None,  # None = find it via Steam's library folders
}
LIMITS = {'opacity': (0.2, 1.0, 0.05), 'text_scale': (0.7, 2.0, 0.1), 'width': (300, 1600), 'height': (320, 2000)}

user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowW.restype = wt.HWND
user32.GetForegroundWindow.restype = wt.HWND
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
user32.MessageBoxW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.UINT]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.c_void_p]
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.AllowSetForegroundWindow.argtypes = [wt.DWORD]

WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
MOD_NOREPEAT = 0x4000
GWL_EXSTYLE, WS_EX_TRANSPARENT, WS_EX_LAYERED = -20, 0x20, 0x80000
MODS = {'alt': 0x1, 'ctrl': 0x2, 'control': 0x2, 'shift': 0x4, 'win': 0x8}
NAMED_KEYS = {'home': 0x24, 'end': 0x23, 'insert': 0x2D, 'ins': 0x2D, 'pause': 0x13, 'scrolllock': 0x91,
              'space': 0x20, 'tab': 0x09, 'pageup': 0x21, 'pagedown': 0x22, 'backquote': 0xC0, '`': 0xC0,
              'up': 0x26, 'down': 0x28, 'left': 0x25, 'right': 0x27, '=': 0xBB, 'plus': 0xBB, '-': 0xBD,
              'minus': 0xBD, '[': 0xDB, ']': 0xDD, 'numplus': 0x6B, 'numminus': 0x6D, 'delete': 0x2E, 'del': 0x2E}


def log(msg):
    DATA.mkdir(parents=True, exist_ok=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(f'{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n')


def load_config(path=CONFIG):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if path.exists():
        try:
            user = json.loads(path.read_text(encoding='utf-8'))
        except Exception as e:  # bad JSON - keep defaults, but say so
            log(f'{path.name} unreadable ({e}); using defaults')
            return cfg
        if 'hotkey' in user and 'hotkeys' not in user:  # v1 config had a single hotkey
            user['hotkeys'] = {'toggle': user['hotkey']}
        user.pop('hotkey', None)
        if user.get('steam_appmanifest') == r'C:\Program Files (x86)\Steam\steamapps\appmanifest_2479810.acf':
            user['steam_appmanifest'] = None  # v1 hard-coded default -> auto-detect
        if (user.get('config_version') or 2) < 3:
            # drop the v2 default bindings that moved; keys the user changed themselves are kept
            hk = user.get('hotkeys') or {}
            for action, old in V2_MOVED.items():
                if hk.get(action) == old:
                    hk.pop(action)
            hk.pop('bigger_window', None)
            hk.pop('smaller_window', None)
        user['config_version'] = CONFIG_VERSION
        cfg['hotkeys'].update(user.pop('hotkeys', {}) or {})
        cfg.update(user)
    cfg['config_version'] = CONFIG_VERSION
    save_config(cfg, path)  # writes new keys into an older config
    return cfg


def save_config(cfg, path=CONFIG):
    path.parent.mkdir(parents=True, exist_ok=True)  # the public package ships without a data/ folder
    path.write_text(json.dumps(cfg, indent=2), encoding='utf-8')


def parse_hotkey(spec):
    mods, vk = 0, None
    parts = [p.strip().lower() for p in re.split(r'\+(?=.)', spec) if p.strip()]
    for part in parts:
        if part in MODS:
            mods |= MODS[part]
        elif part in NAMED_KEYS:
            vk = NAMED_KEYS[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif re.fullmatch(r'f([1-9]|1\d|2[0-4])', part):
            vk = 0x70 + int(part[1:]) - 1
        else:
            raise ValueError(f'unknown key "{part}" in hotkey "{spec}"')
    if vk is None:
        raise ValueError(f'hotkey "{spec}" has no main key')
    return mods, vk


class Hotkeys(threading.Thread):
    """Registers every configured hotkey on one thread; WM_HOTKEY arrives here and is dispatched by id."""

    def __init__(self, bindings, on_action):
        super().__init__(daemon=True, name='hotkeys')
        self.bindings, self.on_action = bindings, on_action
        self.ready = threading.Event()
        self.ok = {}      # id -> (action, spec)
        self.errors = []  # human-readable problems
        self.tid = None

    def run(self):
        self.tid = kernel32.GetCurrentThreadId()
        next_id = 1
        for action, specs in self.bindings.items():
            for spec in ([specs] if isinstance(specs, str) else specs or []):
                try:
                    mods, vk = parse_hotkey(spec)
                except ValueError as e:
                    self.errors.append(f'{action}: {e}')
                    continue
                if user32.RegisterHotKey(None, next_id, mods | MOD_NOREPEAT, vk):
                    self.ok[next_id] = (action, spec)
                    next_id += 1
                else:
                    err = ctypes.get_last_error()
                    self.errors.append(f'{spec} ({action}) is taken by another app' if err == 1409 else f'{spec}: Windows error {err}')
        self.ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in self.ok:
                try:
                    self.on_action(self.ok[msg.wParam][0])
                except Exception:
                    log('hotkey handler error:\n' + traceback.format_exc())
        for hid in self.ok:
            user32.UnregisterHotKey(None, hid)

    def spec_for(self, action):
        return next((spec for a, spec in self.ok.values() if a == action), None)

    def id_for(self, action):
        return next((hid for hid, (a, _) in self.ok.items() if a == action), None)

    def stop(self):
        if self.tid:
            user32.PostThreadMessageW(self.tid, WM_QUIT, 0, 0)


class Api:
    """Exposed to the page as window.pywebview.api.* (underscore attributes stay private)."""

    def __init__(self, shell, finds_file, checklist_file=None):
        self._shell = shell
        self._finds = Path(finds_file)
        self._checklist = Path(checklist_file) if checklist_file else self._finds.with_name('checklist.json')
        self._lock = threading.Lock()

    def get_meta(self):
        s = self._shell
        keys = {a: (s.hotkeys.spec_for(a) if s.hotkeys else None) for a in DEFAULT_HOTKEYS}
        return {'build': s.build, 'hotkey': keys['toggle'], 'keys': keys, 'hotkey_errors': s.hotkeys.errors if s.hotkeys else [],
                'text_scale': s.cfg['text_scale'], 'mode': s.mode, 'edition': s.edition, 'app_version': APP_VERSION,
                'update': feed.update_info(), 'share_url': feed.load_config()['share_url']}

    def get_data(self):
        """The newest valid data for the page, as a parsed JSON object (None = keep the bundled gzw_data.js).
        Downloaded data only ever reaches the page as data through this call - never as a script."""
        source, data, ver = feed.pick(self._shell.edition)
        return {'source': source, 'data_version': ver['data_version'], 'data': data} if data else None

    def get_finds(self):
        if not self._finds.exists():
            return []
        out = []
        for line in self._finds.read_text(encoding='utf-8').splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # a half-written line shouldn't lose the rest
        return out

    def log_find(self, find):
        if not isinstance(find, dict) or not find.get('material'):
            raise ValueError('find must be an object with a material')
        find.setdefault('build', self._shell.build)
        with self._lock:
            self._finds.parent.mkdir(parents=True, exist_ok=True)
            with open(self._finds, 'a', encoding='utf-8') as f:
                f.write(json.dumps(find, ensure_ascii=False) + '\n')
        return True

    def open_url(self, url):
        if isinstance(url, str) and url.startswith('https://'):
            user32.AllowSetForegroundWindow(0xFFFFFFFF)  # ASFW_ANY: let the browser come to the front
            webbrowser.open(url)
            return True
        return False

    # Checklist: what you have + which station upgrades you're building (data/checklist.json)
    def get_checklist(self):
        try:
            return json.loads(self._checklist.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {'have': {}, 'plan': []}

    def save_checklist(self, data):
        if not isinstance(data, dict):
            raise ValueError('checklist must be an object')
        def section(s):
            s = s if isinstance(s, dict) else {}
            return {'have': {str(k): max(0, int(v)) for k, v in (s.get('have') or {}).items() if str(v).lstrip('-').isdigit()},
                    'plan': [str(p) for p in (s.get('plan') or [])][:200]}
        clean = section(data)
        clean['craft'] = section(data.get('craft'))  # the craft checklist: its own have counts and planned recipes
        clean['disassembly'] = section(data.get('disassembly'))  # disassembly checklist: parts you have / parts you want
        with self._lock:
            self._checklist.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._checklist.with_suffix('.tmp')
            tmp.write_text(json.dumps(clean, indent=1), encoding='utf-8')
            tmp.replace(self._checklist)
        return True

    def action(self, name):
        """Same actions as the hotkeys, for in-window keys and buttons."""
        if name in DEFAULT_HOTKEYS:
            threading.Thread(target=self._shell.do, args=(name,), daemon=True).start()
            return True
        return False

    def pin(self):
        self._shell.pin()

    def hide(self):
        self._shell.hide()

    def quit(self):
        threading.Thread(target=self._shell.quit, daemon=True).start()


class Shell:
    def __init__(self, cfg, config_path=CONFIG, dry=False):
        self.cfg, self.config_path, self.dry = cfg, config_path, dry
        self.window = None
        self.mode = 'hidden'
        self.prev_hwnd = None
        self.build = datasync.steam_build_id(cfg.get('steam_appmanifest'))
        self.actions = []  # dry-run record
        self.hotkeys = None
        self.edition = datasync.edition()
        self.feed_base = None  # set by run(): shell/feed.json or --feed-base
        self.finds_file = DATA / 'finds.jsonl'
        self.pending_reload = False
        self.ready = threading.Event()  # set once startup is done; hotkeys wait for it
        self._lock = threading.RLock()
        self._active_since = 0.0
        self._quitting = False

    def hwnd(self):
        return user32.FindWindowW(None, TITLE)

    def _ui(self, fn):
        """Run fn(form) on the WinForms UI thread."""
        from System import Func, Type  # pythonnet, loaded by pywebview
        form = self.window.native

        def call():
            fn(form)

        form.Invoke(Func[Type](call))

    def js(self, code):
        try:
            return self.window.evaluate_js(code)
        except Exception:
            log('js failed: ' + code[:80] + '\n' + traceback.format_exc())
            return None

    def toast(self, msg):
        self.js(f'window.gzwToast && window.gzwToast({json.dumps(msg)})')

    # ---------- hotkey actions ----------
    def do(self, action):
        self.actions.append(action)
        if self.dry and action in ('toggle', 'hide', 'quit'):
            return  # self-test: prove dispatch without showing/closing anything
        if action != 'quit' and not self.dry:
            self.ready.wait(10)  # a hotkey right after launch waits for the page instead of showing it mid-reload
        with self._lock:
            if action == 'toggle':
                self.pin() if self.mode == 'active' else self.activate()
            elif action == 'hide':
                self.hide()
            elif action == 'quit':
                threading.Thread(target=self.quit, daemon=True).start()
            elif action in ('less_see_through', 'more_see_through'):
                which = 'hud' if self.mode == 'hud' else 'active'
                v = self.adjust_opacity(which, 1 if action == 'less_see_through' else -1)
                self.toast(f'{"HUD" if which == "hud" else "Panel"} opacity {round(v * 100)}%')
            elif action in ('bigger_text', 'smaller_text'):
                lo, hi, step = LIMITS['text_scale']
                v = round(min(hi, max(lo, self.cfg['text_scale'] + (step if action == 'bigger_text' else -step))), 2)
                self.cfg['text_scale'] = v
                save_config(self.cfg, self.config_path)
                self.js(f'window.gzwTextScale && window.gzwTextScale({v})')
                self.toast(f'Text {round(v * 100)}%')
            elif action in ('taller', 'shorter'):
                self.resize(1, 1.1 if action == 'taller' else 1 / 1.1)
            elif action in ('wider', 'narrower'):
                self.resize(1.1 if action == 'wider' else 1 / 1.1, 1)

    def resize(self, fw, fh):
        w = int(min(LIMITS['width'][1], max(LIMITS['width'][0], round(self.cfg['width'] * fw))))
        h = int(min(LIMITS['height'][1], max(LIMITS['height'][0], round(self.cfg['height'] * fh))))
        try:  # never larger than the screen it's on
            scr = webview.screens[0]
            w, h = min(w, scr.width - 20), min(h, scr.height - 40)
        except Exception:
            pass
        self.cfg['width'], self.cfg['height'] = w, h
        save_config(self.cfg, self.config_path)
        self.set_size(w, h)
        self.toast(f'Window {w}×{h}')
        self.js('window.gzwFit && setTimeout(window.gzwFit, 60)')

    def set_size(self, w, h):
        """Exact outer size in logical px, top-right corner fixed. Unlike pywebview's resize, never shows a hidden window."""
        def f(form):
            from System.Drawing import Size
            s = getattr(form, '_scale', 1) or 1
            pw, ph = int(w * s), int(h * s)
            right = form.Left + form.Width
            form.Size = Size(pw, ph)
            form.Left = right - pw
        self._ui(f)

    def adjust_opacity(self, which, direction):
        lo, hi, step = LIMITS['opacity']
        key = 'opacity_hud' if which == 'hud' else 'opacity_active'
        with self._lock:
            self.cfg[key] = round(min(hi, max(lo, float(self.cfg[key]) + step * direction)), 2)
            save_config(self.cfg, self.config_path)
            if self.mode != 'hidden' and (self.mode == 'hud') == (which == 'hud'):
                self.apply_mode_styles(self.mode)
            return self.cfg[key]

    # ---------- modes ----------
    def setup_form(self):
        def f(form):
            from System.Drawing import Color
            form.BackColor = Color.FromArgb(*KEY_RGB)
            # don't touch MaximizeBox here: changing it rebuilds window styles and made drags flaky (tested 2026-10-27)
        self._ui(f)
    # Not using WebView2's native drag regions (IsNonClientRegionSupportEnabled + CSS app-region): tested 2026-10-27,
    # the whole page then hit-tests as title bar (clicks on links/buttons would drag) and it races the startup reload.

    def apply_mode_styles(self, mode):
        """Opacity, click-through and page styling for a mode. Doesn't show/hide or move focus.
        The colour key (page pixels in KEY_RGB become see-through) is ONLY on while pinned: with it on,
        Windows hit-tests a resized window as see-through and mouse clicks/drags fall through to whatever is behind."""
        alpha = float(self.cfg['opacity_hud' if mode == 'hud' else 'opacity_active'])

        def f(form):
            from System.Drawing import Color
            form.TransparencyKey = Color.FromArgb(*KEY_RGB) if mode == 'hud' else Color.Empty
            form.Opacity = alpha
        self._ui(f)
        hwnd = self.hwnd()
        if hwnd:
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            click_through = mode == 'hud' and self.cfg.get('hud_click_through', True)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, (ex | WS_EX_TRANSPARENT) if click_through else (ex & ~WS_EX_TRANSPARENT))
        self.js(f"window.gzwMode && window.gzwMode('{mode}')")

    def activate(self):
        with self._lock:
            me = self.hwnd()
            fg = user32.GetForegroundWindow()
            if fg and fg != me:
                self.prev_hwnd = fg  # usually the game - focus goes back here when pinned/hidden
            was = self.mode
            self.apply_mode_styles('active')
            if was == 'hidden':
                self.window.show()  # pywebview: Show() + Activate()
            self.mode = 'active'
            self._active_since = time.monotonic()
            if me:
                self._take_foreground(me)
            self._focus_webview()
            if was == 'hidden':  # fresh open: straight to the search box. Back from the HUD: leave the card as it is.
                self.js('window.gzwFocus && window.gzwFocus()')

    def _take_foreground(self, me):
        user32.SetForegroundWindow(me)  # normally allowed: we just received the hotkey
        if user32.GetForegroundWindow() == me:
            return
        # Fallback: some fullscreen games hold the foreground lock; borrow their input queue for a moment.
        fg = user32.GetForegroundWindow()
        fg_tid = user32.GetWindowThreadProcessId(fg, None)
        my_tid = kernel32.GetCurrentThreadId()
        if fg_tid and fg_tid != my_tid and user32.AttachThreadInput(my_tid, fg_tid, True):
            try:
                user32.BringWindowToTop(me)
                user32.SetForegroundWindow(me)
            finally:
                user32.AttachThreadInput(my_tid, fg_tid, False)
        if user32.GetForegroundWindow() != me:
            log('could not take focus from the foreground window')

    def pin(self, give_focus_back=True):
        with self._lock:
            if self.mode == 'hidden':
                return
            was_active = self.mode == 'active'
            self.apply_mode_styles('hud')
            self.mode = 'hud'
            if give_focus_back and was_active and user32.GetForegroundWindow() == self.hwnd():
                self._focus_prev()

    def hide(self):
        with self._lock:
            if self.mode == 'hidden':
                return
            had_focus = user32.GetForegroundWindow() == self.hwnd()
            self.remember_position()
            self.window.hide()
            self.mode = 'hidden'
            if had_focus:
                self._focus_prev()
            if self.pending_reload:
                self.reload_page()

    def _focus_prev(self):
        if self.prev_hwnd and user32.IsWindow(self.prev_hwnd):
            user32.SetForegroundWindow(self.prev_hwnd)

    def _focus_webview(self):
        try:
            def f(form):
                form.browser.webview.Focus()
            self._ui(f)
        except Exception:
            log('webview focus failed:\n' + traceback.format_exc())

    def remember_position(self):
        try:
            if self.mode != 'hidden':
                self.cfg['x'], self.cfg['y'] = self.window.x, self.window.y
                save_config(self.cfg, self.config_path)
        except Exception:
            log('could not read window position:\n' + traceback.format_exc())

    def watch_focus(self):
        """Clicking back into the game while searching drops the finder into HUD mode instead of staying opaque."""
        while not self._quitting:
            time.sleep(0.3)
            try:
                if self.mode == 'active' and time.monotonic() - self._active_since > 1.0:
                    fg = user32.GetForegroundWindow()
                    if fg and fg != self.hwnd():
                        self.pin(give_focus_back=False)
            except Exception:
                log('focus watcher error:\n' + traceback.format_exc())

    # ---------- data refresh ----------
    def reload_page(self):
        self.pending_reload = False
        self.js('location.reload()')  # page boot re-reads mode + text size via get_meta(), and data via get_data()

    def data_changed(self):
        if self.mode == 'hidden':
            self.reload_page()
        else:
            self.pending_reload = True  # don't yank the page while it's being read: applied on the next hide

    def _sleep(self, seconds):
        for _ in range(int(seconds)):
            if self._quitting:
                return False
            time.sleep(1)
        return not self._quitting

    def sync_loop(self):
        """Maintainer edition: TAC map + sources (datasync rate-limits itself: TAC daily, sources weekly), then the
        publisher (hourly at most, off without a token). Other maintainers' new finds rebuild the local data too."""
        while not self._quitting:
            try:
                rebuilt, msg = datasync.sync(refresh_hours=float(self.cfg.get('refresh_hours', 24)))
                if publish:
                    if publish.run(self.cfg, self.finds_file)['contribs_changed']:
                        log('sync: ' + datasync.build())
                        rebuilt = True
                if rebuilt:
                    self.data_changed()
            except Exception:
                log('sync error:\n' + traceback.format_exc())
            if not self._sleep(3600):
                return

    def feed_loop(self):
        """Both editions: newer shared data + "update available" from the GitHub feed. feed.py does the rate limit
        (startup, then every 6 h) and the back-off; with no feed_base it does nothing at all."""
        startup, shown = True, feed.update_info()
        while not self._quitting:
            try:
                if feed.check(self.feed_base, startup=startup) == 'updated' and feed.pick(self.edition)[0] == 'feed':
                    self.data_changed()
                if feed.update_info() != shown:
                    shown = feed.update_info()
                    self.js('window.gzwMeta && window.gzwMeta()')  # footer + menu pick up "Update available"
            except Exception:
                log('feed error:\n' + traceback.format_exc())
            startup = False
            if not self._sleep(300):
                return

    def quit(self):
        self._quitting = True
        self.remember_position()
        if self.hotkeys:
            self.hotkeys.stop()
        try:
            self.window.destroy()
        except Exception:
            pass


def make_window(cfg, api, focus=True):
    x = cfg['x']
    if x is None:
        try:
            scr = webview.screens[0]
            x = max(0, scr.width - cfg['width'] - 24)
        except Exception:
            x = 100
    return webview.create_window(
        TITLE, url=APP_HTML.as_uri(), js_api=api, width=cfg['width'], height=cfg['height'], x=x, y=cfg['y'],
        resizable=True, frameless=True, easy_drag=False, on_top=True, hidden=True, focus=focus,
        background_color='#010203', text_select=True, min_size=(LIMITS['width'][0], LIMITS['height'][0]),
    )


def arg(name):
    """Value after a CLI option (--config file, --feed-base url), or None."""
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv[:-1] else None


def run():
    # --config <file>: use another settings file (testing / a second profile)
    cfg_path = Path(arg('--config') or CONFIG)
    cfg = load_config(cfg_path)
    shell = Shell(cfg, config_path=cfg_path)
    shell.feed_base = feed.load_config(arg('--feed-base'))['feed_base']  # --feed-base <url>: test feed server
    api = Api(shell, shell.finds_file)
    shell.hotkeys = Hotkeys(cfg['hotkeys'], shell.do)
    shell.hotkeys.start()
    shell.hotkeys.ready.wait(5)
    if shell.hotkeys.id_for('toggle') is None:
        problems = '\n'.join(shell.hotkeys.errors) or 'unknown'
        log('startup aborted: toggle hotkey unavailable: ' + problems)
        user32.MessageBoxW(None, f'The show/hide hotkey could not be registered:\n{problems}\n\n'
                                 'Is the finder already running? Otherwise change "toggle" under "hotkeys" in config.json.', TITLE, 0x30)
        shell.hotkeys.stop()
        return 1
    if shell.hotkeys.errors:
        log('some hotkeys unavailable: ' + '; '.join(shell.hotkeys.errors))
    shell.window = make_window(cfg, api)

    def after_start(window):
        if not window.events.loaded.wait(30):
            log('page never loaded')
            return
        time.sleep(0.3)  # let pywebview finish its hidden-start opacity dance first
        shell.setup_form()
        shell.set_size(cfg['width'], cfg['height'])  # frameless windows otherwise start a border-width short
        shell.apply_mode_styles('active')
        shell.ready.set()
        threading.Thread(target=shell.watch_focus, daemon=True, name='focus-watch').start()
        if shell.edition == 'maintainer':  # the public edition makes no TAC map / source page requests at all
            threading.Thread(target=shell.sync_loop, daemon=True, name='data-sync').start()
        threading.Thread(target=shell.feed_loop, daemon=True, name='data-feed').start()

    log(f'started: {shell.edition} {APP_VERSION} hotkeys={len(shell.hotkeys.ok)} build={shell.build} feed={"on" if shell.feed_base else "off"}')
    webview.start(after_start, (shell.window,), private_mode=True)
    shell._quitting = True
    shell.hotkeys.stop()
    log('exited')
    return 0


def selftest():
    """Hidden window with WS_EX_NOACTIVATE: never shown, never takes focus. Safe while gaming."""
    cfg_path = DATA / 'selftest_config.json'
    cfg = load_config(Path(arg('--config') or CONFIG))
    save_config(cfg, cfg_path)  # all changes go to a throwaway copy, not your real config
    shell = Shell(cfg, config_path=cfg_path, dry=True)
    finds_file = DATA / 'selftest_finds.jsonl'
    checklist_file = DATA / 'selftest_checklist.json'
    finds_file.unlink(missing_ok=True)
    checklist_file.unlink(missing_ok=True)
    api = Api(shell, finds_file, checklist_file)
    results = []

    def check(name, ok, detail=''):
        results.append(bool(ok))
        print(f'{"PASS" if ok else "FAIL"}  {name}{"  -- " + str(detail) if detail else ""}', flush=True)

    shell.hotkeys = Hotkeys(cfg['hotkeys'], shell.do)
    shell.hotkeys.start()
    shell.hotkeys.ready.wait(5)
    want = sum(1 for v in cfg['hotkeys'].values() for _ in ([v] if isinstance(v, str) else v))
    check(f'all {want} hotkeys registered', len(shell.hotkeys.ok) == want, '; '.join(shell.hotkeys.errors))
    check('steam build id found', shell.build is not None, shell.build)

    shell.window = make_window(cfg, api, focus=False)

    def press(action):
        before = len(shell.actions)
        user32.PostThreadMessageW(shell.hotkeys.tid, WM_HOTKEY, shell.hotkeys.id_for(action), 0)  # no keystroke sent anywhere
        for _ in range(40):
            if len(shell.actions) > before:
                break
            time.sleep(0.05)
        time.sleep(0.2)
        return len(shell.actions) > before and shell.actions[-1] == action

    def runner(window):
        try:
            if not window.events.loaded.wait(30):
                check('page loaded', False, 'timeout')
                return
            check('page loaded', True)
            time.sleep(0.5)
            n = window.evaluate_js('window.GZW_DATA.materials.length')
            check('data present', (n or 0) > 30, f'{n} materials')
            top = window.evaluate_js("window.GZW.search('dynmo')[0].label")
            check('fuzzy search', top == 'Dynamo', top)
            want, _, ver = feed.pick(shell.edition)  # bundled, or a verified download in data/feed/
            info = None
            for _ in range(50):
                info = window.evaluate_js('window.GZW.dataInfo()')
                if info and info.get('booted'):
                    break
                time.sleep(0.1)
            check(f'page shows the newest valid data ({shell.edition} edition, {want}{" v%d" % ver["data_version"] if ver else ""})',
                  bool(info) and info.get('booted') and info.get('source') == want and info.get('materials', 0) > 30, info)
            window.evaluate_js("""
                window.__t = 'pending';
                pywebview.api.get_meta()
                  .then(m => pywebview.api.log_find({material: 'dynamo', material_name: 'Dynamo', place: null, poi: 'Sawmill', ts: 'selftest'}))
                  .then(() => pywebview.api.get_finds())
                  .then(f => { window.__t = 'ok:' + f.length; })
                  .catch(e => { window.__t = 'err:' + e; });
            """)
            state = None
            for _ in range(50):
                state = window.evaluate_js('window.__t')
                if state != 'pending':
                    break
                time.sleep(0.1)
            check('JS -> Python bridge (meta, log_find, get_finds)', state == 'ok:1', state)
            check('find written to disk', finds_file.exists() and 'Sawmill' in finds_file.read_text(encoding='utf-8'))
            window.evaluate_js("""
                window.__c = 'pending';
                pywebview.api.save_checklist({have: {steel: 4, dynamo: 1}, plan: ['Generator|1']})
                  .then(() => pywebview.api.get_checklist())
                  .then(c => { window.__c = 'ok:' + c.have.steel + ':' + c.plan.length; })
                  .catch(e => { window.__c = 'err:' + e; });
            """)
            state = None
            for _ in range(50):
                state = window.evaluate_js('window.__c')
                if state != 'pending':
                    break
                time.sleep(0.1)
            check('checklist saves and loads (test file only)', state == 'ok:4:1' and checklist_file.exists(), state)

            shell.setup_form()
            shell.apply_mode_styles('hud')
            hwnd = shell.hwnd()
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            check('HUD: layered + click-through', bool(ex & WS_EX_LAYERED) and bool(ex & WS_EX_TRANSPARENT), hex(ex))
            check('HUD: page in hud style', window.evaluate_js("document.body.classList.contains('hud')") is True)
            shell.apply_mode_styles('active')
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            check('active: clicks not passed through', not (ex & WS_EX_TRANSPARENT), hex(ex))
            check('active: page in panel style', window.evaluate_js("document.body.classList.contains('hud')") is False)

            # every hotkey action, delivered as a real WM_HOTKEY to the hotkey thread
            op0 = cfg['opacity_active']
            check('hotkey: less see-through', press('less_see_through') and cfg['opacity_active'] == round(min(1.0, op0 + 0.05), 2), f"{op0} -> {cfg['opacity_active']}")
            check('hotkey: more see-through', press('more_see_through') and cfg['opacity_active'] == op0, cfg['opacity_active'])
            ts0 = cfg['text_scale']
            ok = press('bigger_text')
            page_scale = window.evaluate_js("getComputedStyle(document.documentElement).getPropertyValue('--fs').trim()")
            check('hotkey: bigger text (page follows)', ok and cfg['text_scale'] == round(ts0 + 0.1, 2) and page_scale == str(cfg['text_scale']), f'{ts0} -> {cfg["text_scale"]}, page --fs={page_scale}')
            check('hotkey: smaller text', press('smaller_text') and cfg['text_scale'] == ts0, cfg['text_scale'])
            w0, h0 = cfg['width'], cfg['height']
            check('hotkey: taller (height only)', press('taller') and cfg['height'] > h0 and cfg['width'] == w0, f"{w0}x{h0} -> {cfg['width']}x{cfg['height']}")
            check('hotkey: shorter', press('shorter') and abs(cfg['height'] - h0) <= 2 and cfg['width'] == w0, f"{cfg['width']}x{cfg['height']}")
            h0 = cfg['height']
            check('hotkey: wider (width only)', press('wider') and cfg['width'] > w0 and cfg['height'] == h0, f"{w0}x{h0} -> {cfg['width']}x{cfg['height']}")
            check('hotkey: narrower', press('narrower') and abs(cfg['width'] - w0) <= 2 and cfg['height'] == h0, f"{cfg['width']}x{cfg['height']}")
            check('hotkey: hide dispatched', press('hide'))
            check('saved settings went to the test copy only', json.loads(cfg_path.read_text(encoding='utf-8'))['text_scale'] == ts0)
            check('window stayed hidden', shell.mode == 'hidden' and not user32.IsWindowVisible(hwnd))
            check('hotkey: quit dispatched', press('quit'))
        except Exception:
            check('selftest runner', False, traceback.format_exc())
        finally:
            shell.dry = False
            shell.quit()

    webview.start(runner, (shell.window,), private_mode=True)
    shell.hotkeys.stop()
    finds_file.unlink(missing_ok=True)
    checklist_file.unlink(missing_ok=True)
    cfg_path.unlink(missing_ok=True)
    ok = bool(results) and all(results)
    print('SELFTEST OK' if ok else 'SELFTEST FAILED', flush=True)
    return 0 if ok else 1


if __name__ == '__main__':
    try:
        sys.exit(selftest() if '--selftest' in sys.argv else run())
    except Exception:
        log('fatal:\n' + traceback.format_exc())
        raise
