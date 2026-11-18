"""Clothing tab: pick an item by category → item → skin (or search by in-game
name), see what's exported, import it or apply the skin to selected meshes."""

import glob
import os
import re
import zlib

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from . import cosmetics, defaults, importer, library, library_ui, search, ui
from .ue_assets import AssetFinder, Log, asset_name, find_skins, read_skin, resolve_material

CHARS_REL = os.path.join("Discovery", "Content", "Discovery", "Characters")
# texture folders covered by Check Setup (shared by many items): not listed per item
SHARED_MARKERS = ("/MaterialLibrary/", "/EmbarkScript/", "/Heads/Shared/", "/Items/Shared/", "/Engine/",
                  "/Pioneer/", "/Textures/Debug/")

_ctx = {"root": lambda context: "", "report": None}
_cache = {}          # (kind, key) → list
_status = {}         # (root, skin path, class) → (level, text, groups)
_enum_keep = []      # every enum list handed to Blender (kept alive)


def _root(context):
    return _ctx["root"](context)


def clear_cache():
    _cache.clear()
    _status.clear()


def _pretty(folder):
    """'BikerLoosePants' → 'Biker Loose Pants'."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", folder).replace("_", " ")


# ── catalog ──────────────────────────────────────────────────────────────────

def _lib_entries():
    lib = library_ui._get_lib(bpy.context)
    return lib["entries"] if lib else []


def _entry_parts(e):
    """(category, item, skin) from a library entry's folder ('Casual', 'BasicTshirt', 'Cotton_X' or ''),
    or None. Any folder layout under Characters (see ui.split_char_folder)."""
    sp = ui.split_char_folder(e["folder"])
    return sp[:3] if sp else None


HIDDEN_CATS = ("Heads", "Body")          # faces / bodies: Character tab
SKIP_DIRS = {"Skins", "Shared", "Textures", "Materials"}


def _is_item_dir(d):
    try:
        files = os.listdir(d)
    except OSError:
        return False
    return "Skins" in files or any(f.endswith((".psk", ".pskx")) or (
        f.startswith("DA_") and f.endswith(".json") and not f.startswith(("DA_MetaData", "DA_Persistence")))
        for f in files)


def _exported_items(cat_dir):
    """{item name: dir} of exported items in a category folder: '<cat>/Assets/<item>' (one level) and
    every other layout ('<cat>/<item>', '<cat>/Nails/<item>', …; up to three levels, not inside items'
    Skins / Textures / Materials folders)."""
    found = {}
    if not os.path.isdir(cat_dir):
        return found
    stack = [(cat_dir, "", 0)]
    while stack:
        d, rel, depth = stack.pop()
        for name in sorted(os.listdir(d)):
            sub = os.path.join(d, name)
            if name in SKIP_DIRS or not os.path.isdir(sub):
                continue
            if not rel and name == "Assets":
                for it in sorted(os.listdir(sub)):
                    di = os.path.join(sub, it)
                    if it not in SKIP_DIRS and os.path.isdir(di) and _is_item_dir(di):
                        found[it] = di
                continue
            r = f"{rel}/{name}" if rel else name
            if _is_item_dir(sub):
                found.setdefault(r, sub)
            if depth < 2:
                stack.append((sub, r, depth + 1))
    return found


def categories(root):
    """Category folders under Characters: exported ones plus those in the name list."""
    key = ("cats", root)
    if key not in _cache:
        base = os.path.join(root, CHARS_REL)
        out = set()
        if os.path.isdir(base):
            out |= {c for c in os.listdir(base) if os.path.isdir(os.path.join(base, c, "Assets"))}
        out |= {p[0] for p in (_entry_parts(e) for e in _lib_entries() if e["kind"] == "item") if p}
        _cache[key] = sorted(out - set(HIDDEN_CATS))
    return _cache[key]


def items(root, cat):
    key = ("items", root, cat)
    if key not in _cache:
        if not cat or cat not in categories(root):
            return []        # no / unknown category: never walk the whole Characters folder
        found = _exported_items(os.path.join(root, CHARS_REL, cat))
        for e in _lib_entries():
            sp = ui.split_char_folder(e["folder"]) if e["kind"] == "item" else None
            if sp and sp[0] == cat and sp[1] not in found:
                found[sp[1]] = os.path.join(root, *sp[3].split("/"))
        _cache[key] = [{"id": f"{cat}/{it}", "dir": d, "name": _pretty(it.replace("/", " / "))}
                       for it, d in sorted(found.items())]
    return _cache[key]


def _lib_names(context):
    """Skin folder (relative, lower case) → library entry, if a library exists."""
    lib = library_ui._get_lib(context)
    if not lib:
        return {}
    key = ("libnames", id(lib))
    if key not in _cache:
        _cache[key] = {e["folder"].replace("\\", "/").lower(): e for e in lib["entries"] if e["kind"] == "item"}
    return _cache[key]


def skins(context, root, item):
    key = ("skins", root, item["id"])
    if key not in _cache:
        names = _lib_names(context)
        out = []
        for name, path in find_skins(item["dir"]):
            folder = os.path.relpath(os.path.dirname(path), root).replace("\\", "/").lower()
            e = names.get(folder)
            label = library.display_name(e) if e else name
            if e and name != "Default" and e.get("name"):
                label = f"{e['name']} ({name})"
            out.append({"id": os.path.relpath(path, root).replace("\\", "/"), "path": path, "name": label,
                        "entry": e})
        # skins in the name list that aren't exported yet
        have = {s["id"].lower() for s in out}
        cat, it = item["id"].split("/", 1)
        for e in _lib_entries():
            p = _entry_parts(e)
            if e["kind"] != "item" or not p or (p[0], p[1]) != (cat, it) or not e.get("da"):
                continue
            if e["da"].replace("\\", "/").lower() in have:
                continue
            label = f"{e['name']} ({p[2] or 'Default'})" if e.get("name") else library.display_name(e)
            out.append({"id": e["da"].replace("\\", "/"), "path": os.path.join(root, e["da"]), "name": label,
                        "entry": e})
        _cache[key] = out
    return _cache[key]


def _num(ident, used):
    n = zlib.crc32(ident.encode("utf-8")) % 1_000_000
    while n in used:
        n = (n + 1) % 1_000_000
    used.add(n)
    return n


def _cat_items(self, context):
    context = context or bpy.context
    root = _root(context)
    used = set()
    out = [(c, _pretty(c), c, "FILE_FOLDER", _num(c, used)) for c in categories(root)]
    if not out:
        out = [("NONE", "No clothing found", "Export Characters/<category> from FModel", "ERROR", 0)]
    _enum_keep.append(out)
    return out


def _item_items(self, context):
    context = context or bpy.context
    root = _root(context)
    used = set()
    out = [(it["id"], it["name"], it["id"], "MATCLOTH", _num(it["id"], used))
           for it in items(root, context.scene.ft_cloth_cat)]
    if not out:
        out = [("NONE", "No items", "", "ERROR", 0)]
    _enum_keep.append(out)
    return out


def _skin_items(self, context):
    context = context or bpy.context
    root = _root(context)
    it = selected_item(context)
    used = set()
    out = []
    cls = context.scene.ft_char_class
    for s in skins(context, root, it) if it else []:
        level = status(root, s["path"], cls)[0]
        out.append((s["id"], s["name"], s["id"], ui.READY_ICON.get(level, "DOT"), _num(s["id"], used)))
    if not out:
        out = [("NONE", "No skins", "", "ERROR", 0)]
    _enum_keep.append(out)
    return out


def _on_cat(self, context):
    its = items(_root(context), context.scene.ft_cloth_cat)
    if its:
        try:
            context.scene.ft_cloth_item = its[0]["id"]
        except TypeError:
            pass


def _on_item(self, context):
    it = selected_item(context)
    ss = skins(context, _root(context), it) if it else []
    if ss:
        try:
            context.scene.ft_cloth_skin = ss[0]["id"]
        except TypeError:
            pass


def selected_item(context):
    sc = context.scene
    root = _root(context)
    return next((it for it in items(root, sc.ft_cloth_cat) if it["id"] == sc.ft_cloth_item), None)


def selected_skin(context):
    it = selected_item(context)
    if it is None:
        return None
    return next((s for s in skins(context, _root(context), it) if s["id"] == context.scene.ft_cloth_skin), None)


# ── status: what's exported ──────────────────────────────────────────────────

def _folder(object_path):
    path = object_path.split(".")[0].rsplit("/", 1)[0]
    return path.split("/Discovery/", 1)[1] if "/Discovery/" in path else path


def _rel_folder(root, file_path):
    rel = os.path.relpath(os.path.dirname(file_path), root).replace("\\", "/")
    return rel.split("Content/Discovery/", 1)[-1]


class _QuietLog(Log):
    def info(self, msg):
        pass

    def warn(self, msg):
        pass


def status(root, skin_path, cls="M"):
    """(level, text, groups) for a skin: 2 = meshes + textures exported, 1 = some textures
    missing or material files need re-saving, 0 = a mesh isn't exported."""
    key = (root, skin_path, cls)
    hit = _status.get(key)
    if hit is not None:
        return hit
    if not os.path.isfile(skin_path):
        sp = ui.split_char_folder(os.path.dirname(os.path.relpath(skin_path, root)))
        item_dir = os.path.join(root, *sp[3].split("/")) if sp else os.path.dirname(skin_path)
        res = _status[key] = (0, "Not exported", {"Item": [ui.game_folder(root, item_dir)]})
        return res
    log = _QuietLog()
    finder = importer.make_finder(root, os.path.dirname(skin_path), log)
    parts = importer.skin_parts(finder, skin_path) if os.path.basename(skin_path).startswith("DA_") else []
    meshes = [_folder(p) for p, _k, _s in parts if not importer.find_part_file(finder, p, cls)]
    textures, flat = [], []
    try:
        overrides = read_skin(finder, skin_path)[0]
    except Exception:
        overrides = {}
    for mat in dict.fromkeys(v for v in overrides.values() if v):
        params = resolve_material(finder, object_path=mat, log=log)
        if params.flat_json:
            flat.append(_rel_folder(root, params.flat_json))
        for t in params.textures.values():
            if not t or any(m in t for m in SHARED_MARKERS) or asset_name(t).lower() in defaults.NULL_TEXTURES:
                continue
            if not finder.has_texture(t) and not finder.array_slice_count(t):
                textures.append(_folder(t))
    groups = ui.split_folders(meshes, textures, flat)
    if not parts and os.path.basename(skin_path).startswith("DA_"):
        missing = cosmetics.textures_missing(root, os.path.relpath(skin_path, root), finder) \
            if cosmetics.is_cosmetic(root, skin_path) else None
        if missing:
            res = (1, f"{len(set(missing))} texture folder(s) not exported", ui.split_folders(textures=missing))
        elif missing is not None:
            res = (2, "Body cosmetic: select a character and click Apply to Character", groups)
        else:
            res = (2, "No mesh (changes the body or another item)", groups)
    elif meshes:
        res = (0, f"{len(meshes)} mesh(es) not exported", groups)
    elif textures:
        res = (1, f"{len(set(textures))} texture folder(s) not exported", groups)
    elif flat:
        res = (1, "Material files need re-saving (see below)", groups)
    else:
        res = (2, "Exported", {})
    _status[key] = res
    return res


