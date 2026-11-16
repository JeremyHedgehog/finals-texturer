"""Placement of static meshes attached to a character socket (hats, helmets, glasses, back / shoulder / hip
items, earrings, pets, watches…), the way the game places them:

    world = socket (rest pose on the Medium body) x TagOverride offset (bOffsetTransform, matching tags)
            x LocalPosition / LocalRotation / LocalScale (from the item's VisualParts entry)

Most hats are modelled in place on the body; their local transform is the socket's inverse, so the result
is the identity. Others (KnightHelmetBase + visor, glasses, earrings, back swords…) are modelled around the
socket and only reach the body this way; without it they sat at the feet.

data/sockets.json is built by dev_tools/build_sockets.py from the character mesh-merge skeleton (body
sockets incl. per-body variants "<socket>___<light|medium|heavy>_<male|female>") and Face_01's head skeleton
(earring / nose sockets). The result is in Medium-body space; importer.fit_class then scales it to the class.
"""
import json
import math
import os

_table = {"data": None}
CLASS_NAMES = {"L": "light", "M": "medium", "H": "heavy"}
FEMININE_TAG = "Customization.BodyType.Feminine"


def _load():
    if _table["data"] is None:
        try:
            with open(os.path.join(os.path.dirname(__file__), "data", "sockets.json"), encoding="utf-8") as fh:
                _table["data"] = json.load(fh)
        except (OSError, ValueError):
            _table["data"] = {"transforms": {}, "attach": {}}
    return _table["data"]


def _socket_name(socket, cls, gender):
    """The per-body socket variant when the skeleton has one."""
    t = _load()["transforms"]
    variant = f"{socket}___{CLASS_NAMES.get(cls or 'M', 'medium')}_{'female' if gender == 'female' else 'male'}"
    return variant if variant in t else socket


def known(socket):
    return bool(socket) and socket != "None" and socket in _load()["transforms"]


def attach_bone(socket):
    """The body bone a socket follows (for the Rigging tab's Attach), or the socket itself."""
    return _load()["attach"].get(socket, socket)


def _trs(pos, rot, scale):
    """UE location / rotator / scale -> Blender matrix (same convention as weapons._ue_matrix), cm."""
    from mathutils import Euler, Matrix, Vector
    pos, rot, scale = pos or {}, rot or {}, scale or {}
    t = Vector((pos.get("X", 0.0), -pos.get("Y", 0.0), pos.get("Z", 0.0)))
    e = Euler((math.radians(rot.get("Roll", 0.0)), math.radians(-rot.get("Pitch", 0.0)),
               math.radians(-rot.get("Yaw", 0.0))), "XYZ")
    s = Matrix.Diagonal((scale.get("X", 1.0) or 1.0, scale.get("Y", 1.0) or 1.0, scale.get("Z", 1.0) or 1.0, 1.0))
    return Matrix.Translation(t) @ e.to_matrix().to_4x4() @ s


def placement(vp, tags=(), cls="M", gender="male"):
    """Matrix (centimetres, Blender axes) that puts a static VisualParts mesh where the game attaches it, or
    None when the socket is unknown."""
    from mathutils import Matrix
    socket = vp.get("AttachmentSocket", "")
    if not known(socket):
        return None
    vals = _load()["transforms"][_socket_name(socket, cls, gender)]
    m = Matrix([vals[i * 4:(i + 1) * 4] for i in range(4)])
    active = set(tags) | ({FEMININE_TAG} if gender == "female" else set())
    for to in vp.get("TagOverrides") or []:
        want = set(to.get("MatchingTags") or [])
        if to.get("bOffsetTransform") and want and want <= active:
            m = m @ _trs(to.get("OffsetPosition"), to.get("OffsetRotation"), to.get("OffsetScale"))
    return m @ _trs(vp.get("LocalPosition"), vp.get("LocalRotation"), vp.get("LocalScale"))


def place(objs, vp, unit, tags=(), cls="M", gender="male"):
    """Move freshly imported objects (mesh data, object scale stays 1) to their socket. Returns True if placed."""
    from mathutils import Matrix
    m = placement(vp, tags, cls, gender)
    if m is None:
        return False
    u = Matrix.Diagonal((unit, unit, unit, 1.0))
    mu = u @ m @ u.inverted()
    for o in objs:
        if o.type == "MESH":
            o.data.transform(mu, shape_keys=True)
            o.data.update()
    return True
