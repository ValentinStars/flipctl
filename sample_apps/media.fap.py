#!/usr/bin/env python3
# /// flipctl
# name = "Media"
# status = true
# audio = true
# runtime = ""
# ///
"""Media Player Remote for FlipCTL.

Controls Spotify, YouTube (browser), VLC, Telegram, and any MPRIS media player.
Ok/Run toggles Play/Pause, Left/Right skips tracks.
"""

import asyncio
import subprocess
import flipctl
import slint

PAGE = """
import { Shell } from "@app/shell.slint";
import { DetailBody, DetailRow } from "@flipctl/detail.slint";

export component App inherits Shell {
    in property <[DetailRow]> rows;
    in property <[string]> buttons;
    callback keyed(string, bool);
    key(text, down) => { root.keyed(text, down); }

    DetailBody {
        rows: root.rows;
        buttons: root.buttons;
        fit_gauges: true;
    }
}
"""


class MediaController:
    def __init__(self):
        self.player_service = ""
        self.player_name = "No Player"
        self.status = "Stopped"
        self.title = "No Track"
        self.artist = ""
        self.woken = asyncio.Event()
        self.refresh()

    def find_player(self):
        try:
            out = subprocess.check_output(
                ["busctl", "--user", "list"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            for line in out.splitlines():
                if "org.mpris.MediaPlayer2." in line:
                    fields = line.split()
                    svc = fields[0]
                    # Don't pick ignored ones
                    self.player_service = svc
                    self.player_name = svc.replace("org.mpris.MediaPlayer2.", "")
                    return True
        except Exception:
            pass
        self.player_service = ""
        self.player_name = "None"
        return False

    def refresh(self):
        if not self.find_player():
            self.status = "Idle"
            self.title = "Waiting for music..."
            self.artist = "-"
            return

        # Get status
        try:
            st = subprocess.check_output(
                ["busctl", "--user", "get-property", self.player_service, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", "PlaybackStatus"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            # Format: 's "Playing"'
            self.status = st.split('"')[1] if '"' in st else st.strip()
        except Exception:
            self.status = "Unknown"

        # Get metadata
        try:
            meta = subprocess.check_output(
                ["busctl", "--user", "get-property", self.player_service, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", "Metadata"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            # Search for xesam:title and xesam:artist
            lines = meta.splitlines()
            for i, line in enumerate(lines):
                if "xesam:title" in line and i + 1 < len(lines):
                    val_line = lines[i + 1]
                    if '"' in val_line:
                        self.title = val_line.split('"')[1]
                if "xesam:artist" in line:
                    for j in range(i, min(len(lines), i + 4)):
                        if '"' in lines[j] and "xesam:artist" not in lines[j]:
                            self.artist = lines[j].split('"')[1]
                            break
        except Exception:
            pass

    def play_pause(self):
        if self.player_service:
            subprocess.run(
                ["busctl", "--user", "call", self.player_service, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", "PlayPause"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.refresh()
            self.woken.set()

    def next_track(self):
        if self.player_service:
            subprocess.run(
                ["busctl", "--user", "call", self.player_service, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", "Next"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.refresh()
            self.woken.set()

    def prev_track(self):
        if self.player_service:
            subprocess.run(
                ["busctl", "--user", "call", self.player_service, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", "Previous"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.refresh()
            self.woken.set()

    def rows(self):
        icon = "[ > ]" if self.status == "Playing" else "[ || ]"
        return [
            {"kind": 0, "label": "Player", "value": self.player_name[:18], "percent": 0, "dim": False},
            {"kind": 0, "label": "Status", "value": f"{icon} {self.status}", "percent": 0, "dim": False},
            {"kind": 1, "label": "", "value": "", "percent": 0, "dim": False},
            {"kind": 0, "label": "Track", "value": self.title[:20], "percent": 0, "dim": False},
            {"kind": 0, "label": "Artist", "value": self.artist[:20] if self.artist else "-", "percent": 0, "dim": True},
            {"kind": 0, "label": "Controls", "value": "Left: Prev, Right: Next", "percent": 0, "dim": True},
        ]

    async def run(self, draw):
        while True:
            self.refresh()
            draw()
            try:
                await asyncio.wait_for(self.woken.wait(), 2.0)
            except asyncio.TimeoutError:
                pass
            self.woken.clear()


def main():
    ui = flipctl.load(PAGE, "media.slint")
    ctl = MediaController()

    def draw():
        ui.rows = ctl.rows()
        play_btn = "Pause" if ctl.status == "Playing" else "Play"
        ui.buttons = ["Close", "Prev", "Next", play_btn, "Play/Pause"]

    @flipctl.on_key(ui)
    def _(key, down):
        if not down:
            return
        if key in (flipctl.Key.BACK, flipctl.Key.ESCAPE):
            slint.quit_event_loop()
        elif key in (flipctl.Key.LEFT,):
            ctl.prev_track()
        elif key in (flipctl.Key.RIGHT,):
            ctl.next_track()
        elif key in (flipctl.Key.RUN, flipctl.Key.ENTER, flipctl.Key.EDIT):
            ctl.play_pause()
        draw()

    draw()
    flipctl.run(ui, ctl.run(draw))


if __name__ == "__main__":
    main()
