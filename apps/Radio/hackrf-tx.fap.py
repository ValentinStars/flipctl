#!/usr/bin/env python3
# /// flipctl
# name = "HackRF TX"
# audio = true
# status = true
# ///
"""FlipCTL controls for the bundled hackrf-tx transmitter."""

import asyncio
from collections import deque
from importlib.util import find_spec
import json
import math
import os
from pathlib import Path
import queue
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import quote, urlencode

import flipctl
from hackrf_tx_backend import (
    FRAME_SECONDS, parse_frequency, select_gain, select_mode, tx_frame,
)

from hackrf_tx_picker import MediaPicker

BACKEND = Path(__file__).resolve().with_name("hackrf_tx_backend.py")
SOURCES = ("File / folder / URL", "Microphone", "System audio", "Virtual output", "M12", "Audio pipe")
MODES = ("Auto", "WFM", "NFM", "USB", "LSB", "AM")
STOP_TIMEOUT = 7
MAX_FREQUENCY = 7_250_000_000
MAX_GAIN = 47
FREQUENCY_STEPS = tuple(10 ** digit for digit in range(9, -1, -1))
STARTUP_STAGES = frozenset({
    "Preparing audio...", "Opening audio input...", "Waiting for audio...", "Starting HackRF...",
})
SOURCE_FLAGS = {"Microphone": "--mic", "System audio": "--system-audio",
                "Virtual output": "--virtual-output"}


def input_preview(value):
    if flipctl.text_width(value) <= 200:
        return value
    value = value[-200:]
    while flipctl.text_width("..." + value) > 200:
        value = value[1:]
    return "..." + value


def checked_frequency(value):
    try:
        frequency = parse_frequency(value)
    except (ValueError, OverflowError) as error:
        raise ValueError("Enter a valid frequency") from error
    if not 1 <= frequency <= MAX_FREQUENCY:
        raise ValueError("Frequency out of range")
    return frequency


def mhz(frequency):
    whole, fraction = divmod(frequency, 1_000_000)
    return f"{whole}.{fraction:06d}".rstrip("0").rstrip(".")


def ask_keyboard(title, text):
    path = os.environ.get("FLIPCTL_SOCKET")
    if not path:
        raise OSError("FlipCTL keyboard is unavailable")
    query = urlencode({"title": title, "text": text}, quote_via=quote)
    with socket.socket(socket.AF_UNIX) as connection:
        connection.connect(path)
        connection.sendall(
            f"POST /keyboard?{query} HTTP/1.1\r\nHost: flipctl\r\nContent-Length: 0\r\n\r\n".encode()
        )
        with connection.makefile("rb") as result:
            status = result.readline().split()
            while line := result.readline():
                if not line.strip():
                    break
            response = json.loads(result.read())
    if len(status) < 2 or status[1] != b"200":
        raise OSError(response.get("error", "Keyboard request failed"))
    return response.get("text")


