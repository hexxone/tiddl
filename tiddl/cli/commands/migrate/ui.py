"""
Sophisticated terminal UI for migration with split-screen layout.

Uses Rich Layout + Live to show migration and download progress side-by-side.
Inspired by immich-go's terminal UI.
"""

import threading
from dataclasses import dataclass, field
from typing import Optional
from collections import deque

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TaskProgressColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text


@dataclass
class MigrationStats:
    """Statistics for migration progress."""
    total_playlists: int = 0
    current_playlist: int = 0
    current_playlist_name: str = ""

    total_tracks: int = 0
    current_track: int = 0
    current_track_name: str = ""

    added: int = 0
    skipped: int = 0
    failed: int = 0

    # Recent activity log (most recent first)
    recent_activity: deque = field(default_factory=lambda: deque(maxlen=8))

    def add_activity(self, icon: str, message: str):
        self.recent_activity.appendleft(f"{icon} {message}")


@dataclass
class DownloadStats:
    """Statistics for download progress."""
    total_playlists: int = 0
    completed: int = 0
    failed: int = 0
    pending: int = 0

    current_playlist_name: str = ""
    is_downloading: bool = False

    # Recent activity log (most recent first)
    recent_activity: deque = field(default_factory=lambda: deque(maxlen=8))

    def add_activity(self, icon: str, message: str):
        self.recent_activity.appendleft(f"{icon} {message}")


