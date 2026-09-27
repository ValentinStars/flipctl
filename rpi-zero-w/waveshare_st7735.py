#!/usr/bin/env python3
"""
Waveshare 1.44inch LCD HAT Driver and FlipCTL Bridge for Raspberry Pi Zero W.
ST7735S SPI Display (128x128) + 8 Hardware Buttons/Joystick.

Zero-compilation: Pure Python using standard spidev, RPi.GPIO / gpiozero, Pillow.
Works both standalone (connecting to local FlipCTL daemon) and remotely (connecting
to FlipCTL running on a laptop/desktop over local network).
"""

import sys
import os
import time
import argparse
import threading
from urllib.request import Request, urlopen
from urllib.error import URLError

try:
    import spidev
except ImportError:
    spidev = None

try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None

# Hardware Pin Definitions for Waveshare 1.44inch LCD HAT
PIN_RST = 27     # Reset
PIN_DC = 25      # Data / Command
PIN_BL = 24      # Backlight (PWM or digital)

# Buttons and Joystick (Active LOW with internal pull-up)
KEY_1 = 21       # Button 1 (Right top)
KEY_2 = 20       # Button 2 (Right mid)
KEY_3 = 16       # Button 3 (Right bottom)
JOY_UP = 6       # Joystick Up
JOY_DOWN = 19    # Joystick Down
JOY_LEFT = 5     # Joystick Left
JOY_RIGHT = 26   # Joystick Right
JOY_PRESS = 13   # Joystick Center Press

# FlipCTL Key Mapping
BUTTON_MAP = {
    KEY_1: "enter",       # OK / Select
    KEY_2: "back",        # Back / Cancel
    KEY_3: "menu",        # Menu / App Switcher
    JOY_UP: "up",         # Navigate Up
    JOY_DOWN: "down",     # Navigate Down
    JOY_LEFT: "left",     # Navigate Left
    JOY_RIGHT: "right",   # Navigate Right
    JOY_PRESS: "enter",   # Joystick Press = OK
}

# ST7735 Commands
SWRESET = 0x01
SLPOUT  = 0x11
FRMCTR1 = 0xB1
FRMCTR2 = 0xB2
FRMCTR3 = 0xB3
INVCTR  = 0xB4
PWCTR1  = 0xC0
PWCTR2  = 0xC1
PWCTR3  = 0xC2
PWCTR4  = 0xC3
PWCTR5  = 0xC4
VMCTR1  = 0xC5
INVOFF  = 0x20
INVON   = 0x21
MADCTL  = 0x36
COLMOD  = 0x3A
CASET   = 0x2A
RASET   = 0x2B
RAMWR   = 0x2C
GMCTRP1 = 0xE0
GMCTRN1 = 0xE1
NORON   = 0x13
DISPON  = 0x29

class ST7735:
    def __init__(self, width=128, height=128, col_offset=2, row_offset=3, spi_speed_hz=24000000):
        self.width = width
        self.height = height
        self.col_offset = col_offset
        self.row_offset = row_offset
        self.spi_speed = spi_speed_hz
        self.spi = None
        self.init_hardware()

    def init_hardware(self):
        if GPIO:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(PIN_RST, GPIO.OUT)
            GPIO.setup(PIN_DC, GPIO.OUT)
            GPIO.setup(PIN_BL, GPIO.OUT)
            GPIO.output(PIN_BL, GPIO.HIGH) # Turn on backlight

        if spidev:
            self.spi = spidev.SpiDev()
            self.spi.open(0, 0) # SPI bus 0, CE0
            self.spi.max_speed_hz = self.spi_speed
            self.spi.mode = 0b00

        self.reset()
        self.init_sequence()

    def reset(self):
        if not GPIO:
            return
        GPIO.output(PIN_RST, GPIO.HIGH)
        time.sleep(0.01)
        GPIO.output(PIN_RST, GPIO.LOW)
        time.sleep(0.01)
        GPIO.output(PIN_RST, GPIO.HIGH)
        time.sleep(0.05)

    def write_cmd(self, cmd):
        if not GPIO or not self.spi:
            return
        GPIO.output(PIN_DC, GPIO.LOW)
        self.spi.writebytes([cmd])

    def write_data(self, data):
        if not GPIO or not self.spi:
            return
        GPIO.output(PIN_DC, GPIO.HIGH)
        if isinstance(data, list):
            # Send in chunks of 4096 bytes for SPI stability
            for i in range(0, len(data), 4096):
                self.spi.writebytes(data[i:i+4096])
        else:
            self.spi.writebytes([data])

    def init_sequence(self):
        self.write_cmd(SWRESET)
        time.sleep(0.12)
        self.write_cmd(SLPOUT)
        time.sleep(0.12)

        self.write_cmd(FRMCTR1)
        self.write_data([0x01, 0x2C, 0x2D])
        self.write_cmd(FRMCTR2)
        self.write_data([0x01, 0x2C, 0x2D])
        self.write_cmd(FRMCTR3)
        self.write_data([0x01, 0x2C, 0x2D, 0x01, 0x2C, 0x2D])

        self.write_cmd(INVCTR)
        self.write_data([0x07])

        self.write_cmd(PWCTR1)
        self.write_data([0xA2, 0x02, 0x84])
        self.write_cmd(PWCTR2)
        self.write_data([0xC5])
        self.write_cmd(PWCTR3)
        self.write_data([0x0A, 0x00])
        self.write_cmd(PWCTR4)
        self.write_data([0x8A, 0x2A])
        self.write_cmd(PWCTR5)
        self.write_data([0x8A, 0xEE])

        self.write_cmd(VMCTR1)
        self.write_data([0x0E])

        self.write_cmd(INVOFF)

        # MADCTL: RGB mode, normal orientation
        self.write_cmd(MADCTL)
        self.write_data([0xC8]) # BGR order, column/row orientation

        # 16-bit color (RGB565)
        self.write_cmd(COLMOD)
        self.write_data([0x05])

        # Gamma sequence
        self.write_cmd(GMCTRP1)
        self.write_data([0x02, 0x1C, 0x07, 0x12, 0x37, 0x32, 0x29, 0x2D, 0x29, 0x25, 0x2B, 0x39, 0x00, 0x01, 0x03, 0x10])
        self.write_cmd(GMCTRN1)
        self.write_data([0x03, 0x1D, 0x07, 0x06, 0x2E, 0x2C, 0x29, 0x2D, 0x2E, 0x2E, 0x37, 0x3F, 0x00, 0x00, 0x02, 0x10])

        self.write_cmd(NORON)
        time.sleep(0.01)
        self.write_cmd(DISPON)
        time.sleep(0.1)

    def set_window(self, x0, y0, x1, y1):
        x0 += self.col_offset
        x1 += self.col_offset
        y0 += self.row_offset
        y1 += self.row_offset
        self.write_cmd(CASET)
        self.write_data([x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF])
        self.write_cmd(RASET)
        self.write_data([y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF])
        self.write_cmd(RAMWR)

    def display(self, image):
        """Send a PIL Image (128x128) to the ST7735 display."""
        if image.width != self.width or image.height != self.height:
            image = image.resize((self.width, self.height), Image.Resampling.BILINEAR)

        rgb = image.convert("RGB")
        pix = rgb.load()
        buf = []
        for y in range(self.height):
            for x in range(self.width):
                r, g, b = pix[x, y]
                # RGB565: 5 bits red, 6 bits green, 5 bits blue
                color = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                buf.append((color >> 8) & 0xFF)
                buf.append(color & 0xFF)

        self.set_window(0, 0, self.width - 1, self.height - 1)
        self.write_data(buf)


