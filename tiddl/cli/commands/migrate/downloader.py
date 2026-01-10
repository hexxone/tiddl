import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, Future
from logging import getLogger
from typing import Callable, Optional

log = getLogger(__name__)


def _get_tiddl_command() -> list[str]:
    """Get the command to run tiddl CLI."""
    # Try to find tiddl executable in PATH
    tiddl_path = shutil.which("tiddl")
    if tiddl_path:
        return [tiddl_path]
    # Fallback to running via Python module (requires __main__.py)
    return [sys.executable, "-m", "tiddl"]


class PlaylistDownloader:
    """Downloads migrated playlists either in parallel (as they complete) or at the end."""

    def __init__(
        self,
        enabled: bool = True,
        parallel: bool = True,
        max_workers: int = 2,
        skip_errors: bool = True,
        on_complete: Optional[Callable[[str, str, bool, str], None]] = None,
    ):
        """
        Initialize the playlist downloader.

        Args:
            enabled: Whether downloading is enabled
            parallel: If True, download playlists as they complete; if False, queue for end
            max_workers: Maximum concurrent playlist downloads (only used if parallel=True)
            skip_errors: If True, pass --skip-errors to tiddl download to skip unavailable tracks
            on_complete: Callback(playlist_uuid, playlist_name, success, message) called when a download finishes
        """
        self.enabled = enabled
        self.parallel = parallel
        self.skip_errors = skip_errors
        self.on_complete = on_complete
        self._executor: Optional[ThreadPoolExecutor] = None
        self._futures: list[Future] = []
        self._queued_playlists: list[tuple[str, str, int]] = []  # (uuid, name, track_count) for sequential mode
        self._playlist_names: dict[str, str] = {}  # uuid -> name mapping
        self._completed: int = 0
        self._failed: int = 0
        self._failed_playlists: list[tuple[str, str, str]] = []  # (uuid, name, error_message)
        self._lock = threading.Lock()

        if enabled and parallel:
            self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def add_playlist(self, playlist_uuid: str, playlist_name: str = "Unknown", track_count: int = 0):
        """Queue a playlist for download."""
        if not self.enabled:
            return

        self._playlist_names[playlist_uuid] = playlist_name

        if self.parallel and self._executor:
            # Start downloading immediately in background
            future = self._executor.submit(self._download_playlist, playlist_uuid, playlist_name, track_count)
            self._futures.append(future)
        else:
            # Queue for later
            self._queued_playlists.append((playlist_uuid, playlist_name, track_count))

    def _download_playlist(self, playlist_uuid: str, playlist_name: str, track_count: int = 0) -> tuple[str, str, bool, str]:
        """Download a single playlist using tiddl CLI. Returns (uuid, name, success, message)."""
        try:
            log.debug(f"Starting download for playlist {playlist_name} ({playlist_uuid})")
            # Build command: tiddl download [options] url <playlist>
            # Options like --skip-errors must come BEFORE the 'url' subcommand
            cmd = _get_tiddl_command() + ["download"]
            if self.skip_errors:
                cmd.append("--skip-errors")
            cmd.extend(["url", f"playlist/{playlist_uuid}"])
            log.debug(f"Running command: {cmd}")

            # Dynamic timeout: 30 seconds per track, minimum 10 minutes, no maximum
            # This accounts for large playlists (e.g., 2000 tracks = ~16 hours)
            if track_count > 0:
                timeout_seconds = max(600, track_count * 30)  # 30 sec/track, min 10 min
            else:
                timeout_seconds = 7200  # Default 2 hours if track count unknown

            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout_seconds,
            )

            stdout = result.stdout.decode(errors="replace")
            stderr = result.stderr.decode(errors="replace")

            with self._lock:
                if result.returncode == 0:
                    self._completed += 1
                    message = "Download completed"
                    success = True
                else:
                    self._failed += 1
                    # Extract a cleaner error message from both stdout and stderr
                    all_output = f"{stdout}\n{stderr}"
                    error_lines = [l.strip() for l in all_output.split('\n') if l.strip() and 'error' in l.lower()]
                    if error_lines:
                        message = error_lines[0][:200]
                    elif stderr.strip():
                        message = stderr.strip()[:200]
                    elif stdout.strip():
                        message = stdout.strip()[:200]
                    else:
                        message = f"Exit code {result.returncode}"
                    success = False
                    self._failed_playlists.append((playlist_uuid, playlist_name, message))
                    log.warning(f"Playlist download failed for {playlist_name}: {message}")

            if self.on_complete:
                self.on_complete(playlist_uuid, playlist_name, success, message)

            return playlist_uuid, playlist_name, success, message

        except subprocess.TimeoutExpired:
            with self._lock:
                self._failed += 1
                message = f"Download timed out (track_count={track_count})"
                self._failed_playlists.append((playlist_uuid, playlist_name, message))
            log.warning(f"Playlist download timeout for {playlist_name}")
            if self.on_complete:
                self.on_complete(playlist_uuid, playlist_name, False, message)
            return playlist_uuid, playlist_name, False, message

        except Exception as e:
            with self._lock:
                self._failed += 1
                message = f"Download error: {e}"
                self._failed_playlists.append((playlist_uuid, playlist_name, message))
            log.error(f"Playlist download error for {playlist_name}: {e}")
            if self.on_complete:
                self.on_complete(playlist_uuid, playlist_name, False, message)
            return playlist_uuid, playlist_name, False, message

    def download_queued(self) -> list[tuple[str, str, bool, str]]:
        """
        Download all queued playlists sequentially.
        Only used when parallel=False. Returns list of (uuid, name, success, message).
        """
        if not self.enabled or self.parallel:
            return []

        results = []
        for playlist_uuid, playlist_name, track_count in self._queued_playlists:
            result = self._download_playlist(playlist_uuid, playlist_name, track_count)
            results.append(result)
        self._queued_playlists.clear()
        return results

    def wait_for_completion(self) -> list[tuple[str, str, bool, str]]:
        """Wait for all parallel downloads to complete. Returns list of (uuid, name, success, message)."""
        if not self.enabled or not self.parallel:
            return []

        results = []
        for future in self._futures:
            try:
                # No timeout here - let the subprocess timeout handle it
                result = future.result(timeout=None)
                results.append(result)
            except Exception as e:
                log.error(f"Future error: {e}")
                results.append(("unknown", "Unknown", False, str(e)))

        self._futures.clear()
        return results

    def shutdown(self):
        """Shutdown the executor."""
        if self._executor:
            self._executor.shutdown(wait=False)
            self._executor = None

    @property
    def stats(self) -> tuple[int, int, int]:
        """Return (completed, failed, pending) counts."""
        pending = len(self._futures) + len(self._queued_playlists)
        return self._completed, self._failed, pending

    @property
    def queued_count(self) -> int:
        """Return number of playlists queued for download."""
        return len(self._queued_playlists) + len(self._futures)

    @property
    def failed_playlists(self) -> list[tuple[str, str, str]]:
        """Return list of failed playlists as (uuid, name, error_message)."""
        return self._failed_playlists.copy()


# Keep legacy class for backwards compatibility (but it's now unused)
class BackgroundDownloader:
    """Legacy background downloader - deprecated, use PlaylistDownloader instead."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.downloaded = 0
        self.failed = 0

    def start(self):
        pass

    def add_track(self, track_id: str):
        pass

    def stop(self):
        pass

    def wait_for_completion(self):
        pass

    @property
    def stats(self) -> tuple[int, int, int]:
        return self.downloaded, self.failed, 0