def export_paths(root, skin_path):
    """What Export Now hands the exporter for a skin: the skin (it brings the files next to it and
    everything it references) and the item folder's own files (base meshes, textures, materials)."""
    from . import exporter
    if not skin_path:
        return []
    norm = skin_path.replace("\\", "/")
    item_dir = norm.rsplit("/Skins/", 1)[0] if "/Skins/" in norm else os.path.dirname(norm)
    return [exporter.rel_path(root, skin_path), exporter.top_level(root, item_dir)]


def store_image(root, skin):
    if skin is None:
        return ""
    e = skin.get("entry")
    if e and e.get("image"):
        p = os.path.join(root, e["image"] + ".png")
        if os.path.isfile(p):
            return p
    hits = sorted(glob.glob(os.path.join(os.path.dirname(skin["path"]), "T_UI_*.png")))
    return hits[0] if hits else ""


# ── search ───────────────────────────────────────────────────────────────────

def _find(context, query):
    lib = library_ui._get_lib(context)
    if not lib:
        return []
    root = _root(context)
    out = []
    for e in library.search(lib, query, limit=60):
        idx = lib["entries"].index(e)
        if e["kind"] == "outfit":
            out.append((f"outfit:{idx}", library.display_name(e), "Outfit", -1, "OUTLINER_COLLECTION"))
        else:
            sp = ui.split_char_folder(e["folder"])
            detail = (sp[1].rsplit("/", 1)[-1] + (f" · {sp[2]}" if sp[2] else "")) if sp else e["folder"]
            ready = library_ui._status(root, lib, e)[0]
            out.append((f"item:{idx}", library.display_name(e), detail, ready, "MATCLOTH"))
    return out


