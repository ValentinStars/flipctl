#!/usr/bin/env python3
# /// flipctl
# name = "2048"
# status = false
# ///
"""2048 Puzzle Game for FlipCTL.

Controls:
  Up / Down / Left / Right: Slide tiles
  Swipe or drag on the field to slide tiles
  B / Enter: Restart game
  Escape / Back: Exit
"""

import random
from pathlib import Path
import flipctl
import slint

SIZE = 4
SCORE_FILE = Path("best-score.txt")

PAGE = """
import { Shell } from "@app/shell.slint";
import { FlipperTheme } from "@theme";

export struct Tile {
    col: int,
    row: int,
    val_str: string,
    filled: bool,
}

export component App inherits Shell {
    in property <int> score: 0;
    in property <int> high_score: 0;
    in property <string> msg: "";
    in property <[Tile]> tiles;

    callback keyed(string, bool);
    callback slide(string);
    callback restart();

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
                text: "2048";
                color: FlipperTheme.white;
                font-size: 16px;
                font-family: "Busy9px";
            }

            Text {
                x: 60px; y: 2px;
                text: "SCORE: " + root.score;
                color: FlipperTheme.white;
                font-size: 16px;
                font-family: "Busy9px";
            }

            Text {
                x: 160px; y: 2px;
                text: "BEST: " + root.high_score;
                color: FlipperTheme.white;
                font-size: 16px;
                font-family: "Busy9px";
            }
        }

        // 4x4 Grid Board (116px x 116px centred at x: 70)
        Rectangle {
            x: 70px; y: 20px;
            width: 116px; height: 116px;
            background: FlipperTheme.black;
            border-width: 1px;
            border-color: FlipperTheme.black;

            for t in root.tiles: Rectangle {
                x: t.col * 29px + 1px;
                y: t.row * 29px + 1px;
                width: 27px; height: 27px;
                background: t.filled ? FlipperTheme.white : FlipperTheme.field_label;

                if t.filled: Text {
                    x: 0; y: 6px;
                    width: parent.width;
                    horizontal-alignment: center;
                    text: t.val_str;
                    color: FlipperTheme.black;
                    font-size: 16px;
                    font-family: "Busy9px";
                }
            }

            if root.msg != "": Rectangle {
                x: 8px; y: 38px;
                width: 100px; height: 40px;
                background: FlipperTheme.white;
                border-width: 1px;
                border-color: FlipperTheme.black;

                Text {
                    x: 6px; y: 12px;
                    text: root.msg;
                    color: FlipperTheme.black;
                    font-size: 16px;
                    font-family: "Busy9px";
                }
            }
        }

        Text {
            x: 8px; y: 62px;
            text: "SWIPE";
            color: FlipperTheme.black;
        }
        playfield := TouchArea {
            x: 0; y: 16px; width: parent.width; height: parent.height - self.y;
            clicked => {
                let dx = self.mouse-x - self.pressed-x;
                let dy = self.mouse-y - self.pressed-y;
                if Math.abs(dx / 1px) >= 12 || Math.abs(dy / 1px) >= 12 {
                    if Math.abs(dx / 1px) >= Math.abs(dy / 1px) {
                        root.slide(dx < 0px ? "left" : "right");
                    } else {
                        root.slide(dy < 0px ? "up" : "down");
                    }
                }
            }
        }
        Rectangle {
            x: 192px; y: 100px; width: 60px; height: 26px;
            border-width: 1px;
            border-color: FlipperTheme.black;
            background: restart_area.pressed ? FlipperTheme.black : FlipperTheme.white;
            Text {
                width: 100%; height: 100%;
                horizontal-alignment: center; vertical-alignment: center;
                text: "B  New";
                color: restart_area.pressed ? FlipperTheme.white : FlipperTheme.black;
            }
            restart_area := TouchArea { clicked => { root.restart(); } }
        }
    }
}
"""


