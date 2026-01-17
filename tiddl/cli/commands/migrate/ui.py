"""
Sophisticated terminal UI for migration with split-screen layout.

Uses Rich Layout + Live to show migration and download progress side-by-side.
Inspired by immich-go's terminal UI.
"""

import logging
import re
import threading
import time
import unicodedata
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


def strip_emojis(text: str) -> str:
    """
    Strip emoji and other problematic characters that can cause width calculation issues.
    Replaces them with a space to maintain readability.
    """
    # Remove emoji and other symbols that cause width issues
    cleaned = []
    for char in text:
        category = unicodedata.category(char)
        # Keep letters, numbers, punctuation, and basic symbols
        # Skip: So (Symbol, other), Sk (Symbol, modifier), Cs (Surrogate), Cn (Not assigned), Co (Private use)
        if category in ('So', 'Sk', 'Cs', 'Cn', 'Co'):
            continue
        # Also skip variation selectors and zero-width chars
        if '\ufe00' <= char <= '\ufe0f' or '\u200b' <= char <= '\u200f':
            continue
        cleaned.append(char)
    return ''.join(cleaned).strip()


def safe_text_width(text: str, max_width: int) -> str:
    """
    Safely truncate text to max_width, accounting for wide characters.
    """
    text = strip_emojis(text)
    if len(text) <= max_width:
        return text
    return text[:max_width - 1] + "…"


@dataclass
class WorkerProgress:
    """Progress for a single migration worker."""
    worker_id: int = 0
    playlist_num: int = 0
    playlist_name: str = ""
    total_tracks: int = 0
    current_track: int = 0
    current_track_name: str = ""
    start_time: float = 0.0
    last_update_time: float = 0.0


@dataclass
class MigrationStats:
    """Statistics for migration progress with multiple workers."""
    total_playlists: int = 0
    completed_playlists: int = 0
    num_workers: int = 1

    # Track active workers - key is worker_id (thread ident or playlist_num)
    active_workers: dict = field(default_factory=dict)  # worker_id -> WorkerProgress

    # Aggregate stats
    total_tracks_all: int = 0  # Sum of all playlist track counts
    added: int = 0
    skipped: int = 0
    failed: int = 0

    # ETA tracking
    tracks_processed_times: list = field(default_factory=list)  # Track processing times for ETA
    migration_start_time: float = 0.0

    # Recent activity log (most recent first)
    recent_activity: deque = field(default_factory=lambda: deque(maxlen=20))

    def add_activity(self, icon: str, message: str):
        # Strip emojis from activity messages too
        safe_msg = safe_text_width(message, 35)
        self.recent_activity.appendleft(f"{icon} {safe_msg}")

    def record_track_time(self, duration: float):
        """Record time taken to process a track for ETA calculation."""
        self.tracks_processed_times.append(duration)
        # Keep only last 100 samples for rolling average
        if len(self.tracks_processed_times) > 100:
            self.tracks_processed_times.pop(0)

    def get_pending_tracks(self) -> int:
        """Get count of pending tracks across all playlists."""
        processed = self.added + self.skipped + self.failed
        return max(0, self.total_tracks_all - processed)

    def get_eta_seconds(self) -> Optional[float]:
        """Calculate ETA in seconds for all remaining tracks."""
        if not self.tracks_processed_times:
            return None
        pending = self.get_pending_tracks()
        if pending <= 0:
            return None
        avg_time = sum(self.tracks_processed_times) / len(self.tracks_processed_times)
        # Divide by number of active workers for parallel processing
        active_count = max(1, len(self.active_workers))
        return (avg_time * pending) / active_count


