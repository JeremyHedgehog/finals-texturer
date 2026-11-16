"""Posing tab: pose a character with the game's own weapon stances and end-of-match (MVP) poses, and put
the weapon in its hands. Works on the raw game rig, the simplified one and an Auto-Rig Pro rig (the pose
then goes onto the ARP controls). The math is in posing.py.

The catalog (data/poses.json, built by dev_tools/build_poses.py from the game files) lists per weapon its
animation bundles (default + skin grips such as the sword's baseball bat) with their third-person stances,
and its MVP poses. A pose is one .psa (+ .json) under Animation/AnimationDatabase; Export Now fetches it.
"""

import json
import os
import zlib

import bpy
from bpy.props import BoolProperty, EnumProperty

from . import importer, posing, search, ui
from .ue_assets import Log

_FILE = os.path.join(os.path.dirname(__file__), "data", "poses.json")
NO_WEAPON = "NONE"
_ctx = {"root": lambda context: "", "report": None}
_data = {"cat": None}
_items_cache = {}
_items_keep = []


def _root(context):
    return _ctx["root"](context)


def clear_cache():
    _items_cache.clear()


def _num(key):
    return zlib.crc32(key.encode()) & 0xFFFFF


def catalog():
    if _data["cat"] is None:
        try:
            with open(_FILE, encoding="utf-8") as fh:
                _data["cat"] = json.load(fh)
        except (OSError, ValueError):
            _data["cat"] = {"weapons": {}, "other": []}
    return _data["cat"]


def weapons():
    """[(weapon id, name)] with poses, by name."""
    return sorted(((wid, w["name"]) for wid, w in catalog()["weapons"].items()), key=lambda x: x[1].lower())


def _pid(wid, path):
    return f"{wid}|{path}"        # the same animation serves several weapons (archetype stances)


def poses(wid):
    """[pose] of a weapon (NO_WEAPON: poses without one): {"id": weapon|path, "path": game path, "name",
    "group", "weapon"}."""
    if wid == NO_WEAPON:
        return [{"id": _pid(NO_WEAPON, p), "path": p, "name": n, "group": "", "weapon": ""}
                for n, p in catalog()["other"]]
    w = catalog()["weapons"].get(wid)
    if not w:
        return []
    out = []
    for gp, b in w["bundles"].items():
        grip = "" if b["name"] == "Default" else b["name"]
        for label, path in b["poses"]:
            out.append({"id": _pid(wid, path), "path": path, "name": f"{grip}: {label}" if grip else label,
                        "group": grip, "weapon": wid})
    for i, path in enumerate(w["mvp"]):
        name = "MVP pose" + (" (fallback)" if "Fallback" in path else f" {i + 1}" if i else "")
        out.append({"id": _pid(wid, path), "path": path, "name": name, "group": "MVP", "weapon": wid})
    return out


def pose_by_id(pid):
    wid = pid.split("|", 1)[0] if "|" in pid else ""
    return next((p for p in poses(wid) if p["id"] == pid), None) if wid else None


def pose_file(root, pose):
    return os.path.join(root, *pose["path"].split("/")) + ".psa" if root and pose else ""


def bundle_for_skin(wid, skin_id):
    """Grip name of the animation bundle a weapon skin uses ("" = default)."""
    w = catalog()["weapons"].get(wid) or {}
    gp = (w.get("skins") or {}).get(skin_id or "")
    b = (w.get("bundles") or {}).get(gp or "")
    return "" if not b or b["name"] == "Default" else b["name"]


# ─────────────────────────────────────────────────────────────────────────────
# Weapon in the hand
# ─────────────────────────────────────────────────────────────────────────────

def _weapon_tops(objs):
    return [o for o in objs if o.parent is None or o.parent not in objs]


def _selected_weapon(context, wid):
    """Top objects of a weapon of this kind among the selected objects (not yet in a hand)."""
    sel = [o for o in context.selected_objects if o.get("ft_weapon") == wid]
    if not sel:
        return []
    tops = set()
    for o in sel:
        while o.parent is not None and o.parent.get("ft_weapon") == wid:
            o = o.parent
        tops.add(o)
    return list(tops)


def _import_weapon(context, wid, log):
    """Import the pose's weapon: the Weapons tab's skin when it's the same weapon, else the default."""
    from . import weapons as wtab
    root = _root(context)
    w = wtab.weapon_by_id(root, wid)
    if w is None:
        log.warn(f"Weapon {wid} isn't in the export folder (export it in the Weapons tab first).")
        return []
    skin = None
    if getattr(context.scene, "ft_weapon", "") == wid:
        skin = wtab.skin_by_id(root, w, context.scene.ft_weapon_skin)
    if skin is not None and skin["id"] == wtab.DEFAULT_SKIN:
        skin = None
    objs = wtab.import_weapon(context, w, skin, log)
    return _weapon_tops(objs)


