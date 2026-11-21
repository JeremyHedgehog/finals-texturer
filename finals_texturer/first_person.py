"""First Person (a mode of the Posing tab): the game's first-person view, hands holding, aiming and inspecting
the weapon, with a camera on the game's first-person camera bone.

The animations come from the weapon's animation bundles (data/poses.json "fp", built by
dev_tools/build_poses.py; skins with their own set, e.g. the Akimbanana, have theirs) and are exported as .psa
by Export Now like the stances. They carry the game's full master skeleton, including bones the body doesn't
have (weapon_root, camera). The animation plays on a "pose rig" made from that skeleton with the PSK
importer's own code; helper bones carry it onto the character's bones whatever their orientation (Simplify
Body Rig re-aims them), the weapon follows weapon_root, and a dual weapon's second gun follows weapon_mag.
Unlike the stances these can play (inspects): pick a frame, then Bake. Needs the PSK/PSA importer
(io_scene_psk_psa).
"""

import copy
import math
import os

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty
from mathutils import Matrix, Vector

from . import importer, posing, poses_tab, ui
from .ue_assets import Log
from .twist import TWIST_PREFIX, add_forearm_twist, character_meshes

POSE_RIG_PREFIX = "FPRig_"
HELPER_PREFIX = "FPH_"
RIG_PROP = "finals_pose_rig"          # on the body: name of its first-person pose rig
CONSTRAINT = "FinalsPose"
FP_CAMERA = "FP_Camera"
FP_MASK = "FP Arms only"
FP_GROUP = "FP_ArmsOnly"
ARM_BONES = ("upperarm_", "lowerarm_", "hand_", "thumb_", "index_", "middle_", "ring_", "pinky_", TWIST_PREFIX)
DUAL_BONES = ("weapon_mag",)          # dual weapons (.50 Akimbo): the second gun hangs on weapon_mag

_ctx = {"root": lambda context: "", "report": None}
_items_keep = []
_items_cache = {}


# ─────────────────────────────────────────────────────────────────────────────
# Pose rig
# ─────────────────────────────────────────────────────────────────────────────

def _psk_psa():
    import addon_utils
    for mod in ("bl_ext.user_default.io_scene_psk_psa", "io_scene_psk_psa"):
        try:
            addon_utils.enable(mod, default_set=True)
        except Exception:
            continue
        try:
            return __import__(mod + ".psk.importer", fromlist=["import_psk", "PskImportOptions"])
        except ImportError:
            continue
    raise RuntimeError("The PSK/PSA importer (io_scene_psk_psa) isn't installed or enabled.")


def _scale_of(arm):
    """Finals Texturer bakes the import size into bones: compare a known bone length to game units."""
    b = arm.data.bones.get("upperarm_r") or arm.data.bones.get("upperarm_l")
    c = None
    for ch in (b.children if b else []):
        if ch.name.startswith("lowerarm"):
            c = ch
    if b and c:
        game_len = 28.3          # upperarm -> lowerarm on the Medium body, in game cm
        return (c.head_local - b.head_local).length / game_len
    return 1.0


class _FakePsk:
    def __init__(self, bones):
        self.bones = bones


def build_pose_rig(context, psa_path, name, scale=1.0):
    """Armature of the .psa's full skeleton (rest pose), with its animation applied."""
    from psk_psa_py.psa.reader import PsaReader
    imp = _psk_psa()
    with open(psa_path, "rb") as fh:
        bones = [copy.copy(b) for b in PsaReader(fh).bones]
    opts = imp.PskImportOptions()
    opts.should_import_mesh = False
    opts.bone_length = 5.0
    prev_active = context.view_layer.objects.active
    if prev_active is not None and prev_active.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    rig = imp.import_psk(_FakePsk(bones), context, name, opts).armature_object
    rig["finals_pose_scale"] = scale             # character's import size (1 = game cm, 0.01 = metres)
    bpy.ops.object.mode_set(mode="OBJECT")
    rig.show_in_front = False
    rig.display_type = "WIRE"
    rig.hide_render = True
    for o in context.selected_objects:
        o.select_set(False)
    rig.select_set(True)
    context.view_layer.objects.active = rig
    if abs(scale - 1.0) > 1e-4:
        # bake the size into the bones (object scale would be copied onto the character's bones)
        rig.scale = (scale, scale, scale)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    before = set(bpy.data.actions)
    # no scale keys: additive animations (inspects) store scale as an offset (+1), which read as absolute
    # doubles the whole body
    bpy.ops.psa.import_all(filepath=psa_path, translation_scale=scale, should_write_scale_keys=False)
    acts = [a for a in bpy.data.actions if a not in before]
    if acts:
        rig.animation_data_create()
        rig.animation_data.action = acts[0]
        try:
            rig.animation_data.action_slot = acts[0].slots[0]
        except (AttributeError, IndexError):
            pass
    if prev_active is not None:
        context.view_layer.objects.active = prev_active
    return rig, (acts[0] if acts else None)


