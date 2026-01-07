"""
Generate CSV reports for migrated playlists with comprehensive track metadata.
"""

import csv
import json
import subprocess
from dataclasses import dataclass, field, asdict
from logging import getLogger
from pathlib import Path
from typing import Optional

from rich.console import Console

console = Console()
log = getLogger(__name__)


@dataclass
class TrackReport:
    """Complete track report with Spotify origin and Tidal/download status."""

    # Spotify metadata (original source)
    spotify_id: str = ""
    spotify_url: str = ""
    spotify_title: str = ""
    spotify_artist: str = ""
    spotify_album: str = ""
    spotify_duration_ms: int = 0
    spotify_track_number: int = 0
    spotify_isrc: str = ""

    # Migration status
    migration_status: str = ""  # "found", "not_found", "failed_to_add"
    migration_source: str = ""  # "odesli", "tidal_search", "metadata_match"

    # Tidal metadata (if found)
    tidal_id: str = ""
    tidal_url: str = ""
    tidal_title: str = ""
    tidal_artist: str = ""
    tidal_album: str = ""
    tidal_duration_ms: int = 0

    # Download status
    download_status: str = ""  # "downloaded", "skipped", "failed", "not_attempted"
    download_file_path: str = ""

    # Audio file metadata (extracted from downloaded file)
    file_size_bytes: int = 0
    file_format: str = ""  # "flac", "m4a", "mp3", etc.
    codec_name: str = ""  # "flac", "aac", "mp3", etc.
    codec_long_name: str = ""
    sample_rate: int = 0  # Hz
    channels: int = 0  # 2 for stereo, 6 for 5.1, etc.
    channel_layout: str = ""  # "stereo", "5.1", etc.
    bit_depth: int = 0  # 16, 24, etc. (for lossless)
    bitrate_avg: int = 0  # bps
    bitrate_max: int = 0  # bps (if available)
    duration_seconds: float = 0.0


def extract_audio_metadata(file_path: Path) -> dict:
    """
    Extract audio metadata from a file using ffprobe.
    Returns a dict with codec, sample rate, channels, bitrate, etc.
    """
    if not file_path.exists():
        return {}

    try:
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(file_path)
        ]

        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode != 0:
            log.warning(f"ffprobe failed for {file_path}: {result.stderr.decode()}")
            return {}

        data = json.loads(result.stdout.decode())

        # Find the audio stream
        audio_stream = None
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "audio":
                audio_stream = stream
                break

        if not audio_stream:
            return {}

        format_info = data.get("format", {})

        return {
            "file_size_bytes": int(format_info.get("size", 0)),
            "file_format": format_info.get("format_name", "").split(",")[0],
            "codec_name": audio_stream.get("codec_name", ""),
            "codec_long_name": audio_stream.get("codec_long_name", ""),
            "sample_rate": int(audio_stream.get("sample_rate", 0)),
            "channels": int(audio_stream.get("channels", 0)),
            "channel_layout": audio_stream.get("channel_layout", ""),
            "bit_depth": int(audio_stream.get("bits_per_raw_sample", 0) or audio_stream.get("bits_per_sample", 0)),
            "bitrate_avg": int(format_info.get("bit_rate", 0)),
            "bitrate_max": int(audio_stream.get("max_bit_rate", 0) or format_info.get("bit_rate", 0)),
            "duration_seconds": float(format_info.get("duration", 0)),
        }
    except subprocess.TimeoutExpired:
        log.warning(f"ffprobe timed out for {file_path}")
        return {}
    except json.JSONDecodeError as e:
        log.warning(f"Failed to parse ffprobe output for {file_path}: {e}")
        return {}
    except Exception as e:
        log.warning(f"Error extracting metadata from {file_path}: {e}")
        return {}


def find_downloaded_file(
    download_path: Path,
    tidal_id: str,
    tidal_title: str,
    tidal_artist: str,
) -> Optional[Path]:
    """
    Try to find the downloaded file for a Tidal track.
    This searches the download directory for files matching the track.
    """
    if not download_path.exists():
        return None

    # Common audio extensions
    extensions = [".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"]

    # Strategy 1: Search for files containing the track title
    # Normalize the title for matching
    title_normalized = tidal_title.lower().replace(" ", "").replace("-", "").replace("_", "")

    for ext in extensions:
        for file_path in download_path.rglob(f"*{ext}"):
            file_name = file_path.stem.lower().replace(" ", "").replace("-", "").replace("_", "")
            # Check if title is in filename
            if title_normalized and title_normalized in file_name:
                return file_path

    return None


def create_track_report_from_spotify(spotify_track: dict) -> TrackReport:
    """Create a TrackReport from a Spotify track dict."""
    artists = ", ".join([a["name"] for a in spotify_track.get("artists", [])])
    album = spotify_track.get("album", {}).get("name", "") if spotify_track.get("album") else ""

    return TrackReport(
        spotify_id=spotify_track.get("id", ""),
        spotify_url=f"https://open.spotify.com/track/{spotify_track.get('id', '')}",
        spotify_title=spotify_track.get("name", ""),
        spotify_artist=artists,
        spotify_album=album,
        spotify_duration_ms=spotify_track.get("duration_ms", 0),
        spotify_track_number=spotify_track.get("track_number", 0),
        spotify_isrc=spotify_track.get("external_ids", {}).get("isrc", ""),
        migration_status="pending",
        download_status="not_attempted",
    )


