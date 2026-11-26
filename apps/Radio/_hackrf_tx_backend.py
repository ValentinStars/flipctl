#!/usr/bin/env python3
"""
hackrf-tx: Universal Multi-Mode RF Transmitter for HackRF One.
Supports WFM, NFM, USB, LSB, AM, built-in M12 Numbers Station generator,
and Live Microphone broadcasting (Walkie-Talkie / FM station).
Runs natively on Linux PC and Android Kali NetHunter.
"""

import sys
import os
import time
import random
import select
import signal
import argparse
import subprocess
import threading
import tempfile
import shutil
import math
from decimal import Decimal, InvalidOperation
from pathlib import Path


FRAME_SECONDS = 0.12
MEDIA_EXTENSIONS = frozenset({
    ".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".opus", ".mp4", ".webm", ".mkv", ".mov",
})


def tx_frame(index):
    word = "transmitting"
    position = index % len(word)
    animated = word[:position] + word[position].upper() + word[position + 1:]
    return animated, "|/-\\"[index % 4]


class TransferStatus:
    """Keep the activity indicator separate from the audio processing loop."""
    def __init__(self, frequency):
        self.label = f"[*] Transmitting on {frequency / 1e6:g} MHz..."
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        if self.thread is not None:
            return
        if not sys.stdout.isatty():
            print(self.label, flush=True)
            self.thread = False
            return
        def animate():
            index = 0
            while not self.stop_event.is_set():
                animated, spinner = tx_frame(index)
                label = self.label.replace("Transmitting", animated)
                sys.stdout.write(f"\r{label} {spinner}")
                sys.stdout.flush()
                index += 1
                self.stop_event.wait(FRAME_SECONDS)
        self.thread = threading.Thread(target=animate, daemon=True)
        self.thread.start()

    def stop(self):
        if self.thread is None:
            return
        self.stop_event.set()
        if self.thread:
            self.thread.join()
            sys.stdout.write("\r\033[2K")
            sys.stdout.flush()
        self.thread = None

def parse_frequency(freq_str):
    value = str(freq_str).strip().lower()
    for suffix, scale in (("khz", 1000), ("mhz", 1_000_000), ("ghz", 1_000_000_000),
                          ("hz", 1), ("k", 1000), ("m", 1_000_000), ("g", 1_000_000_000)):
        if value.endswith(suffix):
            value = value[:-len(suffix)]
            break
    else:
        scale = None

    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid frequency") from exc
    if not number.is_finite() or (number and number.adjusted() > 308):
        raise ValueError("invalid frequency")
    if scale is None:
        scale = 1000 if 1000 <= number <= 30000 else 1_000_000 if number < 1000 else 1
    return int(number * scale)


def select_mode(frequency, mode=None, m12=False):
    if mode is not None:
        return mode.lower()
    if m12:
        return "usb"
    if 87e6 <= frequency <= 108e6:
        return "wfm"
    if 400e6 <= frequency <= 480e6 or 136e6 <= frequency <= 174e6:
        return "nfm"
    return "usb"


def select_gain(mode, gain=None):
    return gain if gain is not None else (22 if mode == "nfm" else 38)


def pulse_server():
    configured = os.environ.get("PULSE_SERVER")
    if configured:
        return configured

    user_runtime = f"/run/user/{os.getuid()}"
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or user_runtime)
    for socket_path in (runtime / "pulse/native", Path(user_runtime) / "pulse/native"):
        try:
            if socket_path.is_socket():
                return f"unix:{socket_path.resolve(strict=True)}"
        except (OSError, RuntimeError):
            continue
    return None


