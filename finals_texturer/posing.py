"""Character poses from the game's own animations (Poses tab). No UI here.

Poses are single-frame AnimSequences on _3P_MasterSkeleton, exported as .psa (ActorX) by the helper:
weapon stances (ThirdPersonBasePose…, stored additive on 3P_Loco_BaseRifle_StandBasePose: CUE4Parse
writes them already applied, but puts a bogus scale 2 on some bones -> scale is ignored) and the
end-of-match MVP poses (full poses).

How a pose is applied (rules checked 2026-11-16 on Face_01 Medium, rifle + sword, raw / simplified / ARP rig):
- World matrices of every game bone in the pose and in the game's rest pose, with the PSK importer's
  math (children store the conjugated local rotation). The psa's bone rest data is NOT mirrored like
  psk/keys: Y of rotation and location flipped.
- Per bone a world-space delta D = pose @ rest⁻¹, scaled to the body (k = body size / game size: import
  size × Light/Heavy class scale). Any rig whose bones sit at the game's rest positions takes the pose as
  bone world = D @ its own rest: raw game rig, simplified + tidied rig (Rigging tab), and Auto-Rig Pro
  controls (Quick Rig, Preserve), set in body order (spine -> limbs -> fingers) over a few passes because
  ARP's bone list isn't parent-first. Result: exact on spine/arms/hands/fingers; IK legs via foot target
  and a pole in the knee plane.
- The weapon goes where the game's weapon_root bone is in that pose (the weapon mesh origin; the socket
  weapon_rSocket sits on it with no offset), not scaled, then parented to hand_r so it follows the hand.
- The pose is remembered on the body (ft_pose) and applied again after Simplify / Generate ARP Controls,
  which both need the rest pose.
"""

import os
import struct
import statistics

import bpy
from mathutils import Matrix, Quaternion, Vector

POSE_PROP = "ft_pose"            # game path of the applied pose, on the body armature
WEAPON_PROP = "ft_pose_weapon"   # weapon id the pose belongs to
WEAPON_BONE = "hand_r"
_cache = {}

ARP_KEEP = ("c_pos", "c_traj")   # the user's placement of the whole rig; poses move everything below it
ARP_FK = [("pelvis", "c_root.x"), ("spine_01", "c_spine_01.x"), ("spine_02", "c_spine_02.x"),
          ("spine_03", "c_spine_03.x"), ("neck_01", "c_subneck_1.x"), ("neck_02", "c_neck.x"),
          ("head", "c_head.x")]
for _s in ("l", "r"):
    ARP_FK += [(f"clavicle_{_s}", f"c_shoulder.{_s}"), (f"upperarm_{_s}", f"c_arm_fk.{_s}"),
               (f"lowerarm_{_s}", f"c_forearm_fk.{_s}"), (f"hand_{_s}", f"c_hand_fk.{_s}")]
    ARP_FK += [(f"thumb_0{i}_{_s}", f"c_thumb{i}.{_s}") for i in (1, 2, 3)]
    for _f in ("index", "middle", "ring", "pinky"):
        ARP_FK += [(f"{_f}_01_{_s}", f"c_{_f}1_base.{_s}")]
        ARP_FK += [(f"{_f}_0{i + 1}_{_s}", f"c_{_f}{i}.{_s}") for i in (1, 2, 3)]
    ARP_FK += [(f"thigh_{_s}", f"c_thigh_fk.{_s}"), (f"calf_{_s}", f"c_leg_fk.{_s}"),
               (f"foot_{_s}", f"c_foot_fk.{_s}"), (f"ball_{_s}", f"c_toes_fk.{_s}")]
ARP_IK = [(f"foot_{s}", f"c_foot_ik.{s}") for s in ("l", "r")] + [(f"hand_{s}", f"c_hand_ik.{s}") for s in ("l", "r")]
# pole control -> (root, middle, end) bones of the limb
ARP_POLES = {}
for _s in ("l", "r"):
    ARP_POLES[f"c_leg_pole.{_s}"] = (f"thigh_{_s}", f"calf_{_s}", f"foot_{_s}")
    ARP_POLES[f"c_arms_pole.{_s}"] = (f"upperarm_{_s}", f"lowerarm_{_s}", f"hand_{_s}")


# ─────────────────────────────────────────────────────────────────────────────
# Reading
# ─────────────────────────────────────────────────────────────────────────────

