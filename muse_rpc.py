#!/usr/bin/env python3
"""
Muse AI - Discord Rich Presence v3 for macOS
Shows what Muse is doing on Discord when Muse.app is open.

Modes, with a different animated icon for each:
  idle     - ahuo_idle.webp     - standing, waiting
  working  - ahuo_working.webp  - headphones + orange laptop
  learning - ahuo_learning.webp - holding a glowing orb, getting new skills
  done     - ahuo_done.webp     - cape + star, finished!

Personal version: the large image is an Ahuo Art Asset uploaded to
the "Muse AI" Discord app (static - Art Assets are always stills).
The small corner image, shown in every mode, is the original fluffy
Muse avatar, also uploaded as an Art Asset (muse_original).
The public repo https://github.com/kitzure/muse_discord_rpc has only
the official Muse avatar and the public code - no Ahuo files in it.

How tracking works (honest version):
  The Muse app itself does not tell this helper what the assistant
  is doing - there is no local API for that. So tracking comes from
  two places:
  1. A status file: ~/muse-discord-rpc/status.json
     Muse (the assistant) writes it when starting/finishing work,
     e.g. {"mode": "working", "task": "SoundSense Round 4"}.
     You can also set it yourself with set_status.py.
  2. Automatic idle: if nobody has touched the Mac for 5 minutes,
     it shows Idle even if a task was set, until you come back.

  When Muse.app closes, presence clears completely.

Status file format (all fields optional except mode):
  {"mode": "working", "task": "SoundSense Round 4"}
  {"mode": "learning", "task": "Kotlin coroutines"}
  {"mode": "done", "task": "SoundSense Round 4"}
  {"mode": "idle"}
  Optional overrides: "details", "state"

Set it with:  python3 set_status.py working "SoundSense Round 4"
              python3 set_status.py learning "Kotlin"
              python3 set_status.py done "SoundSense Round 4"
              python3 set_status.py idle
"""
import json
import os
import pathlib
import time
import subprocess
import sys

CLIENT_ID = os.environ.get("DISCORD_MUSE_CLIENT_ID", "1575569727256793160")

STATUS_FILE = os.path.expanduser("~/muse-discord-rpc/status.json")
CHECK_INTERVAL = 15  # seconds between checks
IDLE_AFTER_SECONDS = 300  # 5 min with no keyboard/mouse -> show Idle
SLEEP_AFTER_SECONDS = 600  # 10 min with no keyboard/mouse -> show Sleeping

# Personal version: animated Ahuo icons are hosted in a SEPARATE repo
# just for them (https://github.com/kitzure/ahuo-discord-assets),
# NOT in the public install repo. Discord only animates images given
# as a public https:// URL, never uploaded Art Assets (those are stills).
# Use GIF, not WebP: Discord often shows WebP as a still first frame,
# while GIF is the format it actually animates in Rich Presence.
# Small corner image in every mode: the blue Muse "M" logo asset ("muse"),
# NOT the fluffy avatar - the user does not want the fluffy avatar as the small circle.
ASSET_BASE = ("https://raw.githubusercontent.com/kitzure/muse_discord_rpc"
              "/main/assets/avatar/activities")
SMALL_IMAGE = "muse"
MODES = {
    "idle": {
        "image": f"{ASSET_BASE}/muse-avatar-idle.gif",
        "details": "Chatting with Muse AI",
        "state": "Idle",
        "large_text": "Muse - Idle",
    },
    "working": {
        "image": f"{ASSET_BASE}/muse-avatar-working.gif",
        "details": "Working with Muse",
        "state": "Working",
        "large_text": "Muse - Working",
    },
    "learning": {
        "image": f"{ASSET_BASE}/muse-avatar-making-something.gif",
        "details": "Learning new skills",
        "state": "Getting new skills",
        "large_text": "Muse - Learning",
    },
    "chatting": {
        "image": f"{ASSET_BASE}/muse-avatar-working.gif",
        "details": "Chatting with Muse AI",
        "state": "Responding...",
        "large_text": "Muse - Chatting",
    },
    "done": {
        "image": f"{ASSET_BASE}/muse-avatar-milestone-level-up.gif",
        "details": "Finished!",
        "state": "Done",
        "large_text": "Muse - Done!",
    },
}


