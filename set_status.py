#!/usr/bin/env python3
"""Set Muse's Discord Rich Presence status.

Usage:
  python3 set_status.py idle
  python3 set_status.py working "SoundSense Round 4"
  python3 set_status.py learning "Kotlin coroutines"
  python3 set_status.py done "SoundSense Round 4"

Writes ~/muse-discord-rpc/status.json, which muse_rpc.py reads
every 15 seconds. Muse (the assistant) uses this when starting
and finishing work for you, so Discord shows what it's working on.
"""
import json
import os
import sys
import time

STATUS_FILE = os.path.expanduser("~/muse-discord-rpc/status.json")
MODES = {"idle", "working", "learning", "done"}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in MODES:
        print(__doc__)
        print("Modes: idle, working, learning, done")
        sys.exit(1)
    mode = sys.argv[1]
    task = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
    data = {"mode": mode, "task": task, "updated": int(time.time())}
    with open(STATUS_FILE, "w") as f:
        json.dump(data, f)
    print(f"Status set: {mode} {task!r}")
