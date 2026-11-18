# Testing changes

The add-on has no unit-test suite; changes are checked by **looking**: against the store images the game ships
with every item, against the regression set, and for shaders against the compiled game shader
([SHADER_PIPELINE.md](SHADER_PIPELINE.md)).

## Setup

| Variable | Meaning |
|---|---|
| `FT_EXPORTS` | Your FModel `Output/Exports` folder, or a scratch export root (see below). Required by most scripts |
| `FT_BLENDER` | Path to `blender.exe` (default: `blender` on PATH) |
| `SP` | A scratch folder for renders |
| `FT_REPO` | The repository (default: found from the script's location) |

Most scripts are run by Blender in the background and import the add-on straight from the repository, with the
PSK importer copy `add-on-io-scene-psk-psa-v9_1_2/` next to it. That copy isn't in git: put the
[io_scene_psk_psa](https://github.com/DarklightGames/io_scene_psk_psa) release you use there (or adapt the import
line).

## Comparison renders (clothing)

```
blender --factory-startup -b --python dev_tools/compare.py -- <path under Characters> <SkinFolder|AUTO> <outname> [yaw] [left|right] [pitch]
```

writes `<SP>/<outname>_cmp.png`: our render on the left, the store image on the right. `FT_NUMBER=3` sets number
decals.

## Regression set

`dev_tools/regress.txt` lists one `compare.py` argument line per item: skins that broke at some point. Render all
of them before and after a builder change and compare the pairs. A change is ready when it fixes the reported
item **and** leaves the regression set unchanged (or visibly better).

## Debugging a "this item looks wrong" report
1. Find the item under `Exports/Discovery/Content/Discovery/Characters/`.
2. Dump its material: `python -I dev_tools/dump_mi.py <MI json>`; master defaults:
   `python -I dev_tools/master_defaults.py <MasterName>` (needs `FT_EXPORTS`).
3. Render ours next to the store image (`compare.py`).
4. Inspect textures: `split_any.py` (per-channel sheets), `ocm_layers.py` (layer coverage), `scheme_by_layer.py`,
   `texel2.py` (UV density), `uv_probe.py` (which mesh area a UV point hits), `zoom.py` / `crop.py` / `montage.py`.
5. Find a rule that explains **all** affected skins, implement it, run the regression set.
6. Survey scripts (`survey*.py`, `fingerprint.py`) measure how many skins a rule affects (`survey.py <out.pkl>`
   then `survey3.py <index.pkl> <mats.pkl>`).

## Testing without touching your exports
To test Export Now or items you haven't exported, export into an **empty scratch folder** and use it as the
root:

```
finals_texturer/bin/exporter/ft_exporter.exe export --out <scratch> <game path>...
```

e.g. a face DA and a few item DAs (`…/DA_X.json` plus `…/<item folder>/*`). Use a short path (e.g. map the
scratch folder to a drive letter with `subst`): the game's folder depth plus a long base path passes Windows'
260-character limit, which Blender's Python can't open.

## Blender pitfalls in test runs
- `--factory-startup` runs can **empty the shared extension library folder**
  (`%APPDATA%\Blender Foundation\Blender\<version>\extensions\.local\lib\python3.13\site-packages`) when they
  enable extensions, which removes the PSK importer's `psk_psa_py` from your real Blender. Enable
  `bl_ext.blender_org.io_scene_psk_psa` too in such runs, check the folder afterwards, and if it's empty start
  Blender once normally (`blender -b --python-expr "pass"`) to restore it.
- To test installing / updating the add-on without touching your own Blender, set `BLENDER_USER_RESOURCES` to a
  scratch folder.
- Auto-Rig Pro needs a GUI Blender (a 3D viewport); background runs can't generate its rig.
- Timers don't run in background mode; popups opened without a window crash Blender.

## Before opening a pull request
- The add-on builds: `blender --factory-startup --command extension build --source-dir finals_texturer --output-dir .`
- It installs and imports at least one affected item in a normal Blender.
- Shader changes: `check_items.py` numbers for the items you changed, and the rule written into
  [MATERIAL_RULES.md](MATERIAL_RULES.md).
- Screenshots of before / after next to the store image help reviewers a lot.
