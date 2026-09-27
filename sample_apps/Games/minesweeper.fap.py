#!/usr/bin/env python3
# /// flipctl
# name = "Minesweeper"
# status = true
# audio = true
# runtime = ""
# ///
"""Retro Minesweeper for FlipCTL.

Classic logic minefield puzzle.
Controls:
  D-Pad (Up/Down/Left/Right): Move Cursor
  Ok / Space / Enter: Reveal Cell
  Edit / View / F / C: Flag / Unflag Mine
  Escape / Back: Exit
  Touch / Mouse: Tap any tile to select and reveal
"""

import asyncio
import random
import flipctl
import slint

COLS = 10
ROWS = 6
TOTAL_MINES = 8
CELL_SIZE = 16
GRID_X = 48
GRID_Y = 28

PAGE = """
import { Shell } from "@app/shell.slint";
import { FlipperTheme } from "@theme";

export struct Cell {
    x: int,
    y: int,
    col: int,
    row: int,
    revealed: bool,
    flagged: bool,
    is_mine: bool,
    neighbor_mines: int,
    label: string,
}

export component App inherits Shell {
    in property <int> cur_col: 0;
    in property <int> cur_row: 0;
    in property <int> flags_left: 8;
    in property <int> timer: 0;
    in property <string> status_face: ":)";
    in property <string> msg: "";
    in property <[Cell]> cells;

    callback keyed(string, bool);
    callback cell_clicked(int, int);

    key(text, down) => { root.keyed(text, down); }

    Rectangle {
        width: 100%;
        height: 100%;
        background: FlipperTheme.white;

        // Top status bar
        Text {
            x: 10px; y: 4px;
            text: "MINES: " + root.flags_left;
            font-family: "HaxrCorp 4090";
            font-size: 16px;
            color: FlipperTheme.black;
        }

        Text {
            x: 120px; y: 4px;
            text: root.status_face;
            font-family: "HaxrCorp 4090";
            font-size: 16px;
            color: FlipperTheme.black;
        }

        Text {
            x: root.width - 65px; y: 4px;
            text: "TIME: " + root.timer;
            font-family: "HaxrCorp 4090";
            font-size: 16px;
            color: FlipperTheme.black;
        }

        // Top line
        Rectangle {
            x: 0; y: 22px;
            width: 100%; height: 1px;
            background: FlipperTheme.black;
        }

        // Grid Container
        Rectangle {
            x: 48px;
            y: 26px;
            width: 160px;
            height: 96px;
            background: FlipperTheme.white;

            // Cells
            for c in root.cells: Rectangle {
                x: c.x * 1px;
                y: c.y * 1px;
                width: 16px;
                height: 16px;
                background: c.revealed ? FlipperTheme.white : FlipperTheme.white;
                border-width: 1px;
                border-color: FlipperTheme.black;

                // Hidden tile shading / pattern
                if !c.revealed: Rectangle {
                    x: 1px; y: 1px;
                    width: 14px; height: 14px;
                    background: c.flagged ? transparent : #eeeeee;
                }

                Text {
                    x: 0; y: 0;
                    width: 16px; height: 16px;
                    text: c.label;
                    font-family: "HaxrCorp 4090";
                    font-size: 16px;
                    color: FlipperTheme.black;
                    horizontal-alignment: center;
                    vertical-alignment: center;
                }

                TouchArea {
                    width: 100%;
                    height: 100%;
                    clicked => {
                        root.cell_clicked(c.col, c.row);
                    }
                }
            }

            // Cursor Highlight Box
            Rectangle {
                x: root.cur_col * 16px;
                y: root.cur_row * 16px;
                width: 16px;
                height: 16px;
                border-width: 2px;
                border-color: FlipperTheme.black;
                background: transparent;
            }
        }

        // Message overlay
        if root.msg != "": Rectangle {
            x: 20px;
            y: 55px;
            width: root.width - 40px;
            height: 24px;
            background: FlipperTheme.white;
            border-width: 1px;
            border-color: FlipperTheme.black;

            Text {
                x: 0; y: 3px;
                width: 100%;
                text: root.msg;
                font-family: "HaxrCorp 4090";
                font-size: 16px;
                color: FlipperTheme.black;
                horizontal-alignment: center;
            }
        }

        // Soft buttons bar
        Rectangle {
            x: 0; y: root.height - 18px;
            width: 100%; height: 18px;
            background: FlipperTheme.white;
            border-width: 1px;
            border-color: FlipperTheme.black;

            Text {
                x: 4px; y: 1px;
                text: "Esc:Quit";
                font-family: "HaxrCorp 4090";
                font-size: 16px;
                color: FlipperTheme.black;
            }
            Text {
                x: 75px; y: 1px;
                text: "Ok:Dig";
                font-family: "HaxrCorp 4090";
                font-size: 16px;
                color: FlipperTheme.black;
            }
            Text {
                x: 135px; y: 1px;
                text: "V:Flag";
                font-family: "HaxrCorp 4090";
                font-size: 16px;
                color: FlipperTheme.black;
            }
            Text {
                x: root.width - 55px; y: 1px;
                text: "B:New";
                font-family: "HaxrCorp 4090";
                font-size: 16px;
                color: FlipperTheme.black;
            }
        }
    }
}
"""