def _image(context, ident):
    """(local PNG, game path) of a search result's store image (browse.py)."""
    lib = library_ui._get_lib(context)
    try:
        e = lib["entries"][int(ident.split(":", 1)[1])] if lib else None
    except (ValueError, IndexError):
        e = None
    img = (e or {}).get("image") or ""
    if not img:
        return "", ""
    root = _root(context)
    local = os.path.join(root, *img.split("/")) + ".png" if root else ""
    return local, img


def _pick(context, ident):
    sc = context.scene
    kind, idx = ident.split(":", 1)
    lib = library_ui._get_lib(context)
    if not lib:
        return
    e = lib["entries"][int(idx)]
    sc.ft_cloth_outfit = idx if kind == "outfit" else ""
    if kind == "outfit" or not e.get("da"):
        return
    sp = ui.split_char_folder(e["folder"])
    if not sp or sp[0] not in categories(_root(context)):
        return
    cat, item = sp[0], f"{sp[0]}/{sp[1]}"
    try:
        sc.ft_cloth_cat = cat
        sc.ft_cloth_item = item
        sc.ft_cloth_skin = e["da"].replace("\\", "/")
    except TypeError:
        pass


# ── attach to the selected character ────────────────────────────────────────

def attach_target(context):
    """The body armature of the selected character (any part of it) if "Attach to selected character"
    is on, else None."""
    if not getattr(context.scene, "ft_cloth_attach", False):
        return None
    from . import posing
    return posing.find_target(context)[0]      # selected objects only


