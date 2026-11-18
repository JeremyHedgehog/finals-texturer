# Architecture

How the add-on is put together: where things live, how an import flows, and the conventions every part
follows. For the per-shader formulas see [MATERIAL_RULES.md](MATERIAL_RULES.md); for how those formulas are
found and checked see [SHADER_PIPELINE.md](SHADER_PIPELINE.md) and [TESTING.md](TESTING.md).

## The big picture

The game's files are read **offline from an FModel export folder** (`Output/Exports`, set in the add-on
preferences). Nothing is read from the running game. An import:

1. finds the item's data asset (a skin `DA_*.json`, a face `DA_Head_*.json`, a weapon skin, a Blueprint);
2. reads which meshes it uses and which material goes in which slot (`ue_assets.read_skin`, `skin_parts`);
3. imports the meshes with the PSK importer (`importer.import_psk`), scales them to the chosen unit size;
4. for each material slot, walks the material chain MI → parent MI → … → master material and merges every
   level's parameters (`ue_assets.resolve_material`, leaf wins);
5. picks the builder for the master (`defaults.shader_type`) and rebuilds the material as Blender nodes
   (`builders.build_material`).

The **Export Now** helper (`exporter/`, C#) writes missing files into the Exports folder in FModel's own
format, so the add-on reads everything the same way whichever tool exported it.

## Code map (`finals_texturer/`)

| File | Role |
|---|---|
| `__init__.py` | Registration, tab switch, preferences (Exports folder, import size, update check), File > Import, the Finals sidebar panel (dispatches to the tab modules), Show Log / Rescan |
| `ue_assets.py` | **No bpy.** `AssetFinder` (UE object path → file in the export folder, name index cached per root), material chain resolve (`resolve_material`), parameter overlays (`overlay_instance`), master guessing for FModel's flattened material files (`guess_master`), skins (`find_skins`, `read_skin` incl. TagOverrides) |
| `defaults.py` | Built-in master defaults, shader-type detection (`_KNOWN`, `_EXACT`, `shader_type`), null textures |
| `nodes.py` | `NodeBuilder`: node helpers (mix / math / vmath / map_range…), sections → frames, layout |
| `groups.py` | Shared node groups (`VERSION`: bump it when a group changes so old files rebuild it) |
| `builders/` | One builder per game master family: `common.py` (`Ctx`: textures, UVs, parameters, output, generic fallback), `clothing.py`, `character.py`, `weapon.py`, `weapon_rank.py`, `objects.py`; `__init__.py` holds the `BUILDERS` table |
| `importer.py` | PSK import, unit scale, slot matching, `apply_to_object` (build every slot's material), `import_item` (an item from its skin DA), class fitting, socket placement |
| `character.py` | Character tab: faces, body + head import per class, head-follows-body constraints |
| `clothing_tab.py` | Clothing tab: category → item → skin pickers, export status, search, Import / Apply, attach to the selected character, body cosmetics |
| `cosmetics.py` | Body paint / tattoos / makeup / eye colours: wear / take off per body slot, parameter overlays |
| `weapons.py` | Weapons tab: weapon list, skins, assembly of parts on sockets |
| `objects_tab.py`, `blueprints.py` | Objects tab: catalogued objects assembled from Blueprints |
| `posing.py`, `poses_tab.py` | Posing tab: `.psa` reader, pose maths, Auto-Rig Pro controls, weapon in hand |
| `first_person.py` | Posing tab, First Person mode: the weapon's first-person animation on a pose rig built from the `.psa`'s master skeleton (helper bones carry it onto the character's bones whatever their orientation), weapon on `weapon_root`, `FP_Camera` on the camera bone, arms-only mask, Bake / Reset |
| `twist.py` | Forearm twist bones (`FT_twist_*`, driven by the hand's roll) and the forearm weight split, added when posing; Simplify merges them back |
| `rigging.py` | Rigging tab: simplify the game rig, attach pieces, Auto-Rig Pro Quick Rig |
| `shapekeys.py` | Body-type and item shape keys |
| `sockets.py` | Socket table (`data/sockets.json`) and static-mesh placement |
| `library.py`, `library_ui.py`, `names.py` | In-game names: the shipped list (`data/names.json.gz`) and a library rebuilt from the user's exports |
| `search.py`, `browse.py` | Search box per tab, store-image browser |
| `exporter.py` | Runs the bundled `ft_exporter` helper in the background |
| `curves.py` | Bakes curve atlases (`CA_*`) that have no image file |
| `updater.py` | Release check on GitHub and Update Now |
| `ui.py` | Shared panel pieces (status box, Copy Path, options fold-out, Check Setup) |
| `data/` | Generated catalogues shipped with the add-on (see below) |

`dev_tools/` holds development scripts (comparison renders, surveys, the compiled-shader pipeline) and
`exporter/` the Export Now helper. Neither is part of the add-on zip except the built helper in
`finals_texturer/bin/exporter` (built by the release workflow, not tracked).

## Conventions

### Units and scale
The game works in centimetres. The **Import Size** preference picks metres (×0.01, default) or game units.
The scale is applied to mesh and armature **data**, never to object scale: rigging and Auto-Rig Pro need
object scale 1. Objects remember `ft_unit_scale`; builders get `ctx.cm_per_unit` for parameters in game
units. Thresholds in fitting / rigging are relative to body size.

### UV conventions (important, easy to get wrong)
- UE's texture space has **v pointing down**; Blender's points up: game `v = 1 − y`.
- UE packs odd UV channels **swapped** into the pixel shader's interpolators (`TexCoords[i/2].zw = (v, u)`) on
  skinned meshes (clothing, characters, weapons). A shader that reads them straight therefore sees (v, u). Static
  meshes (props, objects, attachments on sockets) store channel 2 as (u, v). Spline meshes behave like skinned
  ones. Check which case a shader is in before porting a UV formula.
- Detail textures (texture arrays, hue noise, patterns) use **UV channel 2**; colour masks, normals, decals
  use channel 1 (Blender's first UV map).

### Material building
- A builder gets a `Ctx` (`builders/common.py`): `ctx.p` (resolved parameters: `s()` scalars, `v()` vectors,
  `t()` textures, `sw()` switches), `ctx.tex()` (image node by game path, cached; handles missing files, curve
  atlases, sRGB flags), `ctx.uv()` / `ctx.uv_channel(i)`, `ctx.bsdf()`, `ctx.finish()`.
- `ctx.section("Layer 2")` puts the following nodes in a labelled frame; `nb.layout()` arranges them.
- `ctx.param_color(name, value)` / `ctx.param_value(...)` expose a game parameter as a named node in the
  "Parameters (game values)" frame, for users to tweak.
- `ctx.finish()` inserts the **FT Adjust** group in front of every Principled BSDF (colour / surface / glow /
  opacity knobs, identity at defaults).
- Blender materials are keyed by their source file (`importer._material_for`): two game materials with the same
  name stay separate. Body cosmetics build a separate material "<name> + <cosmetic>".

### Adding a shader
1. Add an entry to `defaults.py` (`_KNOWN` prefix or `_EXACT` name → a new kind constant).
2. Write the builder in the right `builders/` file, ideally from the compiled shader
   ([SHADER_PIPELINE.md](SHADER_PIPELINE.md)), with the formula in its docstring.
3. Add the `BUILDERS` line in `builders/__init__.py`.
4. Check it ([TESTING.md](TESTING.md)) and write the rule into [MATERIAL_RULES.md](MATERIAL_RULES.md).

### Characters and attaching
- A face DA = head mesh + body; TagOverrides swap the body per class (Light / Heavy). The head's armature
  follows the body through Armature constraints ("Follow body").
- Character parts are stamped `ft_char_role` (body / head / extra), `ft_char_class`, `ft_char_gender`,
  `ft_char_face`. `posing.target_of(obj)` finds a character's body armature (and Auto-Rig Pro rig) from any part;
  only **selected** objects count (Blender keeps the last clicked object active after deselecting).
- Static (socket) meshes are placed by socket rest × LocalPosition/Rotation/Scale (`sockets.place`), then scaled
  to the class (`importer.fit_class`). They store `ft_socket` (the body bone) for attaching.
- Pieces remember where they were imported (`ft_import_matrix`); attaching moves unmoved pieces onto a moved /
  posed character.

### Shipped data (`finals_texturer/data/`)
Generated from the game files with scripts in `dev_tools/`; **regenerate after game updates**:

| File | Built by | Contents |
|---|---|---|
| `names.json.gz` | `dev_tools/shader_extract/build_names.py` (from a `Dump.cs` dump) | In-game names of every clothing item, outfit, weapon skin, face |
| `props.json` | `dev_tools/shader_extract/build_props.py` | Objects tab props catalogue |
| `poses.json` | `dev_tools/build_poses.py` | Posing tab weapon stances and MVP poses |
| `sockets.json` | `dev_tools/build_sockets.py` | Character skeleton sockets and bone transforms |

These hold names, paths and transforms only, never game assets.

## Things to know before changing code
- **Never write into the user's export folder** except through the Export Now helper; the store-image browser
  caches in the extension's user folder instead.
- FModel's mesh export **overwrites** full material `.json` files with a simplified format without the parent
  material. `resolve_material` recovers the master from the parameter names (`guess_master`) and the UI asks the
  user to re-save properties for those folders.
- Blender's Python can't open paths of 260 characters or more on Windows; the game's folder depth gets close.
- Dynamic `EnumProperty` item numbers must stay below 2^24 (they pass through a float in the UI).
- Fixes are **universal rules**: a change must explain every affected skin, not just the reported one.