def pose_character(context, pose, attach_weapon, log):
    """Pose the selected character; returns (body, rig, message) or raises RuntimeError."""
    root = _root(context)
    body, rig = posing.find_target(context)
    if body is None:
        raise RuntimeError("Select a character first: its body, head, clothing or its Auto-Rig Pro rig")
    f = pose_file(root, pose)
    if not os.path.isfile(f):
        raise RuntimeError("This pose isn't exported yet: use Export Now")
    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    data = posing.apply(body, f, pose["path"], pose["weapon"], rig)
    msg = f"Posed '{body.name}'" + (" (Auto-Rig Pro controls)" if rig else "")
    held = posing.attached_weapons(body)
    for wid, objs in held.items():
        if wid != pose["weapon"]:
            posing.detach(objs)
            msg += f"; took the {wid.rsplit('/', 1)[-1]} out of the hand"
    if not pose["weapon"]:
        return body, rig, msg
    objs = held.get(pose["weapon"]) or _selected_weapon(context, pose["weapon"])
    if not objs and attach_weapon:
        objs = _import_weapon(context, pose["weapon"], log)
        if objs:
            msg += "; imported the weapon"
    if objs:
        posing.attach(body, objs, data)
        msg += "; weapon in the right hand"
    return body, rig, msg


# ─────────────────────────────────────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────────────────────────────────────

def _weapon_items(self, context):
    items = _items_cache.get("weapons")
    if items is None:
        items = [(wid, name, "", "TOOL_SETTINGS", _num(wid)) for wid, name in weapons()]
        items.append((NO_WEAPON, "No weapon", "Objective, support and snowball MVP poses", "USER", 1))
        _items_cache["weapons"] = items
        _items_keep.append(items)
    return items


def _pose_items(self, context):
    context = context or bpy.context
    sc = getattr(context, "scene", None)
    wid = getattr(sc, "ft_pose_weapon", NO_WEAPON) if sc else NO_WEAPON
    root = _root(context)
    key = (root, wid)
    hit = _items_cache.get(key)
    if hit is not None:
        return hit
    items = []
    for p in poses(wid):
        ok = os.path.isfile(pose_file(root, p))
        items.append((p["id"], p["name"], "Exported" if ok else "Not exported (Export Now)",
                      "CHECKMARK" if ok else "ERROR", _num(p["id"])))
    if not items:
        items.append(("NONE", "No poses", "", "ERROR", 0))
    _items_cache[key] = items
    _items_keep.append(items)
    return items


def _on_weapon(self, context):
    """Preselect the stance of the grip the Weapons tab's skin uses (e.g. baseball bat), else the first."""
    sc = context.scene
    items = _pose_items(self, context)
    grip = bundle_for_skin(sc.ft_pose_weapon, sc.ft_weapon_skin)         if getattr(sc, "ft_weapon", "") == sc.ft_pose_weapon else ""
    first = next((p["id"] for p in poses(sc.ft_pose_weapon) if p["group"] == grip), None)
    try:
        sc.ft_pose = first or items[0][0]
    except (TypeError, IndexError):
        pass


def _selected(context):
    return pose_by_id(context.scene.ft_pose)


class FT_OT_pose_apply(bpy.types.Operator):
    """Pose the selected character with the chosen pose (on its Auto-Rig Pro controls if a rig drives it)
    and put the weapon in its right hand: a selected weapon of that kind, the one already in the hand, or
    a newly imported one (option)"""
    bl_idname = "finals_texturer.pose_apply"
    bl_label = "Pose Selected"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        pose = _selected(context)
        if pose is None:
            self.report({"ERROR"}, "Pick a pose first")
            return {"CANCELLED"}
        log = Log()
        # importing the weapon selects it and makes a weapon part active; the character stays the
        # selection (Rigging's Simplify works on the active object)
        vl = context.view_layer
        active, selected = vl.objects.active, list(context.selected_objects)
        try:
            _body, _rig, msg = pose_character(context, pose, context.scene.ft_pose_attach, log)
        except (RuntimeError, ValueError, OSError) as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        finally:
            for o in context.selected_objects:
                o.select_set(False)
            for o in selected:
                if o.name in vl.objects:
                    o.select_set(True)
            if active is not None and active.name in vl.objects:
                vl.objects.active = active
        if log.lines:
            importer.write_log(log)            # weapon import details -> Show Log
        if log.warnings:
            first = next(l for l in log.lines if l.startswith("WARNING: "))[len("WARNING: "):]
            self.report({"WARNING"}, f"{msg}. {log.warnings} warning(s), first: {first} (Show Log for all)")
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


