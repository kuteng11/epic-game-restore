"""Apply a restore plan: place game folders at the target root and register them
in the eglv2 installations.jsonl journal.

SAFETY: default is a dry-run. Pass --execute to actually change anything.

Usage:
  python apply_restore.py --plan plan.json                     # dry-run
  python apply_restore.py --plan plan.json --execute --mode move
  python apply_restore.py --plan plan.json --execute --launch-after
  python apply_restore.py --rollback "installations.jsonl.bak-20261108-183000"
"""

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import epic_meta as em  # noqa: E402


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_junction(src, dst):
    try:
        import _winapi
        if hasattr(_winapi, "CreateJunction"):
            _winapi.CreateJunction(src, dst)
            return
    except Exception:
        pass
    r = subprocess.run(
        ["cmd", "/c", "mklink", "/J", dst, src],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError("mklink /J failed: %s %s" % (r.stdout, r.stderr))


def record_for(game, dest_root):
    folder = game["folder"]
    return {
        "installationId": game["installationId"],
        "complete": True,
        "dir": os.path.normpath(os.path.join(dest_root, folder)),
        "platform": "Windows",
        "namespace": game["namespace"],
        "itemId": game["itemId"],
        "artifactId": game["artifactId"],
        "version": game["version"],
        "tags": [""],
        "sidecar": {"revision": 0},
        "launchCommand": game.get("launchCommand") or "",
        "launchExecutable": game["launchExecutable"],
        "writtenAt": utc_now(),
    }


def rollback(args):
    journal = args.journal or (
        json.load(open(args.plan, encoding="utf-8"))["journal"] if args.plan else ""
    )
    if not journal:
        sys.exit("ERROR: cannot determine journal path; pass --journal")
    if em.launcher_running():
        sys.exit("ERROR: close the Epic launcher before rolling back")
    if not os.path.isfile(args.rollback):
        sys.exit("ERROR: backup file not found: %s" % args.rollback)
    if not os.path.isfile(journal):
        sys.exit("ERROR: journal not found: %s" % journal)
    shutil.copy2(args.rollback, journal)
    print("Rollback done: %s -> %s" % (args.rollback, journal))
    print("NOTE: game folders are NOT moved back automatically. Move them manually if needed.")
    return


def main():
    p = argparse.ArgumentParser(description="Apply an Epic restore plan")
    p.add_argument("--plan", default="", help="Plan JSON produced by inspect_backups.py")
    p.add_argument("--mode", choices=["move", "copy", "junction"], default="move")
    p.add_argument("--dest", default="", help="Override the plan's destRoot")
    p.add_argument("--execute", action="store_true", help="Actually perform changes (default: dry-run)")
    p.add_argument("--launch-after", action="store_true", help="Start the Epic launcher after success")
    p.add_argument("--rollback", default="", help="Restore installations.jsonl from this backup and exit")
    p.add_argument("--journal", default="", help="Journal path (needed with --rollback when --plan is absent)")
    args = p.parse_args()

    if args.rollback:
        return rollback(args)
    if not args.plan:
        sys.exit("ERROR: --plan is required")

    plan = json.load(open(args.plan, encoding="utf-8"))
    raw_dest = args.dest or plan.get("destRoot") or ""
    if not raw_dest:
        sys.exit("ERROR: no destRoot in plan; pass --dest")
    dest_root = os.path.abspath(raw_dest)
    journal = plan.get("journal") or em.journal_path(plan.get("launcherData") or em.default_launcher_data())
    launcher_exe = plan.get("launcherExe") or em.find_launcher_exe()

    if plan.get("launcherFlavour") != "eglv2":
        sys.exit("ERROR: launcher is not eglv2; use the official re-install workflow instead")
    if not os.path.isfile(journal):
        sys.exit("ERROR: installations.jsonl not found: %s" % journal)
    if em.launcher_running():
        sys.exit("ERROR: EpicGamesLauncher.exe is running. Close it first (no changes made).")

    _header, records = em.read_journal(journal)
    completed = em.completed_installations(records)
    installed_artifacts = {r.get("artifactId") for r in completed.values()}

    games = [g for g in plan["games"] if g.get("artifactId")]
    todo, skip = [], []
    for g in games:
        dst = os.path.normpath(os.path.join(dest_root, g["folder"]))
        g["_dst"] = dst
        if g["artifactId"] in installed_artifacts:
            skip.append((g, "already registered in installations.jsonl"))
            continue
        if os.path.exists(dst):
            sys.exit("ERROR: target already exists but game is not registered: %s" % dst)
        if not os.path.isdir(os.path.join(g["src"], ".egstore")):
            sys.exit("ERROR: .egstore missing: %s" % g["src"])
        if not os.path.isfile(os.path.join(g["src"], ".egstore", g["installationId"] + ".manifest")):
            sys.exit("ERROR: primary manifest missing: %s" % g["folder"])
        if not g.get("launchExecutable"):
            sys.exit("ERROR: no launch executable parsed for %s" % g["folder"])
        todo.append(g)

    total_gb = sum(g.get("sizeGB") or 0 for g in todo)
    if args.mode == "copy" and total_gb:
        free_gb = shutil.disk_usage(dest_root).free / 1024 ** 3
        if free_gb < total_gb * 1.02:
            sys.exit("ERROR: insufficient disk space (need %.1f GB, free %.1f GB)" % (total_gb, free_gb))

    print("Mode            : %s" % args.mode)
    print("Target root     : %s" % dest_root)
    print("Journal         : %s (%d completed installs already)" % (journal, len(completed)))
    print("To restore      : %d games, %.2f GB" % (len(todo), total_gb))
    for g, why in skip:
        print("  [SKIP] %-26s %s" % (g["folder"], why))
    for g in todo:
        print("  [TODO] %-26s %8s  %s -> %s" %
              (g["folder"], "%.2f GB" % (g.get("sizeGB") or 0), g["src"], g["_dst"]))
        if g.get("warnings"):
            for w in g["warnings"]:
                print("         [WARN] %s" % w)

    if not args.execute:
        print("\nDRY RUN - nothing changed. Re-run with --execute to apply.")
        return
    if not todo:
        print("Nothing to do.")
        return

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = journal + ".bak-" + stamp
    shutil.copy2(journal, backup)
    print("\nJournal backup  : %s" % backup)

    # ---- place files ----
    for g in todo:
        src, dst = g["src"], g["_dst"]
        if args.mode == "move":
            try:
                os.rename(src, dst)          # instant when on the same volume
            except OSError:
                print("  cross-volume move detected for %s, using shutil.move (slower)" % g["folder"])
                shutil.move(src, dst)
        elif args.mode == "copy":
            print("  copying %s ..." % g["folder"])
            shutil.copytree(src, dst)
        else:
            make_junction(src, dst)
        ok = os.path.isfile(os.path.join(dst, ".egstore", g["installationId"] + ".manifest"))
        print("  %-26s -> %s  [%s]" % (g["folder"], dst, "OK" if ok else "VERIFY FAILED"))
        if not ok:
            sys.exit("ERROR: metadata verification failed, stopping. Restore journal from %s" % backup)

    # ---- append journal records ----
    with open(journal, "rb") as f:
        f.seek(-1, 2)
        last = f.read(1)
    prefix = "" if last == b"\n" else "\n"
    lines = [json.dumps(record_for(g, dest_root), ensure_ascii=False, separators=(",", ":")) for g in todo]
    with open(journal, "a", encoding="utf-8", newline="\n") as f:
        f.write(prefix)
        f.write("\n".join(lines) + "\n")

    _h2, recs2 = em.read_journal(journal)
    now_done = em.completed_installations(recs2)
    print("\nAppended %d records; completed installs now: %d" % (len(lines), len(now_done)))
    print("NOTE: the journal header's 'entries' count is compacted by the launcher on next start; do not edit it manually.")

    if args.launch_after:
        if not launcher_exe or not os.path.isfile(launcher_exe):
            print("WARNING: launcher exe not found; start it manually.")
        else:
            os.startfile(launcher_exe)
            print("Launcher started: %s" % launcher_exe)

    print("\nNext: wait ~10 s, then run verify_restore.py --plan %s" % args.plan)


if __name__ == "__main__":
    main()
