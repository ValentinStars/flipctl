#!/usr/bin/env python3
# /// flipctl
# name = "Snake"
# status = true
# audio = true
# runtime = ""
# ///
"""Classic Retro Snake for FlipCTL.

Control the snake with D-pad or Arrow keys.
Eat food, grow longer, and avoid collisions.
"""

import asyncio
import collections
import random
import flipctl
import slint

WIDTH_CELLS = 24
HEIGHT_CELLS = 13
CELL_SIZE = 9
BOARD_X = 18
BOARD_Y = 18

PAGE = """
import { Shell } from "@app/shell.slint";
import { FlipperTheme } from "@theme";

export struct Block {
    x: int,
    y: int,
}

export component App inherits Shell {
    in property <int> score: 0;
    in property <int> high_score: 0;
    in property <string> state_msg: "PRESS OK TO START";
    in property <bool> game_over: false;
    in property <[Block]> snake_blocks;
    in property <int> food_x: 10;
    in property <int> food_y: 6;
    in property <[string]> buttons: ["Exit", "", "Pause", "", "Restart"];

    callback keyed(string, bool);
    callback tap_left();
    callback tap_right();
    callback tap_up();
    callback tap_down();
    callback tap_center();

    key(text, down) => { root.keyed(text, down); }

    Rectangle {
        width: 100%;
        height: 100%;
        background: FlipperTheme.white;

        // Top Header
        Rectangle {
            x: 0; y: 0; width: 100%; height: 16px;
            background: FlipperTheme.black;

            Text {
                x: 6px; y: 2px;
                text: "SNAKE";
                color: FlipperTheme.white;
                font-size: 16px;
                font-family: "Busy9px";
            }

            Text {
                x: 70px; y: 2px;
                text: "SCORE: " + root.score;
                color: FlipperTheme.white;
                font-size: 16px;
                font-family: "Busy9px";
            }

            Text {
                x: 160px; y: 2px;
                text: "HIGH: " + root.high_score;
                color: FlipperTheme.white;
                font-size: 16px;
                font-family: "Busy9px";
            }
        }

        // Playing field border
        Rectangle {
            x: 17px; y: 17px;
            width: 218px; height: 119px;
            border-width: 1px;
            border-color: FlipperTheme.black;
            background: FlipperTheme.white;

            // Food item
            Rectangle {
                x: root.food_x * 9px + 1px;
                y: root.food_y * 9px + 1px;
                width: 7px; height: 7px;
                background: FlipperTheme.black;
            }

            // Snake segments
            for b in root.snake_blocks: Rectangle {
                x: b.x * 9px + 1px;
                y: b.y * 9px + 1px;
                width: 7px; height: 7px;
                background: FlipperTheme.black;
            }

            // Center overlay for game over / pause
            if root.state_msg != "": Rectangle {
                x: 20px; y: 40px;
                width: 178px; height: 36px;
                background: FlipperTheme.white;
                border-width: 1px;
                border-color: FlipperTheme.black;

                Text {
                    x: 10px; y: 10px;
                    text: root.state_msg;
                    color: FlipperTheme.black;
                    font-size: 16px;
                    font-family: "Busy9px";
                }
            }
        }

        // Touch quadrants for steering
        TouchArea {
            x: 0; y: 16px; width: 60px; height: root.height - 16px;
            clicked => { root.tap_left(); }
        }
        TouchArea {
            x: root.width - 60px; y: 16px; width: 60px; height: root.height - 16px;
            clicked => { root.tap_right(); }
        }
        TouchArea {
            x: 60px; y: 16px; width: root.width - 120px; height: 50px;
            clicked => { root.tap_up(); }
        }
        TouchArea {
            x: 60px; y: root.height - 50px; width: root.width - 120px; height: 50px;
            clicked => { root.tap_down(); }
        }
        TouchArea {
            x: 60px; y: 66px; width: root.width - 120px; height: root.height - 116px;
            clicked => { root.tap_center(); }
        }
    }
}
"""


