"""Body cosmetics without a mesh: body paint, tattoos, makeup (face paint), eye colours.

In game such an item (CharacterCustomizationItem) has no VisualParts; its ActivatesMaterialParameters list
material instances with Behavior OverrideParameters and SlotNames (BaseBody = body skin, shader_head_shader =
head, shader_eyeLeft/Right_shader = eyes): their own parameter values are laid over the character's material
in those slots (M_Skin / M_Face builders read BodyPaint* / Tattoo*; the eye builder the iris values).

An item takes body slots (Slots: EBodySlot::BodyPaint / FacePaint / Tattoos / Eyes); wearing one takes off
whatever shares a slot with it (a full body paint also takes the FacePaint slot, so makeup replaces it).
The character's body armature remembers what it wears in "ft_cosmetics" ({DA path: [slots]}, Exports-relative),
the meshes get "ft_param_overlays" ({game slot name (lower): [MI paths]}) and are rebuilt.
"""

import json
import os

from .ue_assets import Log, asset_name, ref_path

PROP = "ft_cosmetics"
ORDER = ("Tattoos", "BodyPaint", "FacePaint", "Eyes")      # overlay order (later wins on shared parameters)


def _load(root, rel):
    try:
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    for e in data if isinstance(data, list) else [data]:
        if (e or {}).get("Type") == "CharacterCustomizationItem":
            return e.get("Properties") or {}
    return None


_cache = {}       # (path) → (mtime, result); the panel asks on every redraw


def read(root, rel):
    """{"slots": [...], "params": [(MI object path, [slot names])]} for a material-only cosmetic, else None."""
    path = os.path.join(root, rel)
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return None
    hit = _cache.get(path)
    if hit and hit[0] == mt:
        return hit[1]
    res = _read(root, rel)
    _cache[path] = (mt, res)
    return res


def _read(root, rel):
    props = _load(root, rel)
    if not props or props.get("VisualParts"):
        return None
    params = []
    for a in props.get("ActivatesMaterialParameters") or []:
        mi = ref_path(a.get("MaterialInstance"))
        if mi and "OverrideParameters" in (a.get("Behavior") or "OverrideParameters"):
            params.append((mi, [n for n in a.get("SlotNames") or [] if n]))
    if not params:
        return None
    slots = [s.split("::")[-1] for s in props.get("Slots") or []] or ["BodyPaint"]
    return {"slots": slots, "params": params}


def is_cosmetic(root, path):
    """True for a skin file that is a material-only body cosmetic."""
    if not path or not os.path.basename(path).startswith("DA_"):
        return False
    rel = os.path.relpath(path, root) if os.path.isabs(path) else path
    return read(root, rel) is not None


def worn(body):
    try:
        return dict(json.loads(body.get(PROP, "{}")))
    except (TypeError, ValueError):
        return {}


def _store(body, state):
    body[PROP] = json.dumps(state)


def equip(body, root, rel):
    """Wear a cosmetic (takes off whatever shares one of its body slots). Returns its slots."""
    rel = rel.replace("\\", "/")
    c = read(root, rel)
    if c is None:
        raise RuntimeError(f"{os.path.basename(rel)} isn't a body paint / tattoo / makeup / eye item")
    state = {k: v for k, v in worn(body).items() if not set(v) & set(c["slots"])}
    state[rel] = c["slots"]
    _store(body, state)
    return c["slots"]


def take_off(body, rel=None):
    """Take off one cosmetic (or all)."""
    state = worn(body)
    if rel is None:
        state = {}
    else:
        state.pop(rel.replace("\\", "/"), None)
    _store(body, state)


def overlays(body, root):
    """{game slot name (lower): [MI paths]} for everything the character wears, in ORDER."""
    out = {}
    items = sorted(worn(body).items(), key=lambda kv: min((ORDER.index(s) if s in ORDER else 9) for s in kv[1]))
    for rel, _slots in items:
        c = read(root, rel)
        for mi, names in (c or {}).get("params", []):
            for n in names:
                out.setdefault(n.lower(), []).append(mi)
    return out


def character_meshes(body):
    """The body's and head's meshes (Character tab parts) of the character this body armature belongs to."""
    import bpy
    from . import posing
    out = []
    for o in bpy.data.objects:
        if o.type == "MESH" and o.get("ft_char_role") in ("body", "head", "extra") and posing.target_of(o)[0] == body:
            out.append(o)
    return out


def apply(body, root, log=None):
    """Rebuild the character's skin / head / eye materials with what it wears. Returns the number of
    materials built."""
    from . import character, importer
    log = log or Log()
    face = body.get("ft_char_face") or next((o.get("ft_char_face") for o in character_meshes(body)
                                              if o.get("ft_char_face")), "")
    meshes = character_meshes(body)
    if not meshes or not face:
        raise RuntimeError("Body paint, tattoos, makeup and eye colours go on a character imported with the "
                           "Character tab: select its body, head or rig")
    face_path = os.path.join(root, face) if not os.path.isabs(face) else face
    ov = overlays(body, root)
    tags = character.tags_for(body.get("ft_char_class") or "M")
    done = 0
    for o in meshes:
        o["ft_param_overlays"] = ov
        done += importer.apply_to_object(o, root, face_path, log, tags=tags)
    names = ", ".join(asset_name(k).replace("DA_BodyCosmetics_", "") for k in worn(body)) or "nothing"
    log.info(f"{body.name} wears: {names}")
    return done


def textures_missing(root, rel, finder):
    """Texture folders of a cosmetic that aren't exported (for the status box)."""
    from .ue_assets import resolve_material
    c = read(root, rel)
    out = []
    for mi, _n in (c or {}).get("params", []):
        params = resolve_material(finder, object_path=mi, log=Log())
        for t in params.textures.values():
            if t and "/Characters/BodyCosmetics/" in t and not finder.has_texture(t):
                out.append(t.split(".")[0].rsplit("/", 1)[0])
    return out