def _add_helper_bones(context, rig, arm, names):
    """One helper bone per character bone, child of the matching game bone: it sits on the game bone's
    joint and moves with it, but points the way the character's bone points at rest (Simplify re-aims
    bones; the original orientations are kept in ft_rest_mats)."""
    context.view_layer.update()
    to_rig = rig.matrix_world.inverted() @ arm.matrix_world
    prev = context.view_layer.objects.active
    for o in context.selected_objects:
        o.select_set(False)
    rig.select_set(True)
    context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    eb = rig.data.edit_bones
    stored = arm.get("ft_rest_mats") or {}
    plan = []
    for name in names:
        src = arm.data.bones[name]
        cur = src.matrix_local.to_quaternion()
        vals = stored.get(name)
        orig = Matrix([list(vals[i * 4:i * 4 + 4]) for i in range(4)]).to_quaternion() if vals else cur
        reaim = orig.inverted() @ cur
        game_rot = to_rig.to_quaternion() @ eb[name].matrix.to_quaternion()
        plan.append((name, eb[name].head.copy(), game_rot @ reaim, max(src.length, 1e-4)))
    for name, head, rot, length in plan:
        h = eb.new(HELPER_PREFIX + name)
        h.head, h.tail = (0, 0, 0), (0, length, 0)
        h.matrix = Matrix.LocRotScale(head, rot, None)
        h.use_deform = False
    for name, *_ in plan:
        h = eb[HELPER_PREFIX + name]
        h.parent = eb[name]
        h.use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")
    for name in names:
        b = rig.data.bones.get(HELPER_PREFIX + name)
        if b is not None:
            b.hide = True
    rig.select_set(False)
    if prev is not None:
        context.view_layer.objects.active = prev


def is_posed(arm):
    return arm is not None and bool(arm.get(RIG_PROP))


def remove_pose(arm):
    """Drop a first-person pose: the body's constraints, links of weapons to the pose rig, the rig."""
    old = arm.get(RIG_PROP)
    rig = bpy.data.objects.get(old) if old else None
    for pb in arm.pose.bones:
        for c in [c for c in pb.constraints if c.name == CONSTRAINT]:
            pb.constraints.remove(c)
    if rig is not None:
        for o in bpy.data.objects:
            for c in [c for c in o.constraints if c.name == CONSTRAINT and getattr(c, "target", None) == rig]:
                o.constraints.remove(c)
            if o.type == "ARMATURE" and o is not arm:
                for pb in o.pose.bones:
                    for c in [c for c in pb.constraints
                              if c.name.startswith(CONSTRAINT) and getattr(c, "target", None) == rig]:
                        pb.constraints.remove(c)
        bpy.data.objects.remove(rig)
    arm.pop(RIG_PROP, None)


def apply_pose(context, arm, psa_path, weapon=None):
    """Pose `arm` (a Finals Texturer body armature) with the .psa; attach `weapon` (its armature)."""
    remove_pose(arm)
    for key in (posing.POSE_PROP, posing.WEAPON_PROP):    # a Posing-tab stance would come back after Simplify
        arm.pop(key, None)
    name = POSE_RIG_PREFIX + os.path.splitext(os.path.basename(psa_path))[0]
    rig, action = build_pose_rig(context, psa_path, name, _scale_of(arm))
    rig.matrix_world = arm.matrix_world.copy()
    rig.parent = arm.parent
    for c in arm.users_collection:
        if rig.name not in c.objects:
            c.objects.link(rig)
    for c in list(rig.users_collection):
        if c not in arm.users_collection:
            c.objects.unlink(rig)
    names = [b.name for b in arm.data.bones if b.name in rig.data.bones]
    _add_helper_bones(context, rig, arm, names)
    for name in names:
        c = arm.pose.bones[name].constraints.new("COPY_TRANSFORMS")
        c.name = CONSTRAINT
        c.target, c.subtarget = rig, HELPER_PREFIX + name
    arm[RIG_PROP] = rig.name
    add_forearm_twist(context, arm)
    if weapon is not None:
        attach_weapon(context, rig, weapon)
    if action is not None:
        context.scene.frame_set(int(action.frame_range[0]))
    return rig, len(names)