class FT_OT_pose_reset(bpy.types.Operator):
    """Put the selected character back in its rest pose (the weapon stays in the hand)"""
    bl_idname = "finals_texturer.pose_reset"
    bl_label = "Rest Pose"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        body, rig = posing.find_target(context)
        if body is None:
            self.report({"ERROR"}, "Select a character first")
            return {"CANCELLED"}
        posing.reset(body, rig)
        self.report({"INFO"}, f"'{body.name}' is in its rest pose")
        return {"FINISHED"}


def _find(context, query):
    q = query.lower().split()
    out = []
    for wid, wname in weapons() + [(NO_WEAPON, "No weapon")]:
        for p in poses(wid):
            text = f"{wname} {p['name']}"
            if all(t in text.lower() for t in q):
                out.append((p["id"], f"{wname}: {p['name']}" if wid != NO_WEAPON else p["name"],
                            wname, -1, "POSE_HLT"))
    return out


def _pick(context, ident):
    p = pose_by_id(ident)
    if p is None:
        return
    try:
        context.scene.ft_pose_weapon = p["weapon"] or NO_WEAPON
        context.scene.ft_pose = p["id"]
    except TypeError:
        pass


def export_paths(pose):
    return [pose["path"] + ".uasset"]


def _draw_options(col, context):
    col.prop(context.scene, "ft_pose_attach")


def draw(layout, context):
    sc = context.scene
    root = _root(context)
    search.draw(layout, context, "pose", "Search poses")
    col = layout.column(align=True)
    col.prop(sc, "ft_pose_weapon", text="Weapon")
    col.prop(sc, "ft_pose", text="Pose")
    pose = _selected(context)
    if pose is not None and root:
        ok = os.path.isfile(pose_file(root, pose))
        ui.draw_status(layout, 2 if ok else 0, "Exported" if ok else "Pose not exported",
                       export=export_paths(pose), what=pose["name"])
        if not ok:
            from . import exporter
            st = exporter.check() if exporter.bundled() else {}
            if st.get("ok") and st.get("natives") is False:
                layout.label(text="Poses need FModel to have run once (CUE4Parse-Natives)", icon="INFO")
    body, rig = posing.find_target(context)
    box = layout.box()
    if body is None:
        box.label(text="Select a character (body, head, clothing or ARP rig)", icon="INFO")
    else:
        box.label(text=f"Character: {body.name}", icon="ARMATURE_DATA")
        if rig is not None:
            box.label(text=f"Posed through Auto-Rig Pro: {rig.name}", icon="POSE_HLT")
        held = posing.attached_weapons(body)
        if held:
            box.label(text="In the hand: " + ", ".join(w.rsplit("/", 1)[-1] for w in held), icon="TOOL_SETTINGS")
        cur = body.get(posing.POSE_PROP, "")
        if cur:
            p = pose_by_id(_pid(body.get(posing.WEAPON_PROP) or NO_WEAPON, cur))
            box.label(text=f"Pose: {p['name'] if p else cur.rsplit('/', 1)[-1]}", icon="CHECKMARK")
    row = layout.row(align=True)
    row.scale_y = 1.3
    row.operator(FT_OT_pose_apply.bl_idname, icon="POSE_HLT")
    row.operator(FT_OT_pose_reset.bl_idname, icon="LOOP_BACK")
    ui.draw_options(layout, context, "POSES", lambda c: _draw_options(c, context))


CLASSES = (FT_OT_pose_apply, FT_OT_pose_reset)


def register(root_getter, report):
    _ctx.update(root=root_getter, report=report)
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.ft_pose_weapon = EnumProperty(name="Weapon", items=_weapon_items, update=_on_weapon,
                                                  description="Weapon whose poses to list")
    bpy.types.Scene.ft_pose = EnumProperty(
        name="Pose", items=_pose_items,
        description="Stances come from the weapon's animation set (skins with their own grip, e.g. the "
                    "baseball bat, have their own); MVP = the end-of-match pose. Check = exported")
    bpy.types.Scene.ft_pose_attach = BoolProperty(
        name="Import Weapon If Needed", default=True,
        description="When neither the hand nor the selection has this weapon, import it (the Weapons tab's "
                    "skin if it's the same weapon, else the default) and put it in the right hand")
    search.add_tab("pose", _find, _pick, "Weapon or pose name")


def unregister():
    search.remove_tab("pose")
    del bpy.types.Scene.ft_pose_attach
    del bpy.types.Scene.ft_pose
    del bpy.types.Scene.ft_pose_weapon
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
