#!/usr/bin/env python3
# /// flipctl
# name = "Breakout"
# status = true
# audio = true
# runtime = ""
# ///
"""Retro Breakout (Brick Breaker) for FlipCTL.

Bounce the ball to smash all bricks!
Controls:
  Left / Right / A / D: Move Paddle
  Ok / Space / Up: Launch Ball / Unpause
  Escape / Back: Exit
  Touch / Mouse: Tap left or right side of screen, or drag paddle
"""

import asyncio
import flipctl
import slint

FIELD_W = 256
FIELD_H = 144
PADDLE_W = 38
PADDLE_H = 5
PADDLE_Y = 130
BALL_SIZE = 4
BRICK_ROWS = 4
BRICK_COLS = 8
BRICK_W = 28
BRICK_H = 7
BRICK_GAP_X = 3
BRICK_GAP_Y = 3
OFFSET_X = 4
OFFSET_Y = 22

PAGE = """
import { Shell } from "@app/shell.slint";
import { FlipperTheme } from "@theme";

export struct Brick {
    x: int,
    y: int,
    w: int,
    h: int,
    alive: bool,
}

export component App inherits Shell {
    in property <int> score: 0;
    in property <int> lives: 3;
    in property <int> paddle_x: 109;
    in property <int> ball_x: 126;
    in property <int> ball_y: 126;
    in property <string> msg: "PRESS OK / SPACE TO LAUNCH";
    in property <[Brick]> bricks;

    callback keyed(string, bool);
    callback tap_left();
    callback tap_right();
    callback launch();

    key(text, down) => { root.keyed(text, down); }

    Rectangle {
        width: 100%;
        height: 100%;
        background: FlipperTheme.white;

        // Top scoreboard
        Text {
            x: 6px; y: 4px;
            text: "SCORE: " + root.score;
            font-family: "HaxrCorp 4090";
            font-size: 16px;
            color: FlipperTheme.black;
        }

        Text {
            x: root.width - 70px; y: 4px;
            text: "BALLS: " + root.lives;
            font-family: "HaxrCorp 4090";
            font-size: 16px;
            color: FlipperTheme.black;
        }

        // Header separator
        Rectangle {
            x: 0; y: 18px;
            width: 100%; height: 1px;
            background: FlipperTheme.black;
        }

        // Bricks
        for b in root.bricks: Rectangle {
            x: b.x * 1px;
            y: b.y * 1px;
            width: b.w * 1px;
            height: b.h * 1px;
            background: b.alive ? FlipperTheme.black : transparent;
        }

        // Paddle
        Rectangle {
            x: root.paddle_x * 1px;
            y: 130px;
            width: 38px;
            height: 5px;
            background: FlipperTheme.black;
        }

        // Ball
        Rectangle {
            x: root.ball_x * 1px;
            y: root.ball_y * 1px;
            width: 4px;
            height: 4px;
            background: FlipperTheme.black;
        }

        // Overlay Message
        if root.msg != "": Rectangle {
            x: 18px;
            y: 70px;
            width: root.width - 36px;
            height: 22px;
            background: FlipperTheme.white;
            border-width: 1px;
            border-color: FlipperTheme.black;

            Text {
                x: 0; y: 2px;
                width: 100%;
                text: root.msg;
                font-family: "HaxrCorp 4090";
                font-size: 16px;
                color: FlipperTheme.black;
                horizontal-alignment: center;
            }
        }

        // Touch control left half
        TouchArea {
            x: 0; y: 20px;
            width: root.width / 2;
            height: root.height - 20px;
            clicked => {
                root.tap_left();
                root.launch();
            }
        }

        // Touch control right half
        TouchArea {
            x: root.width / 2; y: 20px;
            width: root.width / 2;
            height: root.height - 20px;
            clicked => {
                root.tap_right();
                root.launch();
            }
        }
    }
}
"""


