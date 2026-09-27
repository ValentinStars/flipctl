#!/usr/bin/env python3
# /// flipctl
# name = "System"
# status = true
# runtime = ""
# ///
"""Laptop System Actions for FlipCTL.

Quick laptop management from phone or keyboard:
Lock screen, suspend/sleep laptop, and view system uptime.
"""

import asyncio
from pathlib import Path
import subprocess
import time
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

ACTIONS = [
    ("Lock Screen", "loginctl lock-session"),
    ("Suspend / Sleep", "systemctl suspend"),
    ("Turn Screen Off", "busctl --user call org.kde.ScreenBrightness /org/kde/ScreenBrightness/display0 org.kde.ScreenBrightness.Display SetBrightness iu 0 0"),
]


class SystemActions:
    def __init__(self):
        self.at = 0
        self.msg = "Ready"
        self.woken = asyncio.Event()

    def uptime_str(self):
        try:
            with open("/proc/uptime", "r") as f:
                sec = float(f.read().split()[0])
            hours = int(sec // 3600)
            mins = int((sec % 3600) // 60)
            return f"{hours}h {mins}m"
        except Exception:
            return "-"

    def choose(self, delta):
        self.at = (self.at + delta) % len(ACTIONS)
        self.msg = "Ready"
        self.woken.set()

    def execute(self):
        name, cmd = ACTIONS[self.at]
        self.msg = f"Ran: {name}"
        try:
            subprocess.Popen(cmd, shell=True)
        except Exception as e:
            self.msg = f"Err: {e}"
        self.woken.set()

    def rows(self):
        act_name, _ = ACTIONS[self.at]
        return [
            {"kind": 0, "label": "Host", "value": "VSTThinkPad", "percent": 0, "dim": False},
            {"kind": 0, "label": "Uptime", "value": self.uptime_str(), "percent": 0, "dim": True},
            {"kind": 1, "label": "", "value": "", "percent": 0, "dim": False},
            {"kind": 0, "label": "Action", "value": f"> {act_name} <", "percent": 0, "dim": False},
            {"kind": 0, "label": "Status", "value": self.msg, "percent": 0, "dim": True},
            {"kind": 0, "label": "Guide", "value": "Left/Right: pick, Ok: Run", "percent": 0, "dim": True},
        ]

    async def run(self, draw):
        while True:
            draw()
            try:
                await asyncio.wait_for(self.woken.wait(), 5.0)
            except asyncio.TimeoutError:
                pass
            self.woken.clear()


def main():
    ui = flipctl.load(PAGE, "sysctl.slint")
    ctl = SystemActions()

    def draw():
        ui.rows = ctl.rows()
        ui.buttons = ["Close", "Prev", "Next", "Execute", "Run"]

    @flipctl.on_key(ui)
    def _(key, down):
        if not down:
            return
        if key in (flipctl.Key.BACK, flipctl.Key.ESCAPE):
            slint.quit_event_loop()
        elif key in (flipctl.Key.LEFT, flipctl.Key.UP):
            ctl.choose(-1)
        elif key in (flipctl.Key.RIGHT, flipctl.Key.DOWN):
            ctl.choose(1)
        elif key in (flipctl.Key.RUN, flipctl.Key.ENTER, flipctl.Key.EDIT):
            ctl.execute()
        draw()

    draw()
    flipctl.run(ui, ctl.run(draw))


if __name__ == "__main__":
    main()
