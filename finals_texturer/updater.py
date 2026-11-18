"""Update check: tells the user when a newer release is on GitHub and can install it ("Update Now").

- One check per Blender session (a few seconds after start-up), in a background thread, only when Blender's
  "Allow Online Access" is on and the preference "Check for Updates" is set. Sends nothing but a plain
  request for the public release list (generic User-Agent).
- Newer = the latest release's tag (v<version>) is above this add-on's manifest version.
- Update Now downloads the release's finals_texturer-<version>.zip (background thread) and installs it over
  this extension with Blender's own installer (main thread, from a timer: never inside one of our operators,
  which the reinstall unregisters). A restart is recommended afterwards.
"""

import json
import os
import tempfile
import threading
import tomllib
import urllib.request

import bpy

REPO = "JeremyHedgehog/finals-texturer"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases"
AGENT = "finals-texturer-update-check"

_state = {
    "latest": None,      # (0, 23, 0)
    "tag": "",
    "url": PAGE,         # release page
    "zip": "",           # download URL of the add-on zip
    "checked": False,
    "busy": "",          # "checking" / "downloading" / "installing"
    "error": "",
    "done": "",          # message after installing
}


def current_version():
    try:
        with open(os.path.join(os.path.dirname(__file__), "blender_manifest.toml"), "rb") as fh:
            return _parse(tomllib.load(fh)["version"])
    except (OSError, KeyError, ValueError):
        return (0, 0, 0)


def _parse(v):
    v = v.strip().lstrip("vV").split("-")[0]
    return tuple(int(p) for p in v.split("."))


def _fmt(t):
    return ".".join(str(p) for p in t)


def online():
    return bool(getattr(bpy.app, "online_access", True))


def _redraw():
    wm = getattr(bpy.context, "window_manager", None)
    for win in (wm.windows if wm else []):
        for area in win.screen.areas:
            if area.type in ("VIEW_3D", "PREFERENCES"):
                area.tag_redraw()


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


# ── check ────────────────────────────────────────────────────────────────────

def _check_worker():
    try:
        with _get(API) as r:
            data = json.load(r)
        tag = data.get("tag_name") or ""
        _state["latest"] = _parse(tag)
        _state["tag"] = tag
        _state["url"] = data.get("html_url") or PAGE
        _state["zip"] = next((a["browser_download_url"] for a in data.get("assets", [])
                              if a.get("name", "").startswith("finals_texturer-") and a["name"].endswith(".zip")), "")
        _state["error"] = ""
    except Exception as e:                       # offline, rate limit, no release yet…
        _state["error"] = f"Couldn't check for updates ({e})"
    _state["checked"] = True
    _state["busy"] = ""
    bpy.app.timers.register(lambda: (_redraw(), None)[1], first_interval=0.1)


def check(force=False):
    if _state["busy"] or (_state["checked"] and not force) or not online():
        return
    _state["busy"] = "checking"
    threading.Thread(target=_check_worker, daemon=True).start()


def update_available():
    return _state["latest"] is not None and _state["latest"] > current_version()


# ── install ──────────────────────────────────────────────────────────────────

def _repo_module():
    """The extension repository this add-on is installed in (bl_ext.<repo>.finals_texturer)."""
    parts = (__package__ or "").split(".")
    return parts[1] if len(parts) >= 3 and parts[0] == "bl_ext" else "user_default"


def _install(path):
    try:
        res = bpy.ops.extensions.package_install_files(filepath=path, repo=_repo_module(),
                                                       enable_on_install=True, overwrite=True)
        if "FINISHED" in res:
            _state["done"] = f"Installed {_state['tag']}. Restart Blender to finish the update."
        else:
            _state["error"] = "Blender didn't install the update. Install the zip by hand: " + path
    except Exception as e:
        _state["error"] = f"Install failed ({e}). The zip is at {path}"
    _state["busy"] = ""
    _notify()
    return None


