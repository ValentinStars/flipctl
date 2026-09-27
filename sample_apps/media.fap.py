#!/usr/bin/env python3
# /// flipctl
# name = "Media"
# status = true
# audio = true
# runtime = ""
# ///
"""Media Player Remote for FlipCTL.

Controls Spotify, Telegram, YouTube (browsers), VLC, and any MPRIS media player.
Shows current playing song, artist, album, progress bar, and player name.
Supports touchscreen/mouse clicks, hardware D-pad, and soft buttons.
"""

import asyncio
import os
import subprocess
import flipctl
import slint

# Ensure D-Bus session bus is reachable even when running hosted under FlipCTL
# (FlipCTL isolates XDG_RUNTIME_DIR to a private directory, so we connect to the real user bus)
uid = os.getuid()
user_bus = f"/run/user/{uid}/bus"
if os.path.exists(user_bus):
    if "DBUS_SESSION_BUS_ADDRESS" not in os.environ:
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={user_bus}"
    rt_dir = os.environ.get("XDG_RUNTIME_DIR")
    if rt_dir and os.path.isdir(rt_dir):
        rt_bus = os.path.join(rt_dir, "bus")
        if not os.path.exists(rt_bus):
            try:
                os.symlink(user_bus, rt_bus)
            except Exception:
                pass

try:
    import dbus
    HAS_DBUS = True
except ImportError:
    HAS_DBUS = False

PAGE = """
import { Shell } from "@app/shell.slint";
import { DetailBody, DetailRow } from "@flipctl/detail.slint";

export component App inherits Shell {
    in property <[DetailRow]> rows;
    in property <[string]> buttons;
    callback keyed(string, bool);
    callback button_clicked(int);
    callback body_clicked();
    callback player_clicked();

    key(text, down) => { root.keyed(text, down); }

    DetailBody {
        rows: root.rows;
        buttons: root.buttons;
        fit_gauges: true;
    }

    // Touch area for top player row (tap to cycle player)
    TouchArea {
        x: 0;
        y: 20px;
        width: root.width;
        height: 25px;
        clicked => { root.player_clicked(); }
    }

    // Touch area for main track / progress body (tap to toggle play/pause)
    TouchArea {
        x: 0;
        y: 45px;
        width: root.width;
        height: root.height - 63px;
        clicked => { root.body_clicked(); }
    }

    // Touch areas for clicking each of the 5 soft buttons at the bottom
    for btn[i] in 5: TouchArea {
        x: i * (root.width / 5);
        y: root.height - 18px;
        width: root.width / 5;
        height: 18px;
        clicked => { root.button_clicked(i); }
    }
}
"""


