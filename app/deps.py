"""What Looper OSS still needs from the operating system, and how to get it.

The AppImage carries everything Python: a CPython, ``websockets`` and
``python-osc``. Nothing on the Python side has to be installed by hand.

Four things are deliberately *not* in the bundle, because they are the
system's own, not the app's:

* **SuperCollider** (``sclang`` + ``scsynth``) -- the audio engine. It is a
  sound server, the same way PipeWire is; it links against the system's libjack,
  libpipewire, libsndfile and libsystemd, and a bundled copy would have to pin
  those too, which is how an audio server ends up fighting the host's.
* **sc3-plugins** -- the extra UGens the engine's SynthDefs use.
* **GTK 3 / WebKit2GTK / PyGObject** -- the window. GTK belongs to the desktop
  and is built against the host's Python; a bundled copy is the classic
  AppImage failure ("works on the build machine").

So instead of making anyone hunt through package names, this module knows what
is missing, names the packages for the distro, and installs them in one step
behind a single polkit prompt. ``--deps`` reports, ``--install-deps`` installs.
"""
from __future__ import annotations

import glob
import os
import pathlib
import shutil
import subprocess
import sys

PLUGIN_DIRS = (
    "/usr/lib/SuperCollider/plugins",
    "/usr/local/lib/SuperCollider/plugins",
    "/usr/lib/sc3-plugins",
    "/usr/share/SuperCollider/Extensions",
    str(pathlib.Path.home() / ".local" / "share" / "SuperCollider" / "Extensions"),
)

# family -> capability -> package names, exactly as the distro spells them
PACKAGES: dict[str, dict[str, str]] = {
    "arch": {
        "gtk": "python-gobject gtk3",
        "webkit": "webkit2gtk-4.1",
        "sc": "supercollider",
        "plugins": "sc3-plugins",
    },
    "debian": {
        "gtk": "python3-gi gir1.2-gtk-3.0",
        "webkit": "gir1.2-webkit2-4.1",
        "sc": "supercollider",
        "plugins": "sc3-plugins",
    },
    "fedora": {
        "gtk": "python3-gobject gtk3",
        "webkit": "webkit2gtk4.1",
        "sc": "supercollider",
        "plugins": "sc3-plugins",
    },
    "suse": {
        "gtk": "python3-gobject gtk3",
        "webkit": "webkit2gtk4.1",
        "sc": "supercollider",
        "plugins": "sc3-plugins",
    },
}

INSTALLERS = {
    "arch": ["pacman", "-S", "--needed", "--noconfirm"],
    "debian": ["apt", "install", "-y"],
    "fedora": ["dnf", "install", "-y"],
    "suse": ["zypper", "install", "-y"],
}


def _os_release() -> dict[str, str]:
    found: dict[str, str] = {}
    try:
        with open("/etc/os-release", encoding="utf-8") as fh:
            for line in fh:
                if "=" in line:
                    key, value = line.rstrip("\n").split("=", 1)
                    found[key] = value.strip().strip('"')
    except OSError:
        pass
    return found


def family() -> str:
    """The package-manager family, from ID/ID_LIKE (arch, debian, ...)."""
    info = _os_release()
    text = f"{info.get('ID', '')} {info.get('ID_LIKE', '')}".lower()
    for fam, markers in (
        ("arch", ("arch", "manjaro", "endeavouros", "cachyos", "artix")),
        ("debian", ("debian", "ubuntu", "linuxmint", "pop", "raspbian")),
        ("fedora", ("fedora", "rhel", "centos", "rocky", "alma")),
        ("suse", ("suse", "opensuse", "sles")),
    ):
        if any(m in text for m in markers):
            return fam
    return "unknown"


def gi_paths() -> list[str]:
    """Distro site-packages that hold PyGObject.

    A virtualenv is created with ``include-system-site-packages = false``, so a
    source check-out's python cannot see the distro's ``gi`` at all -- and the
    window then falls back to a browser without saying why. `_native()` puts
    these back on ``sys.path``; the probe below has to do the same or it would
    report a dependency missing that the window can actually use.
    """
    found: list[str] = []
    for pattern in ("/usr/lib/python3/dist-packages",
                    "/usr/lib/python3.*/site-packages",
                    "/usr/local/lib/python3.*/site-packages"):
        found += sorted(glob.glob(pattern))
    return [p for p in found if os.path.isdir(os.path.join(p, "gi"))]


