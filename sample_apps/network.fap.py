#!/usr/bin/env python3
# /// flipctl
# name = "Network"
# status = true
# runtime = ""
# ///
"""Network Status & Traffic Radar for FlipCTL.

Displays current WiFi SSID / Ethernet interface, Local IP address,
Gateway, live download/upload throughput (KB/s), and signal strength.
"""

import asyncio
from pathlib import Path
import socket
import struct
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


def get_default_route():
    try:
        with open("/proc/net/route", "r", encoding="ascii") as f:
            for line in f.read().splitlines()[1:]:
                fields = line.split()
                if len(fields) > 2 and fields[1] == "00000000" and fields[2] != "00000000":
                    iface = fields[0]
                    gw = socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
                    return iface, gw
    except Exception:
        pass
    return "wlp5s0", "192.168.1.1"


def get_local_ip(target="8.8.8.8"):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((target, 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class NetworkRadar:
    def __init__(self):
        self.iface, self.gateway = get_default_route()
        self.local_ip = get_local_ip()
        self.ssid = "Disconnected"
        self.signal_pct = 0
        self.rx_bytes = 0
        self.tx_bytes = 0
        self.last_time = time.monotonic()
        self.rx_rate = 0.0
        self.tx_rate = 0.0
        self.read_stats()
        self.refresh_wifi()

    def read_stats(self):
        try:
            with open("/proc/net/dev", "r") as f:
                for line in f:
                    if ":" in line:
                        parts = line.split(":")
                        name = parts[0].strip()
                        if name == self.iface:
                            data = parts[1].split()
                            rx = int(data[0])
                            tx = int(data[8])
                            return rx, tx
        except Exception:
            pass
        return 0, 0

    def refresh_wifi(self):
        self.iface, self.gateway = get_default_route()
        self.local_ip = get_local_ip()
        try:
            out = subprocess.check_output(
                ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            for line in out.splitlines():
                parts = line.split(":")
                if len(parts) >= 3 and "wireless" in parts[1]:
                    self.ssid = parts[0]
                    break
                elif len(parts) >= 3 and "ethernet" in parts[1]:
                    self.ssid = f"Eth: {parts[0]}"
                    break
        except Exception:
            pass

        # Read wireless signal if wlp
        try:
            with open("/proc/net/wireless", "r") as f:
                for line in f.read().splitlines()[2:]:
                    parts = line.split(":")
                    if len(parts) >= 2 and self.iface in parts[0]:
                        fields = parts[1].split()
                        link_val = float(fields[1].replace(".", ""))
                        # Link quality typically 0..70
                        self.signal_pct = min(100, int((link_val / 70.0) * 100))
        except Exception:
            self.signal_pct = 80

    def update_rates(self):
        rx, tx = self.read_stats()
        now = time.monotonic()
        dt = max(0.1, now - self.last_time)
        if self.rx_bytes > 0 and rx >= self.rx_bytes:
            self.rx_rate = (rx - self.rx_bytes) / (dt * 1024.0)  # KB/s
            self.tx_rate = (tx - self.tx_bytes) / (dt * 1024.0)  # KB/s
        self.rx_bytes = rx
        self.tx_bytes = tx
        self.last_time = now

    def rows(self):
        def fmt_rate(kbs):
            if kbs >= 1024:
                return f"{kbs / 1024.0:.1f} MB/s"
            return f"{kbs:.1f} KB/s"

        return [
            {"kind": 0, "label": "Network", "value": self.ssid[:18], "percent": 0, "dim": False},
            {"kind": 2, "label": "Signal", "value": f"{self.signal_pct}%", "percent": self.signal_pct, "dim": False},
            {"kind": 1, "label": "", "value": "", "percent": 0, "dim": False},
            {"kind": 0, "label": "IP", "value": self.local_ip, "percent": 0, "dim": False},
            {"kind": 0, "label": "Gateway", "value": self.gateway, "percent": 0, "dim": True},
            {"kind": 0, "label": "Download", "value": fmt_rate(self.rx_rate), "percent": 0, "dim": False},
            {"kind": 0, "label": "Upload", "value": fmt_rate(self.tx_rate), "percent": 0, "dim": True},
        ]

    async def run(self, draw):
        while True:
            self.update_rates()
            draw()
            await asyncio.sleep(1.0)


def main():
    ui = flipctl.load(PAGE, "network.slint")
    radar = NetworkRadar()

    def draw():
        ui.rows = radar.rows()
        ui.buttons = ["Close", "", "", "", "Refresh"]

    @flipctl.on_key(ui)
    def _(key, down):
        if not down:
            return
        if key in (flipctl.Key.BACK, flipctl.Key.ESCAPE):
            slint.quit_event_loop()
        elif key in (flipctl.Key.RUN, flipctl.Key.ENTER, flipctl.Key.EDIT):
            radar.refresh_wifi()
            draw()

    draw()
    flipctl.run(ui, radar.run(draw))


if __name__ == "__main__":
    main()
