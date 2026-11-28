#!/usr/bin/env python3
"""Derive Muse's activity from what the Mac is actually doing.

Muse.app exposes no activity API, so muse_rpc.py can only report what
status.json says. This watcher fills that file from observable signals:

  * how long since the keyboard/mouse was touched  (ioreg HIDIdleTime)
  * which project was touched most recently         (file mtimes in work roots)
  * whether that activity writes code or reads docs (extension + folder name)

States:

  working   code/project files changed in the last 30 seconds
  learning  notes, docs, PDFs or training material changed instead
  done      the work stopped 0-45 seconds ago (shows the last project)
  idle      no keyboard/mouse for 90 seconds, or nothing touched for 45

A manual `set_status.py` call still wins for 30 minutes; after that the
watcher takes over again. It writes only when the derived value changes.
"""
import json
from datetime import datetime
import os
import re
import statistics
import subprocess
import sys
import time

STATUS_FILE = os.path.expanduser("~/muse-discord-rpc/status.json")
DEFAULT_ROOTS = ["~/Developer", "~/Documents", "~/Desktop"]
CONFIG_FILE = os.path.expanduser("~/.muse-activity.json")
ACTIVITY_FILE = os.path.expanduser("~/muse-discord-rpc/activity.json")
REMOTE_FILE = os.path.expanduser("~/muse-discord-rpc/remote.json")


def load_roots():
    """Read `{"roots": [...]}` from ~/.muse-activity.json when present."""
    try:
        data = json.load(open(CONFIG_FILE))
        roots = data.get("roots")
        if isinstance(roots, list) and roots:
            return [str(item) for item in roots]
    except Exception:
        pass
    return DEFAULT_ROOTS


WORK_ROOTS = load_roots()
POLL = 10
MANUAL_TTL = 1800
ACTIVE_WINDOW = 30           # a file counts as "just touched" for 30 seconds
DONE_WINDOW = 45             # show "done" for 45 seconds after the work stops

IGNORE_DIRS = {".git", "node_modules", "build", "DerivedData", ".build",
               "Library", ".venv", "venv", "__pycache__", ".next", "dist",
               "Pods", ".gradle", "target"}
IGNORE_FILES = {".DS_Store", "UserInterfaceState.xcuserstate"}

CODE_EXT = {".swift", ".py", ".js", ".jsx", ".ts", ".tsx", ".dart", ".kt",
            ".java", ".c", ".cc", ".cpp", ".h", ".hpp", ".rs", ".go", ".cs",
            ".php", ".rb", ".sh", ".html", ".css", ".scss", ".vue", ".svelte",
            ".sql", ".yml", ".yaml", ".toml", ".json", ".ipynb"}
DOC_EXT = {".md", ".pdf", ".txt", ".csv", ".docx", ".pptx", ".xlsx", ".rtf",
           ".epub", ".mobi", ".pages", ".key", ".numbers"}
LEARN_HINTS = ("training", "tutorial", "course", "learn", "notes", "docs",
               "study", "reference", "handoff", "research")

PROJECT_MARKERS = (".git", "package.json", "pubspec.yaml", "Cargo.toml",
                   "requirements.txt", "go.mod", "pyproject.toml")
GENERIC = {"ChatGPT", "src", "app", "lib", "build", "Training", "scripts",
           "tests", "docs", "public", "assets", "Desktop", "Documents"}

NAME_MAP = (("SoundSense", "SoundSense"), ("muse-discord-rpc", "Muse Discord RPC"),
            ("sheeridVerify", "SheerID Verify"), ("DineGenie", "DineGenie"),
            ("littleprince", "Little Prince"), ("lpo", "Little Prince Online"))


def run(cmd, timeout=6):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout).stdout.strip()
    except Exception:
        return ""


def idle_seconds():
    out = run(["ioreg", "-c", "IOHIDSystem"])
    match = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
    return int(match.group(1)) / 1e9 if match else 0.0


def project_label(folder, root):
    """Name the project folder, not the file's own sub-folder."""
    current = folder
    for _ in range(6):
        if any(os.path.exists(os.path.join(current, marker)) for marker in PROJECT_MARKERS):
            return os.path.basename(current)
        try:
            names = os.listdir(current)
        except OSError:
            names = []
        if any(name.endswith(".xcodeproj") for name in names):
            return os.path.basename(current)
        parent = os.path.dirname(current)
        if parent == current or not parent.startswith(root):
            break
        current = parent
    rel = os.path.relpath(folder, root)
    top = rel.split(os.sep)[0]
    if top and top not in GENERIC:
        return top
    return os.path.basename(folder) or os.path.basename(root)


def friendly(label):
    for needle, name in NAME_MAP:
        if needle.lower() in label.lower():
            return name
    return label