def generate_morse_audio(text, sample_rate=50000, tone_freq=750, wpm=18):
    import numpy as np

    MORSE_MAP = {
        '0': '-',
        '1': '.----', '2': '..---', '3': '...--', '4': '....-', '5': '.....',
        '6': '-....', '7': '--...', '8': '---..', '9': '----.',
        ' ': ' '
    }
    dot_dur = 1.2 / wpm
    dash_dur = 3 * dot_dur
    intra_char_space = dot_dur
    char_space = 3 * dot_dur
    word_space = 7 * dot_dur
    fade_samples = int(0.005 * sample_rate)

    def make_tone(duration):
        num_samples = int(duration * sample_rate)
        t = np.arange(num_samples) / sample_rate
        wave = np.sin(2 * np.pi * tone_freq * t, dtype=np.float32) * 0.8
        if len(wave) > 2 * fade_samples:
            ramp = np.linspace(0, 1, fade_samples, dtype=np.float32)
            wave[:fade_samples] *= ramp
            wave[-fade_samples:] *= ramp[::-1]
        return wave

    def make_silence(duration):
        num_samples = int(duration * sample_rate)
        return np.zeros(num_samples, dtype=np.float32)

    chunks = []
    for word in text.strip().split():
        for char in word:
            pattern = MORSE_MAP.get(char.upper(), '')
            for sym in pattern:
                if sym == '.':
                    chunks.append(make_tone(dot_dur))
                elif sym == '-':
                    chunks.append(make_tone(dash_dur))
                chunks.append(make_silence(intra_char_space))
            chunks.append(make_silence(char_space))
        chunks.append(make_silence(word_space))

    if chunks:
        return np.concatenate(chunks)
    return np.zeros(sample_rate, dtype=np.float32)

def generate_m12_message(station_id="542", num_groups=15):
    station_id = str(station_id).zfill(3)
    preamble = f"{station_id} {station_id} {station_id}   {station_id} {station_id} {station_id}"
    header = f"{station_id} 1 {num_groups}"
    groups = ["".join(str(random.randint(0, 9)) for _ in range(5)) for _ in range(num_groups)]
    body = "   ".join(groups)
    outro = "00000 00000"
    return f"{preamble}   {header}   {body}   {outro}"