class SnakeGame:
    def __init__(self):
        self.snake = collections.deque([(10, 6), (9, 6), (8, 6)])
        self.dir = (1, 0)
        self.next_dir = (1, 0)
        self.food = (16, 6)
        self.score = 0
        self.high_score = 0
        self.running = False
        self.game_over = False
        self.state_msg = "PRESS OK TO START"
        self.woken = asyncio.Event()

    def reset(self):
        self.snake = collections.deque([(10, 6), (9, 6), (8, 6)])
        self.dir = (1, 0)
        self.next_dir = (1, 0)
        self.food = self._spawn_food()
        self.score = 0
        self.running = True
        self.game_over = False
        self.state_msg = ""
        self.woken.set()

    def _spawn_food(self):
        for _ in range(100):
            fx = random.randint(0, WIDTH_CELLS - 1)
            fy = random.randint(0, HEIGHT_CELLS - 1)
            if (fx, fy) not in self.snake:
                return (fx, fy)
        return (0, 0)

    def set_dir(self, dx, dy):
        if (dx, dy) == (-self.dir[0], -self.dir[1]):
            return  # No reversing into self
        self.next_dir = (dx, dy)
        if not self.running and not self.game_over:
            self.running = True
            self.state_msg = ""
        self.woken.set()

    def toggle_pause(self):
        if self.game_over:
            self.reset()
            return
        self.running = not self.running
        self.state_msg = "" if self.running else "PAUSED"
        self.woken.set()

    def step(self):
        if not self.running:
            return

        self.dir = self.next_dir
        hx, hy = self.snake[0]
        nx = hx + self.dir[0]
        ny = hy + self.dir[1]

        # Wall collision
        if nx < 0 or nx >= WIDTH_CELLS or ny < 0 or ny >= HEIGHT_CELLS:
            self.game_over = True
            self.running = False
            self.state_msg = "GAME OVER! OK TO RESTART"
            return

        # Self collision
        if (nx, ny) in self.snake:
            self.game_over = True
            self.running = False
            self.state_msg = "GAME OVER! OK TO RESTART"
            return

        self.snake.appendleft((nx, ny))

        if (nx, ny) == self.food:
            self.score += 10
            if self.score > self.high_score:
                self.high_score = self.score
            self.food = self._spawn_food()
        else:
            self.snake.pop()


def main():
    ui = flipctl.load(PAGE, "snake.slint")
    game = SnakeGame()

    def draw():
        ui.score = game.score
        ui.high_score = game.high_score
        ui.state_msg = game.state_msg
        ui.game_over = game.game_over
        ui.food_x = game.food[0]
        ui.food_y = game.food[1]
        ui.snake_blocks = [{"x": x, "y": y} for (x, y) in game.snake]

    @ui.tap_left
    def _(): game.set_dir(-1, 0); draw()

    @ui.tap_right
    def _(): game.set_dir(1, 0); draw()

    @ui.tap_up
    def _(): game.set_dir(0, -1); draw()

    @ui.tap_down
    def _(): game.set_dir(0, 1); draw()

    @ui.tap_center
    def _():
        if game.game_over or not game.running:
            game.reset()
        else:
            game.toggle_pause()
        draw()

    @flipctl.on_key(ui)
    def _(key, down):
        if not down:
            return
        if key in (flipctl.Key.BACK, flipctl.Key.ESCAPE):
            slint.quit_event_loop()
        elif key == flipctl.Key.UP:
            game.set_dir(0, -1)
        elif key == flipctl.Key.DOWN:
            game.set_dir(0, 1)
        elif key == flipctl.Key.LEFT:
            game.set_dir(-1, 0)
        elif key == flipctl.Key.RIGHT:
            game.set_dir(1, 0)
        elif key in (flipctl.Key.OK, flipctl.Key.RUN, flipctl.Key.POWER):
            if game.game_over or not game.running:
                game.reset()
            else:
                game.toggle_pause()
        draw()

    draw()
    flipctl.run(ui, game_loop(game, draw))


async def game_loop(game, draw):
    while True:
        if game.running:
            game.step()
            draw()
            delay = max(0.08, 0.18 - (game.score // 50) * 0.02)
        else:
            delay = 0.2
        try:
            await asyncio.wait_for(game.woken.wait(), delay)
        except asyncio.TimeoutError:
            pass
        game.woken.clear()


if __name__ == "__main__":
    main()