class BreakoutGame:
    def __init__(self):
        self.reset_all()

    def reset_all(self):
        self.score = 0
        self.lives = 3
        self.paddle_x = (FIELD_W - PADDLE_W) // 2
        self.ball_x = self.paddle_x + PADDLE_W // 2 - BALL_SIZE // 2
        self.ball_y = PADDLE_Y - BALL_SIZE - 1
        self.vx = 2.0
        self.vy = -2.5
        self.in_play = False
        self.game_over = False
        self.win = False
        self.msg = "PRESS OK / SPACE TO LAUNCH"
        self.init_bricks()

    def init_bricks(self):
        self.bricks = []
        for r in range(BRICK_ROWS):
            for c in range(BRICK_COLS):
                bx = OFFSET_X + c * (BRICK_W + BRICK_GAP_X)
                by = OFFSET_Y + r * (BRICK_H + BRICK_GAP_Y)
                self.bricks.append({
                    "x": bx,
                    "y": by,
                    "w": BRICK_W,
                    "h": BRICK_H,
                    "alive": True,
                })

    def move_paddle(self, dx):
        self.paddle_x = max(0, min(FIELD_W - PADDLE_W, self.paddle_x + dx))
        if not self.in_play and not self.game_over and not self.win:
            self.ball_x = self.paddle_x + PADDLE_W // 2 - BALL_SIZE // 2

    def launch(self):
        if self.game_over or self.win:
            self.reset_all()
            return
        if not self.in_play:
            self.in_play = True
            self.msg = ""

    def update(self):
        if not self.in_play:
            return

        next_x = self.ball_x + self.vx
        next_y = self.ball_y + self.vy

        # Wall collisions
        if next_x <= 0:
            next_x = 0
            self.vx = abs(self.vx)
        elif next_x >= FIELD_W - BALL_SIZE:
            next_x = FIELD_W - BALL_SIZE
            self.vx = -abs(self.vx)

        if next_y <= 19:
            next_y = 19
            self.vy = abs(self.vy)
        elif next_y >= FIELD_H:
            # Ball lost
            self.lives -= 1
            if self.lives <= 0:
                self.in_play = False
                self.game_over = True
                self.msg = "GAME OVER! PRESS OK"
            else:
                self.in_play = False
                self.paddle_x = (FIELD_W - PADDLE_W) // 2
                self.ball_x = self.paddle_x + PADDLE_W // 2 - BALL_SIZE // 2
                self.ball_y = PADDLE_Y - BALL_SIZE - 1
                self.vx = 2.0
                self.vy = -2.5
                self.msg = "BALL LOST! PRESS OK"
            return

        # Paddle collision
        if (
            self.vy > 0
            and next_y + BALL_SIZE >= PADDLE_Y
            and next_y <= PADDLE_Y + PADDLE_H
            and next_x + BALL_SIZE >= self.paddle_x
            and next_x <= self.paddle_x + PADDLE_W
        ):
            hit_offset = (next_x + BALL_SIZE / 2) - (self.paddle_x + PADDLE_W / 2)
            norm_offset = hit_offset / (PADDLE_W / 2)
            self.vx = norm_offset * 3.2
            self.vy = -abs(self.vy)
            next_y = PADDLE_Y - BALL_SIZE

        # Brick collisions
        ball_r = next_x + BALL_SIZE
        ball_b = next_y + BALL_SIZE
        alive_count = 0

        for b in self.bricks:
            if not b["alive"]:
                continue
            alive_count += 1
            bx = b["x"]
            by = b["y"]
            bw = b["w"]
            bh = b["h"]

            # Overlap check
            if next_x < bx + bw and ball_r > bx and next_y < by + bh and ball_b > by:
                b["alive"] = False
                self.score += 10
                self.vy = -self.vy
                alive_count -= 1
                break

        if alive_count == 0:
            self.in_play = False
            self.win = True
            self.msg = "VICTORY! PRESS OK"

        self.ball_x = int(next_x)
        self.ball_y = int(next_y)


def main():
    ui = flipctl.load(PAGE, "breakout.slint")
    game = BreakoutGame()

    def sync_ui():
        ui.score = game.score
        ui.lives = game.lives
        ui.paddle_x = game.paddle_x
        ui.ball_x = game.ball_x
        ui.ball_y = game.ball_y
        ui.msg = game.msg
        ui.bricks = game.bricks

    @ui.tap_left
    def _():
        game.move_paddle(-14)
        sync_ui()

    @ui.tap_right
    def _():
        game.move_paddle(14)
        sync_ui()

    @ui.launch
    def _():
        game.launch()
        sync_ui()

    @flipctl.on_key(ui)
    def _(key, down):
        if not down:
            return
        if key in (flipctl.Key.BACK, flipctl.Key.ESCAPE):
            slint.quit_event_loop()
        elif key in (flipctl.Key.LEFT, flipctl.Key.VIEW):
            game.move_paddle(-16)
        elif key in (flipctl.Key.RIGHT, flipctl.Key.EDIT):
            game.move_paddle(16)
        elif key in (flipctl.Key.OK, flipctl.Key.POWER, flipctl.Key.UP, flipctl.Key.RUN):
            game.launch()
        sync_ui()

    async def loop():
        while True:
            game.update()
            sync_ui()
            await asyncio.sleep(0.02)  # ~50 FPS

    sync_ui()
    flipctl.run(ui, loop())


if __name__ == "__main__":
    main()
