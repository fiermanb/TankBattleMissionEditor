# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Build the standalone Windows executable of the mission editor.

    python packaging/build_exe.py [--game "K:/SteamLibrary/steamapps/common/Tank Battle Classic"]

Steps: create a clean virtual environment (packaging/.venv) with pinned
dependencies, generate the icon and version resource, run PyInstaller (one-file,
windowed), run the executable's self-test against an installed game, and
assemble packaging/release/TankBattleMissionEditor-<version>-win64(.zip) with the
executable, README.txt, LICENSE.txt and THIRD-PARTY-NOTICES.txt.

The proprietary FMOD library is not included: UnityPy's audio conversion is
replaced by packaging/stubs/fmod_toolkit (the editor does not use audio).
"""

import argparse
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
VENV = os.path.join(HERE, ".venv")
WORK = os.path.join(HERE, "build")
DIST = os.path.join(HERE, "dist")
RELEASE = os.path.join(HERE, "release")
NAME = "TankBattleMissionEditor"
UNITYPY = "UnityPy==1.25.4"
BUILD_TOOLS = {"pip", "setuptools", "wheel", "pyinstaller", "pyinstaller-hooks-contrib", "altgraph",
               "pefile", "pywin32-ctypes", "packaging"}


def run(cmd, **kw):
    print(">", " ".join(f'"{c}"' if " " in c else c for c in cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def venv_python():
    return os.path.join(VENV, "Scripts", "python.exe")


def read_version():
    ns = {}
    with open(os.path.join(SRC, "version.py"), encoding="utf-8") as f:
        exec(f.read(), ns)
    return ns["APP_NAME"], ns["__version__"]


def make_venv():
    if not os.path.exists(venv_python()):
        run([sys.executable, "-m", "venv", VENV])
    py = venv_python()
    run([py, "-m", "pip", "install", "--disable-pip-version-check", "-q", "--upgrade", "pip"])
    run([py, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r",
         os.path.join(HERE, "requirements-build.txt")])
    run([py, "-m", "pip", "install", "--disable-pip-version-check", "-q", "--no-deps", UNITYPY])


def make_icon_and_version(app_name, version):
    os.makedirs(WORK, exist_ok=True)
    ico = os.path.join(WORK, "app.ico")
    code = ("import sys; sys.path.insert(0, r'%s'); import icons; ims = icons.app_icon_images(); "
            "ims[-1].save(r'%s', sizes=[(i.width, i.height) for i in ims])" % (SRC, ico))
    run([venv_python(), "-c", code])
    parts = [int(p) for p in version.split(".")] + [0] * 4
    vt = tuple(parts[:4])
    info = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={vt}, prodvers={vt}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', '{app_name}'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', '{NAME}'),
      StringStruct('OriginalFilename', '{NAME}.exe'),
      StringStruct('ProductName', '{app_name}'),
      StringStruct('ProductVersion', '{version}'),
      StringStruct('LegalCopyright', 'Copyright (C) 2026 fierman. GPL-3.0.')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    vfile = os.path.join(WORK, "version_info.txt")
    with open(vfile, "w", encoding="utf-8") as f:
        f.write(info)
    return ico, vfile


def pyinstaller(ico, vfile, console=False):
    cmd = [venv_python(), "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
           "--console" if console else "--windowed", "--noupx",
           "--name", NAME, "--icon", ico, "--version-file", vfile,
           "--distpath", DIST, "--workpath", WORK, "--specpath", WORK,
           "--paths", SRC, "--paths", os.path.join(HERE, "stubs"),
           "--add-data", f"{os.path.join(SRC, 'layouts')}{os.pathsep}layouts", "--collect-data", "UnityPy",
           "--collect-all", "etcpak", "--collect-all", "astc_encoder", "--collect-all", "texture2ddecoder",
           "--collect-data", "archspec", "--collect-all", "tpk_ar",
           "--hidden-import", "fmod_toolkit",
           "--exclude-module", "pyfmodex", "--exclude-module", "tests",
           "--exclude-module", "TypeTreeGeneratorAPI",
           os.path.join(SRC, "editor.py")]
    run(cmd)
    return os.path.join(DIST, NAME + ".exe")


def selftest(exe, game):
    out = os.path.join(WORK, "selftest.txt")
    if os.path.exists(out):
        os.remove(out)
    cmd = [exe, "--selftest", out] + (["--game", game] if game else [])
    print(">", " ".join(cmd), flush=True)
    code = subprocess.run(cmd).returncode
    text = open(out, encoding="utf-8").read() if os.path.exists(out) else "(no result file)"
    print(text)
    if code != 0 or "RESULT: OK" not in text:
        raise SystemExit("Self-test of the executable failed.")


def notices():
    """THIRD-PARTY-NOTICES.txt from the licences of the bundled packages,
    Python and Tcl/Tk (generated inside the build environment)."""
    code = r'''
import importlib.metadata as md, os, sys
skip = set(sys.argv[1].split(","))
out = ["THIRD-PARTY NOTICES", "", "Tank Battle Classic Mission Editor is Copyright (C) 2026 fierman and licensed under the",
       "GNU General Public License version 3 (LICENSE.txt). The executable bundles the following",
       "software under its own licences, whose texts follow.", ""]
dists = sorted({d.metadata["Name"]: d for d in md.distributions()}.values(), key=lambda d: d.metadata["Name"].lower())
body = []
for d in dists:
    name = d.metadata["Name"]
    if name.lower() in skip:
        continue
    lic = d.metadata.get("License-Expression") or ""
    short = (d.metadata.get("License") or "").strip()
    if not lic and short and "\n" not in short and len(short) <= 60:
        lic = short
    if not lic:
        lic = next((c.split("::")[-1].strip() for c in d.metadata.get_all("Classifier") or [] if c.startswith("License ::")), "")
    if not lic:
        lic = (d.metadata.get("License") or "see licence text").strip().splitlines()[0]
    out.append(f"- {name} {d.version}: {lic}")
    texts = []
    for f in d.files or []:
        rel = str(f).replace(os.sep, "/").lower()
        base = rel.rsplit("/", 1)[-1].upper()
        if ".dist-info/" in rel and (base.startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS")) or "/licenses/" in rel):
            try:
                texts.append(open(f.locate(), encoding="utf-8", errors="replace").read())
            except Exception:
                pass
    body.append("=" * 78 + f"\n{name} {d.version}\n" + "=" * 78 + "\n" + ("\n\n".join(texts) if texts else lic) + "\n")
out.append(f"- Python {sys.version.split()[0]}: Python Software Foundation License")
out.append("- Tcl/Tk: Tcl/Tk licence (BSD-style)")
out.append("- PyInstaller bootloader: GPL 2.0 with an exception that allows distributing bundled applications")
out.append("")
py_lic = os.path.join(sys.base_prefix, "LICENSE.txt")
if os.path.exists(py_lic):
    body.append("=" * 78 + "\nPython\n" + "=" * 78 + "\n" + open(py_lic, encoding="utf-8", errors="replace").read())
tcl = os.path.join(sys.base_prefix, "tcl", "tcl8.6", "license.terms")
if os.path.exists(tcl):
    body.append("=" * 78 + "\nTcl/Tk\n" + "=" * 78 + "\n" + open(tcl, encoding="utf-8", errors="replace").read())
text = "\n".join(out) + "\n" + "\n".join(body)
text = "".join(ch if ord(ch) < 128 else "?" for ch in text)
open(sys.argv[2], "w", encoding="utf-8", newline="\n").write(text)
'''
    target = os.path.join(WORK, "THIRD-PARTY-NOTICES.txt")
    run([venv_python(), "-c", code, ",".join(sorted(BUILD_TOOLS)), target])
    return target


def assemble(exe, version, notice_file):
    folder = os.path.join(RELEASE, f"{NAME}-{version}-win64")
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    os.makedirs(folder)
    shutil.copy2(exe, folder)
    shutil.copy2(os.path.join(HERE, "README-release.txt"), os.path.join(folder, "README.txt"))
    shutil.copy2(os.path.join(SRC, "LICENSE"), os.path.join(folder, "LICENSE.txt"))
    shutil.copy2(notice_file, folder)
    zpath = folder + ".zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(os.listdir(folder)):
            z.write(os.path.join(folder, f), f"{NAME}-{version}-win64/{f}")
    return folder, zpath


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--game", help="installed game folder for the self-test (default: detected)")
    ap.add_argument("--skip-venv", action="store_true", help="reuse the existing build environment as is")
    ap.add_argument("--console", action="store_true", help="build with a console window (for diagnosing errors)")
    ap.add_argument("--package", metavar="EXE",
                    help="only assemble the release around an already built (for example signed) executable")
    ap.add_argument("--no-selftest", action="store_true",
                    help="skip the self-test (for builds on machines without the game, such as CI)")
    args = ap.parse_args()
    app_name, version = read_version()
    if args.package:
        folder, zpath = assemble(os.path.abspath(args.package), version, notices())
        print(f"Release folder: {folder}")
        print(f"Release zip:    {zpath}")
        return
    if not args.skip_venv:
        make_venv()
    ico, vfile = make_icon_and_version(app_name, version)
    exe = pyinstaller(ico, vfile, args.console)
    if not args.no_selftest:
        selftest(exe, args.game)
    folder, zpath = assemble(exe, version, notices())
    size = os.path.getsize(exe) / 1e6
    print(f"\nBuilt {exe} ({size:.1f} MB)\nRelease folder: {folder}\nRelease zip:    {zpath}")


if __name__ == "__main__":
    main()
