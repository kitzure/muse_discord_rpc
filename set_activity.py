#!/usr/bin/env python3
"""Show a live activity line in Discord while something is running.

Usage:
  python3 set_activity.py "Checking 42 listings..."        # 120s default
  python3 set_activity.py "Fixing the parser" 300          # 5 minutes
  python3 set_activity.py clear

Writes ~/muse-discord-rpc/activity.json, which muse_activity.py merges into
status.json until it expires. The mode (working/done/idle) stays automatic,
so this only changes the second line Discord shows.
"""
import json
import os
import sys
import time

PATH = os.path.expanduser("~/muse-discord-rpc/activity.json")

if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(1)

if sys.argv[1] == "clear":
    try:
        os.remove(PATH)
    except FileNotFoundError:
        pass
    print("activity cleared")
    sys.exit(0)

text = sys.argv[1]
ttl = int(sys.argv[2]) if len(sys.argv) > 2 else 120
with open(PATH, "w") as handle:
    json.dump({"state": text, "ttl": ttl, "updated": int(time.time())}, handle)
print("activity:", text, "for", ttl, "s")