def read_psa(path):
    """First frame of the first sequence: (names, parents, rest rotations, rest locations, key rotations,
    key locations); rotations as mathutils Quaternions (w, x, y, z), raw file values."""
    with open(path, "rb") as fh:
        data = fh.read()
    pos, chunks = 0, {}
    while pos + 32 <= len(data):
        cid = data[pos:pos + 20].split(b"\0", 1)[0].decode("latin-1")
        _flags, size, count = struct.unpack_from("<3i", data, pos + 20)
        pos += 32
        chunks[cid] = (pos, size, count)
        pos += size * count
    if "BONENAMES" not in chunks or "ANIMKEYS" not in chunks:
        raise ValueError(f"{os.path.basename(path)} is not a PSA animation")
    names, parents, rrot, rloc = [], [], [], []
    p, size, count = chunks["BONENAMES"]
    for i in range(count):
        o = p + i * size
        names.append(data[o:o + 64].split(b"\0", 1)[0].decode("latin-1"))
        _fl, _nc, parent = struct.unpack_from("<Iii", data, o + 64)
        x, y, z, w, lx, ly, lz = struct.unpack_from("<7f", data, o + 76)
        parents.append(parent)
        rrot.append(Quaternion((w, x, y, z)))
        rloc.append(Vector((lx, ly, lz)))
    first = 0
    if "ANIMINFO" in chunks:
        p, size, _c = chunks["ANIMINFO"]
        first = struct.unpack_from("<i", data, p + 160)[0]
    p, size, count = chunks["ANIMKEYS"]
    krot, kloc = [], []
    for i in range(len(names)):
        o = p + (first * len(names) + i) * size
        lx, ly, lz, x, y, z, w = struct.unpack_from("<7f", data, o)
        krot.append(Quaternion((w, x, y, z)))
        kloc.append(Vector((lx, ly, lz)))
    return names, parents, rrot, rloc, krot, kloc


def _worlds(parents, rots, locs):
    """PSK importer math -> armature-space bone matrices (edit-bone convention, game cm, mirrored)."""
    wr, wt, out = [], [], []
    for i, (p, q, t) in enumerate(zip(parents, rots, locs)):
        if i == 0 or p < 0:
            wr.append(q.to_matrix())
            wt.append(t.copy())
        else:
            tt = t.copy()
            tt.rotate(wr[p])
            wt.append(wt[p] + tt)
            r = q.conjugated().to_matrix()
            r.rotate(wr[p])
            wr.append(r)
    for i, q in enumerate(rots):
        m = wr[i].to_4x4() if i else q.to_matrix().to_4x4()
        m.translation = wt[i]
        out.append(m)
    return out


def read_pose(path):
    """{bone: (pose matrix, rest matrix)} in game cm, armature space (cached per file)."""
    key = (path, os.path.getmtime(path))
    hit = _cache.get(key)
    if hit is not None:
        return hit
    names, parents, rrot, rloc, krot, kloc = read_psa(path)
    rrot = [Quaternion((q.w, q.x, -q.y, q.z)) for q in rrot]       # psa rest isn't mirrored like the psk
    rloc = [Vector((v.x, -v.y, v.z)) for v in rloc]
    pose, rest = _worlds(parents, krot, kloc), _worlds(parents, rrot, rloc)
    res = {n: (pose[i], rest[i]) for i, n in enumerate(names)}
    _cache.clear()
    _cache[key] = res
    return res


# ─────────────────────────────────────────────────────────────────────────────
# Targets
# ─────────────────────────────────────────────────────────────────────────────

def is_body(arm):
    return arm is not None and arm.type == "ARMATURE" and all(arm.data.bones.get(b) for b in ("pelvis", "hand_r"))


def is_arp(arm):
    return arm is not None and arm.type == "ARMATURE" and arm.data.bones.get("c_traj") is not None


def driving_rig(body):
    """The Auto-Rig Pro rig whose controls drive this body (Quick Rig Preserve: Copy Transforms), or None."""
    for pb in body.pose.bones:
        for c in pb.constraints:
            t = getattr(c, "target", None)
            if is_arp(t):
                return t
    return None


