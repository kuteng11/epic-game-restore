"""Read-only scanner: turn a folder of backed-up Epic games into a restore plan.

Usage:
  python inspect_backups.py --backup "G:\\Game\\epic备份" --dest "G:\\Game\\Epic Game" --out plan.json

Writes nothing except the optional --out plan file. Prints a human summary.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import epic_meta as em  # noqa: E402


def build_parser():
    p = argparse.ArgumentParser(description="Scan Epic game backups and build a restore plan")
    p.add_argument("--backup", required=True, help="Folder containing backed-up game folders (each with .egstore)")
    p.add_argument("--dest", default="", help="Target install root, e.g. G:\\Game\\Epic Game")
    p.add_argument("--launcher-data", default="", help="eglv2 data dir (default: %%LOCALAPPDATA%%\\com.epicgames.eglv2)")
    p.add_argument("--out", default="", help="Write the plan JSON here (otherwise print JSON to stdout after summary)")
    p.add_argument("--no-size", action="store_true", help="Skip computing folder sizes (faster on huge libraries)")
    p.add_argument("--include", default="", help="Comma-separated folder names to include only")
    p.add_argument("--all-candidates", action="store_true", help="Print every manifest candidate, not just the chosen one")
    return p


def main():
    a = build_parser().parse_args()
    backup = os.path.abspath(a.backup)
    dest = os.path.abspath(a.dest) if a.dest else ""
    data = a.launcher_data or em.default_launcher_data()

    if not os.path.isdir(backup):
        sys.exit("ERROR: backup folder does not exist: %s" % backup)

    flavour = em.detect_launcher_flavour(data)
    journal = em.journal_path(data)
    library = em.load_library_index(data) if flavour == "eglv2" else {}
    existing = {}
    if flavour == "eglv2" and os.path.isfile(journal):
        _h, recs = em.read_journal(journal)
        existing = em.completed_installations(recs)

    include = [x.strip() for x in a.include.split(",") if x.strip()]
    folders = sorted(
        d for d in os.listdir(backup)
        if os.path.isdir(os.path.join(backup, d)) and (not include or d in include)
    )

    games, skipped = [], []
    for folder in folders:
        src = os.path.join(backup, folder)
        eg = os.path.join(src, ".egstore")
        if not os.path.isdir(eg):
            skipped.append({"folder": folder, "reason": "no .egstore"})
            continue
        cands = em.scan_egstore(src)
        if not cands:
            skipped.append({"folder": folder, "reason": "no .manifest"})
            continue
        for c in cands:
            em.score_candidate(c, library)
        cands.sort(key=lambda c: (c["score"], c.get("manifestMtime", 0)), reverse=True)
        chosen = dict(cands[0])

        dst = os.path.normpath(os.path.join(dest, folder)) if dest else ""
        conflict = ""
        if dst and os.path.exists(dst):
            conflict = "target already exists"
        dup = ""
        if existing and chosen.get("artifactId") in [r.get("artifactId") for r in existing.values()]:
            dup = "already in installations.jsonl"

        game = {
            "folder": folder,
            "src": src,
            "dst": dst,
            "sizeGB": None if a.no_size else em.folder_size_gb(src),
            "installationId": chosen["installationId"],
            "namespace": chosen["namespace"],
            "itemId": chosen["itemId"],
            "artifactId": chosen["artifactId"],
            "version": chosen["version"],
            "launchExecutable": chosen["launchExecutable"],
            "launchCommand": chosen.get("launchCommand") or "",
            "libraryTitle": chosen.get("libraryTitle"),
            "inLibrary": chosen.get("inLibrary", False),
            "launchExeExists": chosen.get("launchExeExists", False),
            "manifestFormat": chosen.get("format"),
            "warnings": [w for w in (conflict, dup) if w],
            "candidates": [
                {
                    "installationId": c["installationId"],
                    "artifactId": c["artifactId"],
                    "libraryTitle": c.get("libraryTitle"),
                    "score": c["score"],
                    "manifestSize": c["manifestSize"],
                }
                for c in cands
            ],
        }
        if not chosen.get("hasCompanion"):
            game["warnings"].append("manifest has no matching .mancpn (catalog ids missing)")
        if not chosen.get("launchExeExists"):
            game["warnings"].append("launch executable not found on disk")
        if not chosen.get("inLibrary"):
            game["warnings"].append("catalog ids not found in library-cache (verify ownership/account)")
        games.append(game)

    plan = {
        "schema": "epic-restore-plan-v1",
        "backupRoot": backup,
        "destRoot": dest,
        "launcherData": data,
        "launcherFlavour": flavour,
        "journal": journal,
        "launcherExe": em.find_launcher_exe(),
        "launcherRunning": em.launcher_running(),
        "existingCompletedInstallations": len(existing),
        "games": games,
        "skipped": skipped,
    }

    # ---- human-readable summary ----
    print("Launcher flavour : %s" % flavour)
    print("Journal          : %s (%d completed installs)" % (journal, len(existing)))
    print("Launcher running: %s" % plan["launcherRunning"])
    print("Games found     : %d" % len(games))
    print("-" * 96)
    for g in games:
        size = "?" if g["sizeGB"] is None else "%.2f GB" % g["sizeGB"]
        print("  %-26s %10s  %s" % (g["folder"], size, g["libraryTitle"] or g["artifactId"]))
        print("      id=%s  ver=%s  exe=%s" %
              (g["installationId"], g["version"], g["launchExecutable"]))
        if g["warnings"]:
            for w in g["warnings"]:
                print("      [WARN] %s" % w)
        if a.all_candidates and len(g["candidates"]) > 1:
            for c in g["candidates"]:
                print("      cand %s score=%-7s %s" %
                      (c["installationId"], c["score"], c["libraryTitle"] or c["artifactId"]))
    for s in skipped:
        print("  [SKIP] %-26s %s" % (s["folder"], s["reason"]))
    print("-" * 96)
    if flavour != "eglv2":
        print("NOTE: this is NOT an eglv2 launcher. Do not write installations.jsonl;")
        print("      follow the official re-install workflow in references/eglv2-format.md")
    print("Review the plan, then run apply_restore.py --plan <file> (dry-run first).")

    payload = json.dumps(plan, ensure_ascii=False, indent=2)
    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(payload + "\n")
        print("Plan written to: %s" % os.path.abspath(a.out))
    else:
        print("\n==== PLAN JSON ====")
        print(payload)


if __name__ == "__main__":
    main()
