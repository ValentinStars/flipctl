#!/usr/bin/env python3
# /// flipctl
# name = "Flappy"
# status = true
# audio = true
# runtime = ""
# ///
"""Flappy Flipper (Dolphin Jump) for FlipCTL.

Tap screen or press OK / Up / Z to flap and fly through the obstacles!
Controls:
  Ok / Up / Z: Flap / Jump
  Escape / Back: Exit
"""

import asyncio
import random
import flipctl
import slint

GRAVITY = 0.4
FLAP_VEL = -4.2
PIPE_SPEED = 2.0
SCREEN_W = 256
SCREEN_H = 144
GROUND_Y = 130
PIPE_WIDTH = 20
PIPE_GAP = 46

PAGE = """
import { Shell } from "@app/shell.slint";
import { FlipperTheme } from "@theme";

export struct Pipe {
    x: int,
    top_h: int,
    bot_y: int,
    bot_h: int,
}

export component App inherits Shell {
    in property <int> score: 0;
    in property <int> high_score: 0;
    in property <int> dolphin_y: 60;
    in property <string> msg: "TAP OR PRESS OK TO FLAP";
    in property <[Pipe]> pipes;

    callback keyed(string, bool);
    callback flap();

    key(text, down) => { root.keyed(text, down); }

    Rectangle {
        width: 100%;
        height: 100%;
        background: FlipperTheme.white;

        // Ground line
        Rectangle {
            x: 0; y: 130px;
            width: 100%; height: 14px;
            background: FlipperTheme.black;

            Text {
                x: 6px; y: 1px;
                text: "SCORE: " + root.score + "   BEST: " + root.high_score;
                color: FlipperTheme.white;
                font-size: 16px;
                font-family: "Busy9px";
            }
        }

        // Pipes
        for p in root.pipes: Rectangle {
            x: p.x * 1px;
            y: 0;
            width: 20px;
            height: 130px;
            background: transparent;

            // Top pipe
            Rectangle {
                x: 0; y: 0;
                width: 20px;
                height: p.top_h * 1px;
                background: FlipperTheme.black;
            }

            // Bottom pipe
            Rectangle {
                x: 0; y: p.bot_y * 1px;
                width: 20px;
                height: p.bot_h * 1px;
                background: FlipperTheme.black;
            }
        }

        // Flipper Dolphin (16x10 pixel block)
        Rectangle {
            x: 40px;
            y: root.dolphin_y * 1px;
            width: 16px;
            height: 10px;
            background: FlipperTheme.black;

            // Dolphin eye
            Rectangle {
                x: 12px; y: 2px;
                width: 2px; height: 2px;
                background: FlipperTheme.white;
            }
            // Dolphin fin
            Rectangle {
                x: 6px; y: 7px;
                width: 4px; height: 3px;
                background: FlipperTheme.black;
            }
        }

        // Overlay Message
        if root.msg != "": Rectangle {
            x: 20px; y: 45px;
            width: 216px; height: 36px;
            background: FlipperTheme.white;
            border-width: 1px;
            border-color: FlipperTheme.black;

            Text {
                x: 10px; y: 10px;
                text: root.msg;
                color: FlipperTheme.black;
                font-size: 16px;
                font-family: "Busy9px";
            }
        }

        // Full screen tap area
        TouchArea {
            x: 0; y: 0; width: root.width; height: root.height;
            clicked => { root.flap(); }
        }
    }
}
"""


class FlappyGame:
    def __init__(self):
        self.y = 55.0
        self.vel = 0.0
        self.score = 0
        self.high_score = 0
        self.running = False
        self.game_over = False
        self.msg = "PRESS OK TO FLAP"
        self.pipes = []
        self.woken = asyncio.Event()
        self.reset()

    def reset(self):
        self.y = 55.0
        self.vel = 0.0
        self.score = 0
        self.running = False
        self.game_over = False
        self.msg = "PRESS OK TO FLAP"
        self.pipes = [
            {"x": 200, "gap_y": 50, "passed": False},
            {"x": 330, "gap_y": 65, "passed": False},
        ]
        self.woken.set()

    def flap(self):
        if self.game_over:
            self.reset()
            return
        self.running = True
        self.msg = ""
        self.vel = FLAP_VEL
        self.woken.set()

    def step(self):
        if not self.running or self.game_over:
            return

        self.vel += GRAVITY
        self.y += self.vel

        # Floor / Ceiling collision
        if self.y >= GROUND_Y - 10:
            self.y = GROUND_Y - 10
            self.game_over = True
            self.running = False
            self.msg = "SPLASH! OK TO RESTART"
            return
        if self.y < 0:
            self.y = 0
            self.vel = 0

        # Move pipes
        for p in self.pipes:
            p["x"] -= PIPE_SPEED

            # Scoring
            if not p["passed"] and p["x"] + PIPE_WIDTH < 40:
                p["passed"] = True
                self.score += 1
                if self.score > self.high_score:
                    self.high_score = self.score

            # Collision detection (dolphin is at x: 40..56, y: y..y+10)
            if p["x"] < 56 and p["x"] + PIPE_WIDTH > 40:
                top_pipe_h = p["gap_y"]
                bot_pipe_y = p["gap_y"] + PIPE_GAP
                if self.y < top_pipe_h or (self.y + 10) > bot_pipe_y:
                    self.game_over = True
                    self.running = False
                    self.msg = "CRASH! OK TO RESTART"
                    return

        # Recycle pipes
        if self.pipes and self.pipes[0]["x"] + PIPE_WIDTH < 0:
            self.pipes.pop(0)
            next_x = self.pipes[-1]["x"] + 120
            gap_y = random.randint(20, GROUND_Y - PIPE_GAP - 20)
            self.pipes.append({"x": next_x, "gap_y": gap_y, "passed": False})

    def pipe_models(self):
        out = []
        for p in self.pipes:
            gap_y = p["gap_y"]
            top_h = gap_y
            bot_y = gap_y + PIPE_GAP
            bot_h = max(0, GROUND_Y - bot_y)
            out.append({
                "x": int(p["x"]),
                "top_h": top_h,
                "bot_y": bot_y,
                "bot_h": bot_h,
            })
        return out


def main():
    ui = flipctl.load(PAGE, "flappy.slint")
    game = FlappyGame()

    def draw():
        ui.score = game.score
        ui.high_score = game.high_score
        ui.dolphin_y = int(game.y)
        ui.msg = game.msg
        ui.pipes = game.pipe_models()

    @ui.flap
    def _():
        game.flap()
        draw()

    @flipctl.on_key(ui)
    def _(key, down):
        if not down:
            return
        if key in (flipctl.Key.BACK, flipctl.Key.ESCAPE):
            slint.quit_event_loop()
        elif key in (flipctl.Key.OK, flipctl.Key.UP, flipctl.Key.RUN, flipctl.Key.POWER):
            game.flap()
        draw()

    draw()
    flipctl.run(ui, game_loop(game, draw))


async def game_loop(game, draw):
    while True:
        if game.running and not game.game_over:
            game.step()
            draw()
            delay = 0.033  # ~30 FPS
        else:
            delay = 0.1
        try:
            await asyncio.wait_for(game.woken.wait(), delay)
        except asyncio.TimeoutError:
            pass
        game.woken.clear()


if __name__ == "__main__":
    main()