def _import_class(context, body):
    """(class, gender) to import with: the character's own when attaching, else the Options choice."""
    if body is not None:
        from . import rigging
        cls, gender, _m = rigging.character_settings(body, context.scene)
        return cls, gender
    return context.scene.ft_char_class, ""


def _alive(objs):
    out = []
    for o in objs:
        try:
            o.name
        except ReferenceError:        # the piece's own armature, removed by Attach
            continue
        out.append(o)
    return out


def _attach(context, body, objs, log):
    """Bind freshly imported pieces to the character like the Rigging tab's Attach (weights onto the body
    bones, hats on their bone, shape keys); they then follow its pose / ARP rig."""
    from . import rigging
    if body is None or not objs:
        return
    rp = context.scene.ft_rig
    msgs = []
    rigging.HIP_FOLLOW = rp.hip_follow
    rigging.attach_pieces(body, _alive(objs), rp.delete_src, msgs.append)
    if rp.auto_shapekeys:
        rigging.apply_shape_keys(body, context.scene, msgs.append)
    for m in msgs:
        (log.warn if m.startswith("WARNING") else log.info)(m)
    log.info(f"Attached to {body.name}")


class _Gender:
    def __init__(self, gender):
        self.g = gender

    def __enter__(self):
        importer.GENDER_OVERRIDE["value"] = self.g

    def __exit__(self, *a):
        importer.GENDER_OVERRIDE["value"] = ""


# ── operators ────────────────────────────────────────────────────────────────