def _armature_of(obj):
    if obj is None:
        return None
    if obj.type == "ARMATURE":
        return obj
    for m in getattr(obj, "modifiers", []):
        if m.type == "ARMATURE" and m.object:
            return m.object
    p = obj.parent
    while p is not None:
        if p.type == "ARMATURE":
            return p
        p = p.parent
    return None


def _is_char_body(arm):
    """A character's body armature: stamped "body" by the Character tab, else any body-shaped skeleton that
    isn't a clothing item's own game skeleton (a jacket's has pelvis and hands too) or a head's rig."""
    if arm is None or arm.type != "ARMATURE":
        return False
    if arm.get("ft_char_role") == "body":
        return True
    if arm.get("ft_outfit_skin") or arm.get("ft_char_role") in ("head", "extra"):
        return False
    return is_body(arm)


def target_of(obj):
    """(body armature, ARP rig or None) of one object: the body itself, any mesh or head bound to it, a
    piece or weapon bone-parented to it, or the ARP rig driving it. (None, None) if it isn't part of one."""
    arm = _armature_of(obj)
    if arm is None:
        return None, None
    if is_arp(arm):
        for o in bpy.data.objects:
            if is_body(o) and driving_rig(o) == arm:
                return o, arm
        return None, None
    if obj.parent_type == "BONE" and _is_char_body(obj.parent):
        arm = obj.parent                             # weapon on the hand, hat on the head bone
    seen = set()
    while not _is_char_body(arm) and arm.parent is not None and arm.parent.type == "ARMATURE"             and arm.name not in seen:
        seen.add(arm.name)
        arm = arm.parent                             # head armature -> body it follows
    if _is_char_body(arm):
        return arm, driving_rig(arm)
    return None, None


def find_target(context):
    """(body armature, ARP rig or None) for the active / selected object (see target_of)."""
    # only what is really selected: Blender keeps the last clicked object active after "select none"
    sel = list(context.selected_objects)
    act = context.active_object
    for obj in ([act] if act in sel else []) + [o for o in sel if o != act]:
        body, rig = target_of(obj)
        if body is not None:
            return body, rig
    return None, None


# ─────────────────────────────────────────────────────────────────────────────
# Applying
# ─────────────────────────────────────────────────────────────────────────────

def body_scale(body, pose):
    """Body size / game size (Blender units per game cm × Light/Heavy class scale): median ratio of
    bone distances from the feet, body rest vs the pose's game rest."""
    ratios = []
    for b in body.data.bones:
        e = pose.get(b.name)
        if e is None:
            continue
        g = e[1].translation.length
        if g > 20.0:
            ratios.append(b.head_local.length / g)
    return statistics.median(ratios) if ratios else 1.0


def deltas(pose, k):
    S, Si = Matrix.Scale(k, 4), Matrix.Scale(1.0 / k, 4)
    return {n: S @ (p @ r.inverted()) @ Si for n, (p, r) in pose.items()}


def _set(arm, name, m):
    arm.pose.bones[name].matrix = m
    bpy.context.view_layer.update()


def _set_final(arm, name, m):
    """Like _set, but so the bone ends up at m *after* its constraints: ARP's IK feet / hands / poles have no
    parent and follow c_traj through a constraint, which the matrix setter ignores (moving the rig moved
    them twice). The constraint's effect is measured and taken out (a left-multiplied offset)."""
    pb = arm.pose.bones[name]
    x = m
    for _ in range(3):
        pb.matrix = x
        bpy.context.view_layer.update()
        got = pb.matrix
        if (got.translation - m.translation).length < 1e-5 and \
                got.to_quaternion().rotation_difference(m.to_quaternion()).angle < 1e-5:
            return
        x = (got @ x.inverted()).inverted() @ m


def _apply_plain(body, D):
    for pb in body.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
    bpy.context.view_layer.update()
    for b in body.data.bones:                       # parents first
        if b.name in D:
            _set(body, b.name, D[b.name] @ b.matrix_local)


def _pole(D, rest_bones, rig_rest, to_rig, place):
    """Pole position in the pose: the rest pole's distance from the middle joint, in the bend plane."""
    a, m, e = ((D[n] @ rest_bones[n]).translation for n in rest_bones)
    rm = rest_bones[list(rest_bones)[1]].translation
    axis = e - a
    if axis.length < 1e-6:
        return None
    off = m - (a + axis * ((m - a).dot(axis) / axis.length_squared))
    rest_pole = to_rig.inverted() @ rig_rest.translation
    if off.length < 1e-4:                           # straight limb: carry the rest offset along
        off = (D[list(rest_bones)[1]].to_3x3() @ (rest_pole - rm))
    off.normalize()
    out = rig_rest.copy()
    out.translation = place @ to_rig @ (m + off * (rest_pole - rm).length)
    return out