def newest_activity(now, stats=None):
    """Return (label, age_seconds, kind) for the freshest touched work item.

    ``stats`` collects a per-root file count so a permission problem (macOS
    TCC hides ~/Documents and ~/Desktop from background processes) shows up in
    the log instead of silently narrowing the scan.
    """
    best = None
    for root in WORK_ROOTS:
        root = os.path.abspath(os.path.expanduser(root))
        if not os.path.isdir(root):
            continue
        seen = 0
        denied = []
        walker = os.walk(root, onerror=lambda err: denied.append(
            "%s: %s" % (getattr(err, "filename", "?"), err.strerror or err)))
        if stats is not None:
            stats.setdefault("_denied", {})[root] = denied
        for dirpath, dirnames, filenames in walker:
            dirnames[:] = [d for d in dirnames
                           if d not in IGNORE_DIRS and not d.startswith(".")]
            if dirpath.count(os.sep) - root.count(os.sep) > 3:
                dirnames[:] = []
            for name in filenames:
                if name in IGNORE_FILES or name.startswith("."):
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext not in CODE_EXT and ext not in DOC_EXT:
                    continue
                path = os.path.join(dirpath, name)
                try:
                    age = now - os.path.getmtime(path)
                except OSError:
                    continue
                if age < 0 or age > 3600:
                    continue
                seen += 1
                if best is not None and age >= best[1]:
                    continue
                kind = "code" if ext in CODE_EXT else "doc"
                if kind == "doc":
                    lowered = (dirpath + " " + name).lower()
                    if any(hint in lowered for hint in LEARN_HINTS):
                        kind = "learn"
                best = (friendly(project_label(dirpath, root)), age, kind)
        if stats is not None:
            stats[root] = seen
    return best


PROCESS_HINTS = (
    (("xcodebuild", "swift build", "swiftc"), "Building..."),
    (("npm install", "npm i ", "yarn install", "pnpm install", "pip install",
      "brew install", "cargo build"), "Installing..."),
    (("pytest", "unittest", "npm test", "yarn test", "pnpm test", "tox"),
     "Running tests..."),
    (("ffmpeg", "ffprobe"), "Processing video..."),
    (("docker build", "docker compose up"), "Building container..."),
    (("cargo build", "go build", "make "), "Compiling..."),
)


def muse_running():
    """Only report Muse activity while Muse.app is actually open."""
    out = run(["ps", "-Ao", "comm"])
    return any("Muse.app/Contents/MacOS/Muse" in line for line in out.splitlines())


MUSE_CPU_FLOOR = 10.0   # percent: below this it is never a response
MUSE_CPU_FACTOR = 1.8   # ...and it must also beat the idle baseline by this much
MUSE_CPU_HITS = 2       # consecutive polls before calling it a response
CHAT_HOLD = 45         # keep 'Responding' this long after the CPU drops


def muse_cpu():
    """CPU percent of the Muse.app process itself (0 when closed)."""
    out = run(["ps", "-Ao", "%cpu=,command="], timeout=5)
    best = 0.0
    for line in out.splitlines():
        if "Muse.app/Contents/MacOS/Muse" not in line:
            continue
        if "EndoCrashpadHandler" in line:
            continue
        try:
            best = max(best, float(line.split(None, 1)[0]))
        except (ValueError, IndexError):
            continue
    return best


def remote_activity():
    """Another machine (e.g. the Linux PC) reporting work, mirrored over SSH."""
    try:
        data = json.load(open(REMOTE_FILE))
    except Exception:
        return {}
    try:
        ttl = float(data.get("ttl", 90))
        written = float(data.get("updated", 0))
    except (TypeError, ValueError):
        return {}
    if time.time() - written > ttl:
        return {}
    mode = str(data.get("mode", "working")).lower()
    if mode not in ("working", "learning", "done", "idle"):
        mode = "working"
    return {"mode": mode, "task": str(data.get("task", "") or ""),
            "state": str(data.get("state", "") or "")}


def process_activity():
    """Name the long-running tool in use, e.g. a build or a test run."""
    out = run(["ps", "-Ao", "command"], timeout=5)
    for patterns, label in PROCESS_HINTS:
        for line in out.splitlines():
            if "ps -Ao" in line or "muse_activity" in line:
                continue
            for pattern in patterns:
                if pattern in line:
                    return label
    return ""


def live_activity():
    """A short-lived line set by the assistant, e.g. {"state": "Checking..."}."""
    try:
        data = json.load(open(ACTIVITY_FILE))
    except Exception:
        return {}
    try:
        ttl = float(data.get("ttl", 120))
        written = float(data.get("updated", 0))
    except (TypeError, ValueError):
        return {}
    if time.time() - written > ttl:
        return {}
    return {key: str(data[key]) for key in ("state", "details") if data.get(key)}


def manual_override(data, idle=0, cpu=0.0, remote=None, chat=False):
    if not isinstance(data, dict):
        return False
    if data.get("source") == "auto":
        return False
    try:
        age = time.time() - float(data.get("updated", 0))
    except (TypeError, ValueError):
        return False
    if age >= MANUAL_TTL:
        return False
    # A "working" claim set by hand outlives the work that justified it: drop it
    # once the Mac shows nothing happening (no input, no Muse CPU, no remote work).
    if data.get("mode") in ("working", "learning"):
        if idle >= 90 and cpu < MUSE_CPU_FLOOR and not (remote and remote.get("mode") not in (None, "idle")):
            return False
    # A hand-set "chatting" outlives the reply; the transcript is the truth.
    if data.get("mode") == "chatting" and not chat:
        return False
    return True


