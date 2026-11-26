#!/usr/bin/env python3
# /// flipctl
# name = "Breakout"
# status = false
# ///
"""Retro Breakout (Brick Breaker) for FlipCTL.

Bounce the ball to smash all bricks!
Controls:
  Hold Left / Right or X / V: Move paddle
  OK / B / Up: Serve
  Z / Back: Exit
  Touch / Mouse: Drag the paddle or tap to aim and serve
"""

import asyncio
import time
import flipctl
import slint

FIELD_W = 256
FIELD_H = 144
PADDLE_W = 38
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
STEP = 1 / 60
PADDLE_SPEED = 200
LEFT_KEYS = {flipctl.Key.LEFT, flipctl.Key.VIEW}
RIGHT_KEYS = {flipctl.Key.RIGHT, flipctl.Key.EDIT}
SERVE_KEYS = {flipctl.Key.OK, flipctl.Key.UP, flipctl.Key.RUN}

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
    in property <string> msg: "OK / B TO SERVE";
    in property <[Brick]> bricks;

    callback keyed(string, bool);
    callback aim(float);
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

        TouchArea {
            x: 0; y: 19px;
            width: parent.width;
            height: parent.height - self.y;
            pointer-event(event) => {
                if event.kind == PointerEventKind.down && event.button == PointerEventButton.left {
                    root.aim(self.mouse-x / 1px);
                }
            }
            moved => { root.aim(self.mouse-x / 1px); }
            clicked => { root.launch(); }
        }
    }
}
"""


class BreakoutGame:
    def __init__(self):
        self.held = set()
        self.reset_all()

    def reset_all(self):
        self.score = 0
        self.lives = 3
        self.paddle_x = (FIELD_W - PADDLE_W) // 2
        self.game_over = False
        self.win = False
        self.serve_ready("OK / B TO SERVE")
        self.init_bricks()

    def serve_ready(self, message):
        self.in_play = False
        self.ball_x = self.paddle_x + (PADDLE_W - BALL_SIZE) / 2
        self.ball_y = PADDLE_Y - BALL_SIZE - 1
        self.vx = 100 * STEP
        self.vy = -125 * STEP
        self.msg = message

    def init_bricks(self):
        self.bricks = []
        self.remaining_bricks = BRICK_ROWS * BRICK_COLS
        self.bricks_changed = True
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

    def aim(self, x):
        self.move_paddle(x - PADDLE_W / 2 - self.paddle_x)

    def move_paddle(self, dx):
        self.paddle_x = max(0, min(FIELD_W - PADDLE_W, self.paddle_x + dx))
        if not self.in_play and not self.game_over and not self.win:
            self.ball_x = self.paddle_x + (PADDLE_W - BALL_SIZE) / 2

    def key(self, key, down):
        if not down:
            self.held.discard(key)
        elif key not in self.held:
            self.held.add(key)
            if key in SERVE_KEYS:
                self.launch()

    def launch(self):
        if self.game_over or self.win:
            self.reset_all()
        self.in_play = True
        self.msg = ""

    def update(self):
        if self.game_over or self.win:
            return
        direction = bool(self.held & RIGHT_KEYS) - bool(self.held & LEFT_KEYS)
        if direction:
            self.move_paddle(direction * PADDLE_SPEED * STEP)
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
                self.msg = "GAME OVER  -  OK / B TO RESTART"
            else:
                self.serve_ready("BALL LOST  -  OK / B TO SERVE")
            return

        # Paddle collision
        if (
            self.vy > 0
            and self.ball_y + BALL_SIZE <= PADDLE_Y <= next_y + BALL_SIZE
            and next_x + BALL_SIZE >= self.paddle_x
            and next_x <= self.paddle_x + PADDLE_W
        ):
            hit_offset = (next_x + BALL_SIZE / 2) - (self.paddle_x + PADDLE_W / 2)
            norm_offset = hit_offset / (PADDLE_W / 2)
            self.vx = max(-1, min(1, norm_offset)) * 165 * STEP
            self.vy = -abs(self.vy)
            next_y = PADDLE_Y - BALL_SIZE

        # Brick collisions
        ball_r = next_x + BALL_SIZE
        ball_b = next_y + BALL_SIZE
        for brick in self.bricks:
            if not brick["alive"]:
                continue
            bx, by, bw, bh = (brick[field] for field in ("x", "y", "w", "h"))
            if not (next_x < bx + bw and ball_r > bx and next_y < by + bh and ball_b > by):
                continue
            brick["alive"] = False
            self.bricks_changed = True
            self.remaining_bricks -= 1
            self.score += 10

            crossed_side = self.ball_x + BALL_SIZE <= bx or self.ball_x >= bx + bw
            crossed_face = self.ball_y + BALL_SIZE <= by or self.ball_y >= by + bh
            if crossed_side and not crossed_face:
                next_x = bx - BALL_SIZE if self.vx > 0 else bx + bw
                self.vx = -self.vx
            else:
                next_y = by - BALL_SIZE if self.vy > 0 else by + bh
                self.vy = -self.vy
            break

        if self.remaining_bricks == 0:
            self.in_play = False
            self.win = True
            self.msg = "VICTORY  -  OK / B TO RESTART"

        self.ball_x = next_x
        self.ball_y = next_y


def main():
    ui = flipctl.load(PAGE, "breakout.slint")
    game = BreakoutGame()

    last_frame = {}

    def sync_ui():
        frame = {
            "score": game.score,
            "lives": game.lives,
            "paddle_x": round(game.paddle_x),
            "ball_x": round(game.ball_x),
            "ball_y": round(game.ball_y),
            "msg": game.msg,
        }
        for name, value in frame.items():
            if last_frame.get(name) != value:
                setattr(ui, name, value)
        last_frame.update(frame)
        if game.bricks_changed:
            ui.bricks = [brick.copy() for brick in game.bricks]
            game.bricks_changed = False

    @ui.aim
    def _(x):
        game.aim(x)
        sync_ui()

    @ui.launch
    def _():
        game.launch()
        sync_ui()

    @flipctl.on_key(ui)
    def _(key, down):
        if down and key in (flipctl.Key.BACK, flipctl.Key.ESCAPE):
            slint.quit_event_loop()
            return
        game.key(key, down)

    async def loop():
        previous = time.monotonic()
        pending = 0.0
        while True:
            now = time.monotonic()
            pending += min(now - previous, 0.1)
            previous = now
            while pending >= STEP:
                game.update()
                pending -= STEP
            sync_ui()
            await asyncio.sleep(max(0, STEP - pending - (time.monotonic() - now)))

    sync_ui()
    try:
        flipctl.run(ui, loop())
    finally:
        ui.hide()


if __name__ == "__main__":
    main()
