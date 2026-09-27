#!/usr/bin/env python3
# /// flipctl
# name = "Tetris"
# status = true
# audio = true
# runtime = ""
# ///
"""Classic Retro Tetris for FlipCTL.

Controls:
  Left / Right: Move piece
  Up / Z: Rotate piece
  Down: Soft drop
  Ok / Enter: Hard drop
  Escape / Back: Exit
"""

import asyncio
import random
import flipctl
import slint

COLS = 10
ROWS = 16

# 7 standard Tetrominoes (rotations)
PIECES = {
    "I": [
        [(0, 1), (1, 1), (2, 1), (3, 1)],
        [(2, 0), (2, 1), (2, 2), (2, 3)],
    ],
    "O": [
        [(1, 0), (2, 0), (1, 1), (2, 1)],
    ],
    "T": [
        [(1, 0), (0, 1), (1, 1), (2, 1)],
        [(1, 0), (1, 1), (2, 1), (1, 2)],
        [(0, 1), (1, 1), (2, 1), (1, 2)],
        [(1, 0), (0, 1), (1, 1), (1, 2)],
    ],
    "S": [
        [(1, 0), (2, 0), (0, 1), (1, 1)],
        [(1, 0), (1, 1), (2, 1), (2, 2)],
    ],
    "Z": [
        [(0, 0), (1, 0), (1, 1), (2, 1)],
        [(2, 0), (1, 1), (2, 1), (1, 2)],
    ],
    "J": [
        [(0, 0), (0, 1), (1, 1), (2, 1)],
        [(1, 0), (2, 0), (1, 1), (1, 2)],
        [(0, 1), (1, 1), (2, 1), (2, 2)],
        [(1, 0), (1, 1), (0, 2), (1, 2)],
    ],
    "L": [
        [(2, 0), (0, 1), (1, 1), (2, 1)],
        [(1, 0), (1, 1), (1, 2), (2, 2)],
        [(0, 1), (1, 1), (2, 1), (0, 2)],
        [(0, 0), (1, 0), (1, 1), (1, 2)],
    ],
}

PAGE = """
import { Shell } from "@app/shell.slint";
import { FlipperTheme } from "@theme";

export struct Cell {
    x: int,
    y: int,
}

export component App inherits Shell {
    in property <int> score: 0;
    in property <int> lines: 0;
    in property <string> msg: "";
    in property <[Cell]> filled_cells;
    in property <[Cell]> next_cells;

    callback keyed(string, bool);
    callback move_left();
    callback move_right();
    callback rotate_piece();
    callback drop_piece();
    callback restart_game();

    key(text, down) => { root.keyed(text, down); }

    Rectangle {
        width: 100%;
        height: 100%;
        background: FlipperTheme.white;

        // Playing Well (10 cols x 16 rows, 7px per cell = 70px x 112px)
        Rectangle {
            x: 20px; y: 16px;
            width: 72px; height: 114px;
            border-width: 1px;
            border-color: FlipperTheme.black;
            background: FlipperTheme.white;

            for c in root.filled_cells: Rectangle {
                x: c.x * 7px + 1px;
                y: c.y * 7px + 1px;
                width: 6px; height: 6px;
                background: FlipperTheme.black;
            }
        }

        // Side Stats Panel
        Rectangle {
            x: 105px; y: 16px;
            width: 135px; height: 114px;

            Text {
                x: 0; y: 2px;
                text: "TETRIS";
                color: FlipperTheme.black;
                font-size: 16px;
                font-family: "Busy9px";
            }

            Text {
                x: 0; y: 22px;
                text: "SCORE: " + root.score;
                color: FlipperTheme.black;
                font-size: 16px;
                font-family: "Busy9px";
            }

            Text {
                x: 0; y: 40px;
                text: "LINES: " + root.lines;
                color: FlipperTheme.black;
                font-size: 16px;
                font-family: "Busy9px";
            }

            Text {
                x: 0; y: 60px;
                text: "NEXT:";
                color: FlipperTheme.black;
                font-size: 16px;
                font-family: "Busy9px";
            }

            // Next Piece Box
            Rectangle {
                x: 48px; y: 62px;
                width: 32px; height: 32px;
                border-width: 1px;
                border-color: FlipperTheme.divider;

                for nc in root.next_cells: Rectangle {
                    x: nc.x * 7px + 3px;
                    y: nc.y * 7px + 3px;
                    width: 6px; height: 6px;
                    background: FlipperTheme.black;
                }
            }

            if root.msg != "": Text {
                x: 0; y: 96px;
                text: root.msg;
                color: FlipperTheme.black;
                font-size: 16px;
                font-family: "Busy9px";
            }
        }

        // Touch zones
        TouchArea {
            x: 0; y: 0; width: 60px; height: root.height;
            clicked => { root.move_left(); }
        }
        TouchArea {
            x: 60px; y: 0; width: 60px; height: root.height;
            clicked => { root.move_right(); }
        }
        TouchArea {
            x: 120px; y: 0; width: 60px; height: root.height;
            clicked => { root.rotate_piece(); }
        }
        TouchArea {
            x: 180px; y: 0; width: 76px; height: root.height;
            clicked => { root.drop_piece(); }
        }
    }
}
"""


