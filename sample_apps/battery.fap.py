#!/usr/bin/env python3
# /// flipctl
# name = "Battery"
# status = true
# runtime = ""
# ///
"""Laptop Battery & Power Health Monitor for FlipCTL.

Reads hardware battery stats directly from Linux sysfs (/sys/class/power_supply/BAT0):
Capacity, Charging Status, Real-time Power Consumption (Watts), Voltage, Health and Cycles.
"""

import asyncio
from pathlib import Path
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

BAT_DIR = Path("/sys/class/power_supply/BAT0")


class BatteryMonitor:
    def __init__(self):
        self.capacity = 50
        self.status = "Unknown"
        self.power_w = 0.0
        self.voltage_v = 0.0
        self.health_pct = 100
        self.cycles = 0
        self.time_rem = "-"
        self.refresh()

    def _read_int(self, name: str, default: int = 0) -> int:
        p = BAT_DIR / name
        if p.exists():
            try:
                return int(p.read_text().strip())
            except Exception:
                pass
        return default

    def _read_str(self, name: str, default: str = "") -> str:
        p = BAT_DIR / name
        if p.exists():
            try:
                return p.read_text().strip()
            except Exception:
                pass
        return default

    def refresh(self):
        if not BAT_DIR.exists():
            self.status = "No Battery"
            return

        self.capacity = self._read_int("capacity", 50)
        self.status = self._read_str("status", "Unknown")
        self.cycles = self._read_int("cycle_count", 0)

        # Power & Voltage
        power_u = self._read_int("power_now", 0)
        if power_u == 0:
            current_u = self._read_int("current_now", 0)
            voltage_u = self._read_int("voltage_now", 0)
            power_u = int((current_u * voltage_u) / 1000000)
        self.power_w = power_u / 1000000.0

        voltage_u = self._read_int("voltage_now", 0)
        self.voltage_v = voltage_u / 1000000.0

        # Health
        full_u = self._read_int("energy_full", 0) or self._read_int("charge_full", 0)
        design_u = self._read_int("energy_full_design", 0) or self._read_int("charge_full_design", 0)
        if design_u > 0 and full_u > 0:
            self.health_pct = min(100, int(round((full_u / float(design_u)) * 100)))

        # Time estimate
        now_u = self._read_int("energy_now", 0) or self._read_int("charge_now", 0)
        if power_u > 0:
            if self.status == "Discharging" and now_u > 0:
                hours = now_u / float(power_u)
                mins = int(hours * 60)
                self.time_rem = f"{mins // 60}h {mins % 60}m rem"
            elif self.status == "Charging" and full_u > now_u:
                hours = (full_u - now_u) / float(power_u)
                mins = int(hours * 60)
                self.time_rem = f"{mins // 60}h {mins % 60}m to full"
            else:
                self.time_rem = "-"
        else:
            self.time_rem = "-"

    def rows(self):
        state_str = f"{self.status} ({self.time_rem})" if self.time_rem != "-" else self.status
        return [
            {"kind": 2, "label": "Charge", "value": f"{self.capacity}%", "percent": self.capacity, "dim": False},
            {"kind": 0, "label": "Status", "value": state_str, "percent": 0, "dim": False},
            {"kind": 1, "label": "", "value": "", "percent": 0, "dim": False},
            {"kind": 0, "label": "Power Rate", "value": f"{self.power_w:.2f} W", "percent": 0, "dim": False},
            {"kind": 0, "label": "Voltage", "value": f"{self.voltage_v:.2f} V", "percent": 0, "dim": True},
            {"kind": 0, "label": "Health", "value": f"{self.health_pct}%", "percent": 0, "dim": False},
            {"kind": 0, "label": "Cycles", "value": str(self.cycles), "percent": 0, "dim": True},
        ]

    async def run(self, draw):
        while True:
            self.refresh()
            draw()
            await asyncio.sleep(2.0)


def main():
    ui = flipctl.load(PAGE, "battery.slint")
    mon = BatteryMonitor()

    def draw():
        ui.rows = mon.rows()
        ui.buttons = ["Close", "", "", "", "Refresh"]

    @flipctl.on_key(ui)
    def _(key, down):
        if not down:
            return
        if key in (flipctl.Key.BACK, flipctl.Key.ESCAPE):
            slint.quit_event_loop()
        elif key in (flipctl.Key.RUN, flipctl.Key.ENTER, flipctl.Key.EDIT):
            mon.refresh()
            draw()

    draw()
    flipctl.run(ui, mon.run(draw))


if __name__ == "__main__":
    main()