def write_playlist_csv(
    log_dir: Path,
    playlist_name: str,
    tracks: list[TrackReport],
):
    """
    Write a CSV report for a playlist.
    """
    # Sanitize playlist name for filename
    safe_name = "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in playlist_name)
    safe_name = safe_name.strip().replace(" ", "-")[:100]

    csv_file = log_dir / f"pl-{safe_name}.csv"

    if not tracks:
        log.warning(f"No tracks to write for playlist {playlist_name}")
        return

    # Get field names from dataclass
    fieldnames = list(asdict(tracks[0]).keys())

    try:
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for track in tracks:
                writer.writerow(asdict(track))

        log.debug(f"Wrote CSV report to {csv_file}")
        console.print(f"  [dim]CSV report: {csv_file}[/]")
    except Exception as e:
        log.error(f"Failed to write CSV report {csv_file}: {e}")
        console.print(f"  [yellow]Warning: Failed to write CSV: {e}[/]")


def update_track_with_download_metadata(
    track: TrackReport,
    download_path: Path,
) -> TrackReport:
    """
    Update a track report with metadata from the downloaded file.
    """
    if not track.tidal_id or track.download_status != "downloaded":
        return track

    # Try to find the downloaded file
    file_path = find_downloaded_file(
        download_path=download_path,
        tidal_id=track.tidal_id,
        tidal_title=track.tidal_title,
        tidal_artist=track.tidal_artist,
    )

    if file_path:
        track.download_file_path = str(file_path)
        metadata = extract_audio_metadata(file_path)

        if metadata:
            track.file_size_bytes = metadata.get("file_size_bytes", 0)
            track.file_format = metadata.get("file_format", "")
            track.codec_name = metadata.get("codec_name", "")
            track.codec_long_name = metadata.get("codec_long_name", "")
            track.sample_rate = metadata.get("sample_rate", 0)
            track.channels = metadata.get("channels", 0)
            track.channel_layout = metadata.get("channel_layout", "")
            track.bit_depth = metadata.get("bit_depth", 0)
            track.bitrate_avg = metadata.get("bitrate_avg", 0)
            track.bitrate_max = metadata.get("bitrate_max", 0)
            track.duration_seconds = metadata.get("duration_seconds", 0.0)

    return track


class PlaylistReportCollector:
    """
    Collects track reports during migration for later CSV generation.
    """

    def __init__(self, log_dir: Path, download_path: Path):
        self.log_dir = log_dir
        self.download_path = download_path
        self._playlists: dict[str, list[TrackReport]] = {}  # playlist_name -> tracks
        self._playlist_uuids: dict[str, str] = {}  # playlist_name -> tidal_uuid

    def start_playlist(self, playlist_name: str, tidal_uuid: str = ""):
        """Start collecting tracks for a new playlist."""
        self._playlists[playlist_name] = []
        self._playlist_uuids[playlist_name] = tidal_uuid

    def add_track(
        self,
        playlist_name: str,
        spotify_track: dict,
        tidal_id: Optional[str] = None,
        tidal_info: Optional[dict] = None,
        migration_status: str = "pending",
        migration_source: str = "",
    ):
        """Add a track to the playlist report."""
        if playlist_name not in self._playlists:
            self._playlists[playlist_name] = []

        track = create_track_report_from_spotify(spotify_track)
        track.migration_status = migration_status
        track.migration_source = migration_source

        if tidal_id:
            track.tidal_id = tidal_id
            track.tidal_url = f"https://listen.tidal.com/track/{tidal_id}"

        if tidal_info:
            track.tidal_title = tidal_info.get("title", "")
            track.tidal_artist = tidal_info.get("artist", "")
            track.tidal_album = tidal_info.get("album", "")
            track.tidal_duration_ms = tidal_info.get("duration_ms", 0)

        self._playlists[playlist_name].append(track)

    def mark_playlist_downloaded(self, playlist_name: str, success: bool):
        """Mark all tracks in a playlist as downloaded or failed."""
        if playlist_name not in self._playlists:
            return

        status = "downloaded" if success else "failed"
        for track in self._playlists[playlist_name]:
            if track.migration_status == "found" or track.migration_status == "added":
                track.download_status = status

    def finalize_and_write_reports(self, scan_downloads: bool = True):
        """
        Finalize all playlist reports and write CSVs.
        If scan_downloads is True, extract metadata from downloaded files.
        """
        console.print("\n[cyan]Generating CSV reports...[/]")

        for playlist_name, tracks in self._playlists.items():
            if not tracks:
                continue

            # Optionally scan for downloaded files and extract metadata
            if scan_downloads:
                for i, track in enumerate(tracks):
                    if track.download_status == "downloaded" and track.tidal_id:
                        tracks[i] = update_track_with_download_metadata(track, self.download_path)

            # Write CSV
            write_playlist_csv(self.log_dir, playlist_name, tracks)

        console.print(f"[green]Generated {len(self._playlists)} CSV report(s) in {self.log_dir}[/]")

    @property
    def playlist_names(self) -> list[str]:
        """Get list of playlist names being tracked."""
        return list(self._playlists.keys())
