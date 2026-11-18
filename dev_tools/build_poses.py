"""Build finals_texturer/data/poses.json: the game's character poses per weapon (Poses tab).

    python -I dev_tools/build_poses.py <dump root> <mvp list.jsonl> <archetype dump root> [--check]

<dump root>: FModel-format JSON of Discovery/Content/Discovery/Items (dev_tools/shader_extract:
`dotnet run -- <dump> --dump Discovery/Content/Discovery/Items`).
<mvp list.jsonl>: `ft_exporter list Discovery/Content/Discovery/Animation/AnimationDatabase/Frontend/MVP`.
<archetype dump root>: `ft_exporter export --out <dir> "Discovery/Content/Discovery/Animation/ItemAnimationDataAssets/*"`
(the shared bundles DA_AnimBundle_Archetype_Rifle/Pistol/…: weapons whose bundle has no third-person
poses, e.g. the Lewis Gun, use the archetype's; the game fills each pose type skin bundle -> item bundle ->
archetype).

Per weapon (ids as in names.json.gz):
- bundles: WeaponAnimationBundle data assets that hold third-person poses (ThirdPersonBasePose, …Crouch,
  …Sprint, …ADS: single-frame AnimSequences on _3P_MasterSkeleton). The weapon's *Properties* data asset
  names its default bundle (ItemAnimationBundle); skins with their own grip name theirs
  (WeaponAnimationBundle, e.g. the sword's baseball bat).
- fp / fp_skins: first-person animations (First Person mode of the Posing tab) from the same bundles:
  FirstPersonBasePose…, InspectWeaponLoop (AnimSequences on the master skeleton, camera bone included);
  fp_skins names the bundle of skins with their own first-person set (e.g. the Akimbanana).
- mvp: end-of-match poses (Frontend/MVP/HM_Frontend_MVP_P_<name>_00). Nothing in the game data links them
  to weapons (game script does), so they're matched by name; ALIASES covers the abbreviations/typos.
Poses without a weapon (objective, support) go to "other".
Regenerate after game updates.
"""
import gzip
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "finals_texturer", "data")
ITEMS = "Discovery/Content/Discovery/Items/"
KINDS = (("ThirdPersonBasePose", "Standing"), ("ThirdPersonBasePoseADS", "Aiming"),
         ("ThirdPersonBasePoseCrouch", "Crouching"), ("ThirdPersonBasePoseSprint", "Sprinting"))
# MVP pose name -> weapon folder (abbreviations and typos in the game's file names)
ALIASES = {
    "ak47": "AssaultRifle_01", "arl": "AssaultRifleLight_01", "deagals": "PistolDual_01",
    "dualblads": "DualBlades", "sleadgehammer": "Sledgehammer_01", "throwingknifes": "ThrowingKnives_01",
    "foxte": "FoxTacticalElementum", "brleveraction": "BattleRifleLeverAction_01", "m26": "ShotgunPumpAction_01",
    "m32gl": "Launcher_03", "magnum": "Revolver_02", "polearm": "Polearm_01",
    "remington1900": "RemingtonModel1900", "winchester": "Shotgun_03",
    "honcho": "ShotgunBreakAction_02",      # by elimination: the only shotgun left without an MVP pose
}
FP_KINDS = (("FirstPersonBasePose", "Hold"), ("FirstPersonBasePoseADS", "Aim (ADS)"),
            ("FirstPersonBasePoseCrouch", "Crouch"), ("FirstPersonBasePoseSprint", "Sprint"),
            ("FirstPersonBaseSlide", "Slide"), ("InspectWeaponLoop", "Inspect"))
OTHER_NAMES = {"Objective": "Objective", "Support": "Support", "Snowball": "Snowball (gadget)"}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def game_path(obj_path):
    """/Game/Discovery/X/Y.Y.0 -> Discovery/Content/Discovery/X/Y"""
    p = obj_path.split(".")[0]
    if p.startswith("/Game/"):
        p = "Discovery/Content/" + p[len("/Game/"):]
    return p


