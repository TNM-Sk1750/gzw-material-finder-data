"""Builds the public GZW Material Finder zip from this folder. GitHub Actions runs it (.github/workflows/build.yml),
so the release download is made from exactly the code you can read here:

  source/app, source/shell   the app (these same files go into the zip)
  gzw_data.json              the community data at the repo root (the app also downloads newer copies of it)
  runtime/                   the official Python the build runs on, plus the packages in requirements.txt

Run: source\\shell\\.venv\\Scripts\\python.exe source\\build.py [expected version, e.g. 1.3.0]
Written by the maintainer's export script (it shares these pieces with the maintainer build); don't edit by hand.
"""
import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

SRC = Path(__file__).resolve().parent
REPO = SRC.parent
DIST = REPO / 'dist'
NAME = 'GZW-Material-Finder-public'
PY_HOME = Path(sys.base_prefix)  # the real Python install behind the venv
VENV_SITE = SRC / 'shell' / '.venv' / 'Lib' / 'site-packages'

sys.path.insert(0, str(SRC / 'shell'))
import datasync  # noqa: E402

MAX_ZIP = 10_000_000

LIB_SKIP = {'site-packages', 'test', 'idlelib', 'tkinter', 'turtledemo', 'ensurepip', 'lib2to3', '__pycache__', 'pydoc_data',
            'venv', 'unittest', 'sqlite3', '__phello__', 'xmlrpc', 'curses', 'msilib', 'dbm'}

LIB_FILE_SKIP = {'turtle.py', 'pydoc.py', 'doctest.py'}

DLL_SKIP = {'tcl86t.dll', 'tk86t.dll', '_tkinter.pyd', 'sqlite3.dll', '_sqlite3.pyd', 'xxlimited.pyd', 'xxlimited_35.pyd',
            '_ctypes_test.pyd', '_testbuffer.pyd', '_testcapi.pyd', '_testclinic.pyd', '_testconsole.pyd',
            '_testimportmultiple.pyd', '_testinternalcapi.pyd', '_testmultiphase.pyd', '_testsinglephase.pyd', '_msi.pyd'}

SITE_SKIP_PREFIX = ('pip', '_virtualenv', 'distutils-precedence', '__pycache__')

SITE_FILE_SKIP = re.compile(r'(win-(arm64|x86)[\\/]|[\\/]dlls[\\/]x86[\\/]|pywebview-android\.jar$|\.pdb$|Python\.Runtime\.xml$|WebBrowserInterop\.x86\.dll$)', re.I)

START_CMD = '@echo off\r\nstart "" "%~dp0runtime\\pythonw.exe" -E -s "%~dp0shell\\finder.py"\r\n'

SELFTEST_CMD = ('@echo off\r\necho Running the GZW Material Finder self-test (hidden, safe while gaming)...\r\n'
                '"%~dp0runtime\\python.exe" -E -s "%~dp0shell\\finder.py" --selftest\r\necho.\r\npause\r\n')

HOTKEYS_TXT = """HOTKEYS (work anywhere, even in game)
  Ctrl+Alt+Z        show / search -> pin over the game -> back to search
  Ctrl+Alt+C        hide
  Ctrl+Alt+X        shut down the finder
  Ctrl+Alt+Up/Down     taller / shorter window
  Ctrl+Alt+Right/Left  wider / narrower window
  Alt+Shift+ + / -     less / more see-through (numpad +/- work too)
  Ctrl+Alt+ = / -   bigger / smaller text
In the search box:  Up/Down pick · Enter pin · Ctrl+L log a find · Esc clear/hide · F1 help"""

README_PUBLIC = """GZW MATERIAL FINDER  -  version {version}
===================

A free hideout companion for Gray Zone Warfare 0.5. Press a hotkey, type a hideout material (say, Steel)
and it shows where players report finding it, and which upgrades need it. Pin it and it stays on top of
the game, see-through, and mouse clicks pass through to the game.

It is a separate window: it does not read or modify the game, its memory or files.
Not affiliated with MADFINGER Games or GZW Tac Map.

START
  1. Right-click the zip > Properties > tick "Unblock" > OK  (before extracting; stops Windows warnings)
  2. Extract the folder anywhere (Desktop is fine).
  3. Double-click "Start GZW Finder.cmd". Nothing shows yet; it waits for the hotkey.
  4. In game press Ctrl+Alt+Z.
  Needs Borderless or Windowed display mode, not exclusive Fullscreen (GZW: Settings > Display).

""" + HOTKEYS_TXT + """

RIGHT-CLICK MENU (not while pinned)
  Finder · Checklist · Crafting · Craft checklist · Disassembly · Disassembly checklist · Hotkeys · Hide · Exit
  (plus "Update available" when a newer version is out)
  Checklist: tick the hideout upgrades you're building, set how many of each material you have,
  and it tells you what you still need to pick up. Each material card also shows "pick up N more".
  Disassembly: devices you take apart in the Electronics Workshop and the part each one gives.
  Disassembly checklist: tick the parts you want; it shows which devices to dismantle.
The version is in this file's title, the footer and F1 (help).
Click an area name or a player's quote to open that area on GZW Tac Map in your browser.
Drag the window by its top bar. Change any hotkey in config.json (created on first run).

WHAT THE LABELS MEAN
  MINE       you logged a find there (Ctrl+L); your own finds rank first
  CONFIRMED  2+ different players or sources found it there (the number = how many)
  REPORTED   one player or source found it there
  TIP        recommended by players, but nobody said they found it there

CREDITS
  Player reports come from Steam, YouTube and guides; each one links to its source in the app.
  Area links open GZW Tac Map (gzwtacmap.com) in your browser.

UPDATES
  The finder may check its GitHub page for newer data: one small file, at most every 6 hours, and nothing
  about you is sent. That is its only network traffic. When a newer version of the app is out,
  "Update available" appears in the footer and the right-click menu.

SAFE?
  It uses the normal Windows hotkey API (like Discord): no keyboard hook, no injection, no memory reading.
  "Self-test.cmd" checks everything while hidden. Needs Windows 10/11 with the Edge WebView2 runtime
  (already on Windows 11).

PROBLEMS?
  Open an issue on the GitHub page. Attaching data\\finder.log helps.

See LICENSE.txt.
"""

