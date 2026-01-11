"""
Metadata enrichment service.

Fetches additional metadata from external APIs:
- MusicBrainz: genres, tags
- GetSongBPM: BPM, musical key, Camelot key
"""

from dataclasses import dataclass, field
from logging import getLogger
from typing import Optional

from tiddl.core.musicbrainz.client import MusicBrainzClient
from tiddl.core.getsongbpm.client import GetSongBPMClient, create_client as create_getsongbpm_client

log = getLogger(__name__)


@dataclass
class EnrichedMetadata:
    """Enriched metadata from external sources."""
    # From GetSongBPM
    bpm: Optional[int] = None
    key: str = ""  # Musical key (e.g., "Am", "C#", "F")
    key_camelot: str = ""  # Camelot notation (e.g., "8B", "5A")
    time_signature: str = ""

    # From MusicBrainz
    genres: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    # Combined mood (from tags)
    mood: str = ""


# Common mood-related tags to extract
MOOD_TAGS = {
    "happy", "sad", "melancholic", "uplifting", "dark", "aggressive",
    "calm", "relaxing", "energetic", "romantic", "dreamy", "nostalgic",
    "intense", "peaceful", "euphoric", "mellow", "groovy", "hypnotic",
    "atmospheric", "emotional", "powerful", "gentle", "driving",
}


class MetadataEnrichmentService:
    """Service for enriching track metadata from external APIs."""

    def __init__(
        self,
        use_musicbrainz: bool = True,
        use_getsongbpm: bool = True,
    ):
        """
        Initialize the enrichment service.

        Args:
            use_musicbrainz: Whether to fetch from MusicBrainz
            use_getsongbpm: Whether to fetch from GetSongBPM (requires API key)
        """
        self._musicbrainz: Optional[MusicBrainzClient] = None
        self._getsongbpm: Optional[GetSongBPMClient] = None

        if use_musicbrainz:
            self._musicbrainz = MusicBrainzClient()
            log.debug("MusicBrainz client initialized")

        if use_getsongbpm:
            self._getsongbpm = create_getsongbpm_client()
            if self._getsongbpm:
                log.debug("GetSongBPM client initialized")
            else:
                log.debug("GetSongBPM not configured (no API key)")

    def enrich_track(
        self,
        title: str,
        artist: str,
        isrc: Optional[str] = None,
        duration_ms: Optional[int] = None,
        tidal_bpm: Optional[int] = None,
    ) -> EnrichedMetadata:
        """
        Enrich track with metadata from external APIs.

        Args:
            title: Track title
            artist: Primary artist name
            isrc: ISRC code (for MusicBrainz lookup)
            duration_ms: Track duration in milliseconds
            tidal_bpm: BPM from Tidal API (used as fallback)

        Returns:
            EnrichedMetadata with data from all sources
        """
        result = EnrichedMetadata()

        # Use Tidal BPM as default
        if tidal_bpm:
            result.bpm = tidal_bpm

        # Fetch from MusicBrainz (genres/tags)
        if self._musicbrainz:
            try:
                mb_info = None

                # Try ISRC lookup first (more accurate)
                if isrc:
                    mb_info = self._musicbrainz.lookup_by_isrc(isrc)

                # Fall back to search
                if not mb_info:
                    mb_info = self._musicbrainz.search_track(
                        title=title,
                        artist=artist,
                        duration_ms=duration_ms,
                    )

                if mb_info:
                    result.genres = mb_info.genres
                    result.tags = mb_info.tags

                    # Extract mood from tags
                    for tag in mb_info.tags:
                        tag_lower = tag.lower()
                        if tag_lower in MOOD_TAGS:
                            result.mood = tag.title()
                            break

                    log.debug(f"MusicBrainz: {title} - genres={result.genres}, tags={result.tags[:3]}")

            except Exception as e:
                log.warning(f"MusicBrainz error for '{title}': {e}")

        # Fetch from GetSongBPM (key/BPM)
        if self._getsongbpm:
            try:
                bpm_info = self._getsongbpm.search_track(title=title, artist=artist)

                if bpm_info:
                    # Use GetSongBPM's BPM if we don't have one from Tidal
                    if not result.bpm and bpm_info.bpm:
                        result.bpm = bpm_info.bpm

                    result.key = bpm_info.key
                    result.key_camelot = bpm_info.key_camelot
                    result.time_signature = bpm_info.time_signature

                    # GetSongBPM also provides genres from artist
                    if bpm_info.genres and not result.genres:
                        result.genres = bpm_info.genres

                    log.debug(f"GetSongBPM: {title} - key={result.key}, camelot={result.key_camelot}, bpm={bpm_info.bpm}")

            except Exception as e:
                log.warning(f"GetSongBPM error for '{title}': {e}")

        return result


# Singleton instance for reuse
_service_instance: Optional[MetadataEnrichmentService] = None


def get_enrichment_service(
    use_musicbrainz: bool = True,
    use_getsongbpm: bool = True,
) -> MetadataEnrichmentService:
    """
    Get or create the metadata enrichment service.

    This returns a cached singleton instance for efficiency.
    """
    global _service_instance

    if _service_instance is None:
        _service_instance = MetadataEnrichmentService(
            use_musicbrainz=use_musicbrainz,
            use_getsongbpm=use_getsongbpm,
        )

    return _service_instance


def reset_enrichment_service():
    """Reset the cached service instance."""
    global _service_instance
    _service_instance = None