def _wear(op, context, root, paths):
    """Put material-only body cosmetics (body paint, tattoos, makeup, eye colours) on the selected character."""
    from . import posing
    body = posing.find_target(context)[0]
    if body is None:
        op.report({"ERROR"}, "Select the character (its body, head, clothing or rig) to put this on")
        return {"CANCELLED"}
    log = Log()
    try:
        for p in paths:
            slots = cosmetics.equip(body, root, os.path.relpath(p, root))
            log.info(f"{os.path.basename(p)} → {', '.join(slots)}")
        done = cosmetics.apply(body, root, log)
    except RuntimeError as e:
        op.report({"ERROR"}, str(e))
        return {"CANCELLED"}
    _ctx["report"](op, log, done)
    return {"FINISHED"}


class FT_OT_cosmetic_off(bpy.types.Operator):
    """Take this body cosmetic off the selected character"""
    bl_idname = "finals_texturer.cosmetic_off"
    bl_label = "Take Off"
    bl_options = {"REGISTER", "UNDO"}
    path: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        from . import posing
        body = posing.find_target(context)[0]
        if body is None:
            return {"CANCELLED"}
        cosmetics.take_off(body, self.path or None)
        log = Log()
        try:
            done = cosmetics.apply(body, _root(context), log)
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        _ctx["report"](self, log, done)
        return {"FINISHED"}


def _draw_worn(layout, context):
    """What the selected character wears (body paint, tattoos, makeup, eyes), with take-off buttons."""
    from . import posing
    body = posing.find_target(context)[0]
    state = cosmetics.worn(body) if body is not None else {}
    if not state:
        return
    lib = library_ui._get_lib(context)
    by_da = {(e.get("da") or "").replace("\\", "/").lower(): e for e in (lib["entries"] if lib else [])}
    box = layout.box()
    box.label(text=f"{body.name} wears:", icon="BRUSH_DATA")
    for rel, slots in state.items():
        e = by_da.get(rel.lower())
        name = library.display_name(e) if e else os.path.basename(rel)[:-5]
        row = box.row(align=True)
        row.label(text=f"{', '.join(slots)}: {name}")
        row.operator(FT_OT_cosmetic_off.bl_idname, text="", icon="X").path = rel


class FT_OT_cloth_import(bpy.types.Operator):
    """Import the chosen item with the chosen skin (and, if set in Options, the other meshes the skin uses)"""
    bl_idname = "finals_texturer.cloth_import"
    bl_label = "Import"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        root = _root(context)
        s = selected_skin(context)
        if s is None:
            self.report({"ERROR"}, "Pick an item and a skin first")
            return {"CANCELLED"}
        if not os.path.isfile(s["path"]):
            folder = status(root, s["path"])[2].get("Item", [""])[0]
            self.report({"ERROR"}, f"Not exported yet: export {folder} in FModel (Copy Path), then Rescan")
            return {"CANCELLED"}
        if cosmetics.is_cosmetic(root, s["path"]):
            return _wear(self, context, root, [s["path"]])
        log = Log()
        sc = context.scene
        body = attach_target(context)
        cls, gender = _import_class(context, body)
        try:
            with _Gender(gender):
                if os.path.basename(s["path"]).startswith("DA_"):
                    objs = importer.import_item(s["path"], root, log, whole_skin=sc.ft_cloth_whole, lod_hint=cls)
                else:
                    # lone material skin (MI_*.json): import the item's mesh and apply it
                    it = selected_item(context)
                    mesh = _item_mesh(it["dir"], cls)
                    if not mesh:
                        self.report({"ERROR"}, "The item's mesh isn't exported (see the status box)")
                        return {"CANCELLED"}
                    objs = importer.import_psk(mesh, log)
                    importer.fit_class(objs, mesh, cls, log)
                    for o in objs:
                        if o.type == "MESH":
                            importer.apply_to_object(o, root, s["path"], log)
                    from .rigging import mark_imported
                    mark_imported(objs)
            _attach(context, body, objs, log)
            objs = _alive(objs)
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        _status.clear()
        _ctx["report"](self, log, sum(1 for o in objs if o.type == "MESH"))
        return {"FINISHED"} if objs else {"CANCELLED"}


