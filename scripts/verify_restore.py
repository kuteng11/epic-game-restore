"""Post-restart verification: check launcher logs and installations.jsonl.

Usage:
  python verify_restore.py --plan plan.json
  python verify_restore.py --launcher-data "C:\\Users\\me\\AppData\\Local\\com.epicgames.eglv2"
"""

import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import epic_meta as em  # noqa: E402

KEY_PATTERNS = [
    ("journal replay", re.compile(r"Replayed the installations log", re.I)),
    ("executable check", re.compile(r"ExecutableCheckCompleted", re.I)),
    ("dropped records", re.compile(r"Dropped \d+ unplayable installation record", re.I)),
    ("state CanLaunch", re.compile(r"new_state=CanLaunch", re.I)),
    ("state not launchable", re.compile(r"ProductState transition.*new_state=(?!CanLaunch)\w+", re.I)),
    ("update available", re.compile(r"update available|UpdateCheck|pendingVersion", re.I)),
    ("ERROR-level lines", re.compile(r"\sERROR\s")),
]


def main():
    p = argparse.ArgumentParser(description="Verify an Epic restore after restarting the launcher")
    p.add_argument("--plan", default="")
    p.add_argument("--launcher-data", default="")
    p.add_argument("--log-lines", type=int, default=6, help="Max matching log lines per category")
    a = p.parse_args()

    plan = json.load(open(a.plan, encoding="utf-8")) if a.plan else {}
    data = a.launcher_data or plan.get("launcherData") or em.default_launcher_data()
    journal = plan.get("journal") or em.journal_path(data)
    expected = {g["artifactId"]: g for g in plan.get("games", [])}

    print("Launcher data : %s" % data)
    print("Journal       : %s" % journal)

    # ---- 1. journal state ----
    _h, recs = em.read_journal(journal)
    done = em.completed_installations(recs)
    print("Completed installs: %d\n" % len(done))
    print("%-34s %-7s %-7s %s" % ("artifactId", "dir", "exe", "path"))
    print("-" * 110)
    ok_count = 0
    for iid, r in done.items():
        d = r.get("dir", "")
        exe = (r.get("launchExecutable") or "").replace("\\", "/")
        dir_ok = os.path.isdir(d)
        exe_ok = bool(exe) and os.path.isfile(os.path.join(d, *exe.split("/")))
        if dir_ok and exe_ok:
            ok_count += 1
        mark = "OK" if (dir_ok and exe_ok) else "CHECK"
        print("%-34s %-7s %-7s %-4s %s" %
              (r.get("artifactId", "")[:34], "yes" if dir_ok else "NO",
               "yes" if exe_ok else "NO", mark, d))
    print("-" * 110)
    print("Filesystem check: %d/%d OK" % (ok_count, len(done)))

    # ---- 2. latest launcher log (needed for CanLaunch cross-check) ----
    logs = sorted(glob.glob(os.path.join(data, "logs", "*.log")), key=os.path.getmtime)
    if not logs:
        print("\nNo launcher logs found under %s\\logs" % data)
        return
    lp = logs[-1]
    import datetime
    mt = datetime.datetime.fromtimestamp(os.path.getmtime(lp)).strftime("%Y-%m-%d %H:%M:%S")
    print("\nLatest log: %s (%s)" % (os.path.basename(lp), mt))
    if os.path.getmtime(lp) < os.path.getmtime(journal):
        print("WARNING: log is older than the journal - restart the launcher and re-run.")
    txt = open(lp, encoding="utf-8", errors="replace").read()
    for label, pat in KEY_PATTERNS:
        hits = [l for l in txt.splitlines() if pat.search(l)]
        print("\n--- %s: %d ---" % (label, len(hits)))
        for h in hits[-a.log_lines:]:
            print("   " + h[:280])

    # ---- 3. cross-check restored games against journal and CanLaunch transitions ----
    if expected:
        canlaunch = set()
        for line in txt.splitlines():
            if "new_state=CanLaunch" in line:
                m = re.search(r"app_id=(\S+)", line)
                if m:
                    canlaunch.add(m.group(1))
        print("\nRestored games from plan:")
        print("  %-26s %-12s %s" % ("folder", "journal", "CanLaunch"))
        for art, g in expected.items():
            registered = art in [r.get("artifactId") for r in done.values()]
            key = "%s:%s:%s" % (g["namespace"], g["itemId"], art)
            print("  %-26s %-12s %s" %
                  (g["folder"], "registered" if registered else "MISSING",
                   "yes" if key in canlaunch else "NO"))

    print("\nSuccess indicators:")
    print("  - 'Replayed the installations log ... live=N removed=0'")
    print("  - 'ExecutableCheckCompleted ... suspect=0' and 'Dropped 0 unplayable installation record(s)'")
    print("  - one 'new_state=CanLaunch' per restored game")
    print("If an update is offered, accept it: the launcher verifies and reuses existing files.")


if __name__ == "__main__":
    main()
