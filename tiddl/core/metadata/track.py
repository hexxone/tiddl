from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime

from mutagen.flac import FLAC as MutagenFLAC, Picture
from mutagen.easymp4 import EasyMP4 as MutagenEasyMP4
from mutagen.mp4 import MP4 as MutagenMP4, MP4Cover

from tiddl.core.api.models import AlbumItemsCredits, Track


@dataclass(slots=True)
class Metadata:
    title: str
    track_number: str
    disc_number: str
    copyright: str | None
    album_artist: str
    artists: str
    album_title: str
    date: str
    isrc: str
    bpm: str | None = None
    key: str | None = None  # Musical key (e.g., "Am", "C#", "F")
    key_camelot: str | None = None  # Camelot notation (e.g., "8B", "5A")
    genres: list[str] = field(default_factory=list)  # Genre tags
    mood: str | None = None  # Mood tag
    lyrics: str | None = None
    credits: list[AlbumItemsCredits.ItemWithCredits.CreditsEntry] = field(
        default_factory=list
    )
    cover_data: bytes | None = None
    comment: str = ""


def add_flac_metadata(track_path: Path, metadata: Metadata) -> None:
    mutagen = MutagenFLAC(track_path)

    if metadata.cover_data:
        picture = Picture()
        picture.data = metadata.cover_data
        picture.mime = "image/jpeg"
        picture.type = 3  # front cover
        mutagen.add_picture(picture)

    if metadata.date:
        date = datetime.fromisoformat(metadata.date)
    else:
        date = None

    mutagen.update(
        {
            "TITLE": metadata.title,
            "TRACKNUMBER": metadata.track_number,
            "DISCNUMBER": metadata.disc_number,
            "ALBUM": metadata.album_title,
            "ALBUMARTIST": metadata.album_artist,
            "ARTIST": metadata.artists,
            "DATE": str(date) if date else "",
            "YEAR": (str(date.year) if date else ""),
            "COPYRIGHT": metadata.copyright or "",
            "ISRC": metadata.isrc,
            "COMMENT": metadata.comment,
        }
    )

    # BPM
    if metadata.bpm:
        mutagen["BPM"] = metadata.bpm

    # Musical key (INITIALKEY is the standard Vorbis comment for key)
    if metadata.key:
        mutagen["INITIALKEY"] = metadata.key
    if metadata.key_camelot:
        mutagen["KEY"] = metadata.key_camelot  # Camelot notation

    # Genres (GENRE is standard, can have multiple values)
    if metadata.genres:
        mutagen["GENRE"] = metadata.genres

    # Mood
    if metadata.mood:
        mutagen["MOOD"] = metadata.mood

    # Lyrics
    if metadata.lyrics:
        mutagen["LYRICS"] = metadata.lyrics

    # Credits
    for entry in metadata.credits:
        mutagen[entry.type.upper()] = [c.name for c in entry.contributors]

    mutagen.save()


def add_m4a_metadata(track_path: Path, metadata: Metadata) -> None:
    mutagen = MutagenMP4(track_path)

    if metadata.cover_data:
        mutagen["covr"] = [
            MP4Cover(metadata.cover_data, imageformat=MP4Cover.FORMAT_JPEG)
        ]

    if metadata.lyrics:
        mutagen["\xa9lyr"] = [metadata.lyrics]

    mutagen.save()

    mutagen = MutagenEasyMP4(track_path)

    mutagen.update(
        {
            "title": metadata.title,
            "tracknumber": metadata.track_number,
            "discnumber": metadata.disc_number,
            "album": metadata.album_title,
            "albumartist": metadata.album_artist,
            "artist": metadata.artists,
            "date": metadata.date,
            "copyright": metadata.copyright or "",
            "comment": metadata.comment,
        }
    )

    # BPM
    if metadata.bpm:
        mutagen["bpm"] = metadata.bpm

    # Genre (EasyMP4 supports genre)
    if metadata.genres:
        mutagen["genre"] = metadata.genres[0] if metadata.genres else ""

    mutagen.save()

    # For M4A, some tags need to be set directly on MP4
    # Key and additional genres aren't standard in iTunes tags
    # but we can add them as freeform atoms
    mutagen = MutagenMP4(track_path)

    # Musical key (using freeform atom)
    if metadata.key:
        mutagen["----:com.apple.iTunes:INITIALKEY"] = [metadata.key.encode("utf-8")]
    if metadata.key_camelot:
        mutagen["----:com.apple.iTunes:KEY"] = [metadata.key_camelot.encode("utf-8")]

    # Mood (freeform atom)
    if metadata.mood:
        mutagen["----:com.apple.iTunes:MOOD"] = [metadata.mood.encode("utf-8")]

    mutagen.save()


def sort_credits_contributors(
    entries: list[AlbumItemsCredits.ItemWithCredits.CreditsEntry],
):
    """
    Sorts the contributors within each CreditsEntry alphabetically by surname.

    It assumes the surname is the last word in the contributor's name.
    """

    def get_surname(name: str) -> str:
        parts = name.split()
        return parts[-1] if parts else ""

    for entry in entries:
        entry.contributors.sort(
            key=lambda contributor: get_surname(contributor.name).lower()
        )


def add_track_metadata(
    path: Path,
    track: Track,
    date: str = "",
    album_artist: str = "",
    lyrics: str = "",
    cover_data: bytes | None = None,
    credits_contributors: (
        list[AlbumItemsCredits.ItemWithCredits.CreditsEntry] | None
    ) = None,
    comment: str = "",
    key: str | None = None,
    key_camelot: str | None = None,
    genres: list[str] | None = None,
    mood: str | None = None,
) -> None:
    """Add FLAC or M4A metadata based on file extension."""

    if credits_contributors is None:
        credits_contributors = []

    if genres is None:
        genres = []

    sort_credits_contributors(credits_contributors)

    metadata = Metadata(
        title=f"{track.title} ({track.version})" if track.version else track.title,
        track_number=str(track.trackNumber),
        disc_number=str(track.volumeNumber),
        copyright=track.copyright,
        album_artist=album_artist,
        artists=", ".join(sorted(a.name.strip() for a in track.artists)),
        album_title=track.album.title,
        date=date,
        isrc=track.isrc,
        bpm=str(track.bpm or ""),
        key=key,
        key_camelot=key_camelot,
        genres=genres,
        mood=mood,
        lyrics=lyrics or None,
        cover_data=cover_data,
        credits=credits_contributors,
        comment=comment,
    )

    ext = path.suffix.lower()

    if ext == ".flac":
        add_flac_metadata(path, metadata)
    elif ext == ".m4a":
        add_m4a_metadata(path, metadata)
    else:
        raise ValueError(f"Unsupported file extension: {ext}")