def _item_mesh(item_dir, cls):
    files = sorted(f for f in os.listdir(item_dir) if f.endswith((".psk", ".pskx")))
    pref = [f for f in files if re.search(rf"_{cls}\.pskx?$", f)]
    return os.path.join(item_dir, (pref or files)[0]) if files else ""


class FT_OT_cloth_apply(bpy.types.Operator):
    """Rebuild the selected meshes' materials with the chosen skin"""
    bl_idname = "finals_texturer.cloth_apply"
    bl_label = "Apply to Selected"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(o.type == "MESH" for o in context.selected_objects)

    def execute(self, context):
        s = selected_skin(context)
        if s is None:
            self.report({"ERROR"}, "Pick an item and a skin first")
            return {"CANCELLED"}
        log = Log()
        done = 0
        for o in context.selected_objects:
            if o.type == "MESH":
                done += importer.apply_to_object(o, _root(context), s["path"], log)
        _ctx["report"](self, log, done)
        return {"FINISHED"}


class FT_OT_cloth_outfit(bpy.types.Operator):
    """Import every piece of the outfit picked in the search results"""
    bl_idname = "finals_texturer.cloth_outfit"
    bl_label = "Import Outfit"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        sc = context.scene
        lib = library_ui._get_lib(context)
        if not lib or not sc.ft_cloth_outfit:
            self.report({"ERROR"}, "Pick an outfit in the search results first")
            return {"CANCELLED"}
        root = _root(context)
        e = lib["entries"][int(sc.ft_cloth_outfit)]
        pieces = [p for p in library.outfit_items(lib, e) if p and p.get("da") and p.get("meshes")]
        log = Log()
        log.info(f"Outfit {library.display_name(e)}: {len(pieces)} piece(s) with meshes")
        objs = []
        body = attach_target(context)
        cls, gender = _import_class(context, body)
        try:
            with _Gender(gender):
                for p in pieces:
                    objs += importer.import_item(os.path.join(root, p["da"]), root, log,
                                                 whole_skin=sc.ft_cloth_whole, lod_hint=cls)
            _attach(context, body, objs, log)
            objs = _alive(objs)
            looks = [os.path.join(root, p["da"]) for p in library.outfit_items(lib, e)
                     if p and p.get("da") and not p.get("meshes")]
            looks = [p for p in looks if os.path.isfile(p) and cosmetics.is_cosmetic(root, p)]
            if looks and body is not None:
                for p in looks:
                    cosmetics.equip(body, root, os.path.relpath(p, root))
                cosmetics.apply(body, root, log)
            elif looks:
                log.info(f"{len(looks)} body paint / makeup piece(s): select a character to put them on")
        except RuntimeError as err:
            self.report({"ERROR"}, str(err))
            return {"CANCELLED"}
        _ctx["report"](self, log, sum(1 for o in objs if o.type == "MESH"))
        return {"FINISHED"}


# ── panel ────────────────────────────────────────────────────────────────────

def _outfit_status(context, lib, e):
    root = _root(context)
    meshes = []
    for p in library.outfit_items(lib, e):
        if p and p.get("da"):
            for path in importer.missing_meshes(root, os.path.join(root, p["da"])):
                meshes.append(_folder(path))
    level, text = library_ui._status(root, lib, e)
    return level, text, ui.split_folders(meshes)


def _draw_options(col, context):
    sc = context.scene
    col.prop(sc, "ft_cloth_whole")
    col.prop(sc, "ft_cloth_attach")
    row = col.row()
    row.prop(sc, "ft_char_class", expand=True)
    row.enabled = attach_target(context) is None      # the character's own class wins
    col.prop(sc, "ft_number", text="Number decals")
    from . import _prefs
    p = _prefs(context)
    if p:
        col.prop(p, "import_size", text="Size")
    col.operator("finals_texturer.import_psk", text="Import from File…", icon="FILEBROWSER")
    col.operator(library_ui.FT_OT_lib_rescan.bl_idname, text="Rebuild Name Library", icon="FILE_REFRESH")