# ─────────────────────────────────────────────────────────────────────────────
# Weapon
# ─────────────────────────────────────────────────────────────────────────────

def _weapon_scale(weapon, char_scale):
    """Object scale that gives the weapon the character's import size (game cm or metres)."""
    meshes = [o for o in weapon.children_recursive if o.type == "MESH"]
    if not meshes:
        return 1.0
    cur = weapon.matrix_world.to_scale()[0] or 1.0
    pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
    length = max(max(p[i] for p in pts) - min(p[i] for p in pts) for i in range(3)) / cur
    weapon_units = 0.01 if length < 5.0 else 1.0      # guns are 30-200 cm long
    return char_scale / weapon_units


def _is_dual(weapon):
    return posing.is_dual(weapon)


def link_weapon_bones(rig, weapon, enable=True):
    """Drive the weapon's second-gun bone from the pose rig (dual weapons only)."""
    if weapon is None or weapon.type != "ARMATURE":
        return 0
    for pb in weapon.pose.bones:
        for c in [c for c in pb.constraints
                  if c.name.startswith(CONSTRAINT) or c.name == posing.SECOND_GUN_CONSTRAINT]:
            pb.constraints.remove(c)
    if not enable or rig is None or not _is_dual(weapon):
        return 0
    n = 0
    for name in DUAL_BONES:
        if name in weapon.pose.bones and name in rig.pose.bones:
            pb = weapon.pose.bones[name]
            pb.matrix_basis = Matrix.Identity(4)
            for kind in ("COPY_LOCATION", "COPY_ROTATION"):      # not scale: the weapon may use another size
                c = pb.constraints.new(kind)
                c.name = CONSTRAINT + " " + kind.split("_")[1].title()
                c.target, c.subtarget = rig, name
            n += 1
    return n


def attach_weapon(context, rig, weapon, socket="weapon_root"):
    """Weapon armature's own weapon_root bone onto the pose rig's weapon_root bone."""
    if socket not in rig.pose.bones:
        socket = "prop_01" if "prop_01" in rig.pose.bones else None
    if socket is None:
        return False
    for c in [c for c in weapon.constraints if c.name == CONSTRAINT]:
        weapon.constraints.remove(c)
    weapon.parent = None
    context.view_layer.update()
    target = rig.matrix_world @ rig.pose.bones[socket].matrix
    wb = weapon.data.bones.get("weapon_root")
    own = wb.matrix_local if wb else Matrix.Identity(4)
    s = _weapon_scale(weapon, rig.get("finals_pose_scale", 1.0))
    weapon.matrix_world = target @ Matrix.Diagonal((s, s, s, 1.0)) @ own.inverted()
    context.view_layer.update()
    c = weapon.constraints.new("CHILD_OF")
    c.name = CONSTRAINT
    c.target, c.subtarget = rig, socket
    c.inverse_matrix = target.inverted()
    link_weapon_bones(rig, weapon)
    return True


def _hold_second_gun(body, weapon):
    """After a bake: keep the second gun where it is and make it follow the left hand (like the Posing tab)."""
    pb = weapon.pose.bones.get(posing.SECOND_GUN)
    if pb is None or posing.LEFT_HAND not in body.pose.bones:
        return
    c = pb.constraints.new("CHILD_OF")
    c.name = posing.SECOND_GUN_CONSTRAINT
    c.target, c.subtarget = body, posing.LEFT_HAND
    if hasattr(c, "set_inverse_pending"):
        c.set_inverse_pending = False
    c.inverse_matrix = (body.matrix_world @ body.pose.bones[posing.LEFT_HAND].matrix).inverted() @ weapon.matrix_world
    bpy.context.view_layer.update()


