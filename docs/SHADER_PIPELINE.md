# Reading the game's compiled shaders

Most builders are not guesses: their formulas come from the game's own compiled pixel shaders, read offline and
simulated on the item's textures. This is how to do that for a new shader. Method after **ChronoHax** (author
of finals_materials).

Everything here is read-only: it reads the game's pak files, never modifies them, and nothing of the game is
committed to the repository.

## Requirements
- The game installed, and **FModel** set up for it once (game folder, AES key, mappings file). The tools take
  these from FModel's settings.
- .NET 10 SDK, Python 3.11+ with `numpy` and `Pillow`, Blender 5.1, and the **Windows 10/11 SDK** (the tool
  finds its DirectX shader compiler `fxc.exe` under `C:\Program Files (x86)\Windows Kits\10\bin` for
  disassembly).
- An FModel export of the material's chain and textures (or Export Now / `exporter/` into a scratch folder).

## Settings
The tools read `FT_GAME_PAKS`, `FT_AES`, `FT_USMAP`, `FT_FMODEL_DATA` from the environment. Don't type the key
into files: run commands through

```
python -I dev_tools/shader_extract/ftenv.py <command> [args...]
```

which fills those in from `%APPDATA%\FModel\AppSettings.json` for that one process (never printed or saved).

## 1. Extract a material's shaders

```
python -I dev_tools/shader_extract/ftenv.py dotnet run --project dev_tools/shader_extract -c Release -- <out dir> \
    Discovery/Content/Discovery/Characters/Nails/M_CharacterNails_Base
```

Arguments are package paths (Exports-relative, no extension). Material **instances** work too and matter when
they set static switches: a switch makes its own shader permutation (e.g. `UseColorTexture` on nails,
`UseEmissive` on watch screens). Per material you get `<out>/<name>/`: every pixel and vertex shader (`.asm`),
`base_pass.asm` (the pixel shader that writes the G-buffer), `material_layout.json` (the uniform buffer layout and
preshaders) and `shaders.txt`.

## 2. Annotate

```
python -I dev_tools/shader_extract/annotate.py <out>/<name> <out>/<name>/base_pass.asm <master material .json>
```

Decodes the preshaders into parameter names for every component of the material uniform buffer (`cb2` / `cb3`
/ `cb4`) and names the texture registers, then writes `base_pass.annotated.asm`. Read the printed buffer map
first; it tells you which parameters the permutation uses at all.

**Texture names can be shifted.** The names are assigned in uniform-buffer order to the texture registers after
the last structured buffer. GPU-scene variants have a structured buffer at `t0`/`t1`, textures read only with
`ld` (dither noise) aren't material textures, and vertex-shader-only textures (vertex animation "Pos Tex" / "Rot
Tex") don't count. When the labels look wrong, go by the order of `sample` instructions and what they're used
for.

## 3. Read the formula
Useful facts about these shaders:
- G-buffer outputs: `o1` normal, `o2` = metallic / specular / roughness (x / y / z), `o3` = base colour (`o3.w` AO),
  `o4` = clear coat. Emissive goes to `o0`.
- Interpolators: `v2.xy` = UV channel 1, `v2.zw` = channel 2 **as packed** ((v, u) on skinned meshes, (u, v) on
  static meshes; see [ARCHITECTURE.md](ARCHITECTURE.md#uv-conventions-important-easy-to-get-wrong)), `v3`… more channels or
  vertex colour; check the input signature at the top of the file.
- Everything after the selection colour, dissolve, impact render target, thermal vision and distance fades is
  runtime gameplay: leave it out.
- `sample_b` = sample with mip bias (treat as a normal sample).

Write the result as plain maths in the builder's docstring, then build it with `NodeBuilder`.

## 4. Simulate and compare
`dev_tools/shader_extract/` has a numpy SM5 interpreter (`shadersim.py`) that runs the base pass on the real
textures (`weaponsim.py` builds the uniform buffer from the material instance). `bake_ours.py` bakes the add-on's
material into UV space in Blender, `compare_game.py` compares the two per texel.

The usual entry point is `check_items.py`, which does all of it for a list of items:

```
set FT_EXPORTS=<FModel Output/Exports or a scratch export root>
set FT_BLENDER=<path to blender.exe>
python -I dev_tools/shader_extract/ftenv.py python -I dev_tools/shader_extract/check_items.py <list file> <work dir>
```

List file, one item per line: `<mesh path under Characters> <skin folder | AUTO | NONE | skin file path> <name>`,
or `DA_Head_<…> <L|M|H> <name>` for a face. The output prints mean differences of base colour, roughness and metal
per material slot, and writes a side-by-side sheet `cmp_<name>.png`. Differences of **0.00x are a match**; the
compiled-shader builders are expected to land there.

Helpers:
- `bake_ours.py` honours `FT_SLOT_OVERRIDE="0=/Game/…/MI_X.MI_X"` to put a specific material on a slot (e.g. to
  check a body-paint material directly on the body mesh).
- `bake_ours.py --object <Objects path>` and `--face` modes exist for objects and characters.
- Translucent / forward-shaded materials (glass, eye shells) have no G-buffer output: judge them visually.

## Pitfalls seen so far
- An exact **0.5** difference usually means the textures aren't exported (both sides fall back to a default):
  check the PNGs exist before trusting a mismatch.
- A uniformly grey "game" sheet means the simulator didn't bind the textures (shifted registers, see above).
- Masks whose SRGB flag is unset are sRGB-decoded by the game.
- View-dependent terms (fresnel) differ between the head-on simulation and the bake; compare those visually.