class DSPModulator:
    def __init__(self, mode="wfm", audio_rate=200000, target_rate=2000000, volume=0.85):
        import numpy as np

        self.mode = mode
        self.audio_rate = audio_rate
        self.target_rate = target_rate
        self.interp = target_rate // audio_rate
        self.volume = volume
        self.phase = 0.0

        if mode == "wfm":
            self.k = 2 * np.pi * 75000 / audio_rate
        elif mode == "nfm":
            # 2.8 kHz max deviation for crisp voice without clipping in 12.5 kHz radio channels
            self.k = 2 * np.pi * 2800 / audio_rate

    def modulate(self, audio_chunk):
        import numpy as np

        n_samples = len(audio_chunk)
        if n_samples == 0:
            return b""

        audio = np.clip(audio_chunk * self.volume, -1.0, 1.0)

        if self.mode in ("wfm", "nfm"):
            phases = self.phase + np.cumsum(audio * self.k)
            self.phase = phases[-1] % (2 * np.pi)

            # Smooth phase interpolation to eliminate staircase ZOH spurs
            if self.interp > 1:
                x_old = np.arange(n_samples)
                x_new = np.linspace(0, n_samples - 1, n_samples * self.interp)
                phases_interp = np.interp(x_new, x_old, phases)
                i_interp = (np.cos(phases_interp) * 127).astype(np.int8)
                q_interp = (np.sin(phases_interp) * 127).astype(np.int8)
            else:
                i_interp = (np.cos(phases) * 127).astype(np.int8)
                q_interp = (np.sin(phases) * 127).astype(np.int8)

        elif self.mode == "am":
            carrier = np.clip((1.0 + audio) * 60, 0, 127).astype(np.int8)
            i_s = carrier
            q_s = np.zeros(n_samples, dtype=np.int8)
            if self.interp > 1:
                i_interp = np.repeat(i_s, self.interp)
                q_interp = np.repeat(q_s, self.interp)
            else:
                i_interp, q_interp = i_s, q_s

        elif self.mode in ("usb", "lsb"):
            fft_data = np.fft.fft(audio)
            h = np.zeros(n_samples)
            if n_samples % 2 == 0:
                h[0] = h[n_samples // 2] = 1
                h[1:n_samples // 2] = 2
            else:
                h[0] = 1
                h[1:(n_samples + 1) // 2] = 2
            analytic = np.fft.ifft(fft_data * h)
            i_s = np.clip(np.real(analytic) * 127, -127, 127).astype(np.int8)
            q_s = np.clip(np.imag(analytic) * 127, -127, 127).astype(np.int8)
            if self.mode == "lsb":
                q_s = -q_s
            if self.interp > 1:
                i_interp = np.repeat(i_s, self.interp)
                q_interp = np.repeat(q_s, self.interp)
            else:
                i_interp, q_interp = i_s, q_s

        else:
            raise ValueError(f"Unknown mode: {self.mode}")

        out = np.empty(2 * len(i_interp), dtype=np.int8)
        out[0::2] = i_interp
        out[1::2] = q_interp
        return out.tobytes()

def write_iq(stream, data):
    pending = memoryview(data)
    while pending:
        written = stream.write(pending)
        if not written:
            raise BrokenPipeError("HackRF stopped accepting data")
        pending = pending[written:]


def finish_transmitter(process):
    # EOF lets the tool finish its USB transfers and put the radio in IDLE.
    # The pipe is unbuffered so closing it never waits on a stalled USB transfer.
    process.stdin.close()
    try:
        process.wait(timeout=2.5)
    except subprocess.TimeoutExpired:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            return False
    return True


def stop_decoder(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=0.3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    process.stdout.close()


class PlaylistControls:
    def __init__(self, stream):
        self.fd = stream.fileno()

    def read(self, audio, size):
        """Wait for a complete PCM block or a seek request, whichever arrives first."""
        audio_fd = audio.fileno()
        data = bytearray()
        while len(data) < size:
            watched = [audio_fd] if self.fd is None else [audio_fd, self.fd]
            ready, _, _ = select.select(watched, [], [])
            if self.fd is not None and self.fd in ready:
                commands = os.read(self.fd, 4096)
                if not commands:
                    self.fd = None
                jump = commands.count(b"n") - commands.count(b"p")
                if jump:
                    return b"", jump
            if audio_fd in ready:
                block = os.read(audio_fd, size - len(data))
                if not block:
                    break
                data.extend(block)
        return data, 0


def main():
    parser = argparse.ArgumentParser(
        description="Universal RF transmitter for HackRF One (PC & NetHunter)."
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=None,
        help="Audio/video file, folder of music, URL, 'mic' for live microphone, or '-' for stdin",
    )
    parser.add_argument(
        "--mic",
        action="store_true",
        help="Capture live microphone input (turn HackRF into Walkie-Talkie / Live FM mic)",
    )
    parser.add_argument("--system-audio", action="store_true",
                        help="Capture the monitor of the current default audio output (PulseAudio/PipeWire)")
    parser.add_argument("--virtual-output", action="store_true",
                        help="Create a selectable HackRF TX virtual audio output for this session")
    parser.add_argument("--playlist-controls", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "-f", "--freq",
        default="99.4M",
        help="Frequency: e.g. 99.4M, 107.2M, 433.5M, 145.5M, 24940k (default: 99.4M)",
    )
    parser.add_argument(
        "-m", "--mode",
        choices=["wfm", "nfm", "usb", "lsb", "am"],
        default=None,
        help="Modulation mode: wfm (broadcast FM), nfm (walkie-talkie), usb, lsb, am",
    )
    parser.add_argument(
        "--m12",
        nargs="?",
        const="542",
        default=None,
        help="Generate M12 Morse number station message (e.g. --m12 739)",
    )
    parser.add_argument(
        "--groups",
        type=int,
        default=15,
        help="Number of 5-figure groups for M12 message (default: 15)",
    )
    parser.add_argument(
        "-g", "--gain",
        type=int,
        default=None,
        help="TX VGA gain in dB (0-47, default: 38 for WFM, 22 for NFM/walkie-talkie)",
    )
    parser.add_argument(
        "-a", "--amp",
        action="store_true",
        help="Enable HackRF internal RF amplifier (+14 dB)",
    )
    parser.add_argument(
        "-l", "--loop",
        action="store_true",
        help="Loop audio file / playlist indefinitely",
    )
    parser.add_argument(
        "-v", "--volume",
        type=float,
        default=0.85,
        help="Audio volume scale factor (default: 0.85)",
    )

    args = parser.parse_args()
    try:
        freq_hz = parse_frequency(args.freq)
        if not 1 <= freq_hz <= 7_250_000_000:
            raise ValueError("frequency out of range")
    except (ValueError, OverflowError):
        parser.error("invalid frequency (expected 1 Hz to 7250 MHz)")
    if args.gain is not None and not 0 <= args.gain <= 47:
        parser.error("TX gain must be between 0 and 47 dB")
    if not math.isfinite(args.volume) or args.volume < 0:
        parser.error("volume must be a finite non-negative number")
    if args.groups < 1:
        parser.error("groups must be positive")
    selected_sources = int(bool(args.mic or (args.input and args.input.lower() in ("mic", "microphone", "rec", "voice")))) + int(args.system_audio) + int(args.virtual_output) + int(args.m12 is not None)
    if selected_sources > 1 or ((args.system_audio or args.virtual_output or args.m12 is not None) and args.input):
        parser.error("choose only one input source")
    if args.playlist_controls and (selected_sources or not args.input or not os.path.isdir(args.input)):
        parser.error("playlist controls require a folder input")
    if (args.system_audio or args.virtual_output) and not shutil.which("pactl"):
        parser.error("system audio requires pactl and PulseAudio or PipeWire")
    if not shutil.which("hackrf_transfer"):
        parser.error("hackrf_transfer not found")
    if args.m12 is None and not shutil.which("ffmpeg"):
        parser.error("ffmpeg not found")

    mode = select_mode(freq_hz, args.mode, m12=args.m12 is not None)
    gain = select_gain(mode, args.gain)

    target_rate = 2000000
    if mode in ("wfm", "nfm", "am"):
        audio_rate = 200000
    else:
        audio_rate = 50000

    is_mic = args.mic or (args.input and args.input.lower() in ("mic", "microphone", "rec", "voice"))

    if is_mic or args.system_audio or args.virtual_output:
        # libpulse refuses a symlinked pulse directory in a hosted app's runtime.
        # An explicit server address also keeps pactl and FFmpeg on the same socket.
        server = pulse_server()
        if server:
            os.environ["PULSE_SERVER"] = server

    print("\n  hackrf-tx  /  transmitter")
    print("  ------------------------")
    print(f"  frequency  {freq_hz / 1e6:g} MHz")
    print(f"  mode       {mode.upper()}")
    print(f"  gain       {gain} dB    amp {'on' if args.amp else 'off'}")
    if is_mic:
        print("  source     microphone")
    elif args.system_audio:
        print("  source     default output monitor")
    elif args.virtual_output:
        print("  source     virtual output (HackRF TX)")
    print("  stop       Ctrl+C\n", flush=True)
    status = TransferStatus(freq_hz)

    def prepare_dsp():
        try:
            import numpy as np
        except ImportError as exc:
            raise RuntimeError(f"Python NumPy unavailable: {exc}") from exc
        return np, DSPModulator(mode=mode, audio_rate=audio_rate, target_rate=target_rate, volume=args.volume)

    ffmpeg_proc = None
    hackrf_proc = None
    hackrf_log = tempfile.TemporaryFile(mode="w+t")
    stopping = False
    creating_virtual_output = False
    cleaning_up = False
    virtual_module = None
    ffmpeg_log = tempfile.TemporaryFile(mode="w+t")

    def cleanup():
        nonlocal virtual_module
        if ffmpeg_proc:
            stop_decoder(ffmpeg_proc)
        if virtual_module is not None:
            try:
                subprocess.run(["pactl", "unload-module", virtual_module],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass
            virtual_module = None

    def handle_signal(sig, frame):
        nonlocal stopping
        stopping = True
        if not creating_virtual_output and not cleaning_up:
            raise KeyboardInterrupt

    def send_iq(data):
        nonlocal hackrf_proc
        if hackrf_proc is None:
            print("[*] Starting HackRF...", flush=True)
            hackrf_cmd = [
                "hackrf_transfer",
                "-t", "-",
                "-f", str(freq_hz),
                "-s", str(target_rate),
                "-x", str(gain),
                "-a", "1" if args.amp else "0"
            ]

            try:
                hackrf_proc = subprocess.Popen(
                    hackrf_cmd,
                    stdin=subprocess.PIPE,
                    bufsize=0,
                    stdout=subprocess.DEVNULL,
                    stderr=hackrf_log,
                )
            except OSError as exc:
                raise RuntimeError(f"Cannot start hackrf_transfer: {exc}") from exc
        write_iq(hackrf_proc.stdin, data)
        status.start()

    failed = False
    try:
        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        pulse_source = None
        if args.system_audio or args.virtual_output:
            try:
                if args.virtual_output:
                    sink_name = f"hackrf_tx_{os.getpid()}"
                    # Defer a stop until pactl has returned the ID needed to unload the sink.
                    creating_virtual_output = True
                    try:
                        virtual_module = subprocess.run(
                            ["pactl", "load-module", "module-null-sink", f"sink_name={sink_name}",
                             "sink_properties=device.description=HackRF_TX"],
                            capture_output=True, text=True, check=True, timeout=5).stdout.strip()
                        if not virtual_module.isascii() or not virtual_module.isdigit():
                            raise RuntimeError("pactl returned an invalid module ID")
                    finally:
                        creating_virtual_output = False
                    if stopping:
                        raise KeyboardInterrupt
                    pulse_source = sink_name + ".monitor"
                    print("[*] Select 'HackRF TX' as the audio output in system settings.", flush=True)
                else:
                    sink = subprocess.run(["pactl", "get-default-sink"], capture_output=True,
                                          text=True, check=True, timeout=5).stdout.strip()
                    if not sink:
                        raise RuntimeError("no default audio output")
                    pulse_source = sink + ".monitor"
                    print(f"[*] Monitor   : {sink}", flush=True)
            except subprocess.CalledProcessError as exc:
                details = (exc.stderr or "").strip()
                raise RuntimeError(f"Audio setup failed: {details or exc}") from exc
            except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                raise RuntimeError(f"Audio setup failed: {exc}") from exc

        # For live mic, use low-latency chunk (25 ms)
        chunk_samples = audio_rate // (40 if (is_mic or pulse_source) else 10)
        chunk_bytes = chunk_samples * 4   # 4 bytes per float32

        if args.m12 is not None:
            print("[*] Preparing audio...", flush=True)
            _, modulator = prepare_dsp()
            station_id = args.m12
            msg_text = generate_m12_message(station_id, args.groups)
            print(f"[*] Station   : M12")
            print(f"[*] Message   : {msg_text}")
            audio_data = generate_morse_audio(msg_text, sample_rate=audio_rate, tone_freq=750, wpm=18)
            print(f"[*] Duration  : {len(audio_data) / audio_rate:.1f} s")

            while True:
                for idx in range(0, len(audio_data), chunk_samples):
                    sub = audio_data[idx:idx + chunk_samples]
                    iq = modulator.modulate(sub)
                    send_iq(iq)
                if not args.loop:
                    break
        else:
            ffmpeg_cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
            tracks = []
            inp = None

            if pulse_source:
                ffmpeg_cmd += ["-f", "pulse", "-probesize", "32", "-fragment_size", "4096",
                               "-i", pulse_source]
            elif is_mic:
                # Live low-latency microphone capture
                ffmpeg_cmd += ["-fflags", "nobuffer", "-flags", "low_delay", "-probesize", "32", "-analyzeduration", "0"]
                # Detect PulseAudio / PipeWire or ALSA
                if os.environ.get("PULSE_SERVER"):
                    ffmpeg_cmd += ["-f", "pulse", "-fragment_size", "4096",
                                   "-i", os.environ.get("PULSE_SOURCE", "default")]
                else:
                    ffmpeg_cmd += ["-f", "alsa", "-i", "default"]
            else:
                inp = args.input if args.input else "-"
                if inp != "-" and not inp.startswith(("http://", "https://")):
                    inp = os.path.expanduser(os.path.expandvars(inp))
                    if not os.path.exists(inp):
                        raise RuntimeError(f"Input path does not exist: {inp}")

                if os.path.isdir(inp):
                    for root, _, files in os.walk(inp):
                        for f in files:
                            if os.path.splitext(f)[1].lower() in MEDIA_EXTENSIONS:
                                tracks.append(os.path.abspath(os.path.join(root, f)))
                    if not tracks:
                        raise RuntimeError(f"No audio or video files in {inp}")
                    random.shuffle(tracks)
                    print(f"[*] Playlist  : {inp} ({len(tracks)} tracks, shuffled)")

                else:
                    print(f"[*] Input     : {inp}")
                    if args.loop and inp != "-":
                        ffmpeg_cmd += ["-stream_loop", "-1"]
                    ffmpeg_cmd += ["-i", inp]

            # Audio conditioning filter:
            # NFM: Voice bandpass 300-3400 Hz + compressor/limiter for clean walkie-talkie audio
            # WFM: Broadcast quality
            if mode == "nfm":
                audio_filter = "highpass=f=300,lowpass=f=3400,acompressor=threshold=-12dB:ratio=3:attack=5:release=50,volume=1.3"
            else:
                audio_filter = "highpass=f=30,lowpass=f=16000"

            output_args = [
                "-af", audio_filter,
                "-vn",
                "-ar", str(audio_rate),
                "-ac", "1",
                "-f", "f32le",
                "pipe:1"
            ]

            def decode(input_args, stdin=subprocess.DEVNULL):
                return subprocess.Popen(
                    ffmpeg_cmd + input_args + output_args,
                    stdin=stdin, stdout=subprocess.PIPE, stderr=ffmpeg_log,
                )

            def stream_audio(process, controls=None):
                while True:
                    if controls is None:
                        raw_audio, jump = process.stdout.read(chunk_bytes), 0
                    else:
                        raw_audio, jump = controls.read(process.stdout, chunk_bytes)
                    if not raw_audio:
                        return jump
                    if len(raw_audio) % 4:
                        raise RuntimeError("incomplete audio sample from ffmpeg")
                    audio_chunk = np.frombuffer(raw_audio, dtype=np.float32)
                    send_iq(modulator.modulate(audio_chunk))

            print("[*] Opening audio input...", flush=True)
            if tracks:
                controls = PlaylistControls(sys.stdin) if args.playlist_controls else None
                index = 0
                np, modulator = prepare_dsp()
                while True:
                    track = tracks[index]
                    name = " ".join(os.path.basename(track).split())
                    print(f"[*] Track {index + 1}/{len(tracks)}: {name}", flush=True)
                    ffmpeg_proc = decode(["-i", track])
                    jump = stream_audio(ffmpeg_proc, controls)
                    if jump:
                        stop_decoder(ffmpeg_proc)
                    else:
                        try:
                            if ffmpeg_proc.wait(timeout=1) != 0:
                                break
                        except subprocess.TimeoutExpired as exc:
                            raise RuntimeError("Audio decoder did not exit") from exc
                        stop_decoder(ffmpeg_proc)
                        if not args.loop and index == len(tracks) - 1:
                            break
                    ffmpeg_proc = None
                    ffmpeg_log.seek(0)
                    ffmpeg_log.truncate()
                    index = (index + (jump or 1)) % len(tracks)
            else:
                stdin = sys.stdin if not is_mic and not pulse_source and inp == "-" else subprocess.DEVNULL
                ffmpeg_proc = decode([], stdin)
                # Start the decoder while NumPy and the modulator are loading.
                np, modulator = prepare_dsp()
                print("[*] Waiting for audio...", flush=True)
                stream_audio(ffmpeg_proc)

    except BrokenPipeError:
        # The device's exit status and log are collected below.
        pass
    except KeyboardInterrupt:
        stopping = True
    except (OSError, RuntimeError) as exc:
        print(f"[!] {exc}", file=sys.stderr)
        failed = True
    finally:
        cleaning_up = True
        status.stop()
        if stopping:
            print("[*] Stopping transmission...", flush=True)
        # A decoder's error can arrive just after stdout reaches EOF.
        if ffmpeg_proc and ffmpeg_proc.poll() is None and not stopping:
            try:
                ffmpeg_proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        audio_failed = ffmpeg_proc.poll() not in (None, 0) if ffmpeg_proc else False
        stop_unconfirmed = False
        if hackrf_proc:
            graceful = finish_transmitter(hackrf_proc)
            hackrf_log.seek(0)
            details = hackrf_log.read().strip()
            stopped = "hackrf_stop_tx() done" in details or "hackrf_close() done" in details
            reported_error = "hackrf_stop_tx() failed" in details or "hackrf_close() failed" in details
            device_failed = hackrf_proc.returncode != 0
            stop_unconfirmed = not stopped and (not graceful or reported_error or (stopping and device_failed))
            failed = failed or device_failed
            if stopped:
                print("[*] HackRF confirmed TX stop.")
            if details and (device_failed or stop_unconfirmed):
                print(f"[!] hackrf_transfer:\n{details}", file=sys.stderr)
        cleanup()
        if not stopping and not failed and not audio_failed and hackrf_proc is None:
            failed = True
            print("[!] Audio input ended without samples.", file=sys.stderr)
        if audio_failed and not stopping:
            failed = True
            ffmpeg_log.seek(0)
            details = ffmpeg_log.read().strip()
            print(f"[!] Audio input failed: {details or 'ffmpeg exited with an error'}", file=sys.stderr)
        if stop_unconfirmed:
            print("[!] TX stop not confirmed. Disconnect HackRF.", file=sys.stderr)
        hackrf_log.close()
        ffmpeg_log.close()
    if stop_unconfirmed or (failed and not stopping):
        sys.exit(1)
    print("[*] Done.")

if __name__ == "__main__":
    main()