def bake(context, arm, weapon):
    """Turn the borrowed pose (current frame) into a normal, editable pose; the weapon goes on hand_r."""
    context.view_layer.update()
    mats = {pb.name: pb.matrix.copy() for pb in arm.pose.bones}
    wmat = weapon.matrix_world.copy() if weapon else None
    gun_mats = {}
    if weapon is not None and weapon.type == "ARMATURE":
        gun_mats = {pb.name: pb.matrix.copy() for pb in weapon.pose.bones
                    if any(c.name.startswith(CONSTRAINT) for c in pb.constraints)}
    remove_pose(arm)
    for pb in sorted(arm.pose.bones, key=lambda p: len(p.parent_recursive)):    # parents first
        if pb.name.startswith(TWIST_PREFIX):
            continue                               # driven by the hand's roll
        pb.matrix = mats[pb.name]
        context.view_layer.update()
    if weapon is not None:
        for c in [c for c in weapon.constraints if c.name == CONSTRAINT]:
            weapon.constraints.remove(c)
        for name, m in gun_mats.items():
            weapon.pose.bones[name].matrix = m
        weapon.parent = arm
        weapon.parent_type = "BONE"
        weapon.parent_bone = posing.WEAPON_BONE
        weapon.matrix_parent_inverse = Matrix.Identity(4)
        context.view_layer.update()
        weapon.matrix_world = wmat
        context.view_layer.update()
        if _is_dual(weapon) and gun_mats:
            _hold_second_gun(arm, weapon)


# ─────────────────────────────────────────────────────────────────────────────
# Files, camera, arms-only mask
# ─────────────────────────────────────────────────────────────────────────────

def clear_cache():
    _items_cache.clear()


def _root(context):
    return _ctx["root"](context)


def held_weapon(context, body, wid):
    """Top objects of the weapon to hold: already in the hand, else selected (Posing tab rules)."""
    if body is not None:
        objs = posing.attached_weapons(body).get(wid)
        if objs:
            return objs
    for o in bpy.data.objects:                           # still linked to a first-person pose rig
        if o.get("ft_weapon") == wid and any(c.name == CONSTRAINT for c in o.constraints):
            return [o]
    return poses_tab._selected_weapon(context, wid)


def _skin_of(context, wid, objs):
    """Skin id of the weapon in use (held / selected), else the Weapons tab's when it's the same weapon."""
    for o in objs or []:
        if o.get("ft_weapon_skin"):
            return o["ft_weapon_skin"]
    sc = context.scene
    return sc.ft_weapon_skin if getattr(sc, "ft_weapon", "") == wid else ""


def animations(wid, skin=""):
    """[{"path", "name"}] first-person animations of a weapon: its skin's own set first (if any), then the
    default, then other grips (e.g. Finger Guns). Each game path once."""
    w = poses_tab.catalog()["weapons"].get(wid) or {}
    fp = w.get("fp") or {}
    own = (w.get("fp_skins") or {}).get(skin or "")
    order = ([own] if own in fp else []) + [gp for gp, b in fp.items() if b["name"] == "Default" and gp != own] +         [gp for gp, b in fp.items() if b["name"] != "Default" and gp != own]
    out, seen = [], set()
    for gp in order:
        b = fp[gp]
        for label, path in b["anims"]:
            if path in seen:
                continue
            seen.add(path)
            out.append({"path": path, "name": label if b["name"] == "Default" else f"{b['name']}: {label}"})
    return out


def anim_file(root, path):
    return os.path.join(root, *path.split("/")) + ".psa" if root and path else ""


def _current_skin(context):
    sc = context.scene
    body, _rig = posing.find_target(context)
    return _skin_of(context, sc.ft_pose_weapon, held_weapon(context, body, sc.ft_pose_weapon))


def _fp_items(self, context):
    context = context or bpy.context
    sc = getattr(context, "scene", None)
    wid = getattr(sc, "ft_pose_weapon", "") if sc else ""
    root = _root(context)
    skin = _current_skin(context) if sc else ""
    key = (root, wid, skin)
    hit = _items_cache.get(key)
    if hit is not None:
        return hit
    items = []
    for a in animations(wid, skin):
        ok = os.path.isfile(anim_file(root, a["path"]))
        items.append((a["path"], a["name"], "Exported" if ok else "Not exported (Export Now)",
                      "CHECKMARK" if ok else "ERROR", poses_tab._num(a["path"])))
    if not items:
        items.append(("NONE", "No first-person animations", "", "ERROR", 0))
    _items_cache[key] = items
    _items_keep[:] = _items_keep[-16:] + [items]
    return items