def draw(layout, context):
    sc = context.scene
    root = _root(context)
    lib = library_ui._get_lib(context)
    if lib:
        search.draw(layout, context, "cloth", "Search by in-game or file name")
    else:
        row = layout.row(align=True)
        row.label(text="Search needs the name library", icon="INFO")
        row.operator(library_ui.FT_OT_lib_rescan.bl_idname, text="Build", icon="FILE_REFRESH")
    if sc.ft_cloth_outfit and lib:
        e = lib["entries"][int(sc.ft_cloth_outfit)]
        box = layout.box()
        box.label(text=f"Outfit: {library.display_name(e)}", icon="OUTLINER_COLLECTION")
        level, text, groups = _outfit_status(context, lib, e)
        pieces = [os.path.join(root, p["da"]) for p in library.outfit_items(lib, e) if p and p.get("da")]
        ui.draw_status(box, level, text, groups, export=[x for p in pieces for x in export_paths(root, p)],
                       what=library.display_name(e))
        box.operator(FT_OT_cloth_outfit.bl_idname, icon="IMPORT")
    col = layout.column(align=True)
    col.prop(sc, "ft_cloth_cat", text="Category")
    col.prop(sc, "ft_cloth_item", text="Item")
    col.prop(sc, "ft_cloth_skin", text="Skin")
    s = selected_skin(context)
    if s is not None:
        ui.draw_preview(layout, store_image(root, s))
        level, text, groups = status(root, s["path"], sc.ft_char_class)
        ui.draw_status(layout, level, text, groups, export=export_paths(root, s["path"]),
                       what=os.path.basename(s["path"]))
    body = attach_target(context)
    cosmetic = s is not None and os.path.isfile(s["path"]) and cosmetics.is_cosmetic(root, s["path"])
    if body is not None and not cosmetic:
        layout.label(text=f"Import attaches to {body.name}", icon="LINKED")
    row = layout.row(align=True)
    if cosmetic:
        row.operator(FT_OT_cloth_import.bl_idname, text="Apply to Character", icon="BRUSH_DATA")
    else:
        row.operator(FT_OT_cloth_import.bl_idname, icon="IMPORT")
    row.operator(FT_OT_cloth_apply.bl_idname, icon="MATERIAL")
    img = store_image(root, s)
    op = row.operator(ui.FT_OT_show_image.bl_idname, text="", icon="IMAGE_DATA")
    op.path = img
    _draw_worn(layout, context)
    ui.draw_options(layout, context, "CLOTHING", lambda c: _draw_options(c, context))


CLASSES = (FT_OT_cloth_import, FT_OT_cloth_apply, FT_OT_cloth_outfit, FT_OT_cosmetic_off)


def register(root_getter, report):
    _ctx.update(root=root_getter, report=report)
    for c in CLASSES:
        bpy.utils.register_class(c)
    S = bpy.types.Scene
    S.ft_cloth_cat = EnumProperty(name="Category", items=_cat_items, update=_on_cat,
                                  description="Clothing category (folder under Characters)")
    S.ft_cloth_item = EnumProperty(name="Item", items=_item_items, update=_on_item,
                                   description="Item (folder under the category)")
    S.ft_cloth_skin = EnumProperty(name="Skin", items=_skin_items,
                                   description="Skin. Check = exported, i = some textures missing or "
                                               "material files need re-saving, warning = a mesh isn't exported")
    S.ft_cloth_whole = BoolProperty(name="Import whole skin", default=True,
                                    description="Also import the other meshes the skin uses (pins, hoods, …)")
    S.ft_cloth_outfit = StringProperty(options={"SKIP_SAVE"})
    S.ft_cloth_attach = BoolProperty(
        name="Attach to selected character", default=True,
        description="When any part of a character (body, head, clothing, armature) is selected, import the "
                    "item in its class and gender and bind it to its body right away (like Rigging > Attach): "
                    "it follows the character's position, pose and Auto-Rig Pro rig")
    search.add_tab("cloth", _find, _pick, "In-game, skin or file name; typos are OK", image=_image)


def unregister():
    search.remove_tab("cloth")
    for p in ("ft_cloth_cat", "ft_cloth_item", "ft_cloth_skin", "ft_cloth_whole", "ft_cloth_outfit",
              "ft_cloth_attach"):
        delattr(bpy.types.Scene, p)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
