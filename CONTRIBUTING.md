# Contributing to Finals Texturer

Thanks for helping! Bug reports with a screenshot next to the store image are as valuable as code: most fixes
start from "this item looks wrong".

## Ways to help
- **Report an item that looks wrong**: use the *Item looks wrong* issue form. Include the in-game name, the
  skin, a screenshot of ours and the store image, and the text from **Show Log** in the Finals panel.
- **Report a bug** (errors, crashes, wrong placement, rigging problems): use the *Bug report* form.
- **Build a missing shader**: issues labelled `builder wanted` list game materials that still use the generic
  fallback (fur, LED screens, holograms, diamonds…). [docs/SHADER_PIPELINE.md](docs/SHADER_PIPELINE.md) shows how
  to read the game's compiled shader for one.
- **Improve docs or the UI**: always welcome; issues labelled `good first issue` are a good start.

## Development setup
1. **Blender 5.1** and the [io_scene_psk_psa](https://github.com/DarklightGames/io_scene_psk_psa) extension.
2. **FModel** set up for THE FINALS, with at least `Discovery/Content/Discovery/MaterialLibrary` and the items you
   work on exported (or use **Export Now** in the add-on).
3. Clone the repository. For the dev scripts, put a copy of the PSK importer in `add-on-io-scene-psk-psa-v9_1_2/`
   (not tracked).
4. Build the add-on and install the zip (Edit → Preferences → Get Extensions → Install from Disk):
   ```
   blender --factory-startup --command extension build --source-dir finals_texturer --output-dir .
   ```
   `dev_tools/ft_quick_install.py` is a small dev add-on that reinstalls the newest zip from your checkout in one
   click (set its project folder in its preferences).
5. Optional, for **Export Now** and the shader tools: the .NET 10 SDK. Build the helper with
   `dotnet publish exporter/FtExporter.csproj -c Release -r win-x64 --self-contained false -p:DebugType=none -o finals_texturer/bin/exporter`.

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) first: it covers where things live and the conventions (units,
UV quirks, how materials are built) that are easy to get wrong.

## House rules
- **Universal rules, not per-skin hacks.** A fix must explain every affected skin. If a rule only fixes the
  reported item, it's probably the wrong rule. Check it against store images (`T_UI_*.png` next to each skin) and
  the regression set ([docs/TESTING.md](docs/TESTING.md)).
- **Prefer the game's compiled shader over guesses** for material formulas, and write the formula into the
  builder's docstring and [docs/MATERIAL_RULES.md](docs/MATERIAL_RULES.md).
- **Never commit game files**: no `.psk`, `.png`, `.json` exports, `.uasset`, textures or renders of them. The
  `.gitignore` covers the usual ones; double-check `git status` before committing. Data in
  `finals_texturer/data/` is generated metadata (names, paths, transforms) only.
- **Never commit secrets or personal paths**: the AES key stays in FModel's settings (the tools read it from there);
  dev scripts take paths from environment variables (`FT_EXPORTS`, `FT_BLENDER`, `SP`).
- **Don't write into the user's FModel export folder** from add-on code, except through the Export Now helper.
- Keep the UI the same in every tab (search → pickers → store image → status → buttons → options).

## Pull requests
- Branch from `main`; one topic per PR.
- If the add-on's behaviour changes, bump the version in **both** `finals_texturer/blender_manifest.toml` and
  `bl_info` in `finals_texturer/__init__.py` (the release workflow fails when they differ). A maintainer can do
  this when merging if you prefer.
- Describe what you checked: items imported, `check_items.py` numbers for shader changes, before / after
  screenshots next to the store image.
- Releases are made by the maintainers: pushing a new version to `main` publishes `v<version>` on GitHub.

## Commit identity
GitHub shows the email of every commit. If you don't want yours public, use your GitHub *noreply* address:
`git config user.email "<id>+<username>@users.noreply.github.com"` (shown under GitHub → Settings → Emails).

## Licence
By contributing you agree that your contribution is licensed under the GPL-3.0-or-later, like the rest of the
add-on (see [LICENSE](LICENSE)). Game assets belong to Embark Studios and are never part of this repository.
