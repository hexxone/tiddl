"""
MusicBrainz API client for fetching genres and tags.

MusicBrainz is a free, open music encyclopedia that provides metadata
including genres, tags, and relationships between artists/tracks/albums.

API documentation: https://musicbrainz.org/doc/MusicBrainz_API
Rate limit: 1 request per second
"""

import time
from dataclasses import dataclass, field
from logging import getLogger
from typing import Optional
import requests

log = getLogger(__name__)

# MusicBrainz API requires a descriptive User-Agent
USER_AGENT = "tiddl/1.0 (https://github.com/oskvr37/tiddl)"

# Rate limit: 1 request per second
RATE_LIMIT_SECONDS = 1.0


@dataclass
class MusicBrainzTrackInfo:
    """Track information from MusicBrainz."""
    mbid: str = ""  # MusicBrainz Recording ID
    title: str = ""
    artist: str = ""
    genres: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)  # User-submitted tags (may include genres)


class MusicBrainzClient:
    """Client for MusicBrainz API."""

    BASE_URL = "https://musicbrainz.org/ws/2"

    def __init__(self):
        self._last_request_time: float = 0
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        })

    def _rate_limit(self):
        """Ensure we don't exceed the rate limit."""
        elapsed = time.time() - self._last_request_time
        if elapsed < RATE_LIMIT_SECONDS:
            sleep_time = RATE_LIMIT_SECONDS - elapsed
            log.debug(f"Rate limiting: sleeping {sleep_time:.2f}s")
            time.sleep(sleep_time)
        self._last_request_time = time.time()

    def _request(self, endpoint: str, params: Optional[dict] = None) -> Optional[dict]:
        """Make a rate-limited request to MusicBrainz API."""
        self._rate_limit()

        url = f"{self.BASE_URL}/{endpoint}"
        params = params or {}
        params["fmt"] = "json"

        try:
            response = self._session.get(url, params=params, timeout=10)

            if response.status_code == 404:
                log.debug(f"Not found: {url}")
                return None

            if response.status_code == 503:
                log.warning("MusicBrainz rate limit exceeded, waiting...")
                time.sleep(5)
                return self._request(endpoint, params)

            response.raise_for_status()
            return response.json()

        except requests.RequestException as e:
            log.warning(f"MusicBrainz API error: {e}")
            return None

    def lookup_by_isrc(self, isrc: str) -> Optional[MusicBrainzTrackInfo]:
        """
        Look up a recording by ISRC.

        Args:
            isrc: International Standard Recording Code

        Returns:
            MusicBrainzTrackInfo if found, None otherwise
        """
        if not isrc:
            return None

        log.debug(f"Looking up ISRC: {isrc}")

        # ISRC lookup returns a list of recordings
        data = self._request(f"isrc/{isrc}", params={"inc": "genres+tags+artists"})

        if not data or "recordings" not in data:
            log.debug(f"No recordings found for ISRC: {isrc}")
            return None

        recordings = data.get("recordings", [])
        if not recordings:
            return None

        # Take the first recording (usually there's only one per ISRC)
        recording = recordings[0]

        return self._parse_recording(recording)

    def search_track(
        self,
        title: str,
        artist: str,
        duration_ms: Optional[int] = None,
    ) -> Optional[MusicBrainzTrackInfo]:
        """
        Search for a track by title and artist.

        Args:
            title: Track title
            artist: Artist name
            duration_ms: Track duration in milliseconds (for better matching)

        Returns:
            MusicBrainzTrackInfo if found, None otherwise
        """
        if not title or not artist:
            return None

        # Build search query
        # MusicBrainz uses Lucene query syntax
        query = f'recording:"{title}" AND artist:"{artist}"'

        if duration_ms:
            # Duration is in milliseconds, MusicBrainz uses ms too
            # Allow +/- 3 second tolerance
            dur_min = max(0, duration_ms - 3000)
            dur_max = duration_ms + 3000
            query += f" AND dur:[{dur_min} TO {dur_max}]"

        log.debug(f"Searching MusicBrainz: {query}")

        data = self._request("recording", params={
            "query": query,
            "limit": 5,
        })

        if not data or "recordings" not in data:
            return None

        recordings = data.get("recordings", [])
        if not recordings:
            return None

        # Get the first result and fetch full details with genres/tags
        recording = recordings[0]
        mbid = recording.get("id")

        if not mbid:
            return None

        # Fetch full recording with genres and tags
        full_data = self._request(f"recording/{mbid}", params={"inc": "genres+tags+artists"})

        if not full_data:
            return self._parse_recording(recording)  # Return basic info

        return self._parse_recording(full_data)

    def _parse_recording(self, recording: dict) -> MusicBrainzTrackInfo:
        """Parse a recording response into MusicBrainzTrackInfo."""
        mbid = recording.get("id", "")
        title = recording.get("title", "")

        # Get artist name
        artist_credit = recording.get("artist-credit", [])
        if artist_credit:
            artist = artist_credit[0].get("artist", {}).get("name", "")
        else:
            artist = ""

        # Extract genres (official genres)
        genres = []
        for genre in recording.get("genres", []):
            name = genre.get("name", "")
            if name:
                genres.append(name)

        # Extract tags (user-submitted, may include genres, moods, etc.)
        tags = []
        for tag in recording.get("tags", []):
            name = tag.get("name", "")
            if name:
                tags.append(name)

        # Sort by count if available (most popular first)
        def get_count(item):
            return item.get("count", 0)

        if recording.get("genres"):
            sorted_genres = sorted(recording["genres"], key=get_count, reverse=True)
            genres = [g.get("name", "") for g in sorted_genres if g.get("name")]

        if recording.get("tags"):
            sorted_tags = sorted(recording["tags"], key=get_count, reverse=True)
            tags = [t.get("name", "") for t in sorted_tags if t.get("name")]

        return MusicBrainzTrackInfo(
            mbid=mbid,
            title=title,
            artist=artist,
            genres=genres[:5],  # Limit to top 5 genres
            tags=tags[:10],  # Limit to top 10 tags
        )