class TetrisGame:
    def __init__(self):
        self.grid = [[0 for _ in range(COLS)] for _ in range(ROWS)]
        self.score = 0
        self.lines = 0
        self.cur_shape = "T"
        self.cur_rot = 0
        self.cur_x = 3
        self.cur_y = 0
        self.next_shape = "I"
        self.game_over = False
        self.paused = False
        self.msg = ""
        self.woken = asyncio.Event()
        self.reset()

    def reset(self):
        self.grid = [[0 for _ in range(COLS)] for _ in range(ROWS)]
        self.score = 0
        self.lines = 0
        self.game_over = False
        self.paused = False
        self.msg = ""
        self.next_shape = random.choice(list(PIECES.keys()))
        self._spawn()
        self.woken.set()

    def _spawn(self):
        self.cur_shape = self.next_shape
        self.next_shape = random.choice(list(PIECES.keys()))
        self.cur_rot = 0
        self.cur_x = 3
        self.cur_y = 0
        if not self._valid(self.cur_x, self.cur_y, self.cur_rot):
            self.game_over = True
            self.msg = "GAME OVER!"

    def _valid(self, px, py, rot):
        shape_blocks = PIECES[self.cur_shape][rot % len(PIECES[self.cur_shape])]
        for bx, by in shape_blocks:
            gx = px + bx
            gy = py + by
            if gx < 0 or gx >= COLS or gy >= ROWS:
                return False
            if gy >= 0 and self.grid[gy][gx]:
                return False
        return True

    def move(self, dx):
        if self.game_over or self.paused:
            return
        if self._valid(self.cur_x + dx, self.cur_y, self.cur_rot):
            self.cur_x += dx
            self.woken.set()

    def rotate(self):
        if self.game_over or self.paused:
            return
        new_rot = (self.cur_rot + 1) % len(PIECES[self.cur_shape])
        if self._valid(self.cur_x, self.cur_y, new_rot):
            self.cur_rot = new_rot
            self.woken.set()

    def drop(self):
        if self.game_over or self.paused:
            return
        while self._valid(self.cur_x, self.cur_y + 1, self.cur_rot):
            self.cur_y += 1
        self._lock()
        self.woken.set()

    def step(self):
        if self.game_over or self.paused:
            return
        if self._valid(self.cur_x, self.cur_y + 1, self.cur_rot):
            self.cur_y += 1
        else:
            self._lock()

    def _lock(self):
        shape_blocks = PIECES[self.cur_shape][self.cur_rot % len(PIECES[self.cur_shape])]
        for bx, by in shape_blocks:
            gx = self.cur_x + bx
            gy = self.cur_y + by
            if 0 <= gy < ROWS and 0 <= gx < COLS:
                self.grid[gy][gx] = 1

        # Clear lines
        cleared = 0
        new_grid = [row for row in self.grid if not all(row)]
        cleared = ROWS - len(new_grid)
        while len(new_grid) < ROWS:
            new_grid.insert(0, [0 for _ in range(COLS)])
        self.grid = new_grid

        if cleared > 0:
            self.lines += cleared
            self.score += [0, 40, 100, 300, 1200][min(4, cleared)]

        self._spawn()

    def cells(self):
        out = []
        for r in range(ROWS):
            for c in range(COLS):
                if self.grid[r][c]:
                    out.append({"x": c, "y": r})
        if not self.game_over:
            shape_blocks = PIECES[self.cur_shape][self.cur_rot % len(PIECES[self.cur_shape])]
            for bx, by in shape_blocks:
                gx = self.cur_x + bx
                gy = self.cur_y + by
                if 0 <= gy < ROWS and 0 <= gx < COLS:
                    out.append({"x": gx, "y": gy})
        return out

    def next_preview(self):
        out = []
        blocks = PIECES[self.next_shape][0]
        for bx, by in blocks:
            out.append({"x": bx, "y": by})
        return out


def main():
    ui = flipctl.load(PAGE, "tetris.slint")
    game = TetrisGame()

    def draw():
        ui.score = game.score
        ui.lines = game.lines
        ui.msg = game.msg
        ui.filled_cells = game.cells()
        ui.next_cells = game.next_preview()

    @ui.move_left
    def _(): game.move(-1); draw()

    @ui.move_right
    def _(): game.move(1); draw()

    @ui.rotate_piece
    def _(): game.rotate(); draw()

    @ui.drop_piece
    def _(): game.drop(); draw()

    @ui.restart_game
    def _(): game.reset(); draw()

    @flipctl.on_key(ui)
    def _(key, down):
        if not down:
            return
        if key in (flipctl.Key.BACK, flipctl.Key.ESCAPE):
            slint.quit_event_loop()
        elif key == flipctl.Key.LEFT:
            game.move(-1)
        elif key == flipctl.Key.RIGHT:
            game.move(1)
        elif key in (flipctl.Key.UP, flipctl.Key.VIEW):
            game.rotate()
        elif key == flipctl.Key.DOWN:
            game.step()
        elif key in (flipctl.Key.OK, flipctl.Key.RUN, flipctl.Key.POWER):
            if game.game_over:
                game.reset()
            else:
                game.drop()
        draw()

    draw()
    flipctl.run(ui, game_loop(game, draw))


async def game_loop(game, draw):
    while True:
        if not game.game_over and not game.paused:
            game.step()
            draw()
            delay = max(0.12, 0.45 - (game.lines // 10) * 0.04)
        else:
            delay = 0.2
        try:
            await asyncio.wait_for(game.woken.wait(), delay)
        except asyncio.TimeoutError:
            pass
        game.woken.clear()


if __name__ == "__main__":
    main()