class MigrationUI:
    """
    Split-screen terminal UI for Spotify to Tidal migration.

    Layout:
    ┌─────────────────────────────────┬─────────────────────────────────┐
    │        MIGRATION                │        DOWNLOADS                │
    ├─────────────────────────────────┼─────────────────────────────────┤
    │ Playlist: [2/10] Current Name   │ Queue: 5 pending                │
    │ [████████░░░░░░░░░░░░] 40%      │ Downloading: Playlist Name      │
    │                                 │                                 │
    │ Stats:                          │ Stats:                          │
    │   ✓ Added: 50                   │   ✓ Completed: 3                │
    │   ○ Skipped: 10                 │   ✗ Failed: 1                   │
    │   ✗ Failed: 2                   │                                 │
    │                                 │                                 │
    │ Recent:                         │ Recent:                         │
    │   ✓ Song 1 - Artist             │   ✓ Playlist A                  │
    │   ✓ Song 2 - Artist             │   ✗ Playlist B (error)          │
    └─────────────────────────────────┴─────────────────────────────────┘
    """

    def __init__(self, console: Optional[Console] = None):
        self.console = console or Console()
        self.migration = MigrationStats()
        self.download = DownloadStats()
        self._lock = threading.Lock()
        self._live: Optional[Live] = None
        self._running = False

        # Progress bars (created fresh each time)
        self._migration_progress: Optional[Progress] = None
        self._migration_task_id = None

    def _create_migration_panel(self) -> Panel:
        """Create the migration (left) panel."""
        content_parts = []

        # Header with current playlist
        if self.migration.current_playlist_name:
            header = Text()
            header.append("Playlist: ", style="bold")
            header.append(f"[{self.migration.current_playlist}/{self.migration.total_playlists}] ", style="cyan")
            header.append(self.migration.current_playlist_name[:40], style="white")
            content_parts.append(header)
        else:
            content_parts.append(Text("Waiting to start...", style="dim"))

        content_parts.append(Text())  # Spacer

        # Progress bar for current playlist tracks
        if self.migration.total_tracks > 0:
            pct = (self.migration.current_track / self.migration.total_tracks) * 100
            bar_width = 30
            filled = int(bar_width * pct / 100)
            bar = "█" * filled + "░" * (bar_width - filled)

            progress_text = Text()
            progress_text.append(f"[{bar}] ", style="cyan")
            progress_text.append(f"{pct:.0f}%", style="bold cyan")
            progress_text.append(f" ({self.migration.current_track}/{self.migration.total_tracks})", style="dim")
            content_parts.append(progress_text)

            if self.migration.current_track_name:
                track_text = Text()
                track_text.append("  → ", style="dim")
                track_text.append(self.migration.current_track_name[:45], style="dim italic")
                content_parts.append(track_text)

        content_parts.append(Text())  # Spacer

        # Stats table
        stats = Table.grid(padding=(0, 2))
        stats.add_column(style="bold")
        stats.add_column(justify="right")
        stats.add_row("[green]✓ Added[/]", str(self.migration.added))
        stats.add_row("[cyan]○ Skipped[/]", str(self.migration.skipped))
        stats.add_row("[red]✗ Failed[/]", str(self.migration.failed))
        content_parts.append(stats)

        content_parts.append(Text())  # Spacer

        # Recent activity
        if self.migration.recent_activity:
            content_parts.append(Text("Recent:", style="bold dim"))
            for activity in list(self.migration.recent_activity)[:5]:
                # Activity contains Rich markup, use Text.from_markup
                content_parts.append(Text.from_markup(f"  {activity[:50]}"))

        return Panel(
            Group(*content_parts),
            title="[bold cyan]Migration[/]",
            border_style="cyan",
            height=20,
        )

    def _create_download_panel(self) -> Panel:
        """Create the download (right) panel."""
        content_parts = []

        # Queue status
        queue_text = Text()
        queue_text.append("Queue: ", style="bold")
        if self.download.pending > 0:
            queue_text.append(f"{self.download.pending} pending", style="yellow")
        elif self.download.is_downloading:
            queue_text.append("Processing...", style="green")
        else:
            queue_text.append("Empty", style="dim")
        content_parts.append(queue_text)

        # Current download
        if self.download.current_playlist_name and self.download.is_downloading:
            current = Text()
            current.append("Downloading: ", style="bold green")
            current.append(self.download.current_playlist_name[:35], style="white")
            content_parts.append(current)

            # Spinning indicator
            spinner = Text("  ◐ ", style="green")
            spinner.append("In progress...", style="dim italic")
            content_parts.append(spinner)
        else:
            content_parts.append(Text())

        content_parts.append(Text())  # Spacer

        # Stats table
        stats = Table.grid(padding=(0, 2))
        stats.add_column(style="bold")
        stats.add_column(justify="right")
        stats.add_row("[green]✓ Completed[/]", str(self.download.completed))
        stats.add_row("[red]✗ Failed[/]", str(self.download.failed))
        stats.add_row("[yellow]⏳ Pending[/]", str(self.download.pending))
        content_parts.append(stats)

        content_parts.append(Text())  # Spacer

        # Recent activity
        if self.download.recent_activity:
            content_parts.append(Text("Recent:", style="bold dim"))
            for activity in list(self.download.recent_activity)[:5]:
                # Activity contains Rich markup, use Text.from_markup
                content_parts.append(Text.from_markup(f"  {activity[:50]}"))

        return Panel(
            Group(*content_parts),
            title="[bold green]Downloads[/]",
            border_style="green",
            height=20,
        )

    def _create_layout(self) -> Layout:
        """Create the full split-screen layout."""
        layout = Layout()

        # Split into left (migration) and right (download) panels
        layout.split_row(
            Layout(name="migration", ratio=1),
            Layout(name="download", ratio=1),
        )

        layout["migration"].update(self._create_migration_panel())
        layout["download"].update(self._create_download_panel())

        return layout

    def start(self):
        """Start the live UI display."""
        self._running = True
        self._live = Live(
            self._create_layout(),
            console=self.console,
            refresh_per_second=4,
            transient=False,
        )
        self._live.start()

    def stop(self):
        """Stop the live UI display."""
        self._running = False
        if self._live:
            self._live.stop()
            self._live = None

    def refresh(self):
        """Refresh the UI display."""
        if self._live and self._running:
            with self._lock:
                self._live.update(self._create_layout())

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False

    # Migration update methods
    def start_playlist(self, playlist_num: int, total: int, name: str, track_count: int):
        """Start migrating a new playlist."""
        with self._lock:
            self.migration.current_playlist = playlist_num
            self.migration.total_playlists = total
            self.migration.current_playlist_name = name
            self.migration.total_tracks = track_count
            self.migration.current_track = 0
            self.migration.current_track_name = ""
        self.refresh()

    def update_track(self, track_num: int, track_name: str):
        """Update current track being processed."""
        with self._lock:
            self.migration.current_track = track_num
            self.migration.current_track_name = track_name
        self.refresh()

    def track_added(self, track_name: str):
        """Record a successfully added track."""
        with self._lock:
            self.migration.added += 1
            self.migration.add_activity("[green]✓[/]", track_name[:40])
        self.refresh()

    def track_skipped(self, track_name: str):
        """Record a skipped track."""
        with self._lock:
            self.migration.skipped += 1
            self.migration.add_activity("[cyan]○[/]", track_name[:40])
        self.refresh()

    def track_failed(self, track_name: str, reason: str = ""):
        """Record a failed track."""
        with self._lock:
            self.migration.failed += 1
            msg = f"{track_name[:30]}"
            if reason:
                msg += f" ({reason[:15]})"
            self.migration.add_activity("[red]✗[/]", msg)
        self.refresh()

    # Download update methods
    def queue_download(self, playlist_name: str):
        """Queue a playlist for download."""
        with self._lock:
            self.download.total_playlists += 1
            self.download.pending += 1
        self.refresh()

    def start_download(self, playlist_name: str):
        """Start downloading a playlist."""
        with self._lock:
            self.download.current_playlist_name = playlist_name
            self.download.is_downloading = True
        self.refresh()

    def download_complete(self, playlist_name: str, success: bool, message: str = ""):
        """Record a completed download."""
        with self._lock:
            self.download.pending = max(0, self.download.pending - 1)
            if success:
                self.download.completed += 1
                self.download.add_activity("[green]✓[/]", playlist_name[:40])
            else:
                self.download.failed += 1
                msg = f"{playlist_name[:25]}"
                if message:
                    msg += f" ({message[:20]})"
                self.download.add_activity("[red]✗[/]", msg)

            # Clear current if this was the one downloading
            if self.download.current_playlist_name == playlist_name:
                self.download.current_playlist_name = ""
                self.download.is_downloading = self.download.pending > 0
        self.refresh()

    def get_download_callback(self):
        """
        Get a callback function for the PlaylistDownloader.

        Returns a function with signature:
            callback(playlist_uuid: str, playlist_name: str, success: bool, message: str)
        """
        def callback(playlist_uuid: str, playlist_name: str, success: bool, message: str):
            self.download_complete(playlist_name, success, message)
        return callback


class SimpleProgressUI:
    """
    Simpler fallback UI for when split-screen isn't needed.
    Uses standard Rich progress bars.
    """

    def __init__(self, console: Optional[Console] = None):
        self.console = console or Console()
        self._progress: Optional[Progress] = None
        self._task_id = None

    def start_playlist_progress(self, total_tracks: int, description: str = "Processing"):
        """Start a progress bar for track processing."""
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=self.console,
        )
        self._progress.start()
        self._task_id = self._progress.add_task(description, total=total_tracks)

    def advance(self, amount: int = 1):
        """Advance the progress bar."""
        if self._progress and self._task_id is not None:
            self._progress.update(self._task_id, advance=amount)

    def stop(self):
        """Stop the progress bar."""
        if self._progress:
            self._progress.stop()
            self._progress = None
            self._task_id = None