class MinesweeperGame:
    def __init__(self):
        self.reset()

    def reset(self):
        self.cur_col = 0
        self.cur_row = 0
        self.flags_left = TOTAL_MINES
        self.timer = 0
        self.status_face = ":)"
        self.msg = ""
        self.started = False
        self.game_over = False
        self.win = False

        self.board = []
        for r in range(ROWS):
            row_cells = []
            for c in range(COLS):
                row_cells.append({
                    "col": c,
                    "row": r,
                    "x": c * CELL_SIZE,
                    "y": r * CELL_SIZE,
                    "is_mine": False,
                    "revealed": False,
                    "flagged": False,
                    "neighbor_mines": 0,
                    "label": "",
                })
            self.board.append(row_cells)

    def place_mines(self, safe_c, safe_r):
        all_coords = [(c, r) for r in range(ROWS) for c in range(COLS) if not (c == safe_c and r == safe_r)]
        mine_coords = set(random.sample(all_coords, TOTAL_MINES))

        for r in range(ROWS):
            for c in range(COLS):
                if (c, r) in mine_coords:
                    self.board[r][c]["is_mine"] = True

        for r in range(ROWS):
            for c in range(COLS):
                if not self.board[r][c]["is_mine"]:
                    count = 0
                    for dr in (-1, 0, 1):
                        for dc in (-1, 0, 1):
                            if dr == 0 and dc == 0:
                                continue
                            nr, nc = r + dr, c + dc
                            if 0 <= nr < ROWS and 0 <= nc < COLS and self.board[nr][nc]["is_mine"]:
                                count += 1
                    self.board[r][c]["neighbor_mines"] = count

    def move_cursor(self, dc, dr):
        self.cur_col = max(0, min(COLS - 1, self.cur_col + dc))
        self.cur_row = max(0, min(ROWS - 1, self.cur_row + dr))

    def toggle_flag(self, c=None, r=None):
        if self.game_over or self.win:
            return
        if c is None:
            c, r = self.cur_col, self.cur_row
        cell = self.board[r][c]
        if cell["revealed"]:
            return

        cell["flagged"] = not cell["flagged"]
        if cell["flagged"]:
            cell["label"] = "P"
            self.flags_left = max(0, self.flags_left - 1)
        else:
            cell["label"] = ""
            self.flags_left = min(TOTAL_MINES, self.flags_left + 1)

    def reveal(self, c=None, r=None):
        if self.game_over or self.win:
            self.reset()
            return
        if c is None:
            c, r = self.cur_col, self.cur_row

        if not self.started:
            self.started = True
            self.place_mines(c, r)

        cell = self.board[r][c]
        if cell["flagged"] or cell["revealed"]:
            return

        cell["revealed"] = True

        if cell["is_mine"]:
            # Hit a mine! Game Over
            cell["label"] = "*"
            self.game_over = True
            self.status_face = "X("
            self.msg = "BOOM! GAME OVER"
            for row in self.board:
                for cl in row:
                    if cl["is_mine"]:
                        cl["revealed"] = True
                        cl["label"] = "*"
            return

        # Safe reveal
        if cell["neighbor_mines"] > 0:
            cell["label"] = str(cell["neighbor_mines"])
        else:
            cell["label"] = " "
            # Flood fill adjacent cells
            queue = [(c, r)]
            while queue:
                qc, qr = queue.pop(0)
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        nr, nc = qr + dr, qc + dc
                        if 0 <= nr < ROWS and 0 <= nc < COLS:
                            adj = self.board[nr][nc]
                            if not adj["revealed"] and not adj["is_mine"] and not adj["flagged"]:
                                adj["revealed"] = True
                                if adj["neighbor_mines"] > 0:
                                    adj["label"] = str(adj["neighbor_mines"])
                                else:
                                    adj["label"] = " "
                                    queue.append((nc, nr))

        # Check win condition
        unrevealed_non_mines = sum(
            1 for row in self.board for cl in row if not cl["revealed"] and not cl["is_mine"]
        )
        if unrevealed_non_mines == 0:
            self.win = True
            self.status_face = "B)"
            self.msg = f"YOU WIN! TIME: {self.timer}s"

    def flat_cells(self):
        res = []
        for r in range(ROWS):
            for c in range(COLS):
                res.append(self.board[r][c])
        return res


def main():
    ui = flipctl.load(PAGE, "minesweeper.slint")
    game = MinesweeperGame()

    def sync_ui():
        ui.cur_col = game.cur_col
        ui.cur_row = game.cur_row
        ui.flags_left = game.flags_left
        ui.timer = game.timer
        ui.status_face = game.status_face
        ui.msg = game.msg
        ui.cells = game.flat_cells()

    @ui.cell_clicked
    def _(c, r):
        game.cur_col = c
        game.cur_row = r
        game.reveal(c, r)
        sync_ui()

    @flipctl.on_key(ui)
    def _(key, down):
        if not down:
            return
        if key in (flipctl.Key.BACK, flipctl.Key.ESCAPE):
            slint.quit_event_loop()
        elif key == flipctl.Key.LEFT:
            game.move_cursor(-1, 0)
        elif key == flipctl.Key.RIGHT:
            game.move_cursor(1, 0)
        elif key == flipctl.Key.UP:
            game.move_cursor(0, -1)
        elif key == flipctl.Key.DOWN:
            game.move_cursor(0, 1)
        elif key in (flipctl.Key.OK, flipctl.Key.POWER):
            game.reveal()
        elif key in (flipctl.Key.EDIT, flipctl.Key.VIEW):
            game.toggle_flag()
        elif key == flipctl.Key.RUN:
            game.reset()
        sync_ui()

    async def timer_loop():
        while True:
            await asyncio.sleep(1.0)
            if game.started and not game.game_over and not game.win:
                game.timer += 1
                sync_ui()

    sync_ui()
    flipctl.run(ui, timer_loop())


if __name__ == "__main__":
    main()