def _notify():
    msg = _state["done"] or _state["error"]
    wm = getattr(bpy.context, "window_manager", None)
    if wm and msg and not bpy.app.background and wm.windows:
        # timers run without a window in the context; a popup without one crashes Blender
        with bpy.context.temp_override(window=wm.windows[0]):
            wm.popup_menu(lambda self, _c: self.layout.label(text=msg), title="Finals Texturer update",
                          icon="INFO" if _state["done"] else "ERROR")
    _redraw()


def _download_worker():
    try:
        name = _state["zip"].rsplit("/", 1)[-1]
        path = os.path.join(tempfile.mkdtemp(prefix="ft_update_"), name)
        with _get(_state["zip"], timeout=120) as r, open(path, "wb") as fh:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                fh.write(chunk)
        _state["busy"] = "installing"
        bpy.app.timers.register(lambda: _install(path), first_interval=0.1)
    except Exception as e:
        _state["error"] = f"Download failed ({e})"
        _state["busy"] = ""
        bpy.app.timers.register(lambda: (_notify(), None)[1], first_interval=0.1)


class FT_OT_update_now(bpy.types.Operator):
    """Download the latest Finals Texturer release from GitHub and install it over this version"""
    bl_idname = "finals_texturer.update_now"
    bl_label = "Update Now"

    @classmethod
    def poll(cls, context):
        return update_available() and bool(_state["zip"]) and not _state["busy"] and online()

    def execute(self, context):
        _state["busy"] = "downloading"
        _state["error"] = ""
        threading.Thread(target=_download_worker, daemon=True).start()
        self.report({"INFO"}, f"Downloading {_state['tag']}…")
        return {"FINISHED"}


class FT_OT_check_updates(bpy.types.Operator):
    """Ask GitHub for the latest Finals Texturer release now"""
    bl_idname = "finals_texturer.check_updates"
    bl_label = "Check for Updates"

    def execute(self, context):
        if not online():
            self.report({"WARNING"}, "Blender's online access is off (Preferences > System > Network)")
            return {"CANCELLED"}
        check(force=True)
        return {"FINISHED"}


# ── UI ───────────────────────────────────────────────────────────────────────

def draw_banner(layout):
    """Box at the top of the Finals panel when a newer release exists (or while updating)."""
    if _state["done"]:
        layout.box().label(text=_state["done"], icon="CHECKMARK")
        return
    if not update_available():
        return
    box = layout.box()
    col = box.column(align=True)
    col.label(text=f"Update available: {_state['tag']} (you have {_fmt(current_version())})", icon="INFO")
    if _state["busy"]:
        col.label(text=f"{_state['busy'].capitalize()}…", icon="SORTTIME")
    elif _state["error"]:
        col.label(text=_state["error"], icon="ERROR")
    row = box.row(align=True)
    row.operator(FT_OT_update_now.bl_idname, icon="IMPORT")
    row.operator("wm.url_open", text="What's New", icon="URL").url = _state["url"]


def draw_prefs(layout, prefs):
    box = layout.box()
    row = box.row()
    row.prop(prefs, "check_updates")
    row.operator(FT_OT_check_updates.bl_idname, icon="FILE_REFRESH")
    col = box.column(align=True)
    if not online():
        col.label(text="Blender's online access is off (Preferences > System > Network)", icon="INFO")
    elif _state["busy"]:
        col.label(text=f"{_state['busy'].capitalize()}…", icon="SORTTIME")
    elif _state["error"]:
        col.label(text=_state["error"], icon="ERROR")
    elif _state["checked"]:
        col.label(text=f"Latest release {_state['tag'] or '?'}, you have {_fmt(current_version())}"
                  + ("" if update_available() else " (up to date)"),
                  icon="INFO" if update_available() else "CHECKMARK")
    if update_available():
        draw_banner(box)


CLASSES = (FT_OT_update_now, FT_OT_check_updates)


def register(want_check):
    for c in CLASSES:
        bpy.utils.register_class(c)

    def _first():
        if want_check():
            check()
        return None
    bpy.app.timers.register(_first, first_interval=5.0, persistent=True)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