class InputManager:
    """Manages GPIO buttons and forwards events to FlipCTL or Linux uinput."""
    def __init__(self, flipctl_host="127.0.0.1", flipctl_port=8899):
        self.host = flipctl_host
        self.port = flipctl_port
        self.last_state = {}
        if GPIO:
            for pin in BUTTON_MAP:
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                self.last_state[pin] = 1

    def send_flipctl_event(self, key, state):
        url = f"http://{self.host}:{self.port}/input"
        data = f"key={key}&state={state}".encode("utf-8")
        req = Request(url, data=data, method="POST")
        try:
            with urlopen(req, timeout=0.1) as resp:
                pass
        except Exception:
            pass

    def poll(self):
        if not GPIO:
            return
        for pin, key in BUTTON_MAP.items():
            val = GPIO.input(pin)
            if val != self.last_state[pin]:
                self.last_state[pin] = val
                state = "down" if val == 0 else "up"
                threading.Thread(target=self.send_flipctl_event, args=(key, state), daemon=True).start()


def render_splash(st7735, text="FlipCTL RPi"):
    if not Image:
        return
    img = Image.new("RGB", (st7735.width, st7735.height), (255, 130, 0)) # Flipper Amber
    draw = ImageDraw.Draw(img)
    draw.rectangle([4, 4, 123, 123], outline=(0, 0, 0), width=2)
    draw.text((20, 30), "FLIPPER", fill=(0, 0, 0))
    draw.text((20, 50), "FlipCTL RPi", fill=(0, 0, 0))
    draw.text((20, 80), text, fill=(0, 0, 0))
    st7735.display(img)


def main():
    parser = argparse.ArgumentParser(description="FlipCTL Waveshare 1.44inch LCD HAT (ST7735) Bridge")
    parser.add_argument("--host", default="127.0.0.1", help="FlipCTL daemon IP / host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8899, help="FlipCTL port (default: 8899)")
    parser.add_argument("--fps", type=int, default=20, help="Target FPS (default: 20)")
    parser.add_argument("--col-offset", type=int, default=2, help="ST7735 Column offset (default: 2)")
    parser.add_argument("--row-offset", type=int, default=3, help="ST7735 Row offset (default: 3)")
    args = parser.parse_args()

    print(f"Starting FlipCTL Waveshare ST7735 bridge -> {args.host}:{args.port}")
    st7735 = ST7735(col_offset=args.col_offset, row_offset=args.row_offset)
    inputs = InputManager(flipctl_host=args.host, flipctl_port=args.port)

    render_splash(st7735, "Connecting...")

    screen_url = f"http://{args.host}:{args.port}/screen.png"
    delay = 1.0 / max(1, args.fps)

    while True:
        try:
            inputs.poll()
            req = Request(screen_url, headers={"User-Agent": "FlipCTL-ST7735"})
            with urlopen(req, timeout=0.8) as resp:
                import io
                img_data = resp.read()
                img = Image.open(io.BytesIO(img_data))
                st7735.display(img)
        except URLError:
            render_splash(st7735, "Waiting host...")
            time.sleep(1.0)
        except Exception as e:
            time.sleep(delay)
        time.sleep(delay)


if __name__ == "__main__":
    main()