class Game2048:
    def __init__(self):
        self.board = [[0 for _ in range(SIZE)] for _ in range(SIZE)]
        self.score = 0
        try:
            self.high_score = max(0, int(SCORE_FILE.read_text()))
        except (OSError, ValueError):
            self.high_score = 0
        self.msg = ""
        self.game_over = False
        self.reset()

    def reset(self):
        self.board = [[0 for _ in range(SIZE)] for _ in range(SIZE)]
        self.score = 0
        self.msg = ""
        self.game_over = False
        self._spawn()
        self._spawn()

    def _spawn(self):
        empties = [(r, c) for r in range(SIZE) for c in range(SIZE) if self.board[r][c] == 0]
        if empties:
            r, c = random.choice(empties)
            self.board[r][c] = 4 if random.random() < 0.1 else 2

    def _slide_row_left(self, row):
        non_zeros = [x for x in row if x != 0]
        merged = []
        skip = False
        for i in range(len(non_zeros)):
            if skip:
                skip = False
                continue
            if i + 1 < len(non_zeros) and non_zeros[i] == non_zeros[i + 1]:
                val = non_zeros[i] * 2
                merged.append(val)
                self.score += val
                skip = True
            else:
                merged.append(non_zeros[i])
        while len(merged) < SIZE:
            merged.append(0)
        return merged

    def move(self, direction):
        if self.game_over:
            return
        moved = False
        if direction == "left":
            for r in range(SIZE):
                new_row = self._slide_row_left(self.board[r])
                if new_row != self.board[r]:
                    self.board[r] = new_row
                    moved = True
        elif direction == "right":
            for r in range(SIZE):
                rev = list(reversed(self.board[r]))
                new_row = list(reversed(self._slide_row_left(rev)))
                if new_row != self.board[r]:
                    self.board[r] = new_row
                    moved = True
        elif direction == "up":
            for c in range(SIZE):
                col = [self.board[r][c] for r in range(SIZE)]
                new_col = self._slide_row_left(col)
                for r in range(SIZE):
                    if self.board[r][c] != new_col[r]:
                        moved = True
                    self.board[r][c] = new_col[r]
        elif direction == "down":
            for c in range(SIZE):
                col = list(reversed([self.board[r][c] for r in range(SIZE)]))
                new_col = list(reversed(self._slide_row_left(col)))
                for r in range(SIZE):
                    if self.board[r][c] != new_col[r]:
                        moved = True
                    self.board[r][c] = new_col[r]

        if moved:
            if self.score > self.high_score:
                self.high_score = self.score
                try:
                    pending = SCORE_FILE.with_suffix(".tmp")
                    pending.write_text(str(self.high_score))
                    pending.replace(SCORE_FILE)
                except OSError:
                    pass
            self._spawn()
        if not self._can_move():
            self.game_over = True
            self.msg = "GAME OVER!"

    def _can_move(self):
        for r in range(SIZE):
            for c in range(SIZE):
                if self.board[r][c] == 0:
                    return True
                if c + 1 < SIZE and self.board[r][c] == self.board[r][c + 1]:
                    return True
                if r + 1 < SIZE and self.board[r][c] == self.board[r + 1][c]:
                    return True
        return False

    def tile_list(self):
        out = []
        for r in range(SIZE):
            for c in range(SIZE):
                v = self.board[r][c]
                out.append({
                    "col": c,
                    "row": r,
                    "val_str": str(v) if v > 0 else "",
                    "filled": v > 0,
                })
        return out


def main():
    ui = flipctl.load(PAGE, "game_2048.slint")
    game = Game2048()

    def draw():
        ui.score = game.score
        ui.high_score = game.high_score
        ui.msg = game.msg
        ui.tiles = game.tile_list()

    @ui.slide
    def _(direction): game.move(direction); draw()

    @ui.restart
    def _(): game.reset(); draw()

    @flipctl.on_key(ui)
    def _(key, down):
        if not down:
            return
        if key in (flipctl.Key.BACK, flipctl.Key.ESCAPE):
            slint.quit_event_loop()
        elif key == flipctl.Key.LEFT:
            game.move("left")
        elif key == flipctl.Key.RIGHT:
            game.move("right")
        elif key == flipctl.Key.UP:
            game.move("up")
        elif key == flipctl.Key.DOWN:
            game.move("down")
        elif key in (flipctl.Key.OK, flipctl.Key.RUN):
            game.reset()
        draw()

    draw()
    flipctl.run(ui)


if __name__ == "__main__":
    main()
