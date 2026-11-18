"""Run a command with the shader tools' settings taken from FModel's own setup.

    python -I dev_tools/shader_extract/ftenv.py <command> [args...]

ShaderExtract (dotnet run …) and check_items.py need FT_GAME_PAKS, FT_AES, FT_USMAP and FT_FMODEL_DATA. This
reads them from %APPDATA%\\FModel\\AppSettings.json (the game folder FModel has open, its AES key, its .usmap
mappings, its .data folder) and starts the command with them set. The key only ever lives in that process's
environment: it is never printed or written to a file. Variables you set yourself win.

Example:
    python -I dev_tools/shader_extract/ftenv.py dotnet run --project dev_tools/shader_extract -c Release -- out \\
        Discovery/Content/Discovery/Characters/Nails/M_CharacterNails_Base
"""

import json
import os
import subprocess
import sys


def fmodel_env():
    path = os.path.join(os.environ.get("APPDATA", ""), "FModel", "AppSettings.json")
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    game = d["GameDirectory"]
    per = d.get("PerDirectory", {}).get(game) or {}
    usmap = next((e.get("FilePath") for e in per.get("Endpoints", []) if e.get("FilePath")), "")
    return {
        "FT_GAME_PAKS": os.path.join(game, "Discovery", "Content", "Paks"),
        "FT_AES": (per.get("AesKeys") or {}).get("mainKey", ""),
        "FT_USMAP": usmap,
        "FT_FMODEL_DATA": os.path.join(d.get("OutputDirectory", ""), ".data"),
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    env = dict(os.environ)
    try:
        for k, v in fmodel_env().items():
            env.setdefault(k, v)
    except (OSError, KeyError, ValueError) as e:
        print(f"Couldn't read FModel's settings ({type(e).__name__}): open the game in FModel once, "
              "or set FT_GAME_PAKS / FT_AES / FT_USMAP / FT_FMODEL_DATA yourself.", file=sys.stderr)
        return 1
    missing = [k for k in ("FT_GAME_PAKS", "FT_AES", "FT_USMAP") if not env.get(k)]
    if missing:
        print(f"Not set and not found in FModel's settings: {', '.join(missing)}", file=sys.stderr)
        return 1
    return subprocess.call(sys.argv[1:], env=env)


if __name__ == "__main__":
    sys.exit(main())
