# Material rules

What each game shader does, as rebuilt in `finals_texturer/builders/`. Rules marked **compiled** were read from
the game's compiled pixel shaders and checked texel by texel ([SHADER_PIPELINE.md](SHADER_PIPELINE.md));
**approximate** ones are best effort. Every rule was checked against store images (`T_UI_*.png`) and the
regression set. Don't "simplify" one away without re-checking: most exist because a simpler version was wrong
on some skins.

Notation: UE texture space (v down) unless it says Blender; `sat` = clamp to 0..1; `lerp(a, b, t)`.

## Clothing: layered masters (compiled)

`M_Character_Layered` (old generation) and `M_Character_8Layers_Master` (new), `builders/clothing.py`.

- **Layers**: count from the `UseLayerN` switches (often set by a parent `MI_Character_NLayers`). The layer of a
  texel = `round(OCM.B × 8)`.
- **Roughness** = mask texture (flat = 0.5) **+** offset (offsets can be negative).
- **Colour mask** = sequential lerps from white: → scheme ColorA by mask R, → ColorB by G, → ColorC by B (a later
  channel wins, not an average). Each weight × strength, strength = `sat(Base·sat(2(1−edge−crease)) + Edge·edge +
  Crease·crease)` from ColorMaskStrengths. Mask = ColorMask texture × `ColorMaskSwatch`.
- Then × `BaseColorOverlay.rgb` (alpha unused) × colour texture (lerp(1, tex) by Base/Edge/Crease TextureStrength).
  Edge / crease overlays are painted by `regionMask × overlayAlpha` (new: edge then crease; old: crease then edge).
- **Edge / crease masks** (`groups.region_masks`): edge `x = 1 − sat(OCM.R × (2c − 1))`, `x' = x(x + 1 − EdgeMask)`;
  crease `x = sat(2c)`, `x' = x(x + CreaseMask)`; mask = `sat(((1 + s)·Amount − x')/s)`, s = MaskSoftness. New gen
  × BreakupMask; old gen + BaseColor G/B wear term. `EdgeMaskThickness` doesn't shape them (it only bevels normals).
  Blend order of regions: new base → edge → crease, old base → crease → edge.
- **Colour scheme** (A vs A2…) = BaseColor texture R (old gen × `k_ColorSchemeBlend`).
- **New-gen pattern**: R of slice PatternID (UV1 × Tiling × PatternTiling) moves the colour-mask channels toward
  `PatternSwatch.rgb`, weight `PatternSwatchMask × PatternSwatch.a`. Old-gen pattern: PatternMask /
  PatternColorA–C (`_old_gen_pattern`).
- Hue variation = lerp toward the hue-noise colour by strength × 0.1. `CharacterDesaturation` greys at the end.
- **No AO in the base colour** (the game keeps OCM R in a separate buffer).
- Shade-as-cloth = view-dependent specular `lerp(spec, (1 − facing)·0.5, ShadeAsCloth)` ((1 − facing)² on the new
  master), no sheen.
- **Detail textures** (texture arrays, hue noise, patterns) use **UV channel 2** (50 cm per UV unit on every mesh);
  colour masks, normals, OCM and decals use channel 1.

### Decals
- Old gen, slots k = 1..8 (independent of layers): centre `(0.5 − R, 0.5 + G)`, size = B (fraction of UV),
  rotation A turns × −360°, rotate **then** scale (WidthRatio). `k_ColorOverride` 1 = recolour by brightness
  (white → ColorA, black → ColorB; masks `_M`/`_CM` by R), 0 = own colours. `k_LayerMask` bits (0/255 = all).
- New gen (compiled): centre `(0.5 − PositionU, 0.5 + PositionV)` in Blender UVs (U flipped), scale (`DecalSize`,
  × `DecalWidth` on V) **then** rotate by −DecalRotation turns; `DecalSize` = repeats across UV; `DecalTint` =
  recolour amount; alpha = coverage. "Complex decal": inside the decal × `DecalSurfaceBlend`, DecalMasks R/G/B
  override roughness / metal / cloth.
- Decal UVs wrap to 0..1 (gloves put each hand in another UV tile). Number decals: the game sets the number per
  player; the panel's "Number decals" value stands in.

