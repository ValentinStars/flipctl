"""File and folder selection for the HackRF TX panel."""

from collections import deque
import os
from pathlib import Path
import queue
import stat
import subprocess
import threading

import flipctl
from hackrf_tx_backend import MEDIA_EXTENSIONS


def desktop_folder(kind, fallback):
    try:
        result = subprocess.run(["xdg-user-dir", kind], capture_output=True, text=True,
                                check=True, timeout=0.2)
        directory = Path(result.stdout.strip())
        if directory.is_absolute() and directory != Path.home():
            return directory
    except (OSError, subprocess.SubprocessError):
        pass
    return Path.home() / fallback


class MediaPicker:
    def __init__(self, on_pick, on_path, on_back):
        self.on_pick = on_pick
        self.on_path = on_path
        self.on_back = on_back
        self.directory = Path.home()
        self.entries = []
        self.recent = deque(maxlen=5)
        self.locations = []
        self.notice = ""
        self.pipe_only = False
        self.loading = False
        self.reading_places = False
        self.directory_request = 0
        self.places_request = 0
        self.results = queue.SimpleQueue()
        self.view = flipctl.Menu(self.rows, count=self.row_count,
                                 buttons=["Back", "Type path", "", "Places", "Select"])
        self.view.on_key(self.key)
        self.places = flipctl.Menu(self.place_rows, buttons=["Back", "", "", "", "Open"])
        self.places.on_key(self.place_key)

    def path(self, value):
        return self.directory / Path(os.path.expanduser(os.path.expandvars(value)))

    def show(self, current, pipe_only):
        self.pipe_only = pipe_only
        path = self.directory
        if current and not current.startswith(("http://", "https://")):
            path = self.path(current)
        self.open_dir(path, from_input=True)
        self.view.show()

    def open_dir(self, path, *, from_input=False):
        if not from_input:
            self.directory = path
        self.entries = []
        self.notice = ""
        self.loading = True
        self.view.selected = 0
        self.directory_request += 1
        request, pipe_only = self.directory_request, self.pipe_only

        def scan():
            directory = path
            entries = []
            notice = ""
            try:
                if from_input and not directory.is_dir():
                    directory = directory.parent
                with os.scandir(directory) as files:
                    for entry in files:
                        if entry.name.startswith("."):
                            continue
                        try:
                            is_dir = entry.is_dir()
                            if not is_dir:
                                if pipe_only:
                                    if not stat.S_ISFIFO(entry.stat().st_mode):
                                        continue
                                elif (os.path.splitext(entry.name)[1].lower() not in MEDIA_EXTENSIONS
                                      or not entry.is_file()):
                                    continue
                        except OSError:
                            continue
                        entries.append((entry.name, is_dir))
                entries.sort(key=lambda row: (not row[1], row[0].casefold(), row[0]))
            except OSError:
                notice = "Cannot read folder"
            self.results.put(("directory", request, (directory, entries, notice)))

        threading.Thread(target=scan, daemon=True).start()

    def poll(self):
        while not self.results.empty():
            kind, request, result = self.results.get_nowait()
            if kind == "directory" and request == self.directory_request:
                self.directory, self.entries, self.notice = result
                self.loading = False
                self.view.selected = 0
                self.view.redraw()
            elif kind == "places" and request == self.places_request:
                self.locations = result
                self.reading_places = False
                self.places.selected = 0
                self.places.redraw()

    def has_folder_choice(self):
        return not self.pipe_only and not self.notice and not self.loading

    def row_count(self):
        return (1 + len(self.entries) + int(self.has_folder_choice())
                + int(self.loading or bool(self.notice) or (self.pipe_only and not self.entries)))

    def rows(self, start, stop):
        first_entry = 2 if self.has_folder_choice() else 1
        for index in range(start, stop):
            if index == 0:
                name = self.directory.name or "/"
                yield flipctl.item("Parent folder", status=name if len(name) < 20 else name[:17] + "...")
            elif index < first_entry:
                yield flipctl.item("Use this folder")
            elif index - first_entry < len(self.entries):
                name, is_dir = self.entries[index - first_entry]
                label = name if len(name) < 24 else name[:20] + "..."
                yield flipctl.item(label, status="Folder" if is_dir else "")
            else:
                label = "Reading folder..." if self.loading else self.notice or "No pipes here"
                yield flipctl.item(label, dim=True)

    def open_places(self):
        self.reading_places = True
        self.places_request += 1
        request, pipe_only = self.places_request, self.pipe_only
        recent = tuple(self.recent)
        self.places.selected = 0
        self.places.show()

        def scan():
            home = Path.home()
            paths = [("Home", home), ("Music", desktop_folder("MUSIC", "Music")),
                     ("Downloads", desktop_folder("DOWNLOAD", "Downloads")),
                     ("Videos", desktop_folder("VIDEOS", "Videos"))]
            locations = []
            for label, path in paths:
                try:
                    if (label == "Home" or path != home) and path.is_dir():
                        locations.append((label, path, False))
                except OSError:
                    continue
            for path, recent_pipe in recent:
                try:
                    if recent_pipe == pipe_only and path.exists():
                        locations.append((path.name or "/", path, True))
                except OSError:
                    continue
            self.results.put(("places", request, locations))

        threading.Thread(target=scan, daemon=True).start()

    def place_rows(self):
        if self.reading_places:
            yield flipctl.item("Reading places...", dim=True)
            return
        for label, _, recent in self.locations:
            yield flipctl.item(label[:20] + "..." if len(label) > 23 else label,
                               status="Recent" if recent else "")

    def place_key(self, key):
        if key in (flipctl.Key.BACK, flipctl.Key.ESCAPE):
            self.view.show()
        elif key in (flipctl.Key.OK, flipctl.Key.RUN):
            if not self.reading_places and self.locations:
                _, path, recent = self.locations[self.places.selected]
                if recent:
                    self.pick(path)
                else:
                    self.open_dir(path)
                    self.view.show()
        else:
            return False
        return True

    def pick(self, path):
        entry = (path, self.pipe_only)
        if entry in self.recent:
            self.recent.remove(entry)
        self.recent.appendleft(entry)
        self.on_pick(path)

    def key(self, key):
        if key in (flipctl.Key.BACK, flipctl.Key.ESCAPE):
            self.on_back()
        elif key is flipctl.Key.VIEW:
            self.on_path()
        elif key is flipctl.Key.EDIT:
            self.open_places()
        elif key in (flipctl.Key.OK, flipctl.Key.RUN):
            if self.loading:
                return True
            selected = self.view.selected
            if selected == 0:
                self.open_dir(self.directory.parent)
            elif selected == 1 and self.has_folder_choice():
                self.pick(self.directory)
            else:
                index = selected - (2 if self.has_folder_choice() else 1)
                if 0 <= index < len(self.entries):
                    name, is_dir = self.entries[index]
                    path = self.directory / name
                    if is_dir:
                        self.open_dir(path)
                    else:
                        self.pick(path)
        else:
            return False
        return True
