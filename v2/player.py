#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.13"
# dependencies = ["textual"]
# ///

"""
MPV Video Player TUI (Textual)
Browse top-level subdirectories containing videos and play them on an external monitor.

Left pane:  top-level subdirectories (that contain video files, searched recursively),
            preceded by two pseudo-directories:
              (root files) - videos sitting directly in the target directory
              (all files)  - every video in the tree, as one flat list
Right pane: videos inside the highlighted directory

The "cue mark" (green, prefixed with ▶) is the video that will play next.
It advances by one on every play, and you can move it yourself without
playing anything.

  Enter / Space (either pane)   -> play the cued video
  Right / l on a directory      -> move into it to step the cue file by file
  Up / Down / k / j in files    -> move the cue mark
  [ / ]                         -> step the cue back / forward from either pane
  r                             -> move the cue to a random video
  s                             -> shuffle the group's play order
  (none of [ ] r s start playback - press Enter when you like the pick)
  Left / h                      -> back to the directory list
  /                             -> filter the focused pane
  Esc                           -> clear filter / dismiss notifications
  q                             -> quit

Requires: pip install textual
"""

import os.path
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.geometry import Region
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".webm",
    ".flv",
    ".wmv",
    ".m4v",
    ".mpg",
    ".mpeg",
}

CUE_STYLE = "bold green"  # how the cued file is drawn
CUE_MARKER = "▶ "  # prefix for the cued file
CUE_PAD = "  "  # same width, keeps names aligned

FILES_DEBOUNCE = 0.08  # seconds to coalesce files-pane rebuilds while navigating

PSEUDO_STYLE = "italic cyan"  # how the pseudo-directories are drawn
ROOT_GROUP_NAME = "<root>"
ALL_GROUP_NAME = "<all videos>"

PRACTICE_TITLE = Path.home() / "foleyoke" / "practice.mp4"


@dataclass(eq=False)
class Group:
    """A top-level subdirectory and every video file beneath it"""

    name: str
    path: Path
    files: List[Path]
    cue: int = 0  # index of the cued file: what plays next
    pseudo: bool = False

    def __post_init__(self) -> None:
        # Path.relative_to() is surprisingly costly, and these never change
        self._rel = {f: str(f.relative_to(self.path)) for f in self.files}
        self._lower = {f: r.lower() for f, r in self._rel.items()}

    def rel(self, file: Path) -> str:
        return self._rel[file]

    def rel_lower(self, file: Path) -> str:
        return self._lower[file]

    def cued(self) -> Path:
        return self.files[self.cue]

    def advance_cue(self) -> None:
        self.cue = (self.cue + 1) % len(self.files)

    def cue_file(self, file: Path) -> None:
        self.cue = self.files.index(file)

    def shuffle(self) -> None:
        # keep the same file cued so shuffling doesn't change what plays next
        cued = self.cued() if self.files else None
        random.shuffle(self.files)
        if cued is not None:
            self.cue_file(cued)


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS and path.is_file()


def scan_videos(base: Path) -> List[Path]:
    """Every video beneath `base`, skipping dotted files and directories"""
    try:
        return [
            f
            for f in base.rglob("*")
            if is_video(f)
            and not any(part.startswith(".") for part in f.relative_to(base).parts)
        ]
    except OSError:
        return []


def scan_groups(root: Path, shuffle=True) -> List[Group]:
    """Top-level subdirectories containing videos, plus two pseudo-directories"""
    groups: List[Group] = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return groups

    everything: List[Path] = []
    for sub in entries:
        if sub.name.startswith(".") or not sub.is_dir():
            continue
        files = scan_videos(sub)
        if files:
            if shuffle:
                random.shuffle(files)
            else:
                files.sort(key=lambda f: str(f.relative_to(sub)).lower())
            groups.append(Group(sub.name, sub, files))
            everything.extend(files)

    loose = sorted(
        (f for f in entries if not f.name.startswith(".") and is_video(f)),
        key=lambda f: f.name.lower(),
    )
    everything.extend(loose)
    everything.sort(key=lambda f: str(f.relative_to(root)).lower())

    pseudo: List[Group] = []
    if loose:
        pseudo.append(Group(ROOT_GROUP_NAME, root, loose, pseudo=True))
    # with only one source of files, "(all files)" would be a pure duplicate
    if len(groups) + bool(loose) > 1:
        pseudo.append(Group(ALL_GROUP_NAME, root, everything, pseudo=True))
    return groups + pseudo