def _apply_arp(rig, body, D):
    to_rig = rig.matrix_world.inverted() @ body.matrix_world
    pbs, bones = rig.pose.bones, rig.data.bones
    for pb in pbs:
        if pb.name.startswith("c_") and pb.name not in ARP_KEEP:
            pb.matrix_basis = Matrix.Identity(4)
    bpy.context.view_layer.update()
    place = Matrix.Identity(4)
    if "c_traj" in pbs:                             # keep where the user put the rig
        place = pbs["c_traj"].matrix @ bones["c_traj"].matrix_local.inverted()
    Dr = {n: place @ to_rig @ d @ to_rig.inverted() for n, d in D.items()}
    body_rest = {b.name: b.matrix_local for b in body.data.bones}
    for _ in range(3):
        for src, ctl in ARP_FK + ARP_IK:
            if ctl in pbs and src in Dr:
                _set_final(rig, ctl, Dr[src] @ bones[ctl].matrix_local)
        for ctl, chain in ARP_POLES.items():
            if ctl in pbs and all(n in D and n in body_rest for n in chain):
                m = _pole(D, {n: body_rest[n] for n in chain}, bones[ctl].matrix_local, to_rig, place)
                if m is not None:
                    _set_final(rig, ctl, m)


def apply(body, pose_file, game_path="", weapon_id="", rig=None):
    """Pose the body with a .psa (through its ARP controls when a rig drives it); game_path is
    remembered on the body. Returns the pose data."""
    from . import first_person, twist
    if first_person.is_posed(body):                 # its constraints would override this pose
        first_person.leave(body)
    pose = read_pose(pose_file)
    k = body_scale(body, pose)
    D = deltas(pose, k)
    rig = rig if rig is not None else driving_rig(body)
    if rig is not None:
        _apply_arp(rig, body, D)
    else:
        _apply_plain(body, D)
    body[POSE_PROP] = game_path or pose_file
    body[WEAPON_PROP] = weapon_id
    twist.add_forearm_twist(bpy.context, body)      # rolled wrists don't pinch (Simplify merged the game's)
    bpy.context.view_layer.update()
    return pose


def reset(body, rig=None):
    rig = rig if rig is not None else driving_rig(body)
    if rig is not None:
        for pb in rig.pose.bones:
            if pb.name.startswith("c_") and pb.name not in ARP_KEEP:
                pb.matrix_basis = Matrix.Identity(4)
    else:
        for pb in body.pose.bones:
            pb.matrix_basis = Matrix.Identity(4)
    bpy.context.view_layer.update()
    for key in (POSE_PROP, WEAPON_PROP):
        if key in body:
            del body[key]


# ─────────────────────────────────────────────────────────────────────────────
# Weapons
# ─────────────────────────────────────────────────────────────────────────────

def weapon_world(body, pose):
    """World matrix of the game's weapon_root in this pose, following the body's hand as it is now
    (so moving the ARP rig or the body object is respected). None if the pose has no weapon bone."""
    if "weapon_root" not in pose or WEAPON_BONE not in body.pose.bones:
        return None
    k = body_scale(body, pose)
    D = deltas(pose, k)
    w = pose["weapon_root"][0].copy()
    w.translation = w.translation * k
    hand_target = D[WEAPON_BONE] @ body.data.bones[WEAPON_BONE].matrix_local
    hand_now = body.pose.bones[WEAPON_BONE].matrix
    return body.matrix_world @ hand_now @ hand_target.inverted() @ w


def attached_weapons(body):
    """Top objects of weapons parented to the body's hand: {weapon id: [objects]}."""
    out = {}
    for o in body.children:
        if o.parent_type == "BONE" and o.parent_bone == WEAPON_BONE and o.get("ft_weapon"):
            out.setdefault(o["ft_weapon"], []).append(o)
    return out


SECOND_GUN = "weapon_mag"      # dual weapons (.50 Akimbo): the left gun hangs on the weapon's weapon_mag
LEFT_HAND = "hand_l"           # bone, which the game's poses put in the left hand
SECOND_GUN_CONSTRAINT = "FT Left hand"