## Clothing: other masters

- **Attachment** `M_CharacterAttachment` (compiled; earrings, hats, plushies, pets, gem nails…): colour = CR ×
  `ColorMultiply`, `ColorDesaturation` (Rec. 709 luminance); roughness CR.a × Roughness. NOH: normal RG; metal only
  with switch `ActivateMetallicTexture` = `sat(lerp(B, A, ActivateAlphaMetallic_s) × Metallic)`, else 0;
  `ActivateOpacity`: opacity = NOH.B (masked, dithered: coverage `sat((1.02·B − 0.0033)/0.83)`). B as ambient
  occlusion goes to the G-buffer only, **never** into the colour.
- **Nails** `M_CharacterNails_Base` (compiled): hand = `round(sat(v))` (≥ 0.5 = right) picks Left/Right ColorA/B;
  pattern UV `((uv − 0.5)·PatternTiling; x·(1 + MirrorLeftHand·(2·hand − 2)), y·PatternTilingY) + offset + 0.5`.
  Plain: t = Pattern R (sRGB-decoded unless its SRGB flag is false) → colour lerp(A, B, t), metal / roughness
  lerp(A, B, t). `UseColorTexture`: colour = lerp(A, 2·tex·B, tex.a), MetallicA, RoughnessA.
- **Watch** `M_Watch_Base` / `M_Watch_Digital` (compiled): UV × (uTiling, vTiling); CR × ColorMultiply + roughness
  A; NMH = normal RG, B = opacity (`NMH_BlueAsOpacity`) / occlusion / glow mask (`UseEmissive`), A = metal. Detail
  `Weapon_DTN` slice Detail_Type on UV2 × Detail_Tiling (d = alpha): colour × (1.3 − 0.6d), roughness ×
  (1 + s(2d − 1)), normal RG × (1 + s(2·detail − 1)). Digital: four digits HH MM from `NumbersFont` (cell =
  floor(4u), font u = (digit + x)/10, x = 4u − cell ± HourMinuteDistance), mask = smoothstep(SmoothstepMin, +0.05,
  R) paints TimeBaseColor / TimeEmissiveColor. Glow = lerp(EmissiveColor·Intensity·B, TimeEmissiveColor,
  digits) × 0.05.
- Glass (`M_CharacterGlass_*`, `M_Glass_Weapon_01`): colour / gradient tint, roughness, fresnel opacity
  (approximate; forward-translucent, not comparable). Feather: R grey × TintColor, A cut-out. Team colour shows the
  enemy colour by default. Hair cards `M_Hair_Metahuman_01`: approximate.

## Characters

- **Body** `M_Skin` (compiled): body/hands colour + normal by UV tile (U < 1 body, U > 1 arms/hands).
  `SkinDesaturation` greys the texture **before** `ColorMultiply` tints it. Roughness = Roughness ×
  lerp(normal-map alpha, 0.8, Schlick fresnel).
- **Face** `M_Face` (compiled): the normal map keeps the normal in R/G only (**B = roughness**). Roughness /
  specular per pixel from lip mask (Mask Texture B), zone mask, cavity (BaseColor A), micro cavity, stubble
  (`_face_surface`). Opacity = built-in `T_Face_NeckFade` R × FadeAmount, clipped like the game's masked mode
  (`map_range(fade, 0.333, 0.683)`), so the head fades into the shoulders without a patch.
- **Body paint, tattoos, makeup** (compiled, `M_Skin` and `M_Face`; `_body_layers` in `builders/character.py`):
  tile `x = u / Tiles + Placement.r`, `y = frac(v)`; the texture shows only inside `0 < x < 1`.
  `colour = skin × lerp(1, tattoo, inside) × sat(paint + 1 − sat(a + inside·BodyColorMultiplyNonMasked))` →
  lerp toward the tattoo by alpha·TattooColorOverride → lerp toward the paint by a·BodyColorOverride (a = paint
  alpha · inside). `BodySurfaceOverride`: roughness → paint data B, metal = data A. On `M_Skin`,
  `BodyPaintUV` / `TattooUV` pick a UV channel and **channels ≥ 1 are read swapped** (x = v, y = u) because the
  shader takes them as they arrive. `M_Face` uses UV channel 1 straight, no tiles.