def is_muse_running() -> bool:
    """True if Muse.app is running on this Mac."""
    try:
        import psutil
        for proc in psutil.process_iter(["name", "exe"]):
            try:
                name = (proc.info.get("name") or "")
                exe = (proc.info.get("exe") or "")
                if name == "Muse" or "Muse.app" in exe:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False
    except ImportError:
        pass
    try:
        r = subprocess.run(["pgrep", "-x", "Muse"],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0 and bool(r.stdout.strip())
    except Exception:
        return False


def get_idle_seconds():
    """Seconds since last keyboard/mouse input on this Mac, or None."""
    try:
        out = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem"],
            capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            if "HIDIdleTime" in line:
                # value is in nanoseconds, after the '=' sign
                val = int(line.split("=")[-1].strip())
                return val / 1_000_000_000
    except Exception:
        pass
    return None


def read_status():
    """Read status.json, return (mode, task, details_override, state_override)."""
    try:
        with open(STATUS_FILE) as f:
            data = json.load(f)
        mode = str(data.get("mode", "idle")).lower()
        if mode not in MODES:
            mode = "idle"
        return (mode,
                str(data.get("task", "") or ""),
                str(data.get("details", "") or ""),
                str(data.get("state", "") or ""))
    except Exception:
        return ("idle", "", "", "")


BOT_NAME_FILE = pathlib.Path.home() / ".muse-bot-name"
APP_NAME_FILE = pathlib.Path.home() / ".muse-app-name"


def _cached(path):
    try:
        return path.read_text().strip()
    except Exception:
        return ""


def fetch_app_name(client_id):
    """The Discord application name - what Discord prints as the card title."""
    cached = _cached(APP_NAME_FILE)
    if cached:
        return cached
    try:
        import urllib.request
        url = f"https://discord.com/api/v10/applications/{client_id}/rpc"
        with urllib.request.urlopen(url, timeout=8) as response:
            data = json.loads(response.read())
        name = (data.get("name") or "").strip()
        if name:
            try:
                APP_NAME_FILE.write_text(name)
            except Exception:
                pass
            return name
    except Exception:
        pass
    return "Muse AI"


PERSONA_PREFS = pathlib.Path.home() / "Library" / "Preferences" / "com.meta.endo.plist"


def fetch_persona_name():
    """The name the user set in Muse's identity settings (hatch_identity_cache)."""
    try:
        import plistlib
        with open(PERSONA_PREFS, "rb") as handle:
            cache = plistlib.load(handle).get("hatch_identity_cache")
        if isinstance(cache, bytes):
            cache = cache.decode("utf-8", "ignore")
        for entry in json.loads(cache).values():
            name = ((entry or {}).get("name") or "").strip()
            if name:
                return name
    except Exception:
        pass
    return ""


def fetch_bot_name(client_id):
    """Name used inside the card text: override, else the Muse persona, else the bot user."""
    override = _cached(BOT_NAME_FILE)
    if override:
        return override
    persona = fetch_persona_name()
    if persona:
        return persona
    try:
        import urllib.request
        url = f"https://discord.com/api/v10/applications/{client_id}/rpc"
        with urllib.request.urlopen(url, timeout=8) as response:
            data = json.loads(response.read())
        bot = data.get("bot") or {}
        name = (bot.get("global_name") or bot.get("username") or data.get("name") or "").strip()
        if name:
            return name
    except Exception:
        pass
    return fetch_app_name(client_id)


APP_NAME = fetch_app_name(CLIENT_ID)
BOT_NAME = fetch_bot_name(CLIENT_ID)


def current_bot_name(now):
    """Cached for a minute: the plist is cheap, but not every 10 s poll."""
    if now - getattr(current_bot_name, "_at", 0) >= 60:
        current_bot_name._at = now
        current_bot_name._name = fetch_bot_name(CLIENT_ID)
    return current_bot_name._name


def build_presence(mode, task, details_override, state_override, auto_idle=None):
    """Build (details, state, large_image, large_text, small_image) for a mode.
    auto_idle: None = idle set in status.json, "idle" = 5 min no input,
    "sleeping" = 10+ min no input."""
    cfg = MODES[mode]
    if mode == "idle":
        # Idle has three levels: app open and waiting = ready,
        # 5 min with no input = idle, 10+ min with no input = sleeping.
        if auto_idle == "sleeping":
            cfg = dict(cfg, state="Sleeping...", large_text="Muse - Sleeping")
        elif auto_idle == "idle":
            cfg = dict(cfg, state="Idle", large_text="Muse - Idle")
        else:
            cfg = dict(cfg, state="Ready", large_text="Muse - Ready")
    if details_override:
        details = details_override
    elif task and mode == "working":
        details = f"Working on {task}"
    elif task and mode == "learning":
        details = f"Learning: {task}"
    elif task and mode == "done":
        details = f"Finished: {task}"
    else:
        details = cfg["details"].replace("Muse AI", BOT_NAME)
    state = state_override or cfg["state"]
    if task and mode == "working" and not state_override:
        state = "Working"
    large_text = (cfg.get("large_text") or "").replace("\u963f\u6d3b", BOT_NAME)\
        .replace("Muse", BOT_NAME)
    return (details[:128], state[:128], cfg["image"], large_text,
            SMALL_IMAGE)


def connect_rpc(Presence):
    """Connect to Discord, trying IPC pipes 0-9 in order.

    pypresence's default (pipe=None) scans for any discord-ipc-* socket
    and crashes on a stale one - on this Mac discord-ipc-1 is a stale
    socket from Discord.app, while discord-ipc-0 is the live Discord
    Phoenix app. Trying each pipe explicitly avoids that.
    """
    last_err = None
    for pipe in range(10):
        try:
            rpc = Presence(CLIENT_ID, pipe=pipe)
            rpc.connect()
            print(f"Connected to Discord on pipe {pipe}.", flush=True)
            return rpc
        except Exception as e:
            last_err = e
            try:
                rpc.close()
            except Exception:
                pass
            continue
    raise last_err if last_err else RuntimeError("Discord not found")


def main():
    if not CLIENT_ID or CLIENT_ID == "YOUR_APPLICATION_ID_HERE":
        print("ERROR: Set CLIENT_ID in muse_rpc.py or DISCORD_MUSE_CLIENT_ID env var.")
        sys.exit(1)

    try:
        from pypresence import Presence, DiscordNotFound
    except ImportError:
        print("ERROR: pypresence not installed. Run: pip3 install pypresence psutil")
        sys.exit(1)

    rpc = None
    connected = False
    start_time = None
    last_key = None

    print(f"Muse Discord RPC v4 watching... (personal, Ahuo via Discord assets) (checks every {CHECK_INTERVAL}s)", flush=True)
    print("Modes: idle / working / learning / done - set with set_status.py", flush=True)

    while True:
        running = is_muse_running()

        if running and not connected:
            try:
                rpc = connect_rpc(Presence)
                connected = True
                start_time = int(time.time())
                last_key = None
                print("Muse detected - connected to Discord.", flush=True)
            except DiscordNotFound:
                print("Discord desktop app not found - is Discord running? Retrying...", flush=True)
                rpc = None
                connected = False
            except Exception as e:
                print(f"Could not connect to Discord: {e}. Retrying...", flush=True)
                rpc = None
                connected = False

        if running and connected:
            global BOT_NAME
            BOT_NAME = current_bot_name(time.time())
            mode, task, details_o, state_o = read_status()
            idle_s = get_idle_seconds()
            # Tiered auto-idle: 5 min with no input -> Idle,
            # 10+ min with no input -> Sleeping. An idle set in
            # status.json (not from no input) is "Ready".
            auto_idle = None
            remote_work = False
            try:
                with open(STATUS_FILE) as handle:
                    remote_work = bool(json.load(handle).get("remote"))
            except Exception:
                remote_work = False
            if idle_s is not None and mode not in ("idle", "chatting") and not remote_work:
                if idle_s > SLEEP_AFTER_SECONDS:
                    mode, task, details_o, state_o = "idle", "", "", ""
                    auto_idle = "sleeping"
                elif idle_s > IDLE_AFTER_SECONDS:
                    mode, task, details_o, state_o = "idle", "", "", ""
                    auto_idle = "idle"
            details, state, image, large_text, small_image = build_presence(mode, task, details_o, state_o, auto_idle=auto_idle)
            key = (mode, task, details, state, image)
            # Restart the elapsed timer when the activity changes
            if key != last_key:
                start_time = int(time.time())
                last_key = key
            # Show the start time in the text too, so the time is visible
            # even on clients that do not show the elapsed timer.
            # Working: details already says "Working on ...", so the state
            # is just the time, otherwise it reads "Working" twice.
            # Timezone is included (HKT, Hong Kong, UTC+8) so viewers
            # elsewhere know which time it is.
            display_state = state
            if mode == "chatting":
                display_state = state
            elif mode == "working" and start_time:
                display_state = state_o or f"Since {time.strftime('%H:%M', time.localtime(start_time))} HKT"
            elif mode != "idle" and start_time:
                display_state = f"{state} - since {time.strftime('%H:%M', time.localtime(start_time))} HKT"
            print(f"Status: {mode} | {details} | {display_state} | {image}", flush=True)
            try:
                rpc.update(details=details, state=display_state, start=start_time,
                           large_image=image, large_text=large_text,
                           small_image=small_image, small_text="Muse")
            except Exception as e:
                print(f"Update failed: {e} - will reconnect next check.", flush=True)
                connected = False
                try:
                    rpc.close()
                except Exception:
                    pass

        if not running and connected:
            try:
                rpc.clear()
                rpc.close()
                print("Muse closed - presence cleared.", flush=True)
            except Exception:
                pass
            connected = False
            start_time = None
            last_key = None
            rpc = None

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