def _imports(code: str) -> bool:
    """Ask *the interpreter that will open the window*, not some other python."""
    prelude = "import sys\n" + "".join(
        f"sys.path.append({p!r})\n" for p in gi_paths())
    try:
        done = subprocess.run(
            [sys.executable, "-c", prelude + code],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _has_plugins() -> bool:
    return any(pathlib.Path(d).is_dir() for d in PLUGIN_DIRS)


CHECKS = (
    ("sc", "SuperCollider (sclang + scsynth)",
     lambda: shutil.which("sclang") is not None and shutil.which("scsynth") is not None),
    ("plugins", "sc3-plugins",
     _has_plugins),
    ("gtk", "GTK 3 + PyGObject",
     lambda: _imports("import gi; gi.require_version('Gtk', '3.0')")),
    ("webkit", "WebKit2GTK",
     lambda: _imports(
         "import gi\n"
         "try:\n"
         "    gi.require_version('WebKit2', '4.1')\n"
         "except ValueError:\n"
         "    gi.require_version('WebKit2', '4.0')")),
)


def report() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for key, label, check in CHECKS:
        try:
            ok = bool(check())
        except Exception:                      # noqa: BLE001 - a probe must not crash a start
            ok = False
        rows.append({"key": key, "label": label, "ok": ok})
    return rows


def missing_keys() -> list[str]:
    return [str(row["key"]) for row in report() if not row["ok"]]


def install_command(keys: list[str] | None = None) -> list[str] | None:
    keys = missing_keys() if keys is None else keys
    fam = family()
    base = INSTALLERS.get(fam)
    table = PACKAGES.get(fam)
    if not base or not table:
        return None
    names: list[str] = []
    for key in keys:
        names += table.get(key, "").split()
    if not names:
        return None
    return [*base, *sorted(set(names))]


def install(keys: list[str] | None = None) -> int:
    """Install what is missing. Returns the process's exit code (0 = fine)."""
    keys = missing_keys() if keys is None else keys
    if not keys:
        print("Looper OSS: nothing to install -- every dependency is present.")
        return 0
    cmd = install_command(keys)
    if cmd is None:
        print("Looper OSS: I do not recognise this distribution's package manager.\n"
              "Install these, then start Looper OSS again:\n"
              "  SuperCollider (sclang + scsynth), sc3-plugins,\n"
              "  GTK 3 and WebKit2GTK with their PyGObject bindings.")
        return 1
    # A double-clicked AppImage has no terminal, so sudo cannot ask for a
    # password. pkexec raises a desktop password dialog instead.
    for elevator in ("pkexec", "sudo"):
        if shutil.which(elevator):
            cmd = [elevator, *cmd]
            break
    print("Looper OSS: installing " + ", ".join(keys))
    print("  + " + " ".join(cmd), flush=True)
    try:
        code = subprocess.run(cmd).returncode
    except OSError as exc:
        print(f"  could not run the installer: {exc}")
        return 1
    still = missing_keys()
    if still:
        print("  still missing: " + ", ".join(still))
        return code or 1
    print("Looper OSS: all dependencies present.")
    return 0


def hint() -> str:
    """One line a person can act on, or '' when nothing is missing."""
    keys = missing_keys()
    if not keys:
        return ""
    if os.environ.get("LOOPER_OSS_PACKAGED"):
        return ("Missing: " + ", ".join(keys) + ".\n"
                "Let Looper OSS install them:  --install-deps")
    return ("Missing: " + ", ".join(keys) + ".\n"
            "Install them with:  python3 looper_oss.py --install-deps")


def summary() -> str:
    rows = report()
    lines = [f"  {'ok  ' if r['ok'] else 'MISS'}  {r['label']}" for r in rows]
    cmd = install_command()
    if cmd:
        lines.append("")
        lines.append("  install: " + " ".join(cmd))
    elif any(not r["ok"] for r in rows):
        lines.append("")
        lines.append("  install: unknown distribution -- install the missing packages by hand")
    return "\n".join(lines)
