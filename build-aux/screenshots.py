#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render the AppStream screenshots in data/screenshots from sample notes.

Runs the app from this checkout on its own Xvfb display, session bus and
throwaway HOME, so no real notes or desktop settings are read or changed:
    xvfb-run -a -s "-screen 0 1600x1000x24" build-aux/screenshots.py
Needs ImageMagick (import) and xwininfo.
"""
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "screenshots"
NOW = time.time()
# A session bus that starts no services: the app needs none for these shots,
# and an auto-started document portal would leave a FUSE mount behind.
BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:tmpdir=/tmp</listen>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""


def note(color, segments, open=False, pinned=False, remind_in=None, age=0, size=(320, 320)):
    d = {"id": uuid.uuid4().hex, "color": color, "content": segments,
         "created": NOW - age - 60, "modified": NOW - age, "open": open,
         "width": size[0], "height": size[1]}
    if pinned:
        d["pinned"] = True
    if remind_in is not None:
        d["remind_at"] = NOW + remind_in
    return d


def t(text, *tags):
    return {"t": text, "s": list(tags)}


NOTES = [
    note("yellow", [t("Weekend groceries", "bold"), t("\n☐ Oat milk\n☐ Basil\n☐ Lemons\n"
                                                      "☑ Coffee beans\n☑ Bread")],
         pinned=True, open=True, size=(300, 300)),
    note("blue", [t("Team sync", "bold"), t("\n• Release notes for 1.1\n• Screenshot refresh\n"
                                             "• Check the Flatpak build")], remind_in=3 * 3600, age=600),
    note("green", [t("Ideas", "bold"), t("\nA reading list for the "), t("long", "italic"),
                   t(" train ride, and a few podcasts.")], age=3600),
    note("pink", [t("Call the dentist before Friday", "underline")], age=7200),
    note("purple", [t("1. Sketch the layout\n2. Pick the colors\n3. Write it up")], age=86400),
    note("gray", [t("Wi-Fi password is on the back of the router.")], age=2 * 86400),
]


def windows():
    """(id, title, width, height) of the top-level windows on the display."""
    tree = subprocess.run(["xwininfo", "-root", "-children"], capture_output=True,
                          text=True, check=True).stdout
    found = []
    for wid, title, w, h in re.findall(r'(0x[0-9a-f]+) "([^"]*)": .*?(\d+)x(\d+)\+', tree):
        if int(w) > 100 and int(h) > 100:
            found.append((wid, title, int(w), int(h)))
    return found


def main():
    if not os.environ.get("DISPLAY"):
        sys.exit("run under xvfb-run (see the top of this file)")
    OUT.mkdir(parents=True, exist_ok=True)
    shoot("light", {"notes-list": "notes-list", "note": "note"})
    shoot("dark", {"notes-list": "notes-list-dark"})


def shoot(style, names):
    with tempfile.TemporaryDirectory() as home:
        env = dict(os.environ, HOME=home, XDG_CONFIG_HOME=f"{home}/.config",
                   XDG_DATA_HOME=f"{home}/.local/share", XDG_CACHE_HOME=f"{home}/.cache",
                   XDG_RUNTIME_DIR=tempfile.mkdtemp(dir=home), GSETTINGS_BACKEND="memory",
                   GTK_A11Y="none", NO_AT_BRIDGE="1", PYTHONDONTWRITEBYTECODE="1")
        data = Path(env["XDG_DATA_HOME"]) / "dusk-sticky-notes"
        data.mkdir(parents=True)
        (data / "notes.json").write_text(json.dumps({"version": 1, "notes": NOTES}))
        config = Path(env["XDG_CONFIG_HOME"]) / "dusk-sticky-notes"
        config.mkdir(parents=True)
        (config / "settings.json").write_text(json.dumps({"style": style}))
        os.chmod(env["XDG_RUNTIME_DIR"], 0o700)
        bus = Path(home) / "bus.conf"
        bus.write_text(BUS_CONFIG)
        app = subprocess.Popen(["dbus-run-session", f"--config-file={bus}", "--",
                                str(ROOT / "dusk-sticky-notes")], env=env,
                               start_new_session=True)
        try:
            time.sleep(6)
            shots = windows()
            if not shots:
                sys.exit("no app windows appeared")
            for wid, title, w, h in shots:
                name = names.get("notes-list" if w > 400 else "note")
                if name is None:
                    continue
                path = OUT / f"{name}.png"
                subprocess.run(["import", "-window", wid, str(path)], check=True)
                print(f"{path.relative_to(ROOT)}: {title!r} {w}x{h}")
        finally:
            # The app and its bus are one process group; stop them all.
            os.killpg(app.pid, signal.SIGTERM)
            app.wait(timeout=10)


if __name__ == "__main__":
    main()
