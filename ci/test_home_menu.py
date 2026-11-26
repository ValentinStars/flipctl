"""Exercise the Home shortcuts through the running panel's input endpoint."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.request import Request, urlopen


def wait_for(log, process, pattern, count=1):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        output = log.read_text(errors="replace")
        matches = re.findall(pattern, output, re.MULTILINE)
        if len(matches) >= count:
            return matches[-1]
        if process.poll() is not None:
            break
        time.sleep(0.05)
    raise AssertionError(f"Missing {pattern!r} in panel log:\n{output}")


def press(url, key):
    for down in (True, False):
        request = Request(
            url + "input",
            data=json.dumps({"key": key, "down": down}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=5) as reply:
            reply.read()


def main(binary):
    with TemporaryDirectory(prefix="flipctl-home-") as scratch:
        root = Path(scratch)
        runtime = root / "run"
        runtime.mkdir(mode=0o700)
        log = root / "panel.log"
        env = {
            **os.environ,
            "HOME": str(root),
            "XDG_RUNTIME_DIR": str(runtime),
            "XDG_CACHE_HOME": str(root / "cache"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_DATA_HOME": str(root / "data"),
        }
        with log.open("wb") as output, subprocess.Popen(
            [binary, "--headless", "--remote", "127.0.0.1:0"],
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        ) as panel:
            try:
                url = wait_for(log, panel, r"^remote view +(http://127\.0\.0\.1:\d+/)")
                press(url, "view")
                wait_for(log, panel, r'^key view -> slot 1 "Menu"$')
                wait_for(log, panel, r"^screen +menu$")
                press(url, "back")
                wait_for(log, panel, r"^screen +idle$")
                press(url, "ok")
                wait_for(log, panel, r"^screen +menu$", count=2)
            finally:
                panel.terminate()
                try:
                    panel.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    panel.kill()


if __name__ == "__main__":
    main(sys.argv[1])