class Transmission:
    def __init__(self, command, playlist=False):
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE if playlist else subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, errors="replace", start_new_session=True)
        self.playlist = playlist
        self.track = None
        if self.process.stdin is not None:
            os.set_blocking(self.process.stdin.fileno(), False)
        self.events = queue.SimpleQueue()
        self.log = deque(maxlen=60)
        self.first_samples = None
        self.startup = "Preparing audio..."
        self.stopping = None
        self.stopped_by_user = False
        self.output_closed = False
        threading.Thread(target=self.read_output, daemon=True).start()

    def read_output(self):
        try:
            with self.process.stdout as output:
                for line in output:
                    self.events.put(line.rstrip())
        finally:
            self.events.put(None)

    def update(self):
        while not self.events.empty():
            line = self.events.get_nowait()
            if line is None:
                self.output_closed = True
                if self.process.stdin is not None:
                    self.process.stdin.close()
            elif line:
                self.log.append(line)
                if self.playlist and line.startswith("[*] Track "):
                    position, _, name = line[len("[*] Track "):].partition(": ")
                    self.track = position, name
                if self.first_samples is None and line.startswith("[*] "):
                    stage = line[4:]
                    if stage in STARTUP_STAGES:
                        self.startup = stage
                    elif stage.startswith("Transmitting on "):
                        self.first_samples = time.monotonic()
        if self.stopping is not None and time.monotonic() >= self.stopping:
            self.send(signal.SIGKILL)
            self.stopping = None
        return self.process.poll() is not None and self.output_closed

    def change_track(self, previous):
        if self.process.poll() is None and not self.output_closed and not self.stopped_by_user:
            try:
                os.write(self.process.stdin.fileno(), b"p" if previous else b"n")
            except (BrokenPipeError, BlockingIOError):
                pass

    def send(self, sig):
        try:
            os.killpg(self.process.pid, sig)
        except ProcessLookupError:
            pass

    def stop(self):
        if self.process.poll() is None and not self.stopped_by_user:
            self.stopped_by_user = True
            self.stopping = time.monotonic() + STOP_TIMEOUT
            # The backend shuts down its children and unloads the PulseAudio sink.
            self.process.terminate()

    def close(self):
        self.stop()
        try:
            self.process.wait(timeout=STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            self.send(signal.SIGKILL)
            self.process.wait()
        finally:
            if self.process.stdin is not None:
                self.process.stdin.close()


class App:
    def __init__(self):
        self.values = {
            "Source": SOURCES[0], "Input": "", "Frequency": "99.4M", "Mode": "Auto",
            "TX gain": "Auto", "RF amp": False, "Loop": False, "Audio level": "0.85",
            "Station ID": "542", "Groups": "15",
        }
        self.notice = ""
        self.pending_command = None
        self.pending_playlist = False
        self.summary = []
        self.device = ["Checking for HackRF..."]
        self.running = None
        self.outcome = ""
        self.elapsed = None
        self.events = queue.SimpleQueue()
        self.editing = False
        self.settings = flipctl.Menu(self.options, buttons=self.settings_buttons)
        self.picker = MediaPicker(self.pick_input, lambda: self.edit_text("Input"), self.settings.show)
        self.source_menu = flipctl.Menu(self.source_rows, buttons=["Back", "", "", "", "Select"])
        self.source_menu.on_key(self.source_key)
        self.frequency_editor = flipctl.Frequency(self.frequency_readout, buttons=self.frequency_buttons)
        self.frequency_editor.on_key(self.frequency_key)
        self.gain_editor = flipctl.Detail(self.gain_rows, buttons=self.gain_buttons, fit_gauges=True)
        self.gain_editor.on_key(self.gain_key)
        self.review = flipctl.Detail(
            self.review_rows, buttons=lambda: ["Back", "", "", "Transmit" if self.pending_command else "", ""])
        self.transfer = flipctl.Detail(self.tx_rows, buttons=self.tx_buttons)
        self.device_view = flipctl.Detail(self.device_rows, buttons=["Back", "", "", "", "Refresh"])
        self.logs = flipctl.Log(self.log_lines, title="HackRF TX Log", buttons=self.log_buttons)
        for view, handler in ((self.settings, self.setting_key), (self.review, self.review_key),
                              (self.transfer, self.transfer_key), (self.device_view, self.device_key)):
            view.on_key(handler)
        self.logs.on_key(self.log_key)

    def fields(self):
        source = self.values["Source"]
        fields = ["Source"]
        if source in (SOURCES[0], "Audio pipe"):
            fields.append("Input")
        fields.extend(["Frequency", "Mode", "TX gain", "RF amp", "Loop", "Audio level"])
        if source == "M12":
            fields.extend(["Station ID", "Groups"])
        return fields

    def options(self):
        rows = []
        for field in self.fields():
            value = self.values[field]
            if isinstance(value, bool):
                value = "On" if value else "Off"
            value = str(value) or "Choose input"
            if field == "Source" and value == SOURCES[0]:
                value = "Media"
            if field == "Frequency":
                value = mhz(checked_frequency(value)) + " MHz"
            if field == "TX gain" and value != "Auto":
                value += " dB"
            if field == "Input" and len(value) > 22:
                value = "..." + value[-19:]
            if field in ("Source", "Mode", "RF amp", "Loop"):
                rows.append(flipctl.item(field, value=value, chevrons=1))
            else:
                rows.append(flipctl.item(field, status=value))
        return rows

    def settings_buttons(self):
        field = self.fields()[self.settings.selected]
        action = "Browse" if field == "Input" else "Select" if field == "Source" else "Edit"
        return ["Close", "Device", "", action, "Review"]

    def pick_input(self, path):
        self.values["Input"] = str(path)
        self.settings.show()

    def source_rows(self):
        yield from (flipctl.item(source) for source in SOURCES)

    def source_key(self, key):
        if key in (flipctl.Key.BACK, flipctl.Key.ESCAPE):
            self.settings.show()
        elif key in (flipctl.Key.OK, flipctl.Key.RUN):
            self.values["Source"] = SOURCES[self.source_menu.selected]
            self.settings.show()
        else:
            return False
        return True

    def edit(self, field, key):
        value = self.values[field]
        from_arrow = key in (flipctl.Key.LEFT, flipctl.Key.RIGHT)
        if field == "Source" and not from_arrow:
            self.source_menu.selected = SOURCES.index(value)
            self.source_menu.show()
        elif field == "Input" and not from_arrow:
            self.picker.show(value, self.values["Source"] == "Audio pipe")
        elif field == "Frequency" and not from_arrow:
            self.frequency_value = checked_frequency(value)
            self.frequency_digit = 3
            self.frequency_editor.show()
        elif field == "TX gain" and not from_arrow:
            self.auto_gain = value == "Auto"
            self.gain_value = self.default_gain() if self.auto_gain else int(value)
            self.gain_editor.show()
        elif field in ("Source", "Mode"):
            choices = SOURCES if field == "Source" else MODES
            step = -1 if key is flipctl.Key.LEFT else 1
            self.values[field] = choices[(choices.index(value) + step) % len(choices)]
        elif isinstance(value, bool):
            self.values[field] = not value
        elif not from_arrow:
            self.edit_text(field)

    def frequency_valid(self):
        return 1 <= self.frequency_value <= MAX_FREQUENCY

    def frequency_buttons(self):
        return ["Cancel", "MHz", "", "Type", "Apply" if self.frequency_valid() else ""]

    def frequency_readout(self):
        digits = f"{self.frequency_value:010d}"
        step = FREQUENCY_STEPS[self.frequency_digit]
        step_text = f"{step // 1_000_000} MHz" if step >= 1_000_000 else (
            f"{step // 1000} kHz" if step >= 1000 else f"{step} Hz")
        padding = 4 - max(1, len(digits[:4].lstrip("0")))
        return digits, self.frequency_digit, padding, step_text, self.frequency_valid()

    def frequency_key(self, key):
        if key in (flipctl.Key.BACK, flipctl.Key.ESCAPE):
            self.settings.show()
        elif key in (flipctl.Key.OK, flipctl.Key.RUN):
            if self.frequency_valid():
                self.values["Frequency"] = f"{self.frequency_value}Hz"
                self.settings.show()
        elif key is flipctl.Key.VIEW:
            self.frequency_digit = 3
        elif key is flipctl.Key.EDIT:
            self.edit_text("Frequency", mhz(self.frequency_value) + "M")
        elif key in (flipctl.Key.LEFT, flipctl.Key.RIGHT):
            direction = -1 if key is flipctl.Key.LEFT else 1
            self.frequency_digit = max(0, min(self.frequency_digit + direction, len(FREQUENCY_STEPS) - 1))
        elif key in (flipctl.Key.UP, flipctl.Key.DOWN):
            step = FREQUENCY_STEPS[self.frequency_digit]
            digit = self.frequency_value // step % 10
            direction = 1 if key is flipctl.Key.UP else -1
            next_digit = (digit + direction) % 10
            self.frequency_value += (next_digit - digit) * step
        else:
            return False
        return True

    def effective_mode(self, frequency):
        mode = self.values["Mode"]
        return select_mode(frequency, None if mode == "Auto" else mode,
                           m12=self.values["Source"] == "M12")

    def default_gain(self):
        frequency = checked_frequency(self.values["Frequency"])
        return select_gain(self.effective_mode(frequency))

    def gain_buttons(self):
        return ["Cancel", "Manual" if self.auto_gain else "Auto", "", "", "Apply"]

    def gain_rows(self):
        gain = self.default_gain() if self.auto_gain else self.gain_value
        return [flipctl.heading("TX Gain", f"{gain} dB"), flipctl.rule(),
                flipctl.gauge("", "", round(gain * 100 / MAX_GAIN)),
                flipctl.pair("0 dB", f"{MAX_GAIN} dB", dim=True),
                flipctl.pair("Control", "Auto" if self.auto_gain else "Manual"),
                flipctl.text("Left / Right   1 dB", dim=True),
                flipctl.text("Down / Up      5 dB", dim=True)]

    def gain_key(self, key):
        if key in (flipctl.Key.BACK, flipctl.Key.ESCAPE):
            self.settings.show()
        elif key in (flipctl.Key.OK, flipctl.Key.RUN):
            self.values["TX gain"] = "Auto" if self.auto_gain else str(self.gain_value)
            self.settings.show()
        elif key is flipctl.Key.VIEW:
            self.auto_gain = not self.auto_gain
        elif key in (flipctl.Key.LEFT, flipctl.Key.RIGHT, flipctl.Key.DOWN, flipctl.Key.UP):
            delta = {flipctl.Key.LEFT: -1, flipctl.Key.RIGHT: 1, flipctl.Key.DOWN: -5, flipctl.Key.UP: 5}[key]
            gain = self.default_gain() if self.auto_gain else self.gain_value
            self.auto_gain = False
            self.gain_value = max(0, min(gain + delta, MAX_GAIN))
        else:
            return False
        return True

    def edit_text(self, field, initial=None):
        if self.editing:
            return
        self.editing = True
        value = self.values[field] if initial is None else initial

        def worker():
            try:
                self.events.put(("edited", field, ask_keyboard(field, value)))
            except (OSError, ValueError) as error:
                self.events.put(("edit_error", str(error), None))

        threading.Thread(target=worker, daemon=True).start()

    def command(self):
        cfg = self.values
        self.pending_playlist = False
        if find_spec("numpy") is None:
            raise ValueError("Install Python NumPy")

        source = cfg["Source"]
        required = ["hackrf_transfer"] + (["ffmpeg"] if source != "M12" else [])
        if source in ("System audio", "Virtual output"):
            required.append("pactl")
        missing = [tool for tool in required if shutil.which(tool) is None]
        if missing:
            raise ValueError("Missing " + ", ".join(missing))

        frequency = checked_frequency(cfg["Frequency"])
        gain = cfg["TX gain"]
        if gain.lower() != "auto" and (not gain.isdigit() or not 0 <= int(gain) <= MAX_GAIN):
            raise ValueError("TX gain must be Auto or 0-47 dB")
        try:
            volume = float(cfg["Audio level"])
        except ValueError as error:
            raise ValueError("Enter a valid audio level") from error
        if not math.isfinite(volume) or volume < 0:
            raise ValueError("Audio level must be finite and non-negative")

        args = [sys.executable, "-u", str(BACKEND), f"--freq={frequency}Hz", f"--volume={volume}"]
        if cfg["Mode"] != "Auto":
            args.append(f"--mode={cfg['Mode'].lower()}")
        if gain.lower() != "auto":
            args.append(f"--gain={gain}")
        args += [flag for field, flag in (("RF amp", "--amp"), ("Loop", "--loop")) if cfg[field]]
        if source in SOURCE_FLAGS:
            args.append(SOURCE_FLAGS[source])
        elif source == "M12":
            station, groups = cfg["Station ID"], cfg["Groups"]
            if not (station.isascii() and station.isdigit() and 1 <= len(station) <= 3):
                raise ValueError("Station ID must have 1-3 digits")
            if not groups.isdigit() or int(groups) < 1:
                raise ValueError("Groups must be a positive whole number")
            args += [f"--m12={station}", f"--groups={groups}"]
        else:
            value = cfg["Input"]
            if not value or value == "-":
                raise ValueError("Enter a path, URL or named pipe")
            if value.startswith(("http://", "https://")):
                if source == "Audio pipe":
                    raise ValueError("Enter the path of a named pipe")
            else:
                path = self.picker.path(value)
                if source == "Audio pipe" and not path.is_fifo():
                    raise ValueError("Enter the path of a named pipe")
                if not path.exists():
                    raise ValueError("Input path does not exist")
                self.pending_playlist = path.is_dir()
                value = str(path)
            if self.pending_playlist:
                args.append("--playlist-controls")
            args += ["--", value]
            self.input_label = value
        mode = self.effective_mode(frequency)
        self.frequency = f"{mhz(frequency)} MHz"
        self.mode = mode.upper()
        self.gain = f"{select_gain(mode, None if gain.lower() == 'auto' else int(gain))} dB"
        self.volume = f"{volume:g}×"
        return args

    def setting_key(self, key):
        if key in (flipctl.Key.OK, flipctl.Key.EDIT, flipctl.Key.LEFT, flipctl.Key.RIGHT):
            self.edit(self.fields()[self.settings.selected], key)
        elif key is flipctl.Key.VIEW:
            self.refresh_device()
            self.device_view.show()
        elif key is flipctl.Key.RUN:
            try:
                self.pending_command = self.command()
                self.notice = ""
                self.summary = [
                    flipctl.pair("Source", str(self.values["Source"])),
                    flipctl.pair("Mode", self.mode),
                    flipctl.pair("TX gain", self.gain),
                    flipctl.pair("RF amp", "On" if self.values["RF amp"] else "Off"),
                    flipctl.pair("Audio level", self.volume),
                    flipctl.pair("Loop", "On" if self.values["Loop"] else "Off"),
                ]
                source = self.values["Source"]
                if source in (SOURCES[0], "Audio pipe"):
                    self.summary.insert(1, flipctl.text(input_preview(self.input_label)))
                    if self.pending_playlist:
                        self.summary.insert(2, flipctl.pair("Playlist", "Shuffle"))
                elif source == "M12":
                    self.summary.append(flipctl.pair("Station / groups",
                        f"{self.values['Station ID']} / {self.values['Groups']}"))
            except (OSError, ValueError) as error:
                self.pending_command = None
                self.notice = str(error)
            self.review.offset = 0
            self.review.show()
        else:
            return False
        return True

    def review_rows(self):
        if self.notice:
            return [flipctl.heading("Check settings"), *map(flipctl.text, flipctl.wrap(self.notice))]
        return [flipctl.heading("Confirm TX", self.frequency), flipctl.rule(), *self.summary]

    def review_key(self, key):
        if key in (flipctl.Key.BACK, flipctl.Key.ESCAPE):
            self.settings.show()
        elif key is flipctl.Key.EDIT and self.pending_command:
            self.outcome = ""
            self.elapsed = None
            self.transfer.offset = self.logs.offset = 0
            try:
                self.running = Transmission(self.pending_command, playlist=self.pending_playlist)
            except OSError as error:
                self.running = None
                self.outcome = str(error)
            self.transfer.show()
        else:
            return False
        return True

    def playlist_controls(self):
        return self.running and self.running.playlist and not self.outcome and not self.running.stopped_by_user

    def tx_buttons(self):
        if self.playlist_controls():
            return ["Stop", "Prev", "", "Next", "Log"]
        active = bool(self.running and not self.outcome)
        return ["Stop" if active else "Back", "Log", "", "", "Stop" if active else ""]

    def log_buttons(self):
        if self.running and not self.outcome:
            return ["Back", "Stop", "", "", ""] if self.running.playlist else ["Back", "", "", "", "Stop"]
        return ["Back", "", "", "", ""]

    def tx_rows(self):
        job = self.running
        if self.outcome:
            label = self.outcome
        elif job and job.stopped_by_user:
            label = "Stopping..."
        elif job and job.first_samples is not None:
            frame = int((time.monotonic() - job.first_samples) / FRAME_SECONDS)
            animated, spinner = tx_frame(frame)
            label = f"{animated} {spinner}"
        else:
            label = job.startup if job else "Preparing audio..."
        elapsed = self.elapsed
        if elapsed is None and job and job.first_samples is not None:
            elapsed = int(time.monotonic() - job.first_samples)
        elapsed_rows = []
        if elapsed is not None:
            minutes, seconds = divmod(elapsed, 60)
            elapsed_rows = [flipctl.pair("On air", f"{minutes:02d}:{seconds:02d}")]
        source_rows = [flipctl.pair("Source", self.values["Source"])]
        if job and job.track:
            position, name = job.track
            source_rows = [flipctl.pair("Track", position), flipctl.text(input_preview(name))]
        return [
            flipctl.heading("HackRF TX", self.frequency), flipctl.rule(),
            *map(flipctl.text, flipctl.wrap(label)),
            *source_rows,
            flipctl.pair("Mode", f"{self.mode}  /  {self.gain}"),
            *elapsed_rows,
            *([flipctl.text("Select HackRF TX audio output", dim=True)]
              if self.values["Source"] == "Virtual output" and not self.outcome else []),
        ]

    def transfer_key(self, key):
        if self.playlist_controls():
            if key in (flipctl.Key.VIEW, flipctl.Key.LEFT, flipctl.Key.EDIT, flipctl.Key.RIGHT):
                self.running.change_track(previous=key in (flipctl.Key.VIEW, flipctl.Key.LEFT))
                return True
            if key is flipctl.Key.RUN:
                self.logs.show()
                return True
        if key is flipctl.Key.VIEW:
            self.logs.show()
        elif key in (flipctl.Key.BACK, flipctl.Key.ESCAPE, flipctl.Key.RUN):
            if self.running and not self.outcome:
                self.running.stop()
            elif key in (flipctl.Key.BACK, flipctl.Key.ESCAPE):
                self.settings.show()
        else:
            return False
        return True

    def log_lines(self):
        return [part for line in self.running.log for part in flipctl.wrap(line)] if self.running else []

    def log_key(self, key):
        if key in (flipctl.Key.BACK, flipctl.Key.ESCAPE):
            self.transfer.show()
        elif self.running and not self.outcome and key is (
            flipctl.Key.VIEW if self.running.playlist else flipctl.Key.RUN
        ):
            self.running.stop()
            self.transfer.show()
        else:
            return False
        return True

    def refresh_device(self):
        self.device = ["Checking for HackRF..."]

        def probe():
            try:
                result = subprocess.run(["hackrf_info"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, errors="replace", timeout=4)
                lines = result.stdout.strip().splitlines() or ["No device information"]
            except (OSError, subprocess.TimeoutExpired) as error:
                lines = [str(error)]
            self.events.put(("device", lines, None))

        threading.Thread(target=probe, daemon=True).start()

    def device_rows(self):
        return [flipctl.heading("HackRF device"), flipctl.rule(),
                *(flipctl.text(part) for line in self.device for part in flipctl.wrap(line))]

    def device_key(self, key):
        if key in (flipctl.Key.BACK, flipctl.Key.ESCAPE):
            self.settings.show()
        elif key is flipctl.Key.RUN:
            self.refresh_device()
        else:
            return False
        return True

    async def tick(self):
        while True:
            self.picker.poll()
            while not self.events.empty():
                kind, value, text = self.events.get_nowait()
                if kind == "device":
                    self.device = value
                    self.device_view.redraw()
                else:
                    self.editing = False
                    if kind == "edited" and text is not None:
                        text = text.strip()
                        if value == "Frequency":
                            try:
                                text = f"{checked_frequency(text)}Hz"
                            except ValueError as error:
                                self.pending_command = None
                                self.notice = str(error)
                                self.review.offset = 0
                                self.review.show()
                                continue
                        if value == "Input" and text and not text.startswith(("http://", "https://")):
                            path = self.picker.path(text)
                            if (path.is_fifo() if self.picker.pipe_only else path.exists()):
                                self.picker.pick(path)
                                continue
                        self.values[value] = text
                        self.settings.show()
                    elif kind == "edit_error":
                        self.pending_command = None
                        self.notice = value
                        self.review.show()
            job = self.running
            if job and not self.outcome:
                if job.update():
                    if job.first_samples is not None:
                        self.elapsed = int(time.monotonic() - job.first_samples)
                    if job.process.returncode == 0:
                        self.outcome = "Stopped" if job.stopped_by_user else "Finished"
                    else:
                        error = next((line[4:] for line in reversed(job.log) if line.startswith("[!] ")), "")
                        fallback = ("TX stop not confirmed. Disconnect HackRF." if job.stopped_by_user else
                                    f"Failed (exit {job.process.returncode}). See Log.")
                        self.outcome = error or fallback
                self.transfer.redraw()
                self.logs.redraw()
            await asyncio.sleep(0.06)


def main():
    app = App()

    def leave(sig, frame):
        raise SystemExit(128 + sig)

    signal.signal(signal.SIGTERM, leave)
    try:
        app.settings.run(app.tick())
    finally:
        if app.running:
            app.running.close()


if __name__ == "__main__":
    main()