- **How cosmetics apply**: a body-paint / tattoo / makeup / eye item has no mesh; its
  `ActivatesMaterialParameters` lay their own MI's values (`OverrideParameters`) over the character's material
  in slots `BaseBody`, `shader_head_shader`, `shader_eyeLeft/Right_shader`. One item per body slot
  (BodyPaint / FacePaint / Tattoos / Eyes).
- **Eyes** `M_EyeRefractive_2` (approximate, no refraction): iris disc of `Iris UV Radius`, IrisColor × hue shift
  × IrisHueTint × IrisBrightness, IrisSaturation; glow = iris colour × IrisEmissiveIntensity. Eyelashes: alpha
  cut-out, blended. Eye textures live under `/Game/Pioneer/…` (`Discovery/Content/Pioneer`).
- **Glitter skin** `M_Skin_Glitter` (compiled, with a glossier roughness by choice): flake normal, seed-based
  sparkle × (1 − N·V)⁴, palette emission ColorA→D.
- Heads are made for the Medium body; Light / Heavy skeletons are the Medium one scaled ×0.85 / ×1.15 about the feet.

## Weapons (`builders/weapon.py`, `weapon_rank.py`)

- **M_Weapon_Base_5Layers** (compiled): NWO = normal RG, **B = wear / cavity** (0.5 neutral), **A = variation**
  (no AO). Index on UV channel 2. `UseGradientTexture`: GradientMap at (index, NWO A) gives colour **and roughness
  (alpha)**. Edge-wear metal, Weapon_DTN detail (A darkens colour / scales roughness), dirt + grunge, curvature.
  Layers 1–4: mask = dot(ID, k_Mask) (+ empty ID with MaskBlack) minus edge wear. The graphic is painted **inside
  each layer** (weight `0k_GraphicWeight` × alpha), or by the ID texture with `UseIDMask`.
- **Graphic placement**: UV channel 2 × GraphicUVs.zw + xy in UE space; `DYNAMIC_RotateUVs` swaps axes;
  `Clamp_UV_X/Y` clamp per axis.
- **Curve atlases** (`CA_*`) are baked from their curve JSONs (`curves.py`) and sampled **clamped**; gradient
  lookups are Color Ramps (an image lookup driven by a pattern blurs at stripe edges on the GPU).
- `M_WeaponPreset_CR+NOM`: CR = colour + roughness A; NOM blue = AO / opacity / **emissive mask with "Use NEM"**,
  metal A. `M_WeaponText`: opacity AlphaTexture R × TextColor.
- Rank skins: Gold = layer shader; Platinum `M_LegendaryWeaponFresnel` (compiled view-angle rainbow bands);
  Diamond / Ruby / Emerald `M_Diamond_Weapon` (approximate: no parallax march or animated shimmer). Other weapon FX
  masters fall back to `WEAPON_FX` (approximate).

## Objects (`builders/objects.py`, compiled unless noted)

- Canisters `M_ATK_Canister_01`: lerp(2·CR·Secondary, 2·CR·ColorTint, NOH.B), metal NOH.A, glow, icon decal atlas.
- Cashout: overlay(CR, ColorTint), metal packed A, roughness CR.a; station detail normal / roughness / label decal;
  progress strips glow where UV.x < Progress.
- Arena kit `M_4MaterialsByUVOffset`: UV channel 2 tile picks one of four sub-materials; sponsor colour from
  `T_SponsorColorTable` (13 sponsors × primary / secondary).
- Props `M_Props_01` (+ cloth, flags, vegetation, trunks, trims, cables, decals); `M_2LayerBlend_01` approximate.
- Decal atlases on **static meshes read UV channel 2 as stored (u, v)**; spline meshes store it like skinned meshes.
- Textures whose **SRGB flag is false are read raw** (every builder); masks with the flag unset are sRGB in the
  game (applied to the nail pattern so far, `Ctx.tex(follow_srgb=True)`).

## Known gaps
Paint normals (BodyPaintData RG), eye refraction, vertex-animated pets (VAT), fur, LED screens, holograms, glitch
effects, diamond / leopard / gemstone looks, layered emissive, emotes. See the GitHub issues for what's being
worked on.