def option(label: str, style: str = "") -> Option:
    # Text() so filenames containing [brackets] aren't parsed as markup
    return Option(Text(label, style=style))


class PlayerApp(App):
    TITLE = "Foleyoke Player"

    CSS = """
    #filter {
        display: none;
        margin: 0 1;
    }
    #filter.shown {
        display: block;
    }
    Horizontal {
        height: 1fr;
    }
    OptionList {
        height: 1fr;
        padding: 0 1;
        border: round $panel;
    }
    OptionList:focus {
        border: round $accent;
    }
    /* size to the directory names, but never hog the window */
    #dirs {
        width: auto;
        min-width: 20;
        max-width: 40%;
        padding-left: 1;
        padding-right: 1;
    }
    #files {
        width: 1fr;
    }
    /* the cue mark already shows the position here, so drop the block cursor */
    #files > .option-list--option-highlighted,
    #files:focus > .option-list--option-highlighted {
        color: $foreground;
        background: transparent;
        text-style: none;
    }
    """

    # arrows work too: up/down are handled by OptionList itself, left/right below
    NAV = Binding.Group("Navigate")
    CUE = Binding.Group("Move cue")

    BINDINGS = [
        Binding("j", "cursor_down", "Down", key_display="↓/j", group=NAV),
        Binding("k", "cursor_up", "Up", key_display="↑/k", group=NAV),
        Binding("h,left", "focus_left", "Back", key_display="←/h", group=NAV),
        Binding("l,right", "focus_right", "Open dir", key_display="→/l", group=NAV),
        Binding("left_square_bracket", "step_next(-1)", "Prev", group=CUE),
        Binding("right_square_bracket", "step_next(1)", "Next", group=CUE),
        Binding("r", "randomize", "Random", group=CUE),
        Binding("s", "shuffle", "Shuffle"),
        Binding("space", "play", "Play", show=False),
        Binding("p", "practice", "Practice"),
        Binding("a", "again", "Play Again"),
        Binding("slash", "filter", "Filter"),
        Binding("escape", "clear_filter", "Clear filter / dismiss", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        self.groups = scan_groups(root, shuffle=True)
        self.dir_view: List[Group] = list(self.groups)  # after filtering
        self.current: Optional[Group] = None  # highlighted directory
        self.file_view: List[Path] = []  # current dir's files, after filtering
        self.filter_target: Optional[str] = None  # "dirs" or "files"
        self.needles: Dict[str, str] = {"dirs": "", "files": ""}
        self._files_timer: Optional[Timer] = None
        self._syncing_cue = False  # guards highlight <-> cue feedback
        self.sub_title = str(root)
        self.last_played: Optional[Path] = None
        # self.status = StatusBar()
        # self.status.set_left("init")

    # ---------- layout ----------

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="Filter (Enter to accept, Esc to clear)", id="filter")
        with Horizontal():
            yield OptionList(id="dirs")
            yield OptionList(id="files")
        # yield self.status
        yield Footer()

    def on_mount(self) -> None:
        self.dirs_list.border_title = "Categories"
        if not self.groups:
            self.notify(
                f"No subdirectories with videos found in {self.root}",
                severity="warning",
                timeout=10,
            )
        self.rebuild_dirs()
        self.dirs_list.focus()

    @property
    def dirs_list(self) -> OptionList:
        return self.query_one("#dirs", OptionList)

    @property
    def files_list(self) -> OptionList:
        return self.query_one("#files", OptionList)

    # ---------- list building ----------

    def rebuild_dirs(self) -> None:
        keep = self.current
        ol = self.dirs_list
        ol.clear_options()
        ol.add_options(
            [
                option(f"{g.name}  ({len(g.files)})", PSEUDO_STYLE if g.pseudo else "")
                for g in self.dir_view
            ]
        )
        if self.dir_view:
            idx = self.dir_view.index(keep) if keep in self.dir_view else 0
            ol.highlighted = idx
            self.set_current(self.dir_view[idx])
        else:
            self.set_current(None)
        self.update_subtitle()

    def set_current(self, group: Optional[Group]) -> None:
        if group is self.current:
            return
        self.current = group
        self.needles["files"] = ""
        self.file_view = list(group.files) if group else []
        # Rebuilding a large file list takes long enough to be felt when keys
        # repeat, so coalesce bursts: only the group we settle on gets rendered.
        self.schedule_files_rebuild()

    def schedule_files_rebuild(self) -> None:
        """Debounce rebuild_files so held nav keys don't rebuild once per press"""
        if self._files_timer is not None:
            self._files_timer.stop()
        self._files_timer = self.set_timer(FILES_DEBOUNCE, self.flush_files_rebuild)

    def cancel_files_rebuild(self) -> bool:
        """Drop any queued files-pane rebuild; True if one was pending"""
        if self._files_timer is None:
            return False
        self._files_timer.stop()
        self._files_timer = None
        return True

    def flush_files_rebuild(self) -> None:
        """Render any pending files-pane rebuild right now"""
        if self.cancel_files_rebuild():
            self.rebuild_files()

    def rebuild_files(self) -> None:
        ol = self.files_list
        group = self.current
        ol.border_title = group.name if group else ""
        ol.clear_options()
        if group:
            cued = group.cued() if group.files else None
            ol.add_options(
                [
                    option(CUE_MARKER + group.rel(f), CUE_STYLE)
                    if f == cued
                    else option(CUE_PAD + group.rel(f))
                    for f in self.file_view
                ]
            )
        self.sync_highlight_to_cue()

    def sync_highlight_to_cue(self) -> None:
        """Park the files-pane highlight on the cued file

        The highlight *is* the cue in that pane, so every OptionList movement
        (keys, mouse, page up/down) moves the cue for free.
        """
        group = self.current
        ol = self.files_list
        if not group or not group.files:
            return
        try:
            idx = self.file_view.index(group.cued())
        except ValueError:
            return  # cued file is filtered out of view
        if ol.highlighted != idx:
            self._syncing_cue = True
            try:
                ol.highlighted = idx
            finally:
                self._syncing_cue = False

    def update_subtitle(self) -> None:
        focused = self.focused
        if isinstance(focused, OptionList) and focused.id == "files" and self.current:
            group = self.current
            cue = (group.cue + 1) if group.files else 0
            shown = (
                f" ({len(self.file_view)} shown)"
                if len(self.file_view) != len(group.files)
                else ""
            )
            self.sub_title = (
                f"{self.root}  |  {group.name}: cue {cue}/{len(group.files)}{shown}"
            )
        else:
            idx = self.dirs_list.highlighted
            pos = (idx + 1) if idx is not None else 0
            self.sub_title = f"{self.root}  |  {pos}/{len(self.dir_view)} of {len(self.groups)} directories"

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        if event.option_list.id == "dirs":
            # Read the live highlight rather than the event index, which can be stale
            idx = self.dirs_list.highlighted
            if idx is not None and idx < len(self.dir_view):
                self.set_current(self.dir_view[idx])
        elif not self._syncing_cue:
            # moving the highlight in the files pane re-cues
            self.cue_from_highlight()
        self.update_subtitle()

    def cue_from_highlight(self) -> None:
        """Adopt the files-pane highlight as the cue, redrawing the marker"""
        group = self.current
        idx = self.files_list.highlighted
        if not group or idx is None or idx >= len(self.file_view):
            return
        file = self.file_view[idx]
        if group.cued() is file:
            return
        group.cue_file(file)
        self.cancel_files_rebuild()
        self.rebuild_files()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Enter on a highlighted option"""
        self.play_cued()

    def on_descendant_focus(self) -> None:
        self.update_subtitle()

    # ---------- filtering ----------

    def apply_filter(self, target: str, needle: str) -> None:
        needle = needle.strip().lower()
        if self.needles[target] == needle:
            return
        self.needles[target] = needle

        if target == "dirs":
            self.dir_view = [g for g in self.groups if needle in g.name.lower()]
            self.rebuild_dirs()
        elif self.current:
            group = self.current
            self.file_view = [f for f in group.files if needle in group.rel_lower(f)]
            self.cancel_files_rebuild()
            self.rebuild_files()
            self.update_subtitle()

    def reset_filter(self) -> None:
        target = self.filter_target
        self.filter_target = None
        filter_input = self.query_one("#filter", Input)
        filter_input.value = ""
        filter_input.remove_class("shown")
        if target:
            self.apply_filter(target, "")

    def on_input_changed(self, event: Input.Changed) -> None:
        if self.filter_target:
            self.apply_filter(self.filter_target, event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        (self.files_list if self.filter_target == "files" else self.dirs_list).focus()

    # ---------- actions ----------

    def action_cursor_down(self) -> None:
        if isinstance(self.focused, OptionList):
            self.focused.action_cursor_down()

    def action_cursor_up(self) -> None:
        if isinstance(self.focused, OptionList):
            self.focused.action_cursor_up()

    def action_focus_right(self) -> None:
        if (
            self.focused is not self.dirs_list
            or not self.current
            or not self.current.files
        ):
            return
        self.flush_files_rebuild()
        self.reset_filter()
        self.files_list.focus()
        self.sync_highlight_to_cue()

    def action_focus_left(self) -> None:
        if self.focused is not self.files_list:
            return
        self.reset_filter()
        self.dirs_list.focus()

    def action_clear_filter(self) -> None:
        self.clear_notifications()
        target = self.filter_target
        if target is None:
            return
        self.reset_filter()
        (self.files_list if target == "files" else self.dirs_list).focus()

    def action_play(self) -> None:
        if isinstance(self.focused, OptionList):
            self.play_cued()

    def action_practice(self) -> None:
        """play the cued video in practice mode, without advancing the cue point"""
        group = self.cue_group()
        if group is None:
            return
        video = group.cued()
        self.play_video(video, practice=True)

    def action_again(self) -> None:
        """Play the last-played clip again, without advancing the cue point"""
        if self.last_played is None:
            self.notify("Nothing to replay. Play a video the normal way first.")
            return
        else:
            self.play_video(self.last_played)

    def action_filter(self) -> None:
        pane = self.focused
        if not isinstance(pane, OptionList):
            return
        self.flush_files_rebuild()
        self.filter_target = "files" if pane.id == "files" else "dirs"
        filter_input = self.query_one("#filter", Input)
        filter_input.value = self.needles[self.filter_target]
        filter_input.add_class("shown")
        filter_input.focus()

    def action_shuffle(self) -> None:
        """Shuffle the play order of the cued group"""
        if not isinstance(self.focused, OptionList):
            return
        self.flush_files_rebuild()
        group = self.cue_group()
        if group is None:
            return
        group.shuffle()
        if group is self.current:
            self.needles["files"] = ""
            self.file_view = list(group.files)
            self.cancel_files_rebuild()
            self.rebuild_files()
            self.scroll_to_cue()

    def action_randomize(self) -> None:
        """Move the cue to a random video without playing it"""
        if not isinstance(self.focused, OptionList):
            return
        self.flush_files_rebuild()
        group = self.cue_group()
        if group is None or len(group.files) < 2:
            return
        choices = [i for i in range(len(group.files)) if i != group.cue]
        self.move_cue(group, random.choice(choices))

    def action_step_next(self, delta: int) -> None:
        """Walk the cue mark through the group, from either pane"""
        if not isinstance(self.focused, OptionList):
            return
        self.flush_files_rebuild()
        group = self.cue_group()
        if group is None:
            return
        self.move_cue(group, (group.cue + delta) % len(group.files))

    def cue_group(self) -> Optional[Group]:
        """The group whose cue mark the current pane acts on

        The files pane always shows `self.current`; in the dirs pane the
        highlighted row may not have settled into `current` yet.
        """
        if self.focused is self.files_list:
            group = self.current
        else:
            idx = self.dirs_list.highlighted
            if idx is None or idx >= len(self.dir_view):
                return None
            group = self.dir_view[idx]
        return group if group and group.files else None

    def move_cue(self, group: Group, cue: int) -> None:
        """Move a group's cue mark, keeping it visible if it's on screen"""
        group.cue = cue
        if group is not self.current:
            return
        self.cancel_files_rebuild()
        self.rebuild_files()
        self.scroll_to_cue()

    def scroll_to_cue(self) -> None:
        """Scroll the files pane so the cue mark is visible

        Usually the highlight is already on the cue and Textual has scrolled
        for us, but the cue also moves from the dirs pane, where it hasn't.
        """
        group = self.current
        if not group or not group.files:
            return
        try:
            idx = self.file_view.index(group.cued())
        except ValueError:
            return  # filtered out of view
        ol = self.files_list
        if not ol.is_mounted:
            return
        try:
            line = ol._index_to_line[idx]
            height = ol._heights[idx]
        except KeyError:
            return
        ol.scroll_to_region(
            Region(0, line, ol.scrollable_content_region.width, height),
            force=True,
            animate=False,
            immediate=True,
        )

    def play_cued(self) -> None:
        """Play the cued video, then advance the cue to the next one"""
        group = self.cue_group()
        if group is None:
            return
        video = group.cued()
        group.advance_cue()
        if group is self.current:
            # we're rendering right now, so drop any rebuild still queued
            self.cancel_files_rebuild()
            self.rebuild_files()
            self.scroll_to_cue()
        else:
            self.flush_files_rebuild()
        self.play_video(video)

    # ---------- playback ----------

    def play_video(self, video_path: Path, practice: bool = False) -> None:
        """Suspend the TUI, hand the terminal to mpv, then resume"""
        if shutil.which("mpv") is None:
            self.notify(
                "could not play %s.\nmpv not found - is it installed and on your PATH?"
                % os.path.basename(video_path),
                severity="error",
                timeout=4,
            )
            return

        error = None
        with self.suspend():
            # Catch everything *inside* the block: suspend() only restores
            # the terminal if the block exits normally.
            try:
                subprocess.run(
                    [
                        "mpv",
                        "--fullscreen",
                        "--fs-screen=0",
                        str(PRACTICE_TITLE) if practice else "",
                        str(video_path),
                    ]
                )
            except KeyboardInterrupt:
                pass
            except OSError as e:
                error = str(e)
            finally:
                self.last_played = video_path

        if error:
            self.notify(f"Failed to launch mpv: {error}", severity="error", timeout=4)


class StatusBar(Widget):
    DEFAULT_CSS = """
    StatusBar {
        layout: horizontal;
        height: 1;
        background: $panel;
    }
    StatusBar > Static {
        width: 1fr;
        padding: 0 1;
    }
    StatusBar > #status-left {
    padding-left: 1;
    }
    StatusBar > #status-right {
        text-align: right;
    }
    """

    def __init__(self):
        super().__init__()
        self._left = "left init"
        self._right = "right init"

    def compose(self) -> ComposeResult:
        yield Static(self._left, id="status-left")
        yield Static(self._right, id="status-right")

    def set_left(self, text: str):
        # self.query_one("#status-left", Static).update(text)
        self._left = text

    def set_right(self, text: str):
        # self.query_one("#status-right", Static).update(text)
        self._right = text


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {Path(sys.argv[0]).name} <directory>", file=sys.stderr)
        sys.exit(1)

    root = Path(sys.argv[1]).expanduser()
    if not root.is_dir():
        print(f"Error: '{root}' is not a valid directory", file=sys.stderr)
        sys.exit(1)

    PlayerApp(root).run()


if __name__ == "__main__":
    main()