def ref(v):
    if isinstance(v, dict):
        return v.get("ObjectPath") or v.get("AssetPathName") or ""
    return ""


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return []


def main(dump, mvp_list, arch_dump, check=False):
    roots = [dump, arch_dump]

    def load_gp(gp):
        for r in roots:
            f = os.path.join(r, *gp.split("/")) + ".json"
            if os.path.isfile(f):
                return load(f)
        return []

    names = json.load(gzip.open(os.path.join(DATA, "names.json.gz")))
    weapons = {w["id"]: w for w in names["weapons"]}
    bundle_cache = {}

    def bundle(gp):
        """{pose kind: game path} of a bundle's third- and first-person poses."""
        if gp not in bundle_cache:
            got = {}
            for o in load_gp(gp):
                wa = (o.get("Properties") or {}).get("WeaponAnimations") or {}
                for key, _label in KINDS + FP_KINDS:
                    if ref(wa.get(key)):
                        got[key] = game_path(ref(wa.get(key)))
            bundle_cache[gp] = got
        return bundle_cache[gp]

    def poses(*chain, kinds=KINDS):
        out, seen = [], set()
        for key, label in kinds:
            p = next((bundle(gp)[key] for gp in chain if gp and key in bundle(gp)), "")
            if p and p not in seen:
                seen.add(p)
                out.append([label, p])
        return out

    split = lambda t: [x for x in re.split(r"[_/]|(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])", t) if x]

    def label_of(stem, wid, default_poses):
        """Bundle name without the words of the weapon's own path / default animation folder
        (DA_AssaultRIfleLight01_Barista_AnimBundle -> Barista)."""
        stop = {"da", "anim", "bundle", "animbundle"}
        own = set()
        for t in [wid] + [p.split("/3P/")[1].split("/")[0] for _l, p in default_poses if "/3P/" in p]:
            for x in split(t):
                own |= {x.lower(), x.lower().rstrip("s")}
        toks = [t for t in split(stem) if t.lower() not in stop and not t.isdigit() and len(t) > 1]
        while len(toks) > 1 and (toks[0].lower() in own or toks[0].lower().rstrip("s") in own):
            toks = toks[1:]          # only leading weapon words: Sword_LongSword -> Long Sword
        return " ".join(toks) or stem

    out = {}
    for wid, w in weapons.items():
        wdir = os.path.join(dump, *(ITEMS + wid).split("/"))
        default, arch, skins = "", "", {}
        props = sorted(f for f in os.listdir(wdir) if f.endswith(".json") and "Properties" in f)             if os.path.isdir(wdir) else []
        props.sort(key=lambda f: (not re.match(r"DA_(Weapon|Melee|Item)Properties_", f), "Pendulum" in f, len(f)))
        for f in props:
            for o in load(os.path.join(wdir, f)):
                pr = o.get("Properties") or {}
                if ref(pr.get("ItemAnimationBundle")) and not default:
                    default = game_path(ref(pr.get("ItemAnimationBundle")))
                if ref(pr.get("ArchetypeAnimationBundle")) and not arch:
                    arch = game_path(ref(pr.get("ArchetypeAnimationBundle")))
        for s_ in w["skins"]:
            if not s_["path"]:
                continue
            for o in load(os.path.join(dump, *s_["path"].split("/"))):
                p = ref((o.get("Properties") or {}).get("WeaponAnimationBundle"))
                if p and game_path(p) != default:
                    skins[s_["id"]] = game_path(p)
        bundles = {}
        base = poses(default, arch)
        if base:
            bundles[default or arch] = {"name": "Default", "poses": base}
        for gp in sorted(set(skins.values())):
            ps = poses(gp, default, arch)
            if ps and ps != base:
                bundles[gp] = {"name": label_of(gp.rsplit("/", 1)[-1], wid, base),
                               "poses": ps}
        fp = {}
        fp_base = poses(default, arch, kinds=FP_KINDS)
        if fp_base:
            fp[default or arch] = {"name": "Default", "anims": fp_base}
        for gp in sorted(set(skins.values())):
            fa = poses(gp, default, arch, kinds=FP_KINDS)
            if fa and fa != fp_base:
                fp[gp] = {"name": label_of(gp.rsplit("/", 1)[-1], wid, base), "anims": fa}
        out[wid] = {"name": w["name"], "default": (default or arch) if base else "",
                    "bundles": bundles, "skins": {k: v for k, v in skins.items() if v in bundles}, "mvp": []}
        if fp:
            out[wid]["fp"] = fp
            out[wid]["fp_skins"] = {k: v for k, v in skins.items() if v in fp}

    # MVP poses by name
    keys = {}
    for wid, w in weapons.items():
        ks = {norm(w["folder"]), norm(w["name"]), norm(re.sub(r"_\d+$", "", w["folder"]))}
        for b in out[wid]["bundles"].values():
            for _l, p in b["poses"]:
                parts = p.split("/")
                if "3P" in parts:
                    ks.add(norm(parts[parts.index("3P") + 1]))
        for k in ks:
            if k:
                keys.setdefault(k, set()).add(wid)
    other, unmatched = [], []
    for line in open(mvp_list, encoding="utf-8"):
        ev = json.loads(line)
        if ev.get("event") != "file":
            continue
        gp = ev["path"].rsplit(".", 1)[0]
        stem = gp.rsplit("/", 1)[-1]
        m = re.match(r"HM_Frontend_MVP_P_(.+?)(?:_(\d+))?$", stem)
        if not m:
            continue
        nm = m.group(1)
        k = norm(nm.replace("_Fallback", ""))
        wid = None
        if k in ALIASES:
            wid = next((i for i, w in weapons.items() if w["folder"] == ALIASES[k]), None)
        elif k in keys and len(keys[k]) == 1:
            wid = next(iter(keys[k]))
        if wid:
            out[wid]["mvp"].append(gp)
        elif nm.split("_")[0] in OTHER_NAMES:
            num = m.group(2) or (nm.split("_")[1] if "_" in nm else "")
            other.append([f"{OTHER_NAMES[nm.split('_')[0]]} {int(num)}".replace(" 0", "") if num.isdigit()
                          else OTHER_NAMES[nm.split("_")[0]], gp])
        else:
            unmatched.append(stem)
    for w in out.values():
        w["mvp"].sort(key=lambda p: ("Fallback" in p, p))
    other.sort()
    res = {"version": 1, "generated": time.strftime("%Y-%m-%d"),
           "weapons": {k: v for k, v in out.items() if v["bundles"] or v["mvp"] or v.get("fp")}, "other": other}
    if check:
        for wid, w in sorted(res["weapons"].items()):
            print(f"{w['name']:24s} {wid:48s} bundles={[b['name'] for b in w['bundles'].values()]} "
                  f"skins={len(w['skins'])} mvp={[p.rsplit('_P_', 1)[-1] for p in w['mvp']]}")
        print("no poses:", [w["name"] for i, w in out.items() if not (w["bundles"] or w["mvp"])])
        print("other:", other)
        print("UNMATCHED MVP:", unmatched)
        return
    with open(os.path.join(DATA, "poses.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, separators=(",", ":"))
    n = sum(len(b["poses"]) for w in res["weapons"].values() for b in w["bundles"].values())
    nfp = sum(len(b["anims"]) for w in res["weapons"].values() for b in (w.get("fp") or {}).values())
    print(f"poses.json: {len(res['weapons'])} weapons, {n} stance poses, {nfp} first-person animations, "
          f"{sum(len(w['mvp']) for w in res['weapons'].values())} MVP poses, {len(other)} other; "
          f"unmatched MVP: {unmatched}")


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if x != "--check"]
    main(a[0], a[1], a[2], "--check" in sys.argv)