LICENSE_PUBLIC = ('Free to use. You may not redistribute, re-upload, sell, or publish modified versions without permission. '
                  'Provided as-is with no warranty.\n')

def copy_tree(src, dst, skip_dir=lambda p: False, skip_file=lambda p: False):
    for p in src.rglob('*'):
        rel = p.relative_to(src)
        if any(skip_dir(Path(*rel.parts[:i + 1])) for i in range(len(rel.parts) - (0 if p.is_dir() else 1))):
            continue
        if p.is_dir():
            continue
        if skip_file(p) or p.suffix in ('.pyc', '.pyo'):
            continue
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, out)


def copy_runtime(rt):
    rt.mkdir()
    for f in ['python.exe', 'pythonw.exe', 'python3.dll', f'python{sys.version_info.major}{sys.version_info.minor}.dll',
              'vcruntime140.dll', 'vcruntime140_1.dll', 'LICENSE.txt']:
        if (PY_HOME / f).exists():
            shutil.copy2(PY_HOME / f, rt / f)
    copy_tree(PY_HOME / 'DLLs', rt / 'DLLs', skip_file=lambda p: p.name in DLL_SKIP)
    copy_tree(PY_HOME / 'Lib', rt / 'Lib', skip_dir=lambda rel: rel.parts[0] in LIB_SKIP or rel.name == '__pycache__',
              skip_file=lambda p: p.parent == PY_HOME / 'Lib' and p.name in LIB_FILE_SKIP)
    copy_tree(VENV_SITE, rt / 'Lib' / 'site-packages',
              skip_dir=lambda rel: rel.parts[0].startswith(SITE_SKIP_PREFIX) or rel.name == '__pycache__',
              skip_file=lambda p: p.name.startswith(SITE_SKIP_PREFIX) or (p.suffix == '.pth' and 'virtualenv' in p.name)
              or bool(SITE_FILE_SKIP.search(str(p))))
    # pywebview requires these folders to EXIST (it adds all three to PATH) even though x64 only loads win-x64.
    for arch in ('win-arm64', 'win-x86'):
        d = rt / 'Lib' / 'site-packages' / 'webview' / 'lib' / 'runtimes' / arch / 'native'
        d.mkdir(parents=True, exist_ok=True)
        (d / 'placeholder.txt').write_text('x64-only build: this loader was left out to keep the zip small.\n', encoding='ascii')


def check(out):
    """Refuse to zip a build that is missing a piece."""
    py_dll = f'python{sys.version_info.major}{sys.version_info.minor}.dll'
    need = ['runtime/pythonw.exe', f'runtime/{py_dll}', 'runtime/vcruntime140.dll', 'app/data/gzw_data.js', 'app/lang.js',
            'shell/finder.py', 'runtime/Lib/site-packages/webview/__init__.py', 'runtime/Lib/site-packages/clr_loader/__init__.py']
    missing = [f for f in need if not (out / f).is_file()]
    if missing:
        raise SystemExit('refusing to package, missing: ' + ', '.join(missing))


def main():
    want = sys.argv[1].lstrip('v') if len(sys.argv) > 1 else None
    if want and want != datasync.APP_VERSION:
        raise SystemExit(f'version mismatch: asked for {want}, the code says {datasync.APP_VERSION}')
    out = DIST / NAME
    shutil.rmtree(out, ignore_errors=True)
    for f in ['index.html', 'style.css', 'lang.js', 'app.js']:
        (out / 'app' / 'data').mkdir(parents=True, exist_ok=True)
        shutil.copy2(SRC / 'app' / f, out / 'app' / f)
    data = json.loads((REPO / 'gzw_data.json').read_text(encoding='utf-8'))
    (out / 'app' / 'data' / 'gzw_data.js').write_text(datasync.data_js(data), encoding='utf-8')
    (out / 'shell').mkdir()
    for f in ['finder.py', 'datasync.py', 'feed.py', 'feed.json']:
        shutil.copy2(SRC / 'shell' / f, out / 'shell' / f)
    (out / 'edition.json').write_text(json.dumps({'edition': 'public'}) + '\n', encoding='utf-8')
    copy_runtime(out / 'runtime')
    (out / 'Start GZW Finder.cmd').write_text(START_CMD, encoding='ascii')
    (out / 'Self-test.cmd').write_text(SELFTEST_CMD, encoding='ascii')
    (out / 'README.txt').write_text(README_PUBLIC.replace('{version}', datasync.APP_VERSION).replace('\n', '\r\n'), encoding='utf-8')
    (out / 'LICENSE.txt').write_text(LICENSE_PUBLIC.replace('\n', '\r\n'), encoding='utf-8')
    check(out)

    zpath = DIST / f'{NAME}-{datasync.APP_VERSION}.zip'
    zpath.unlink(missing_ok=True)
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(out.rglob('*')):
            if p.is_file():
                z.write(p, Path(NAME) / p.relative_to(out))
    size = zpath.stat().st_size
    if size >= MAX_ZIP:
        raise SystemExit(f'{zpath.name} is {size:,} bytes - over the 10 MB limit')
    print(f'{zpath.name}  {size:,} bytes  sha256 {hashlib.sha256(zpath.read_bytes()).hexdigest()}')


if __name__ == '__main__':
    main()