def is_dual(obj):
    """Two guns: the second one's bones (weapon_slide2...) or, on skins like Akimbanana, a separate part
    hung on weapon_mag."""
    if obj.type != "ARMATURE" or obj.data.bones.get(SECOND_GUN) is None:
        return False
    return any(obj.data.bones.get(b) for b in ("weapon_slide2", "weapon_trigger2")) or         any(c.parent_type == "BONE" and c.parent_bone == SECOND_GUN for c in obj.children)


def _free_second_gun(obj):
    pb = obj.pose.bones.get(SECOND_GUN) if obj.type == "ARMATURE" else None
    if pb is None:
        return None
    for c in [c for c in pb.constraints if c.name == SECOND_GUN_CONSTRAINT]:
        pb.constraints.remove(c)
    pb.matrix_basis = Matrix.Identity(4)
    return pb


def attach_second_gun(body, obj, pose):
    """Put a dual weapon's left gun where the pose holds it and make it follow the left hand."""
    pb = _free_second_gun(obj)
    if pb is None or "weapon_root" not in pose or SECOND_GUN not in pose or LEFT_HAND not in body.pose.bones:
        return False
    bpy.context.view_layer.update()
    g_root, g_rest_root = pose["weapon_root"]
    g_mag, g_rest_mag = pose[SECOND_GUN]
    rel = g_root.inverted() @ g_mag                        # left gun relative to the right one, game cm
    root_b = obj.data.bones.get("weapon_root")
    r_root = root_b.matrix_local if root_b else Matrix.Identity(4)
    # the weapon's units per game cm, from the rest offset weapon_root -> weapon_mag in both skeletons
    game_len = (g_rest_root.inverted() @ g_rest_mag).translation.length
    own_len = (r_root.inverted() @ pb.bone.matrix_local).translation.length
    rel.translation = rel.translation * (own_len / game_len if game_len > 1e-6 and own_len > 1e-6 else 1.0)
    pb.matrix = r_root @ rel
    bpy.context.view_layer.update()
    c = pb.constraints.new("CHILD_OF")
    c.name = SECOND_GUN_CONSTRAINT
    c.target, c.subtarget = body, LEFT_HAND
    if hasattr(c, "set_inverse_pending"):
        c.set_inverse_pending = False                      # keep our inverse (new Child Of sets its own)
    # a bone's Child Of works on the bone's armature-space matrix: undo the hand, redo the weapon object
    c.inverse_matrix = (body.matrix_world @ body.pose.bones[LEFT_HAND].matrix).inverted() @ obj.matrix_world
    bpy.context.view_layer.update()
    return True


def attach(body, objs, pose):
    """Put a weapon's top objects where the pose holds it and parent them to the right hand."""
    world = weapon_world(body, pose)
    if world is None:
        return False
    for o in objs:
        o.parent = None
        o.parent = body
        o.parent_type = "BONE"
        o.parent_bone = WEAPON_BONE
        o.matrix_parent_inverse = Matrix.Identity(4)
        bpy.context.view_layer.update()
        o.matrix_world = world
    bpy.context.view_layer.update()
    for o in objs:
        if is_dual(o):
            attach_second_gun(body, o, pose)
    return True


def detach(objs):
    for o in objs:
        if is_dual(o):
            _free_second_gun(o)
        mw = o.matrix_world.copy()
        o.parent = None
        o.matrix_world = mw


def reapply(body, root, report=print):
    """Apply the body's remembered pose again (after Simplify / ARP rig creation reset it) and put the
    attached weapons back in the hand."""
    path = body.get(POSE_PROP, "")
    if not path:
        return False
    f = path if os.path.isabs(path) else (os.path.join(root, *path.split("/")) + ".psa" if root else "")
    if not f or not os.path.isfile(f):
        report(f"Note: the remembered pose {path.rsplit('/', 1)[-1]} isn't exported; '{body.name}' stays in "
               f"the rest pose")
        return False
    pose = apply(body, f, path, body.get(WEAPON_PROP, ""))
    for objs in attached_weapons(body).values():
        attach(body, objs, pose)
    report(f"Pose {path.rsplit('/', 1)[-1]} applied again to '{body.name}'.")
    return True