def derive(now, idle, activity, last):
    """Pure state machine: (state dict, updated history)."""
    if idle >= 90:
        return {"mode": "idle", "task": ""}, {"last_active": 0, "task": ""}
    if activity and activity[1] <= ACTIVE_WINDOW:
        label, _, kind = activity
        mode = "learning" if kind == "learn" else "working"
        return ({"mode": mode, "task": label},
                {"last_active": now, "task": label})
    if last.get("last_active") and now - last["last_active"] <= DONE_WINDOW:
        return {"mode": "done", "task": last.get("task", "")}, last
    return {"mode": "idle", "task": ""}, {"last_active": 0, "task": ""}


TRANSCRIPT = os.path.expanduser(
    "~/Library/Caches/com.meta.endo/HatchEmbedDurableCache/chat-main-transcript.json")
REPLY_FRESH = 20    # a reply that just landed still reads as "responding"
PENDING_WINDOW = 600  # a user turn younger than this is still awaiting a reply


def chat_busy(now):
    """(streaming, pending) from Muse's transcript; (False, False) if unreadable.

    ``isTextStreaming`` flips while a reply streams, so this replaces CPU
    sampling: the app tells us directly, and reading a 110 KB JSON costs nothing.
    """
    try:
        data = json.load(open(TRANSCRIPT))
        messages, order = data["messagesById"], data["messageIds"]
    except Exception:
        return False, False
    if any(message.get("isTextStreaming") for message in messages.values()):
        return True, True
    last = messages.get(order[-1]) if order else None
    if not last:
        return False, False
    try:
        age = now - datetime.fromisoformat(
            last["createdAt"].replace("Z", "+00:00")).timestamp()
    except Exception:
        return False, False
    role = last.get("role")
    if role == "user" and 0 <= age <= PENDING_WINDOW:
        return True, True
    if role == "assistant" and 0 <= age <= REPLY_FRESH:
        return True, False
    return False, False


def main():
    if not os.path.isdir(os.path.dirname(STATUS_FILE)):
        sys.exit("muse-discord-rpc folder not found")
    last_key = None
    history = {"last_active": 0, "task": ""}
    while True:
        try:
            current = {}
            if os.path.exists(STATUS_FILE):
                try:
                    current = json.load(open(STATUS_FILE))
                except Exception:
                    current = {}
            now = time.time()
            idle = idle_seconds()
            stats = {}
            activity = newest_activity(now, stats)
            denied = stats.get("_denied", {})
            hidden = [root for root, errs in denied.items() if errs]
            if hidden and not getattr(main, "_warned", False):
                print("note: no readable files in", ", ".join(hidden),
                      "- macOS privacy protection; grant Full Disk Access to",
                      sys.executable, "to include them", flush=True)
                main._warned = True
            state, history = derive(now, idle, activity, history)
            cpu = 0.0
            # Muse's own transcript cache says whether it is answering, so no
            # process sampling is needed (and nothing on the machine gets hot).
            streaming, pending = chat_busy(now)
            if streaming and state["mode"] in ("idle", "done"):
                state = {"mode": "chatting", "task": ""}
                if pending:
                    state["state"] = "Thinking..."
            remote = remote_activity()
            if remote and remote["mode"] != "idle":
                state = {"mode": remote["mode"], "task": remote["task"],
                         "remote": True}
                if remote["state"]:
                    state["state"] = remote["state"]
            remote_here = bool(remote_activity())
            if not muse_running() and not remote_here:
                state = {"mode": "idle", "task": ""}
            explicit = live_activity()
            if explicit:
                state.update(explicit)
            else:
                busy = process_activity()
                if busy:
                    state["state"] = busy
            print(time.strftime("%H:%M:%S"), "idle=%ds chat=%s/%s activity=%r -> %s"
                  % (int(idle), streaming, pending, activity, state), flush=True)
            key = (state["mode"], state["task"], state.get("state", ""), state.get("details", ""))
            if not manual_override(current, idle=idle, cpu=cpu, remote=remote,
                                   chat=streaming) and key != last_key:
                state["source"] = "auto"
                state["updated"] = int(now)
                tmp = STATUS_FILE + ".tmp"
                with open(tmp, "w") as handle:
                    json.dump(state, handle)
                os.replace(tmp, STATUS_FILE)
                print(time.strftime("%H:%M:%S"), "wrote", json.dumps(state), flush=True)
                last_key = key
        except Exception as exc:  # keep the watcher alive
            print("error:", exc, file=sys.stderr, flush=True)
        time.sleep(POLL)


if __name__ == "__main__":
    main()
