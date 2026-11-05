# GZW Material Finder

A free hotkey overlay for Gray Zone Warfare 0.5. Search a hideout, crafting or disassembly item and see where
players report finding it, plus have/need checklists for hideout upgrades, recipes and disassembly parts.

**Download:** [Releases](https://github.com/TNM-Sk1750/gzw-material-finder-data/releases). Unzip anywhere, run
`Start GZW Finder.cmd`, then press Ctrl+Alt+Z in game (Borderless or Windowed mode).

Not affiliated with MADFINGER Games or GZW Tac Map.

## Is it safe?

You don't have to take my word for it:

- **All the code is here.** [`source/`](source) holds every file the app runs: `app/` is the window (HTML/JS),
  `shell/` is the Python behind it. It's a separate window. It does not read or change the game, its memory or
  its files, and it uses the normal Windows hotkey API (like Discord), not a keyboard hook.
- **GitHub builds the download, not me.** The zip on the release page is built by
  [this workflow](.github/workflows/build.yml) on GitHub's own Windows machine, from the code in this repo. You can
  open the [Actions tab](https://github.com/TNM-Sk1750/gzw-material-finder-data/actions) and see every build log.
- **You can prove the zip came from that build.** Each zip has a signed GitHub attestation:
  `gh attestation verify GZW-Material-Finder-public-1.3.0.zip --repo TNM-Sk1750/gzw-material-finder-data`
- **Why a `.exe` and a `.cmd`?** The zip carries the official Python runtime (`runtime/python.exe` comes from
  python.org through GitHub's `setup-python`) so you don't have to install anything. `Start GZW Finder.cmd` is two
  lines you can open in Notepad: it starts `shell/finder.py` with that Python.
- **Network:** the app downloads `gzw_data.json` and `version.json` from this repo, at most every 6 hours, and sends
  nothing about you. That's all.

Windows may show "Windows protected your PC" the first time, because the app isn't code-signed (that costs money).
Click More info > Run anyway, or don't, if you'd rather not.

## What's in this repo

| Path | What it is |
| --- | --- |
| `source/` | the app's code and its build script |
| `gzw_data.json` | the community data every copy downloads |
| `version.json` | the data version, so copies know when to update |
| `contrib/` | finds shared by players (right-click > Share my finds in the app) |

## Sharing finds

Log a find in game with Ctrl+L, then right-click > Share my finds. It opens a GitHub issue with your finds
(material, area and date only) filled in. They're checked, added to `contrib/`, and reach everyone's copy within a day.

Feedback and bug reports: [open an issue](https://github.com/TNM-Sk1750/gzw-material-finder-data/issues).
