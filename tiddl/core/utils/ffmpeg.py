import json
import subprocess
from logging import getLogger
from pathlib import Path
from typing import Optional

log = getLogger(__name__)


def run(cmd: list[str]):
    """Run process without printing to terminal"""
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def is_ffmpeg_installed() -> bool:
    """Checks if `ffmpeg` is installed."""

    try:
        run(["ffmpeg", "-version"])
        return True
    except FileNotFoundError:
        return False


def convert_to_mp4(source: Path) -> Path:
    output_path = source.with_suffix(".mp4")

    run(["ffmpeg", "-y", "-i", str(source), "-c", "copy", str(output_path)])

    source.unlink()

    return output_path


def extract_flac(source: Path) -> Path:
    """
    Extracts flac audio from mp4 container
    """

    tmp = source.with_suffix(".tmp.flac")

    run(["ffmpeg", "-y", "-i", str(source), "-c", "copy", str(tmp)])

    tmp.replace(source.with_suffix(".flac"))

    return source.with_suffix(".flac")


def verify_audio_file(
    file_path: Path,
    expected_duration_sec: Optional[float] = None,
    duration_tolerance_sec: float = 5.0,
    min_duration_sec: float = 1.0,
) -> tuple[bool, str]:
    """
    Verify audio file integrity using ffprobe.

    Checks:
    1. File exists and has content
    2. ffprobe can parse the file
    3. File contains at least one audio stream
    4. Duration is reasonable (not 0 or too short)
    5. If expected_duration provided, actual duration is within tolerance

    Args:
        file_path: Path to the audio file
        expected_duration_sec: Expected duration in seconds (optional)
        duration_tolerance_sec: Allowed difference from expected duration (default 5s)
        min_duration_sec: Minimum acceptable duration (default 1s)

    Returns:
        (is_valid, error_message) - error_message is empty if valid
    """
    if not file_path.exists():
        return False, "File does not exist"

    # Check file has content
    file_size = file_path.stat().st_size
    if file_size == 0:
        return False, "File is empty (0 bytes)"

    # Very small files are likely corrupted (less than 1KB)
    if file_size < 1024:
        return False, f"File too small ({file_size} bytes)"

    try:
        cmd = [
            "ffprobe",
            "-v", "error",  # Only show errors
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(file_path)
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=30,
        )

        # Check if ffprobe reported errors
        stderr = result.stderr.decode(errors="replace").strip()
        if result.returncode != 0:
            error_msg = stderr[:200] if stderr else f"ffprobe exit code {result.returncode}"
            return False, f"ffprobe error: {error_msg}"

        # Parse output
        try:
            data = json.loads(result.stdout.decode())
        except json.JSONDecodeError as e:
            return False, f"Failed to parse ffprobe output: {e}"

        # Check for audio stream
        streams = data.get("streams", [])
        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

        if not audio_streams:
            return False, "No audio stream found"

        # Check format info
        format_info = data.get("format", {})

        # Check duration
        try:
            duration = float(format_info.get("duration", 0))
        except (TypeError, ValueError):
            duration = 0

        if duration < min_duration_sec:
            return False, f"Duration too short ({duration:.1f}s < {min_duration_sec}s)"

        # Check against expected duration if provided
        if expected_duration_sec is not None and expected_duration_sec > 0:
            duration_diff = abs(duration - expected_duration_sec)
            if duration_diff > duration_tolerance_sec:
                return False, (
                    f"Duration mismatch: expected {expected_duration_sec:.1f}s, "
                    f"got {duration:.1f}s (diff: {duration_diff:.1f}s)"
                )

        # Check audio stream has valid codec
        audio_stream = audio_streams[0]
        codec_name = audio_stream.get("codec_name", "")
        if not codec_name:
            return False, "Audio stream has no codec"

        # All checks passed
        return True, ""

    except subprocess.TimeoutExpired:
        return False, "ffprobe timed out"
    except FileNotFoundError:
        return False, "ffprobe not installed"
    except Exception as e:
        return False, f"Verification error: {e}"


def is_ffprobe_installed() -> bool:
    """Checks if `ffprobe` is installed."""
    try:
        run(["ffprobe", "-version"])
        return True
    except FileNotFoundError:
        return False
