#!/usr/bin/env python3
# /// flipctl
# name = "Volume"
# status = true
# audio = true
# runtime = ""
# ///
"""Laptop Volume Control for FlipCTL.

Controls PipeWire / PulseAudio default audio sink directly.
Adjust volume with Left/Right/Up/Down, toggle mute with Ok/Run.
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


class VolumeController:
    def __init__(self):
        self.volume = 50
        self.muted = False
        self.sink_name = "Default Sink"
        self.woken = asyncio.Event()
        self.refresh()

    def refresh(self):
        try:
            out = subprocess.check_output(
                ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            # Format: "Volume: 0.59 [MUTED]" or "Volume: 0.59"
            parts = out.strip().split()
            if len(parts) >= 2:
                self.volume = min(100, max(0, int(round(float(parts[1]) * 100))))
            self.muted = "[MUTED]" in out
        except Exception:
            pass

        try:
            status_out = subprocess.check_output(
                ["wpctl", "status"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            for line in status_out.splitlines():
                if "*" in line and "Sinks:" not in line and ("vol:" in line or "muted" in line):
                    cleaned = line.replace("*", "").strip()
                    # e.g. "50. Встроенное аудио Аналоговый стерео [vol: 0.59]"
                    parts = cleaned.split("[")[0].strip()
                    if "." in parts:
                        self.sink_name = parts.split(".", 1)[1].strip()
                    else:
                        self.sink_name = parts
                    break
        except Exception:
            pass

    def adjust(self, delta: int):
        target = max(0, min(100, self.volume + delta))
        frac = target / 100.0
        subprocess.run(
            ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{frac:.2f}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.volume = target
        self.woken.set()

    def toggle_mute(self):
        subprocess.run(
            ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.refresh()
        self.woken.set()

    def rows(self):
        state_str = "MUTED" if self.muted else "ACTIVE"
        vol_str = f"{self.volume}%"
        if self.muted:
            vol_str += " (Muted)"

        return [
            {"kind": 0, "label": "Device", "value": self.sink_name[:20], "percent": 0, "dim": False},
            {"kind": 2, "label": "Volume", "value": vol_str, "percent": self.volume, "dim": self.muted},
            {"kind": 1, "label": "", "value": "", "percent": 0, "dim": False},
            {"kind": 0, "label": "State", "value": state_str, "percent": 0, "dim": False},
            {"kind": 0, "label": "Step", "value": "Left/Right: 5%, Up/Dn: 10%", "percent": 0, "dim": True},
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
    ui = flipctl.load(PAGE, "volume.slint")
    ctl = VolumeController()

    def draw():
        ui.rows = ctl.rows()
        mute_btn = "Unmute" if ctl.muted else "Mute"
        ui.buttons = ["Close", "-5%", "+5%", mute_btn, "Toggle"]

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
            ctl.toggle_mute()
        draw()

    draw()
    flipctl.run(ui, ctl.run(draw))


if __name__ == "__main__":
    main()
