#!/usr/bin/env python3
# /// flipctl
# name = "Brightness"
# status = true
# runtime = ""
# ///
"""Screen Brightness Control for FlipCTL.

Controls laptop display brightness via KDE ScreenBrightness D-Bus service.
Left/Right adjusts brightness, Ok/Run toggles quick presets.
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

SERVICE = "org.kde.ScreenBrightness"
PATH = "/org/kde/ScreenBrightness/display0"
INTERFACE = "org.kde.ScreenBrightness.Display"


class BrightnessController:
    def __init__(self):
        self.brightness = 50
        self.max_brightness = 10000
        self.raw_val = 5000
        self.label = "Laptop Screen"
        self.woken = asyncio.Event()
        self.refresh()

    def refresh(self):
        try:
            out = subprocess.check_output(
                ["busctl", "--user", "get-property", SERVICE, PATH, INTERFACE, "Brightness"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            # Format: "i 6500"
            parts = out.strip().split()
            if len(parts) >= 2:
                self.raw_val = int(parts[1])

            max_out = subprocess.check_output(
                ["busctl", "--user", "get-property", SERVICE, PATH, INTERFACE, "MaxBrightness"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            parts_max = max_out.strip().split()
            if len(parts_max) >= 2:
                self.max_brightness = int(parts_max[1])

            self.brightness = int(round((self.raw_val / float(self.max_brightness)) * 100))
        except Exception:
            pass

    def adjust(self, delta_pct: int):
        new_pct = max(5, min(100, self.brightness + delta_pct))
        new_raw = int(round((new_pct / 100.0) * self.max_brightness))
        subprocess.run(
            ["busctl", "--user", "call", SERVICE, PATH, INTERFACE, "SetBrightness", "iu", str(new_raw), "0"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.brightness = new_pct
        self.raw_val = new_raw
        self.woken.set()

    def toggle_preset(self):
        # Cycle through 30% -> 70% -> 100% -> 30%
        if self.brightness < 50:
            target = 70
        elif self.brightness < 90:
            target = 100
        else:
            target = 30
        self.adjust(target - self.brightness)

    def rows(self):
        return [
            {"kind": 0, "label": "Display", "value": "Internal eDP", "percent": 0, "dim": False},
            {"kind": 2, "label": "Brightness", "value": f"{self.brightness}%", "percent": self.brightness, "dim": False},
            {"kind": 1, "label": "", "value": "", "percent": 0, "dim": False},
            {"kind": 0, "label": "Presets", "value": "30% | 70% | 100%", "percent": 0, "dim": True},
            {"kind": 0, "label": "Controls", "value": "Left/Right: 5%, Up/Dn: 10%", "percent": 0, "dim": True},
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
    ui = flipctl.load(PAGE, "brightness.slint")
    ctl = BrightnessController()

    def draw():
        ui.rows = ctl.rows()
        ui.buttons = ["Close", "-5%", "+5%", "Preset", "Preset"]

    @flipctl.on_key(ui)
    def _(key, down):
        if not down:
            return
        if key in (flipctl.Key.BACK, flipctl.Key.ESCAPE):
            slint.quit_event_loop()
        elif key in (flipctl.Key.LEFT,):
            ctl.adjust(-5)
        elif key in (flipctl.Key.RIGHT,):
            ctl.adjust(5)
        elif key in (flipctl.Key.DOWN,):
            ctl.adjust(-10)
        elif key in (flipctl.Key.UP,):
            ctl.adjust(10)
        elif key in (flipctl.Key.RUN, flipctl.Key.ENTER, flipctl.Key.EDIT):
            ctl.toggle_preset()
        draw()

    draw()
    flipctl.run(ui, ctl.run(draw))


if __name__ == "__main__":
    main()
