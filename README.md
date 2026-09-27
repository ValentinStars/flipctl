# FlipCTL

[![Upstream](https://img.shields.io/badge/upstream-flipperdevices%2Fflipctl--slint-blue)](https://github.com/flipperdevices/flipctl-slint)
[![License: GPL-3.0-only](https://img.shields.io/badge/License-GPL--3.0--only-blue.svg)](LICENSE)
[![Language](https://img.shields.io/badge/Rust-1.80%2B-orange.svg)](Cargo.toml)
[![Architecture](https://img.shields.io/badge/arch-x86__64%20%7C%20aarch64%20%7C%20armv6zk-lightgrey.svg)]()
[![Display](https://img.shields.io/badge/display-256x144%20%7C%20128x128%20ST7735-orange.svg)]()

This repository is an enhanced fork of [flipperdevices/flipctl-slint](https://github.com/flipperdevices/flipctl-slint), the Slint-based UI implementation for Flipper One.

The upstream project is tightly coupled to Flipper One engineering hardware (Allwinner H6 / Cortex-A53 `aarch64`, Debian/dpkg, 256x144 SPI panel). This fork adapts the UI to run on standard Linux desktop distributions (Arch, Ubuntu, Debian, Fedora) and microcontrollers/SBCs like the Raspberry Pi Zero W with 128x128 ST7735 SPI LCDs.

---

## Changes in this fork

1. **Multi-distro package manager detection** (`crates/flipper-ui/src/app.rs`)
   - Upstream hardcoded `dpkg-query -W` and `apt-get install`, which fails on non-Debian distributions (e.g. Arch Linux where `/var/lib/dpkg/status` is empty).
   - Added direct `$PATH` lookup: if a package binary (`python3`, `curl`, `jq`, `mpv`) exists, it is marked installed immediately without querying package managers.
   - Added dynamic detection for `pacman` (Arch/Manjaro), `apt` (Debian/Ubuntu), and `dnf` (Fedora).
   - Added package name mapping between Debian and Arch (`python3` -> `python`, `libsdl2-2.0-0` -> `sdl2`, etc.).

2. **Native x86_64 Doom bundle** (`sample_apps/Games/doom.fap.AppImage`)
   - Upstream shipped an `aarch64` AppImage that returned `Exec format error` on x86_64 machines.
   - Replaced with a self-contained x86_64 `dsda-doom` AppImage including `freedoom1.wad` (28.7 MB) and calibrated Flipper Amber palette (`palette.wad`).

3. **Raspberry Pi Zero W + Waveshare 1.44" LCD HAT support** (`rpi-zero-w/`)
   - Standalone zero-compilation hardware bridge (`waveshare_st7735.py`) using `spidev`, `RPi.GPIO`, and `Pillow`.
   - Supports ST7735S 128x128 16-bit RGB565 SPI LCD and all 8 hardware controls (Key1, Key2, Key3, 5-way joystick).
   - Works either as a local display driver or as a wireless hardware companion communicating with FlipCTL over WiFi.
   - Includes one-click `install.sh`, systemd unit, and kernel `dtoverlay` configuration.

4. **Flipper Amber backlight rendering & scaling** (`crates/flipper-ui/src/wl_sink.rs`, `bin/flipctl/src/main.rs`)
   - Added runtime color conversion mapping grayscale panel buffer to Flipper Amber (`#FF8200`).
   - Configurable via environment variables: `FLIPCTL_AMBER=1` and `FLIPCTL_SCALE=2` (1x to 8x integer scaling).

5. **Sample apps starter pack** (`sample_apps/`)
   - Working desktop integration utilities: Volume (PipeWire), Screen Brightness (D-Bus), Battery telemetry (sysfs), Network throughput (sysfs/WiFi), Media remote (MPRIS), System controls, Ping, Sysmon, and Doom.

6. **Default feature flags** (`bin/flipctl/Cargo.toml`)
   - Enabled `device`, `wayland`, `remote`, and `slint` by default so standard `cargo build` produces a complete build with GUI and web interface out of the box.

---

## Quickstart (Desktop / Laptop)

### Requirements

- Linux (x86_64 or aarch64)
- Rust toolchain (`cargo`, `rustc`)
- Wayland compositor (KDE Plasma, GNOME, Sway) or headless

### Build and Run

```bash
git clone https://github.com/ValentinStars/flipctl.git
cd flipctl

# Install sample apps to ~/Apps
bash sample_apps/install_apps.sh

# Run FlipCTL (starts GUI window and local web server on port 8899)
./start.sh
```

To run in headless mode (accessible exclusively via web browser at `http://<ip>:8899`):
```bash
./start.sh lan
```

---

## Raspberry Pi Zero W Setup (Waveshare 1.44" LCD HAT)

The Waveshare 1.44inch LCD HAT uses an ST7735S display (128x128 SPI) and 8 inputs.

### Pinout

| Function | Raspberry Pi BCM Pin | Physical Header Pin | Description |
|---|---|---|---|
| **MOSI** | GPIO 10 | Pin 19 | SPI Data |
| **SCLK** | GPIO 11 | Pin 23 | SPI Clock |
| **CE0** | GPIO 8 | Pin 24 | SPI Chip Select |
| **DC** | GPIO 25 | Pin 22 | Data / Command |
| **RST** | GPIO 27 | Pin 13 | Hardware Reset |
| **BL** | GPIO 24 | Pin 18 | Backlight |
| **Key 1** | GPIO 21 | Pin 40 | OK / Confirm |
| **Key 2** | GPIO 20 | Pin 38 | Back / Cancel |
| **Key 3** | GPIO 16 | Pin 36 | Menu / Switcher |
| **Joy Up** | GPIO 6 | Pin 31 | Navigation Up |
| **Joy Down** | GPIO 19 | Pin 35 | Navigation Down |
| **Joy Left** | GPIO 5 | Pin 29 | Navigation Left |
| **Joy Right** | GPIO 26 | Pin 37 | Navigation Right |
| **Joy Press**| GPIO 13 | Pin 33 | Confirm |

### Installation

On Raspberry Pi OS (32-bit):
```bash
git clone https://github.com/ValentinStars/flipctl.git
cd flipctl/rpi-zero-w
sudo bash install.sh
```

The script enables SPI, installs dependencies, and enables `flipctl-st7735.service`.

If FlipCTL runs on a separate PC on the same WiFi network, point the service to the host IP:
```bash
# In /etc/systemd/system/flipctl-st7735.service:
# change --host 127.0.0.1 to your PC's IP address:
ExecStart=/usr/bin/python3 /path/to/waveshare_st7735.py --host 192.168.1.50 --port 8899
```

---

## Keybindings

| Flipper Button | Keyboard Key | Waveshare HAT | Action |
|---|---|---|---|
| **OK** | `Return` / `Enter` | Key 1 / Joy Press | Open / Confirm |
| **Back** | `Escape` / `Backspace` | Key 2 | Back / Exit app |
| **Menu** | `Tab` / `m` | Key 3 | App Switcher / Menu |
| **D-Pad Up** | `Up Arrow` | Joy Up | Move cursor up |
| **D-Pad Down** | `Down Arrow` | Joy Down | Move cursor down |
| **D-Pad Left** | `Left Arrow` | Joy Left | Previous item / Left |
| **D-Pad Right** | `Right Arrow` | Joy Right | Next item / Right |

---

## Syncing with Upstream

To pull updates from the original `flipperdevices/flipctl-slint` repository:

```bash
git remote add upstream https://github.com/flipperdevices/flipctl-slint.git
git fetch upstream
git merge upstream/dev
```

---

## License

- Source code: **MIT**
- Compiled binary: **GPL-3.0-only** (due to static linking with Slint).
- Pixel fonts in `third_party/flipctl-fonts`:
  - `Busy9px`: MIT
  - `HaxrCorp 4090`: CC-BY-SA-3.0
  - `Born2bSportyV2`: Unlicense
- See `THIRD-PARTY-LICENSES.md` and `REUSE.toml` for full license details.