@dataclass
class DownloadStats:
    """Statistics for download progress."""
    total_playlists: int = 0
    completed: int = 0
    failed: int = 0
    pending: int = 0

    current_playlist_name: str = ""
    current_track_name: str = ""
    current_track_num: int = 0
    current_playlist_total_tracks: int = 0
    is_downloading: bool = False

    # Aggregate track statistics across all downloads
    total_tracks_queued: int = 0  # Total tracks across all queued playlists
    total_tracks_completed: int = 0  # Tracks from completed playlists
    total_tracks_failed: int = 0  # Tracks from failed playlists

    # ETA tracking
    download_start_time: float = 0.0
    playlist_download_times: list = field(default_factory=list)  # Time per playlist for ETA

    # Recent activity log (most recent first)
    recent_activity: deque = field(default_factory=lambda: deque(maxlen=20))

    def add_activity(self, icon: str, message: str):
        safe_msg = safe_text_width(message, 40)
        self.recent_activity.appendleft(f"{icon} {safe_msg}")

    def record_playlist_time(self, duration: float):
        """Record time taken to download a playlist for ETA calculation."""
        self.playlist_download_times.append(duration)
        if len(self.playlist_download_times) > 20:
            self.playlist_download_times.pop(0)

    def get_eta_seconds(self) -> Optional[float]:
        """Calculate ETA in seconds for remaining downloads."""
        if not self.playlist_download_times or self.pending <= 0:
            return None
        avg_time = sum(self.playlist_download_times) / len(self.playlist_download_times)
        return avg_time * self.pending

    def get_pending_tracks(self) -> int:
        """Get approximate count of pending tracks."""
        return max(0, self.total_tracks_queued - self.total_tracks_completed - self.total_tracks_failed)


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

        # Track timing per worker thread for ETA
        self._worker_track_times: dict[int, float] = {}

        # Track playlist track counts for download completion
        self._playlist_track_counts: dict[str, int] = {}

        # Progress bars (created fresh each time)
        self._migration_progress: Optional[Progress] = None
        self._migration_task_id = None

        # Suppress logging to console during UI
        self._log_handler: Optional[logging.Handler] = None

    @staticmethod
    def _format_eta(seconds: Optional[float]) -> str:
        """Format ETA seconds into human-readable string."""
        if seconds is None or seconds <= 0:
            return ""
        if seconds < 60:
            return f"~{int(seconds)}s"
        elif seconds < 3600:
            mins = int(seconds / 60)
            secs = int(seconds % 60)
            return f"~{mins}m {secs}s"
        else:
            hours = int(seconds / 3600)
            mins = int((seconds % 3600) / 60)
            return f"~{hours}h {mins}m"

    def _get_panel_height(self) -> int:
        """Get the panel height based on terminal size."""
        terminal_height = self.console.size.height
        # Use full terminal height minus a small margin for any system chrome
        return max(20, terminal_height - 2)

    def _create_migration_panel(self) -> Panel:
        """Create the migration (left) panel with multi-worker support."""
        content_parts = []
        panel_height = self._get_panel_height()

        # Calculate how many workers we can show based on panel height
        # Each worker takes ~2 lines (progress + track), plus header/stats/recent
        # Header: 2 lines, Stats: 6 lines, Recent header + items: ~7 lines = 15 lines overhead
        available_for_workers = max(2, (panel_height - 18) // 2)
        max_recent = max(3, (panel_height - 15 - available_for_workers * 2) // 1)

        # Header with playlist count and worker info
        header = Text()
        header.append("Playlists: ", style="bold")
        header.append(f"[{self.migration.completed_playlists}/{self.migration.total_playlists}] ", style="cyan")
        if self.migration.num_workers > 1:
            header.append(f"({self.migration.num_workers} workers)", style="dim")
        content_parts.append(header)

        content_parts.append(Text())  # Spacer

        # Show progress for each active worker
        active_workers = list(self.migration.active_workers.values())
        if active_workers:
            # Sort by worker_id for consistent display
            active_workers.sort(key=lambda w: w.worker_id)

            for i, worker in enumerate(active_workers[:available_for_workers]):
                # Progress bar
                if worker.total_tracks > 0:
                    pct = (worker.current_track / worker.total_tracks) * 100
                    bar_width = 15
                    filled = int(bar_width * pct / 100)
                    bar = "█" * filled + "░" * (bar_width - filled)

                    worker_line = Text()
                    worker_line.append(f"{i+1}. ", style="dim")
                    worker_line.append(f"[{bar}] ", style="cyan")
                    worker_line.append(f"{worker.current_track}/{worker.total_tracks} ", style="dim")

                    # Playlist name (truncated)
                    safe_name = safe_text_width(worker.playlist_name, 20)
                    worker_line.append(safe_name, style="white")

                    content_parts.append(worker_line)

                    # Current track on next line
                    if worker.current_track_name:
                        track_line = Text()
                        track_line.append("   -> ", style="dim")
                        safe_track = safe_text_width(worker.current_track_name, 35)
                        track_line.append(safe_track, style="dim italic")
                        content_parts.append(track_line)
                else:
                    worker_line = Text()
                    worker_line.append(f"{i+1}. ", style="dim")
                    safe_name = safe_text_width(worker.playlist_name, 30)
                    worker_line.append(safe_name, style="white")
                    worker_line.append(" (loading...)", style="dim")
                    content_parts.append(worker_line)

            if len(self.migration.active_workers) > available_for_workers:
                content_parts.append(Text(f"   ... +{len(self.migration.active_workers) - available_for_workers} more", style="dim"))
        else:
            content_parts.append(Text("Waiting to start...", style="dim"))

        content_parts.append(Text())  # Spacer

        # Aggregate stats table
        stats = Table.grid(padding=(0, 2))
        stats.add_column(style="bold")
        stats.add_column(justify="right")
        pending = self.migration.get_pending_tracks()
        stats.add_row("[dim]... Pending[/]", str(pending))
        stats.add_row("[green]+ Added[/]", str(self.migration.added))
        stats.add_row("[cyan]o Skipped[/]", str(self.migration.skipped))
        stats.add_row("[red]x Failed[/]", str(self.migration.failed))
        content_parts.append(stats)

        content_parts.append(Text())  # Spacer

        # ETA
        eta = self.migration.get_eta_seconds()
        eta_str = self._format_eta(eta)
        if eta_str:
            eta_text = Text()
            eta_text.append("ETA: ", style="bold dim")
            eta_text.append(eta_str, style="yellow")
            content_parts.append(eta_text)

        content_parts.append(Text())  # Spacer

        # Recent activity - show more items based on available space
        if self.migration.recent_activity:
            content_parts.append(Text("Recent:", style="bold dim"))
            for activity in list(self.migration.recent_activity)[:max_recent]:
                content_parts.append(Text.from_markup(f"  {activity[:40]}"))

        return Panel(
            Group(*content_parts),
            title="[bold cyan]Migration[/]",
            border_style="cyan",
            height=panel_height,
        )

    def _create_download_panel(self) -> Panel:
        """Create the download (right) panel."""
        content_parts = []
        panel_height = self._get_panel_height()

        # Calculate how many recent items to show
        max_recent = max(5, panel_height - 18)

        # Queue status with ETA
        queue_text = Text()
        queue_text.append("Queue: ", style="bold")
        if self.download.pending > 0:
            queue_text.append(f"{self.download.pending} pending", style="yellow")
            eta = self.download.get_eta_seconds()
            eta_str = self._format_eta(eta)
            if eta_str:
                queue_text.append(f" (ETA: {eta_str})", style="dim yellow")
        elif self.download.is_downloading:
            queue_text.append("Processing...", style="green")
        else:
            queue_text.append("Empty", style="dim")
        content_parts.append(queue_text)

        content_parts.append(Text())  # Spacer

        # Current download with playlist and track info
        if self.download.current_playlist_name and self.download.is_downloading:
            current = Text()
            current.append("Downloading: ", style="bold green")
            safe_name = safe_text_width(self.download.current_playlist_name, 30)
            current.append(safe_name, style="white")
            content_parts.append(current)

            # Track progress within playlist
            if self.download.current_playlist_total_tracks > 0:
                track_progress = Text()
                track_progress.append("  Tracks: ", style="dim")
                track_progress.append(
                    f"{self.download.current_track_num}/{self.download.current_playlist_total_tracks}",
                    style="cyan"
                )
                content_parts.append(track_progress)

            # Current track name
            if self.download.current_track_name:
                track_text = Text()
                track_text.append("  -> ", style="dim")
                safe_track = safe_text_width(self.download.current_track_name, 35)
                track_text.append(safe_track, style="dim italic")
                content_parts.append(track_text)
        else:
            content_parts.append(Text("Waiting for playlists...", style="dim"))

        content_parts.append(Text())  # Spacer

        # Stats table - Playlists section
        stats = Table.grid(padding=(0, 2))
        stats.add_column(style="bold")
        stats.add_column(justify="right")
        stats.add_row("[dim]Playlists:[/]", "")
        stats.add_row("[green]  + Completed[/]", str(self.download.completed))
        stats.add_row("[red]  x Failed[/]", str(self.download.failed))
        stats.add_row("[yellow]  ... Pending[/]", str(self.download.pending))
        content_parts.append(stats)

        content_parts.append(Text())  # Spacer

        # Track statistics
        if self.download.total_tracks_queued > 0:
            track_stats = Table.grid(padding=(0, 2))
            track_stats.add_column(style="bold")
            track_stats.add_column(justify="right")
            pending_tracks = self.download.get_pending_tracks()
            track_stats.add_row("[dim]Tracks:[/]", "")
            track_stats.add_row("[green]  + Completed[/]", str(self.download.total_tracks_completed))
            track_stats.add_row("[red]  x Failed[/]", str(self.download.total_tracks_failed))
            track_stats.add_row("[yellow]  ... Pending[/]", str(pending_tracks))
            content_parts.append(track_stats)
            content_parts.append(Text())  # Spacer

        # Recent activity - show more items based on available space
        if self.download.recent_activity:
            content_parts.append(Text("Recent:", style="bold dim"))
            for activity in list(self.download.recent_activity)[:max_recent]:
                content_parts.append(Text.from_markup(f"  {activity[:45]}"))

        return Panel(
            Group(*content_parts),
            title="[bold green]Downloads[/]",
            border_style="green",
            height=panel_height,
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
        import sys

        self._running = True
        self._pending_refresh = False  # Flag for pending refresh requests

        # Suppress console handlers for root and tiddl loggers during UI
        # This prevents log messages from interfering with the Live display
        self._suppressed_handlers = []
        for logger_name in (None, 'tiddl', 'urllib3', 'requests'):
            logger = logging.getLogger(logger_name)
            for handler in logger.handlers[:]:
                if isinstance(handler, logging.StreamHandler):
                    if hasattr(handler, 'stream') and handler.stream in (sys.stdout, sys.stderr, None):
                        self._suppressed_handlers.append((logger, handler))
                        logger.removeHandler(handler)

        self._live = Live(
            self._create_layout(),
            console=self.console,
            refresh_per_second=4,
            transient=False,
            redirect_stdout=True,  # Capture any stray stdout
            redirect_stderr=True,  # Capture any stray stderr
        )
        self._live.start()

    def stop(self):
        """Stop the live UI display."""
        self._running = False
        if self._live:
            self._live.stop()
            self._live = None

        # Restore suppressed log handlers
        if hasattr(self, '_suppressed_handlers'):
            for logger, handler in self._suppressed_handlers:
                logger.addHandler(handler)
            self._suppressed_handlers = []

    def refresh(self, force: bool = False):
        """Refresh the UI display immediately."""
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
    def set_migration_config(self, total_playlists: int, num_workers: int, total_tracks_all: int):
        """Set initial migration configuration."""
        with self._lock:
            self.migration.total_playlists = total_playlists
            self.migration.num_workers = num_workers
            self.migration.total_tracks_all = total_tracks_all
            self.migration.migration_start_time = time.time()
        self.refresh(force=True)

    def start_playlist(self, playlist_num: int, total: int, name: str, track_count: int):
        """Start migrating a new playlist. Uses thread ID as worker identifier."""
        import threading
        worker_id = threading.get_ident()
        current_time = time.time()

        with self._lock:
            self.migration.total_playlists = total

            # Create or update worker progress
            worker = WorkerProgress(
                worker_id=worker_id,
                playlist_num=playlist_num,
                playlist_name=name,
                total_tracks=track_count,
                current_track=0,
                current_track_name="",
                start_time=current_time,
                last_update_time=current_time,
            )
            self.migration.active_workers[worker_id] = worker

            # Track timing for this worker
            if worker_id not in self._worker_track_times:
                self._worker_track_times[worker_id] = current_time
        self.refresh()

    def finish_playlist(self, playlist_num: int = 0):
        """Mark a playlist as finished and remove from active workers."""
        import threading
        worker_id = threading.get_ident()

        with self._lock:
            if worker_id in self.migration.active_workers:
                del self.migration.active_workers[worker_id]
            if worker_id in self._worker_track_times:
                del self._worker_track_times[worker_id]
            self.migration.completed_playlists += 1
        self.refresh()

    def update_track(self, track_num: int, track_name: str):
        """Update current track being processed for this worker."""
        import threading
        worker_id = threading.get_ident()
        current_time = time.time()

        with self._lock:
            worker = self.migration.active_workers.get(worker_id)
            if worker:
                # Record time for previous track (if any)
                prev_time = self._worker_track_times.get(worker_id, 0)
                if worker.current_track > 0 and prev_time > 0:
                    track_duration = current_time - prev_time
                    self.migration.record_track_time(track_duration)

                worker.current_track = track_num
                worker.current_track_name = track_name
                worker.last_update_time = current_time
                self._worker_track_times[worker_id] = current_time
        self.refresh()

    def track_added(self, track_name: str):
        """Record a successfully added track."""
        with self._lock:
            self.migration.added += 1
            self.migration.add_activity("[green]+[/]", track_name[:35])
        self.refresh()

    def track_skipped(self, track_name: str):
        """Record a skipped track."""
        with self._lock:
            self.migration.skipped += 1
            self.migration.add_activity("[cyan]o[/]", track_name[:35])
        self.refresh()

    def track_failed(self, track_name: str, reason: str = ""):
        """Record a failed track."""
        with self._lock:
            self.migration.failed += 1
            msg = f"{track_name[:25]}"
            if reason:
                msg += f" ({reason[:10]})"
            self.migration.add_activity("[red]x[/]", msg)
        self.refresh()

    # Download update methods
    def queue_download(self, playlist_name: str, track_count: int = 0):
        """Queue a playlist for download."""
        with self._lock:
            self.download.total_playlists += 1
            self.download.pending += 1
            self.download.total_tracks_queued += track_count
            # Store track count for this playlist (for use in download_complete)
            self._playlist_track_counts[playlist_name] = track_count
            self.download.add_activity("[dim]...[/]", f"Queued: {playlist_name[:30]}")
        self.refresh()

    def start_download(self, playlist_name: str, track_count: int = 0):
        """Start downloading a playlist."""
        with self._lock:
            self.download.current_playlist_name = playlist_name
            self.download.current_playlist_total_tracks = track_count
            self.download.current_track_num = 0
            self.download.current_track_name = ""
            self.download.is_downloading = True
            self.download.download_start_time = time.time()
            self.download.add_activity("[cyan]>[/]", f"Started: {playlist_name[:30]}")
        self.refresh()

    def update_download_track(self, track_num: int, track_name: str):
        """Update current track being downloaded."""
        with self._lock:
            self.download.current_track_num = track_num
            self.download.current_track_name = track_name
        self.refresh()

    def download_complete(self, playlist_name: str, success: bool, message: str = ""):
        """Record a completed download."""
        with self._lock:
            # Record download time for ETA calculation
            if self.download.download_start_time > 0:
                duration = time.time() - self.download.download_start_time
                self.download.record_playlist_time(duration)

            # Get track count for this playlist
            track_count = self._playlist_track_counts.get(playlist_name, 0)

            self.download.pending = max(0, self.download.pending - 1)
            if success:
                self.download.completed += 1
                self.download.total_tracks_completed += track_count
                self.download.add_activity("[green]+[/]", f"{playlist_name[:30]} ({track_count} tracks)")
            else:
                self.download.failed += 1
                self.download.total_tracks_failed += track_count
                msg = f"{playlist_name[:25]}"
                if message:
                    msg += f" ({message[:15]})"
                self.download.add_activity("[red]x[/]", msg)

            # Clean up stored track count
            if playlist_name in self._playlist_track_counts:
                del self._playlist_track_counts[playlist_name]

            # Clear current if this was the one downloading
            if self.download.current_playlist_name == playlist_name:
                self.download.current_playlist_name = ""
                self.download.current_track_name = ""
                self.download.current_track_num = 0
                self.download.current_playlist_total_tracks = 0
                self.download.is_downloading = self.download.pending > 0
        self.refresh()

    def get_download_callback(self):
        """
        Get a callback function for the PlaylistDownloader on_complete.

        Returns a function with signature:
            callback(playlist_uuid: str, playlist_name: str, success: bool, message: str)
        """
        def callback(playlist_uuid: str, playlist_name: str, success: bool, message: str):
            self.download_complete(playlist_name, success, message)
        return callback

    def get_download_start_callback(self):
        """
        Get a callback function for the PlaylistDownloader on_start.

        Returns a function with signature:
            callback(playlist_uuid: str, playlist_name: str, track_count: int)
        """
        def callback(playlist_uuid: str, playlist_name: str, track_count: int):
            self.start_download(playlist_name, track_count)
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