def _anim_name(context, path):
    return next((a["name"] for a in animations(context.scene.ft_pose_weapon, _current_skin(context))
                 if a["path"] == path), path.rsplit("/", 1)[-1])


def _arm_mask(objs, enable):
    """Arms only: hide every vertex not weighted to the arms / hands (body, head, clothing alike)."""
    for o in objs:
        if o.type != "MESH":
            continue
        for m in [m for m in o.modifiers if m.name == FP_MASK]:
            o.modifiers.remove(m)
        if not enable:
            vg = o.vertex_groups.get(FP_GROUP)
            if vg is not None:
                o.vertex_groups.remove(vg)
            continue
        groups = {vg.index for vg in o.vertex_groups if vg.name.startswith(ARM_BONES)}
        vg = o.vertex_groups.get(FP_GROUP) or o.vertex_groups.new(name=FP_GROUP)
        vg.remove(list(range(len(o.data.vertices))))
        keep = [v.index for v in o.data.vertices if sum(g.weight for g in v.groups if g.group in groups) > 0.5]
        if keep:
            vg.add(keep, 1.0, "REPLACE")
        mm = o.modifiers.new(FP_MASK, "MASK")
        mm.vertex_group = FP_GROUP


def setup_camera(context, rig, fov_deg):
    """Camera on the game's first-person camera bone (bone X forward, Z up), following it."""
    bone = "camera" if "camera" in rig.pose.bones else "camera_root"
    if bone not in rig.pose.bones:
        return None
    cam = bpy.data.objects.get(FP_CAMERA)
    if cam is None or cam.type != "CAMERA":
        cam = bpy.data.objects.new(FP_CAMERA, bpy.data.cameras.new(FP_CAMERA))
        context.scene.collection.objects.link(cam)
    for c in list(cam.constraints):
        cam.constraints.remove(c)
    cam.parent = None
    k = rig.get("finals_pose_scale", 1.0)
    cam.data.lens_unit = "FOV"
    cam.data.sensor_fit = "HORIZONTAL"           # the game's FOV is horizontal
    cam.data.angle = math.radians(fov_deg)
    cam.data.clip_start = 2.0 * k
    cam.data.clip_end = 20000.0 * k
    context.view_layer.update()
    bw = rig.matrix_world @ rig.pose.bones[bone].matrix
    # camera axes in the bone's frame: right = -Y, up = +Z, back = -X
    corr = Matrix(((0, 0, -1, 0), (-1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
    loc, rot, _s = bw.decompose()
    cam.matrix_world = Matrix.LocRotScale(loc, rot, None) @ corr
    c = cam.constraints.new("CHILD_OF")
    c.name = CONSTRAINT
    c.target, c.subtarget = rig, bone
    c.inverse_matrix = bw.inverted()
    context.scene.camera = cam
    return cam


def leave(arm):
    """Everything First Person put on the character: pose, arms-only mask, camera link."""
    _arm_mask(character_meshes(arm), False)
    remove_pose(arm)
    cam = bpy.data.objects.get(FP_CAMERA)
    if cam is not None:
        for c in list(cam.constraints):
            cam.constraints.remove(c)


def _character(context):
    body, _rig = posing.find_target(context)
    return body


def _fp_fov_update(self, context):
    cam = bpy.data.objects.get(FP_CAMERA)
    if cam is not None and cam.type == "CAMERA":
        cam.data.angle = math.radians(context.scene.ft_fp_fov)


def has_mask(arm):
    return arm is not None and any(m.name == FP_MASK for o in character_meshes(arm) for m in o.modifiers)


def _fp_mask_update(self, context):
    arm = _character(context)
    if is_posed(arm) or has_mask(arm):              # also after Bake (the pose rig is gone, the mask isn't)
        _arm_mask(character_meshes(arm), context.scene.ft_fp_arms_only)


# ─────────────────────────────────────────────────────────────────────────────
# Operators and tab
# ─────────────────────────────────────────────────────────────────────────────

def _weapon_armature(objs):
    for o in objs or []:
        if o.type == "ARMATURE" and "weapon_root" in o.data.bones:
            return o
    return None


def _fp_weapon(context):
    """The weapon armature First Person is using for the current weapon (in the hand / linked / selected)."""
    body, _rig = posing.find_target(context)
    return _weapon_armature(held_weapon(context, body, context.scene.ft_pose_weapon))


class FT_OT_fp_setup(bpy.types.Operator):
    """First-person view: pose the selected character with the weapon's first-person animation, put the
    weapon in its hands (the one in the hand, a selected one, or a newly imported one) and a camera on the
    game's first-person camera bone"""
    bl_idname = "finals_texturer.fp_setup"
    bl_label = "Set Up First Person"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        sc = context.scene
        body, _rig = posing.find_target(context)
        wid = sc.ft_pose_weapon
        if body is None:
            self.report({"ERROR"}, "Select a character first: its body, head, clothing or its Auto-Rig Pro rig")
            return {"CANCELLED"}
        if not wid or wid == poses_tab.NO_WEAPON:
            self.report({"ERROR"}, "Pick a weapon first")
            return {"CANCELLED"}
        path = sc.ft_fp_anim if sc.ft_fp_anim != "NONE" else ""
        f = anim_file(_root(context), path)
        if not f or not os.path.isfile(f):
            self.report({"ERROR"}, "This animation isn't exported yet: use Export Now")
            return {"CANCELLED"}
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        vl = context.view_layer
        active, selected = vl.objects.active, list(context.selected_objects)
        log = Log()
        objs = held_weapon(context, body, wid)
        if not objs and sc.ft_pose_attach:
            objs = poses_tab._import_weapon(context, wid, log)
        weapon = _weapon_armature(objs)
        if objs and weapon is None:
            self.report({"WARNING"}, "This weapon has no weapon_root bone: it isn't put in the hands")
        if weapon is not None:
            posing.detach(objs)                      # out of the Posing tab's hand parenting

        def restore_selection():
            for o in context.selected_objects:
                o.select_set(False)
            for o in selected:
                if o.name in vl.objects:
                    o.select_set(True)
            if active is not None and active.name in vl.objects:
                vl.objects.active = active
        restore_selection()
        frame = sc.frame_current
        try:
            rig, n = apply_pose(context, body, f, weapon)
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        restore_selection()
        act = rig.animation_data.action if rig.animation_data else None
        if act is not None and act.frame_range[1] - act.frame_range[0] > 2:
            sc.frame_set(int(min(max(frame, act.frame_range[0]), act.frame_range[1])))
        cam = setup_camera(context, rig, sc.ft_fp_fov)
        _arm_mask(character_meshes(body), sc.ft_fp_arms_only)
        if cam is not None and context.area and context.area.type == "VIEW_3D":
            context.area.spaces.active.region_3d.view_perspective = "CAMERA"
        if log.lines:
            importer.write_log(log)
        clear_cache()
        self.report({"INFO"}, f"First person: {_anim_name(context, path)} ({n} bones posed)")
        return {"FINISHED"}


class FT_OT_fp_bake(bpy.types.Operator):
    """Make the current frame's first-person pose a normal pose you can edit (removes the link to the
    animation; the weapon then follows hand_r)"""
    bl_idname = "finals_texturer.fp_bake"
    bl_label = "Bake Pose"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm, _rig = posing.find_target(context)
        if not is_posed(arm):
            self.report({"ERROR"}, "Select a character set up with Set Up First Person")
            return {"CANCELLED"}
        cam = bpy.data.objects.get(FP_CAMERA)
        cmat = cam.matrix_world.copy() if cam is not None else None
        bake(context, arm, _fp_weapon(context))
        if cam is not None:
            for c in list(cam.constraints):
                cam.constraints.remove(c)
            cam.matrix_world = cmat
        self.report({"INFO"}, "Pose baked: edit it in Pose Mode (the weapon follows hand_r)")
        return {"FINISHED"}


class FT_OT_fp_clear(bpy.types.Operator):
    """Leave first person: show the whole body again, unlink the camera and put the character back in
    its rest pose (to keep a pose and only show the body, untick Arms only instead)"""
    bl_idname = "finals_texturer.fp_clear"
    bl_label = "Reset First Person"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm, _rig = posing.find_target(context)
        if arm is None:
            return {"CANCELLED"}
        w = _fp_weapon(context)
        leave(arm)
        for pb in arm.pose.bones:
            pb.matrix_basis = Matrix.Identity(4)
        if w is not None:
            for c in [c for c in w.constraints if c.name == CONSTRAINT]:
                w.constraints.remove(c)
            link_weapon_bones(None, w, False)
            for pb in w.pose.bones:
                if pb.name in DUAL_BONES:
                    pb.matrix_basis = Matrix.Identity(4)
        return {"FINISHED"}


def draw(layout, context):
    sc = context.scene
    root = _root(context)
    col = layout.column(align=True)
    col.prop(sc, "ft_pose_weapon", text="Weapon")
    col.prop(sc, "ft_fp_anim", text="View")
    path = sc.ft_fp_anim if sc.ft_fp_anim != "NONE" else ""
    if path and root:
        ok = os.path.isfile(anim_file(root, path))
        ui.draw_status(layout, 2 if ok else 0, "Exported" if ok else "Animation not exported",
                       export=[path + ".uasset"], what=_anim_name(context, path))
    body, _rig = posing.find_target(context)
    box = layout.box()
    if body is None:
        box.label(text="Select a character (body, head, clothing or ARP rig)", icon="INFO")
    else:
        box.label(text=f"Character: {body.name}", icon="ARMATURE_DATA")
        w = _fp_weapon(context)
        if w is not None:
            box.label(text=f"Weapon: {w.name}", icon="TOOL_SETTINGS")
        if is_posed(body):
            rig = bpy.data.objects.get(body[RIG_PROP])
            act = rig.animation_data.action if rig and rig.animation_data else None
            if act is not None and act.frame_range[1] - act.frame_range[0] > 2:
                box.prop(sc, "frame_current", text=f"Frame ({int(act.frame_range[0])}-{int(act.frame_range[1])})")
        if has_mask(body):
            box.label(text="Whole body hidden: untick Arms only to show it", icon="HIDE_ON")
    row = layout.row(align=True)
    row.prop(sc, "ft_fp_fov")
    row.prop(sc, "ft_fp_arms_only", toggle=True)
    row = layout.row(align=True)
    row.scale_y = 1.3
    row.operator(FT_OT_fp_setup.bl_idname, icon="VIEW_CAMERA")
    row = layout.row(align=True)
    row.operator(FT_OT_fp_bake.bl_idname, icon="CHECKMARK")
    row.operator(FT_OT_fp_clear.bl_idname, text="Reset", icon="LOOP_BACK")
    layout.label(text="Gloves / sleeves: attach them first (Rigging tab)", icon="INFO")


CLASSES = (FT_OT_fp_setup, FT_OT_fp_bake, FT_OT_fp_clear)


def register(root_getter, report):
    _ctx.update(root=root_getter, report=report)
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.ft_pose_mode = EnumProperty(
        name="Mode", items=(("STANCE", "Stances", "Pose the whole character: the game's weapon stances and MVP poses",
                             "POSE_HLT", 0),
                            ("FIRST_PERSON", "First Person", "The game's first-person view: hands holding, aiming and "
                             "inspecting the weapon, with the game's camera", "VIEW_CAMERA", 1)))
    bpy.types.Scene.ft_fp_anim = EnumProperty(
        name="View", items=_fp_items,
        description="The weapon's first-person animation (hold, aim, crouch, sprint, inspect); skins with "
                    "their own set list theirs first. Check = exported")
    bpy.types.Scene.ft_fp_fov = IntProperty(name="FOV", default=80, min=40, max=130, update=_fp_fov_update,
                                            description="Horizontal field of view, like the game's FOV setting")
    bpy.types.Scene.ft_fp_arms_only = BoolProperty(name="Arms only", default=True, update=_fp_mask_update,
                                                   description="Hide everything but the arms, hands, gloves and sleeves")


def unregister():
    for prop in ("ft_fp_arms_only", "ft_fp_fov", "ft_fp_anim", "ft_pose_mode"):
        delattr(bpy.types.Scene, prop)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