class MediaController:
    def __init__(self):
        self.players = []
        self.player_idx = 0
        self.player_service = ""
        self.player_name = "None"
        self.status = "Idle"
        self.title = "No Music Playing"
        self.artist = "-"
        self.album = ""
        self.percent = 0
        self.time_str = "--:-- / --:--"
        self.woken = asyncio.Event()
        self.bus = None
        if HAS_DBUS:
            try:
                self.bus = dbus.SessionBus()
            except Exception:
                self.bus = None
        self.refresh()

    def scan_players(self):
        found = []
        if self.bus is not None:
            try:
                for name in self.bus.list_names():
                    if name.startswith("org.mpris.MediaPlayer2."):
                        found.append(str(name))
            except Exception:
                pass
        if not found:
            try:
                out = subprocess.check_output(
                    ["busctl", "--user", "list"],
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                for line in out.splitlines():
                    if "org.mpris.MediaPlayer2." in line:
                        found.append(line.split()[0])
            except Exception:
                pass

        # Sort players: prioritize Playing, then ones with title, then others
        scored = []
        for svc in found:
            score = 0
            st, title, _ = self._get_player_info(svc)
            if st == "Playing":
                score += 100
            elif st == "Paused":
                score += 50
            if title and title != "No Music Playing":
                score += 30
            # Deprioritize kdeconnect if other players exist
            if "kdeconnect" in svc.lower():
                score -= 40
            scored.append((score, svc))

        scored.sort(key=lambda x: x[0], reverse=True)
        self.players = [svc for _, svc in scored]

        if not self.players:
            self.player_service = ""
            self.player_name = "None"
            return False

        if self.player_service not in self.players:
            self.player_idx = 0
            self.player_service = self.players[0]
        else:
            self.player_idx = self.players.index(self.player_service)

        self._update_name()
        return True

    def _update_name(self):
        if not self.player_service:
            self.player_name = "None"
            return
        name = self.player_service.replace("org.mpris.MediaPlayer2.", "")
        # Clean up known app names
        for prefix in ("instance", "chromium", "firefox", "telegram", "spotify", "vlc", "mpv", "kdeconnect"):
            if prefix in name.lower():
                name = prefix.capitalize()
                break
        self.player_name = name

    def _get_player_info(self, svc):
        status = "Stopped"
        title = ""
        artist = ""
        if self.bus is not None:
            try:
                obj = self.bus.get_object(svc, "/org/mpris/MediaPlayer2")
                props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
                status = str(props.Get("org.mpris.MediaPlayer2.Player", "PlaybackStatus"))
                meta = props.Get("org.mpris.MediaPlayer2.Player", "Metadata")
                title = str(meta.get("xesam:title", ""))
                art = meta.get("xesam:artist", "")
                if isinstance(art, (list, tuple)):
                    artist = ", ".join(str(x) for x in art)
                else:
                    artist = str(art)
                return status, title, artist
            except Exception:
                pass
        return status, title, artist

    def cycle_player(self):
        if not self.players:
            self.scan_players()
            self.refresh()
            return
        self.player_idx = (self.player_idx + 1) % len(self.players)
        self.player_service = self.players[self.player_idx]
        self._update_name()
        self.refresh()
        self.woken.set()

    def refresh(self):
        if not self.scan_players():
            self.status = "Idle"
            self.title = "No Music Playing"
            self.artist = "-"
            self.album = ""
            self.percent = 0
            self.time_str = "--:--"
            return

        pos_sec = 0
        len_sec = 0

        if self.bus is not None and self.player_service:
            try:
                obj = self.bus.get_object(self.player_service, "/org/mpris/MediaPlayer2")
                props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
                self.status = str(props.Get("org.mpris.MediaPlayer2.Player", "PlaybackStatus"))
                meta = props.Get("org.mpris.MediaPlayer2.Player", "Metadata")
                self.title = str(meta.get("xesam:title", "Unknown Track"))
                art = meta.get("xesam:artist", "")
                if isinstance(art, (list, tuple)):
                    self.artist = ", ".join(str(x) for x in art)
                else:
                    self.artist = str(art)
                self.album = str(meta.get("xesam:album", ""))

                try:
                    pos_us = int(props.Get("org.mpris.MediaPlayer2.Player", "Position"))
                    pos_sec = max(0, pos_us // 1_000_000)
                except Exception:
                    pos_sec = 0

                try:
                    len_us = int(meta.get("mpris:length", 0))
                    len_sec = max(0, len_us // 1_000_000)
                except Exception:
                    len_sec = 0
            except Exception:
                pass

        if len_sec > 0:
            self.percent = min(100, max(0, int((pos_sec / len_sec) * 100)))
            cur_m, cur_s = divmod(pos_sec, 60)
            tot_m, tot_s = divmod(len_sec, 60)
            self.time_str = f"{cur_m:02d}:{cur_s:02d} / {tot_m:02d}:{tot_s:02d}"
        else:
            self.percent = 0
            cur_m, cur_s = divmod(pos_sec, 60)
            self.time_str = f"{cur_m:02d}:{cur_s:02d}" if pos_sec > 0 else "--:--"

        if not self.title:
            self.title = "Unknown Track"
        if not self.artist:
            self.artist = "-"

    def play_pause(self):
        if self.bus is not None and self.player_service:
            try:
                obj = self.bus.get_object(self.player_service, "/org/mpris/MediaPlayer2")
                player = dbus.Interface(obj, "org.mpris.MediaPlayer2.Player")
                player.PlayPause()
                self.refresh()
                self.woken.set()
                return
            except Exception:
                pass
        if self.player_service:
            subprocess.run(
                ["busctl", "--user", "call", self.player_service, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", "PlayPause"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.refresh()
            self.woken.set()

    def next_track(self):
        if self.bus is not None and self.player_service:
            try:
                obj = self.bus.get_object(self.player_service, "/org/mpris/MediaPlayer2")
                player = dbus.Interface(obj, "org.mpris.MediaPlayer2.Player")
                player.Next()
                self.refresh()
                self.woken.set()
                return
            except Exception:
                pass
        if self.player_service:
            subprocess.run(
                ["busctl", "--user", "call", self.player_service, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", "Next"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.refresh()
            self.woken.set()

    def prev_track(self):
        if self.bus is not None and self.player_service:
            try:
                obj = self.bus.get_object(self.player_service, "/org/mpris/MediaPlayer2")
                player = dbus.Interface(obj, "org.mpris.MediaPlayer2.Player")
                player.Previous()
                self.refresh()
                self.woken.set()
                return
            except Exception:
                pass
        if self.player_service:
            subprocess.run(
                ["busctl", "--user", "call", self.player_service, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", "Previous"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.refresh()
            self.woken.set()

    def rows(self):
        status_icon = "[ > ]" if self.status == "Playing" else "[ || ]"
        p_count = f"({self.player_idx + 1}/{len(self.players)})" if self.players else ""
        return [
            {"kind": 0, "label": "Player", "value": f"{self.player_name} {p_count}".strip()[:20], "percent": 0, "dim": False},
            {"kind": 0, "label": "Status", "value": f"{status_icon} {self.status}"[:20], "percent": 0, "dim": False},
            {"kind": 0, "label": "Track", "value": self.title[:22], "percent": 0, "dim": False},
            {"kind": 0, "label": "Artist", "value": self.artist[:22], "percent": 0, "dim": True},
            {"kind": 2, "label": "Progress", "value": "", "percent": self.percent, "dim": False},
            {"kind": 0, "label": "Time", "value": self.time_str, "percent": 0, "dim": True},
        ]


def main():
    ui = flipctl.load(PAGE, "media.slint")
    ctl = MediaController()

    def draw():
        ui.rows = ctl.rows()
        play_label = "Pause" if ctl.status == "Playing" else "Play"
        ui.buttons = ["Close", "Prev", play_label, "Next", "Switch"]

    # Touch / Mouse click on soft buttons
    @ui.button_clicked
    def _(slot):
        if slot == 0:
            slint.quit_event_loop()
        elif slot == 1:
            ctl.prev_track()
        elif slot == 2:
            ctl.play_pause()
        elif slot == 3:
            ctl.next_track()
        elif slot == 4:
            ctl.cycle_player()
        draw()

    # Touch / Mouse click on player header cycles player
    @ui.player_clicked
    def _():
        ctl.cycle_player()
        draw()

    # Touch / Mouse click on main body toggles play/pause
    @ui.body_clicked
    def _():
        ctl.play_pause()
        draw()

    # Hardware keyboard / D-pad
    @flipctl.on_key(ui)
    def _(key, down):
        if not down:
            return
        if key in (flipctl.Key.BACK, flipctl.Key.ESCAPE):
            slint.quit_event_loop()
        elif key in (flipctl.Key.LEFT, flipctl.Key.VIEW):
            ctl.prev_track()
        elif key in (flipctl.Key.RIGHT, flipctl.Key.EDIT):
            ctl.next_track()
        elif key in (flipctl.Key.OK, flipctl.Key.POWER):
            ctl.play_pause()
        elif key in (flipctl.Key.UP, flipctl.Key.DOWN, flipctl.Key.RUN):
            ctl.cycle_player()
        draw()

    draw()
    flipctl.run(ui, ctl_loop(ctl, draw))


async def ctl_loop(ctl, draw):
    while True:
        ctl.refresh()
        draw()
        try:
            await asyncio.wait_for(ctl.woken.wait(), 1.0)
        except asyncio.TimeoutError:
            pass
        ctl.woken.clear()


if __name__ == "__main__":
    main()
