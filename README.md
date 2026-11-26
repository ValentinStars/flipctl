# FlipCTL

[![Upstream](https://img.shields.io/badge/upstream-flipperdevices%2Fflipctl--slint-blue)](https://github.com/flipperdevices/flipctl-slint)
[![License: GPL-3.0-only](https://img.shields.io/badge/License-GPL--3.0--only-blue.svg)](LICENSE)
[![Language](https://img.shields.io/badge/Rust-1.92%2B-orange.svg)](Cargo.toml)
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
   - Wayland automatically centers the frame at the largest integer scale that fits. Set `FLIPCTL_SCALE=2` to cap it at 2× or `FLIPCTL_AMBER=0` for monochrome.

5. **Sample apps starter pack** (`sample_apps/`)
   - Working desktop integration utilities: Volume (PipeWire), Screen Brightness (D-Bus), Battery telemetry (sysfs), Network throughput (sysfs/WiFi), Media remote (MPRIS), System controls, Ping, Sysmon, and Doom.

6. **Default feature flags** (`bin/flipctl/Cargo.toml`)
   - Enabled `device`, `wayland`, `remote`, and `slint` by default so standard `cargo build` produces a complete build with GUI and web interface out of the box.

---

## Quickstart (Desktop / Laptop)

### Requirements

- Linux (x86_64 or aarch64)
- Rust 1.92 or newer (`cargo`, `rustc`)
- Wayland compositor (KDE Plasma, GNOME, Sway) or headless

### Build and Run

```bash
git clone https://github.com/ValentinStars/flipctl.git
cd flipctl

# Run FlipCTL on Wayland, with a local browser view at http://127.0.0.1:8899
./start.sh
```

To build the desktop analogue with its native apps and launch it in one command:

```bash
./start.sh build gui
```

The build includes Browser, Disk Benchmark, Hello, Internet Radio, Voice
Recorder, Speed test, System Monitor, and Tone Lab, plus native Slint runtimes
for Python and JavaScript. Building them requires Python 3 with pip and npm.
The staged Slint binding requires Python 3.12 or newer at runtime.
Running the shell uses a Wayland compositor. Sway hosts the apps, and
Chromium and mpv provide the browser engine and video playback.

Build both Linux architectures from the same checkout:

```bash
./start.sh build --target all
```

Use `--target amd64` or `--target arm64` for just one. The build stages the
shell, apps and runtimes under `target/desktop-apps/x86_64/` and
`target/desktop-apps/aarch64/`. When moving a build, copy that directory to the
same location in a checkout of this revision on the destination. Run
`./start.sh gui` there. FlipCTL selects the matching architecture and skips
AppImages built for another CPU.

Cross-builds need the Rust standard library for the target triple and a GNU
cross-linker with a Linux sysroot. On AMD64, install
`aarch64-unknown-linux-gnu` and `aarch64-linux-gnu-gcc`. For the reverse
direction use `x86_64-unknown-linux-gnu` and `x86_64-linux-gnu-gcc`. Set
`CARGO_TARGET_<TRIPLE>_LINKER` with the uppercase triple and underscores to
choose a different linker. By default the cross-build downloads and verifies
a Debian 12 glibc 2.36 sysroot using curl and dpkg-deb. Native builds use the
host system libraries. Use a target-compatible toolchain when building for a
different distribution baseline.

The source launch also discovers scripts and sample apps in the checkout and
`~/Apps`. Video Player and Dither Test use mpv. The AMD64 sample has its own
Doom AppImage. On ARM64 the source launcher for Doom uses the system
`dsda-doom` and Freedoom data. Flipper One hardware utilities require the
corresponding hardware.

Without Wayland, `./start.sh` serves the browser view at `http://127.0.0.1:8899`.
Use `./start.sh lan` to listen on all interfaces and `./start.sh panel` for the
original Flipper One SPI panel. Set `FLIPCTL_ADDRESS` to configure the listening address.

### External HDMI touchscreen

For the Waveshare 4.3inch HDMI LCD (B), connect HDMI for video and USB for touch.
Use the display's 800×480 mode at 100% desktop scaling. A matching Wayland
surface shows the 768×432 frame at 3× scaling against the top edge. The
remaining 48 pixels hold the five Flipper One soft keys.

```bash
./start.sh gui --output HDMI-A-1
```

Use `wayland-info` to list the output names available to FlipCTL. In KDE, a
mirrored HDMI screen shares the laptop panel's Wayland output. Run `./start.sh`
to use both mirrored screens. For a dedicated HDMI view, select an extended
desktop and map the touchscreen to HDMI in the compositor's input settings.

Tap or click **Z**, **X**, **C**, **V**, or **B** to send Escape, View, Power,
Edit, or Run. The onscreen labels inside the FlipCTL frame describe what each
key does in the current screen or app. The Power key sends a software input
event. Flipper One's MCU handles hardware power-off on the original device.
Use a physical D-pad or the arrow keys and Enter for directional navigation.
The top edge of the FlipCTL frame acts as Back. On displays without space for
the soft-key strip, touch and mouse can also navigate inside the frame.
Hosted apps receive pointer input inside their content. The bundled 2048 sample
accepts swipes, mouse drags and D-pad input, and keeps its best score in its app
data directory.

On desktop Linux, App Manager checks dependencies with the package manager for
the distribution. It supports apt, pacman, dnf and zypper. The system readout
shows uptime when charger power is unavailable.

### HackRF TX

Open **Apps → Radio → HackRF TX**. Use the D-pad to select a setting and **V**
to edit it. On **Input**, **V** opens the media browser. From there, **V**
opens **Places** with the standard folders and recently selected inputs,
and **X** opens the keyboard for a URL or a path.

On **Frequency**, Left and Right choose a digit. Up and Down cycle that
digit between 0 and 9 independently. **X** jumps back to the MHz units digit. **V** accepts a typed frequency such
as `99.4M` or `433920k`. On **TX Gain**, Left and Right adjust by 1 dB,
Down and Up by 5 dB, and **X** switches between Auto and Manual. **B** applies
either edit.

From settings, **B** reviews the frequency, input and transmission settings.
Press **V** on the review to start. **Z** stops transmission, and **X** shows
device information from settings. During single-source playback, **X** shows
the log and **B** also stops transmission.

Folder playback shuffles its tracks once per run. During playlist
transmission, **X** and **V** move to the previous or next track in that order,
and **B** shows the log. Loop replays the shuffled order. Use **Stop** before
returning to the launcher, where apps can run in the background.

The app and its Python backend are shared by the AMD64 and ARM64 builds.
Connect a HackRF One over USB and install the HackRF host tools
(`hackrf_transfer` and `hackrf_info`) and NumPy for the Python interpreter
used by FlipCTL. Install FFmpeg for audio input and `pactl` (PulseAudio or
PipeWire) for system audio and the virtual output. In virtual output mode,
route the desired audio to the **HackRF TX** output.

Audio pipe mode accepts a named FIFO. The bundled CLI also accepts standard
input with `-`. Run `python3 apps/Radio/hackrf_tx_backend.py --help` to see
all CLI options.

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
  - `helvb08` and its generated glyph table: LicenseRef-X11-Adobe-DEC
- Wi-Fi Scanner data: Apache-2.0 for the device signature table and
  LicenseRef-IEEE-OUI-Public-Listing for the vendor table.
- See `THIRD-PARTY-LICENSES.md` and `REUSE.toml` for full license details.
