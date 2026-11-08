"""Shared helpers for reading Epic Games Launcher (eglv2) install metadata.

This module is intentionally dependency-free (stdlib only) and Windows-oriented.
It is imported by inspect_backups.py / apply_restore.py / verify_restore.py.
"""

import glob
import io
import json
import os
import struct
import subprocess
import sys
import zlib

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# --------------------------------------------------------------------------
# Launcher discovery
# --------------------------------------------------------------------------

def local_app_data():
    return os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local"
    )


def default_launcher_data():
    return os.path.join(local_app_data(), "com.epicgames.eglv2")


def find_launcher_exe(explicit=None):
    if explicit and os.path.isfile(explicit):
        return explicit
    candidates = [
        os.path.join(local_app_data(), "Epic Games", "Launcher", "EpicGamesLauncher.exe"),
        r"C:\Program Files\Epic Games\Launcher\Portal\Binaries\Win64\EpicGamesLauncher.exe",
        r"C:\Program Files (x86)\Epic Games\Launcher\Portal\Binaries\Win64\EpicGamesLauncher.exe",
        os.path.join(local_app_data(), "EpicGamesLauncher", "EpicGamesLauncher.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def launcher_running():
    """Return True if EpicGamesLauncher.exe is in the task list."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq EpicGamesLauncher.exe"],
            capture_output=True, timeout=30,
        ).stdout
        return b"EpicGamesLauncher.exe" in out
    except Exception:
        return False


def detect_launcher_flavour(launcher_data):
    """Return 'eglv2', 'legacy', or 'unknown'."""
    if os.path.isfile(os.path.join(launcher_data, "installations.jsonl")):
        return "eglv2"
    legacy = r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests"
    if os.path.isdir(legacy):
        return "legacy"
    return "unknown"


def journal_path(launcher_data):
    return os.path.join(launcher_data, "installations.jsonl")


def read_journal(path):
    """Return (header_dict_or_None, [record_dicts]) from an installations.jsonl."""
    with open(path, encoding="utf-8") as f:
        lines = [l for l in f.read().splitlines() if l.strip()]
    if not lines:
        return None, []
    header = None
    start = 0
    first = json.loads(lines[0])
    if "entries" in first and "installationId" not in first:
        header, start = first, 1
    return header, [json.loads(l) for l in lines[start:]]


def completed_installations(records):
    """Collapse the append-only log: last record per installationId wins."""
    latest = {}
    for r in records:
        latest[r.get("installationId")] = r
    return {k: v for k, v in latest.items() if v.get("complete")}


# --------------------------------------------------------------------------
# Library cache (used to resolve which manifest is the real game, not a DLC/tool)
# --------------------------------------------------------------------------

EXTRA_TITLE_WORDS = (
    "assembly kit", "dlc", "editor", "tool", "tools", "sdk", "soundtrack",
    "bonus", "content pack", "preorder", "pre-order", "test", "server",
    "dedicated", "mod", "beta", "demo", "sound pack", "artbook",
)


def load_library_index(launcher_data):
    """Map (namespaceId, itemId, appName) -> title from all account caches."""
    pattern = os.path.join(launcher_data, "cache", "prod", "account", "*", "library-cache.json")
    index = {}
    for p in glob.glob(pattern):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        for it in d.get("items", []):
            a = it.get("appId")
            if not isinstance(a, dict):
                continue
            key = (a.get("namespaceId"), a.get("itemId"), a.get("appName"))
            index[key] = it.get("title") or ""
    return index


# --------------------------------------------------------------------------
# .egstore manifest parsing
# --------------------------------------------------------------------------

def _read_fstring(bio):
    raw = bio.read(4)
    if len(raw) < 4:
        return None
    n = struct.unpack("<i", raw)[0]
    if n == 0:
        return ""
    if n < 0:
        return bio.read(-n * 2).decode("utf-16-le", errors="replace").rstrip("\x00")
    return bio.read(n).decode("utf-8", errors="replace").rstrip("\x00")


def parse_manifest(path):
    """Extract metadata from a .manifest (binary eglv2 or legacy JSON).

    Returns dict with keys: appName, buildVersion, launchExe, launchCommand,
    fileCount (JSON only), format ('json'|'binary').
    """
    with open(path, "rb") as f:
        data = f.read()
    if data[:1] == b"{":
        d = json.loads(data.decode("utf-8-sig"))
        return {
            "format": "json",
            "appName": d.get("AppNameString") or "",
            "buildVersion": d.get("BuildVersionString") or "",
            "launchExe": d.get("LaunchExeString") or "",
            "launchCommand": d.get("LaunchCommand") or "",
            "fileCount": len(d.get("FileManifestList") or []),
        }
    if len(data) < 41:
        raise ValueError("manifest too short: %s" % path)
    header_size = struct.unpack_from("<I", data, 4)[0]
    stored_as = data[36]
    body = data[header_size:]
    if stored_as & 1:
        body = zlib.decompress(body)
    bio = io.BytesIO(body)
    # Meta header: u32 size, u8 dataVersion, u32 featureLevel, u8 isFile, u32 appId
    bio.read(4 + 1 + 4 + 1 + 4)
    fields = [
        "appName", "buildVersion", "launchExe", "launchCommand",
        "prereqName", "prereqPath", "prereqArgs", "buildId",
    ]
    out = {"format": "binary", "fileCount": None}
    for key in fields:
        v = _read_fstring(bio)
        if v is None:
            break
        out[key] = v
    for key in fields:
        out.setdefault(key, "")
    return out


def parse_mancpn(path):
    """Read a .mancpn companion file -> catalog ids."""
    d = json.load(open(path, encoding="utf-8-sig"))
    return {
        "namespace": d.get("CatalogNamespace") or "",
        "itemId": d.get("CatalogItemId") or "",
        "artifactId": d.get("AppName") or "",
    }


def scan_egstore(game_dir):
    """Return all candidate installation records for one game folder.

    Each candidate is keyed by the manifest GUID (= installationId).
    """
    eg = os.path.join(game_dir, ".egstore")
    if not os.path.isdir(eg):
        return []
    candidates = []
    for mpath in sorted(glob.glob(os.path.join(eg, "*.manifest"))):
        guid = os.path.splitext(os.path.basename(mpath))[0]
        rec = {
            "installationId": guid,
            "manifest": os.path.basename(mpath),
            "manifestSize": os.path.getsize(mpath),
            "manifestMtime": os.path.getmtime(mpath),
            "hasCompanion": False,
            "namespace": "", "itemId": "", "artifactId": "",
            "appName": "", "version": "", "launchExecutable": "",
            "launchCommand": "", "fileCount": None, "format": "",
            "launchExeExists": False,
        }
        cpath = os.path.join(eg, guid + ".mancpn")
        if os.path.isfile(cpath):
            rec.update(parse_mancpn(cpath))
            rec["hasCompanion"] = True
        try:
            m = parse_manifest(mpath)
        except Exception as e:
            rec["parseError"] = str(e)
            candidates.append(rec)
            continue
        rec.update({
            "format": m["format"],
            "appName": m.get("appName") or "",
            "version": m.get("buildVersion") or "",
            "launchExecutable": (m.get("launchExe") or "").replace("\\", "/"),
            "launchCommand": m.get("launchCommand") or "",
            "fileCount": m.get("fileCount"),
        })
        exe = rec["launchExecutable"]
        if exe:
            rec["launchExeExists"] = os.path.isfile(
                os.path.join(game_dir, *exe.split("/"))
            )
        candidates.append(rec)
    return candidates


def score_candidate(c, library_index):
    """Rank candidates so the playable base game wins over DLCs / tool packs."""
    score = 0.0
    if c.get("hasCompanion"):
        score += 10
    if c.get("launchExeExists"):
        score += 10
    if c.get("parseError"):
        score -= 100
    title = library_index.get((c.get("namespace"), c.get("itemId"), c.get("artifactId")))
    c["libraryTitle"] = title
    c["inLibrary"] = title is not None
    if title:
        score += 8
        if any(w in title.lower() for w in EXTRA_TITLE_WORDS):
            score -= 25
    score += min(c.get("manifestSize", 0) / 1_000_000.0, 20)  # main game manifest is much larger
    c["score"] = round(score, 2)
    return score


def folder_size_gb(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return round(total / 1024 ** 3, 2)
