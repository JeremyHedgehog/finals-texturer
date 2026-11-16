"""Build finals_texturer/data/sockets.json: rest-pose transforms of the character attachment sockets, so
static meshes attached to a socket (hats, glasses, back items, earrings, pets, watches…) can be placed as
the game does: socket x the item's LocalPosition / LocalRotation / LocalScale.

Run in Blender (needs mathutils and the PSK importer only for --check):
    blender -b --python dev_tools/build_sockets.py -- <Exports root> [--check]

Sources (FModel / ft_exporter exports):
  * Characters/Avatar/MeshMerge/SK_CharacterMeshMergeTarget_Skeleton.json: the merged character's bones and
    its sockets, with per-body variants "<socket>___<light|medium|heavy>_<male|female>";
  * Characters/Heads/Face_01/Base/SK_Head_Face_01_Base_Skeleton.json: face sockets (earring_*, nose_attachment)
    on the facial bones (same rest pose on every face for these; Face_01 = the default head).
Output: {"transforms": {"<socket or bone>": 16 floats (row-major 4x4, Blender axes = PSK axes (Y flipped),
centimetres)}, "attach": {"<socket or bone>": body bone it follows (nearest SK_Body_M bone up the chain)}}.
Bones are included too (DA files also name bones directly: "head", "spine_03", "calf_l"…).
--check imports Characters/Body/SK_Body_M.psk and compares bone positions.
"""
import json
import math
import os
import sys

from mathutils import Euler, Matrix, Quaternion, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, "..", "finals_texturer", "data", "sockets.json"))
MERGE = "Discovery/Content/Discovery/Characters/Avatar/MeshMerge/SK_CharacterMeshMergeTarget_Skeleton.json"
FACE = "Discovery/Content/Discovery/Characters/Heads/Face_01/Base/SK_Head_Face_01_Base_Skeleton.json"
BODY = "Discovery/Content/Discovery/Characters/Body/SK_Body_M_Skeleton.json"


def ue_quat(q):
    """UE FQuat (left-handed) -> Blender quaternion in PSK axes (Y flipped)."""
    return Quaternion((q["W"], -q["X"], q["Y"], -q["Z"]))


def ue_rotator(rot):
    """Same convention as weapons._ue_matrix (verified on weapon parts and objects)."""
    return Euler((math.radians(rot.get("Roll", 0.0)), math.radians(-rot.get("Pitch", 0.0)),
                  math.radians(-rot.get("Yaw", 0.0))), "XYZ").to_matrix().to_4x4()


def trs(loc, rot4, scale):
    t = Matrix.Translation(Vector((loc.get("X", 0.0), -loc.get("Y", 0.0), loc.get("Z", 0.0))))
    s = Matrix.Diagonal(((scale or {}).get("X", 1.0), (scale or {}).get("Y", 1.0), (scale or {}).get("Z", 1.0), 1.0))
    return t @ rot4 @ s


def skeleton(path):
    data = json.load(open(path, encoding="utf-8"))
    skel = next(e for e in data if e.get("Type") == "Skeleton")
    info = skel["ReferenceSkeleton"]["FinalRefBoneInfo"]
    pose = skel["ReferenceSkeleton"]["FinalRefBonePose"]
    world = {}
    mats = []
    parent = {b["Name"]: (info[b["ParentIndex"]]["Name"] if b["ParentIndex"] >= 0 else "") for b in info}
    for i, (b, p) in enumerate(zip(info, pose)):
        local = trs(p["Translation"], ue_quat(p["Rotation"]).to_matrix().to_4x4(), p.get("Scale3D"))
        m = local if b["ParentIndex"] < 0 else mats[b["ParentIndex"]] @ local
        mats.append(m)
        world[b["Name"]] = m
    sockets, sock_bone = {}, {}
    for e in data:
        if e.get("Type") != "SkeletalMeshSocket":
            continue
        pr = e.get("Properties") or {}
        bone = pr.get("BoneName")
        if bone not in world:
            continue
        rel = trs(pr.get("RelativeLocation") or {}, ue_rotator(pr.get("RelativeRotation") or {}), pr.get("RelativeScale"))
        sockets[pr["SocketName"]] = world[bone] @ rel
        sock_bone[pr["SocketName"]] = bone
    return world, sockets, parent, sock_bone


def flat(m):
    return [round(v, 5) for row in m for v in row]


def main():
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    root = args[0]
    bones, sockets, parent, sock_bone = skeleton(os.path.join(root, MERGE))
    body = {b["Name"] for b in next(e for e in json.load(open(os.path.join(root, BODY), encoding="utf-8"))
                                    if e.get("Type") == "Skeleton")["ReferenceSkeleton"]["FinalRefBoneInfo"]}

    def follow(bone):
        while bone and bone not in body:
            bone = parent.get(bone, "")
        return bone or "root"

    out = {name: flat(m) for name, m in bones.items()}
    out.update({name: flat(m) for name, m in sockets.items()})
    attach = {name: follow(name) for name in bones}
    attach.update({name: follow(sock_bone[name]) for name in sockets})
    fp = os.path.join(root, FACE)
    if os.path.isfile(fp):
        fbones, fsockets, _fp, _fs = skeleton(fp)
        # face bones sit on the same head: express them through the body's head bone
        if "head" in fbones and "head" in bones:
            fix = bones["head"] @ fbones["head"].inverted()
            for name, m in fsockets.items():
                out.setdefault(name, flat(fix @ m))
                attach.setdefault(name, "head")
    json.dump({"version": 1, "units": "cm, Blender (PSK) axes", "transforms": out, "attach": attach},
              open(OUT, "w", encoding="utf-8"), separators=(",", ":"))
    print(f"{OUT}: {len(bones)} bones, {len(sockets)} body sockets, {len(out) - len(bones) - len(sockets)} face sockets")
    if "--check" in args:
        import bpy
        sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..")))
        __import__("add-on-io-scene-psk-psa-v9_1_2").register()
        bpy.ops.psk.import_file(filepath=os.path.join(root, "Discovery/Content/Discovery/Characters/Body/SK_Body_M.psk"))
        arm = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")
        worst = 0.0
        for b in arm.data.bones:
            if b.name in bones:
                d = (arm.matrix_world @ b.head_local - bones[b.name].to_translation()).length
                worst = max(worst, d)
                if b.name in ("head", "spine_03", "hand_r", "clavicle_l"):
                    print(f"  {b.name}: psk {tuple(round(x, 2) for x in arm.matrix_world @ b.head_local)} "
                          f"ours {tuple(round(x, 2) for x in bones[b.name].to_translation())}")
        print("CHECK worst bone position difference (cm):", round(worst, 3))


if __name__ == "__main__":
    main()
