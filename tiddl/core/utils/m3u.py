import os
from logging import getLogger
from pathlib import Path
from typing import Optional

from tiddl.core.api.models import Track

log = getLogger(__name__)


def save_tracks_to_m3u(
    tracks_with_path: list[tuple[Path, Track]],
    path: Path,
    owner: Optional[str] = None,
    use_relative_paths: bool = True,
):
    """
    Save tracks to an M3U playlist file.

    Args:
        tracks_with_path: List of (track_path, Track) tuples
        path: Base path for the m3u file (without owner subfolder)
        owner: Optional owner name to create subfolder (e.g., "m3u/<owner>/<playlist>.m3u")
        use_relative_paths: If True, use paths relative to the m3u file location (recommended)
    """
    # Build the final path with optional owner subfolder
    if owner:
        # Sanitize owner name for filesystem
        safe_owner = sanitize_for_path(owner)
        # Insert owner before the filename: path/to/playlist.m3u -> path/to/<owner>/playlist.m3u
        file = path.parent / safe_owner / path.name
    else:
        file = path

    file = file.with_suffix(".m3u")
    log.debug(f"{path=}, {owner=}, {file=}")

    if not tracks_with_path:
        log.warning(f"can't save '{file}', no tracks")
        return

    try:
        file.parent.mkdir(parents=True, exist_ok=True)

        with file.open("w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for track_path, track in tracks_with_path:
                # Convert to relative path if requested
                if use_relative_paths and track_path:
                    try:
                        # Make path relative to the m3u file's directory
                        relative_path = os.path.relpath(track_path, file.parent)
                        display_path = relative_path
                    except ValueError:
                        # Can happen on Windows with different drives
                        display_path = str(track_path)
                else:
                    display_path = str(track_path) if track_path else ""

                artist_name = track.artist.name if track.artist else ""
                f.write(f"#EXTINF:{track.duration},{artist_name} - {track.title}\n{display_path}\n")

            log.debug(f"saved m3u file as '{file}' with {len(tracks_with_path)} tracks")

    except Exception as e:
        log.error(f"can't save m3u file: {e}")

    return file


def sanitize_for_path(name: str) -> str:
    """Sanitize a name for use in file paths."""
    if not name:
        return "Unknown"
    # Remove/replace characters that are problematic in paths
    invalid_chars = '<>:"/\\|?*'
    result = name
    for char in invalid_chars:
        result = result.replace(char, '_')
    # Collapse multiple underscores/spaces
    while '__' in result:
        result = result.replace('__', '_')
    while '  ' in result:
        result = result.replace('  ', ' ')
    return result.strip().strip('_') or "Unknown"


def regenerate_m3u_with_relative_paths(
    m3u_path: Path,
    tracks_base_path: Optional[Path] = None,
) -> bool:
    """
    Regenerate an existing M3U file with relative paths.

    This reads an existing M3U file and rewrites it using paths relative
    to the M3U file's location.

    Args:
        m3u_path: Path to the existing M3U file
        tracks_base_path: Optional base path to resolve relative track paths against.
                         If not provided, assumes tracks are at absolute paths.

    Returns:
        True if successful, False otherwise
    """
    if not m3u_path.exists():
        log.warning(f"M3U file does not exist: {m3u_path}")
        return False

    try:
        # Read existing M3U
        lines = m3u_path.read_text(encoding="utf-8").splitlines()

        new_lines = []
        i = 0
        while i < len(lines):
            line = lines[i]

            if line.startswith("#EXTM3U") or line.startswith("#EXTINF:"):
                new_lines.append(line)
                i += 1
                # Next line should be the path
                if i < len(lines) and not lines[i].startswith("#"):
                    track_path_str = lines[i].strip()
                    if track_path_str:
                        track_path = Path(track_path_str)

                        # If it's a relative path and we have a base path, resolve it
                        if not track_path.is_absolute() and tracks_base_path:
                            track_path = tracks_base_path / track_path

                        # Convert to relative path from m3u location
                        if track_path.is_absolute() or tracks_base_path:
                            try:
                                relative_path = os.path.relpath(track_path, m3u_path.parent)
                                new_lines.append(relative_path)
                            except ValueError:
                                new_lines.append(track_path_str)
                        else:
                            new_lines.append(track_path_str)
                    else:
                        new_lines.append(track_path_str)
                    i += 1
            elif line.strip():
                # Non-empty line that's not a directive
                new_lines.append(line)
                i += 1
            else:
                i += 1

        # Write back
        m3u_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        log.debug(f"Regenerated M3U with relative paths: {m3u_path}")
        return True

    except Exception as e:
        log.error(f"Failed to regenerate M3U file {m3u_path}: {e}")
        return False
