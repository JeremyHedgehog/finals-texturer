"""Forearm twist. No UI here.

The game body spreads the hand's roll along the forearm with twist bones the game moves itself
(lowerarm_vpj_*); they aren't in the animations, and Simplify Body Rig merges them into lowerarm.
Without them a rolled wrist (crossbow / pistol inspects, some stances) pinches like a candy wrapper.
We add our own: FT_twist_<i>_<side> along the forearm, children of lowerarm, each turning a share of the
hand's roll (driver), and split the forearm's skin weights across them. With the hand unrolled they sit
still, so nothing else changes. Simplify merges them back into lowerarm; posing adds them again.
"""

import re

import bpy
from mathutils import Matrix

TWIST_PREFIX = "FT_twist_"
TWIST_SHARES = (0.25, 0.5, 0.75, 1.0)          # share of the hand's roll, at that fraction of the forearm
_FOREARM_GROUP = re.compile(r"^lowerarm(_vpj_\d+)?_([lr])$")


def twist_name(i, side):
    return f"{TWIST_PREFIX}{i + 1:02d}_{side}"


def character_meshes(arm):
    """Meshes that belong to the character: deformed by its armature, or by armatures parented to it."""
    arms = {arm} | {o for o in bpy.data.objects if o.type == "ARMATURE" and o.parent == arm}
    out = []
    for o in bpy.data.objects:
        if o.type != "MESH":
            continue
        if any(m.type == "ARMATURE" and m.object in arms for m in o.modifiers) or o.parent in arms:
            out.append(o)
    return out


def _split_forearm_weights(arm, meshes, side):
    lower, hand = arm.data.bones.get(f"lowerarm_{side}"), arm.data.bones.get(f"hand_{side}")
    elbow, wrist = lower.head_local, hand.head_local
    axis = wrist - elbow
    span = axis.length_squared or 1.0
    nodes = (0.0,) + TWIST_SHARES                  # node 0 = lowerarm itself (no roll)
    names = [twist_name(i, side) for i in range(len(TWIST_SHARES))]
    for o in meshes:
        if any(o.vertex_groups.get(n) for n in names):
            continue                               # already split (Simplify removes the groups again)
        src = {vg.index for vg in o.vertex_groups if (m := _FOREARM_GROUP.match(vg.name)) and m.group(2) == side}
        if not src:
            continue
        base = o.vertex_groups.get(f"lowerarm_{side}") or o.vertex_groups.new(name=f"lowerarm_{side}")
        tw = [o.vertex_groups.new(name=n) for n in names]
        targets = [base] + tw
        to_arm = arm.matrix_world.inverted() @ o.matrix_world
        for v in o.data.vertices:
            w = sum(g.weight for g in v.groups if g.group in src)
            if w <= 0.0:
                continue
            t = min(max((to_arm @ v.co - elbow).dot(axis) / span, 0.0), 1.0)
            for g in [g.group for g in v.groups if g.group in src]:
                o.vertex_groups[g].remove([v.index])
            for k in range(len(nodes) - 1):        # linear blend between the two nodes around t
                if nodes[k] <= t <= nodes[k + 1]:
                    f = (t - nodes[k]) / (nodes[k + 1] - nodes[k])
                    if 1.0 - f > 1e-4:
                        targets[k].add([v.index], w * (1.0 - f), "ADD")
                    if f > 1e-4:
                        targets[k + 1].add([v.index], w * f, "ADD")
                    break


def add_forearm_twist(context, arm):
    """Twist bones + weights on the character (once), driven by each hand's roll. Returns the number of
    arms set up (0 if the body has no forearms or couldn't be edited)."""
    sides = [sd for sd in ("l", "r") if f"lowerarm_{sd}" in arm.data.bones and f"hand_{sd}" in arm.data.bones]
    if not sides:
        return 0
    missing = [(i, sd) for sd in sides for i in range(len(TWIST_SHARES)) if twist_name(i, sd) not in arm.data.bones]
    if missing:
        prev = context.view_layer.objects.active
        prev_mode = prev.mode if prev is not None else "OBJECT"
        sel = list(context.selected_objects)
        try:
            if prev is not None and prev.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            for o in sel:
                o.select_set(False)
            arm.select_set(True)
            context.view_layer.objects.active = arm
            bpy.ops.object.mode_set(mode="EDIT")
        except RuntimeError:
            return 0                               # hidden / not in the view layer: leave it
        eb = arm.data.edit_bones
        plan = {sd: (eb[f"lowerarm_{sd}"].matrix.copy(), eb[f"lowerarm_{sd}"].head.copy(),
                     eb[f"hand_{sd}"].head.copy()) for sd in sides}
        for i, sd in missing:
            mat, elbow, wrist = plan[sd]
            b = eb.new(twist_name(i, sd))
            b.head, b.tail = (0, 0, 0), (0, max((wrist - elbow).length * 0.2, 1e-3), 0)
            b.matrix = Matrix.LocRotScale(elbow.lerp(wrist, TWIST_SHARES[i] - 0.125), mat.to_quaternion(), None)
            b.parent = eb[f"lowerarm_{sd}"]
            b.use_deform = True
        bpy.ops.object.mode_set(mode="OBJECT")
        for i, sd in missing:
            arm.data.bones[twist_name(i, sd)].hide = True
        arm.select_set(False)
        for o in sel:
            try:
                o.select_set(True)
            except RuntimeError:
                pass
        if prev is not None:
            context.view_layer.objects.active = prev
            if prev_mode not in ("OBJECT", "EDIT"):
                try:
                    bpy.ops.object.mode_set(mode=prev_mode)
                except RuntimeError:
                    pass
    meshes = [o for o in character_meshes(arm) if any(m.type == "ARMATURE" and m.object == arm for m in o.modifiers)]
    for sd in sides:
        _split_forearm_weights(arm, meshes, sd)
        for i, share in enumerate(TWIST_SHARES):
            pb = arm.pose.bones[twist_name(i, sd)]
            pb.rotation_mode = "XYZ"
            pb.driver_remove("rotation_euler", 1)
            fc = pb.driver_add("rotation_euler", 1)
            d = fc.driver
            d.type = "SCRIPTED"
            d.expression = f"roll * {share}"
            v = d.variables.new()
            v.name, v.type = "roll", "TRANSFORMS"
            t = v.targets[0]
            t.id, t.bone_target = arm, f"hand_{sd}"
            # local space includes constraints (First Person's Copy Transforms, ARP's controls)
            t.transform_type, t.rotation_mode, t.transform_space = "ROT_Y", "SWING_TWIST_Y", "LOCAL_SPACE"
    return len(sides)
