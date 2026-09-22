#!/usr/bin/env python3
"""
MPV Video Player TUI
Select and play videos from a directory on external monitor
"""
import curses
import os
import sys
import subprocess
from pathlib import Path
from typing import List, Any

VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv', '.m4v', '.mpg', '.mpeg'}

def get_video_files(directory: str) -> List[Path]:
    """Get all video files from directory"""
    path = Path(directory)
    if not path.is_dir():
        return []
    
    files = []
    for item in sorted(path.iterdir(), key=lambda path: path.name.lower()):
        if item.is_file() and item.suffix.lower() in VIDEO_EXTENSIONS:
            files.append(item)
    return files

def play_video(video_path: Path) -> Any:
    """Play video with mpv, giving it terminal control"""
    # Reset terminal to normal mode before launching mpv
    curses.endwin()
    
    # Run mpv with full terminal control
    try:
        subprocess.run([
            'mpv',
            '--fullscreen',
            '--fs-screen=0',
            str(video_path)
        ])
    except KeyboardInterrupt:
        pass
    
    # Reinitialize curses after mpv exits
    return curses.initscr()

def draw_menu(stdscr: Any, files: List[Path], selected_idx: int, directory: str) -> None:
    """Draw the file selection menu"""
    stdscr.clear()
    h, w = stdscr.getmaxyx()
    
    # Header
    title = f"Foleyoke Player - {directory}"
    stdscr.addstr(0, 0, title[:w-1], curses.A_BOLD)
    stdscr.addstr(1, 0, "=" * min(len(title), w-1))
    stdscr.addstr(2, 0, "↓/↑ or j/k to navigate, ENTER or <space> to play, 'q' to quit")
    
    # File list
    start_row = 4
    visible_rows = h - start_row - 1
    
    # Calculate scroll offset
    scroll_offset = max(0, selected_idx - visible_rows + 1)
    
    for idx, file in enumerate(files[scroll_offset:scroll_offset + visible_rows]):
        actual_idx = idx + scroll_offset
        row = start_row + idx
        
        if row >= h - 1:
            break
        
        # Highlight selected file
        if actual_idx == selected_idx:
            stdscr.addstr(row, 0, f"> {file.name}"[:w-1], curses.A_REVERSE)
        else:
            stdscr.addstr(row, 0, f"  {file.name}"[:w-1])
    
    # Footer with file count
    footer = f"Files: {len(files)} | Selected: {selected_idx + 1}/{len(files)}"
    if h > 1:
        stdscr.addstr(h - 1, 0, footer[:w-1], curses.A_DIM)
    
    stdscr.refresh()

def main(stdscr: Any, directory: str) -> None:
    """Main TUI loop"""
    # Setup curses
    curses.curs_set(0)  # Hide cursor
    stdscr.keypad(True)  # Enable arrow keys
    
    # Get video files
    files = get_video_files(directory)
    
    if not files:
        stdscr.addstr(0, 0, f"No video files found in {directory}")
        stdscr.addstr(1, 0, "Press any key to exit...")
        stdscr.refresh()
        stdscr.getch()
        return
    
    selected_idx = 0
    
    while True:
        draw_menu(stdscr, files, selected_idx, directory)
        
        key = stdscr.getch()
        
        if key == ord('q') or key == ord('Q'):
            break
        elif key in (curses.KEY_UP, ord('k')):
            selected_idx = max(0, selected_idx - 1)
        elif key in (curses.KEY_DOWN, ord('j')):
            selected_idx = min(len(files) - 1, selected_idx + 1)
        elif key == curses.KEY_PPAGE:  # Page Up
            selected_idx = max(0, selected_idx - 10)
        elif key == curses.KEY_NPAGE:  # Page Down
            selected_idx = min(len(files) - 1, selected_idx + 10)
        elif key == curses.KEY_HOME:
            selected_idx = 0
        elif key == curses.KEY_END:
            selected_idx = len(files) - 1
        elif key in (curses.KEY_ENTER, ord('\n'), ord('\r'), ord(' ')):
            # Play selected video
            stdscr = play_video(files[selected_idx])
            # Reconfigure stdscr after reinitializing
            curses.curs_set(0)
            stdscr.keypad(True)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python mpv_player.py <directory>")
        sys.exit(1)
    
    directory = sys.argv[1]
    
    if not os.path.isdir(directory):
        print(f"Error: '{directory}' is not a valid directory")
        sys.exit(1)
    
    try:
        curses.wrapper(main, directory)
    except KeyboardInterrupt:
        pass
