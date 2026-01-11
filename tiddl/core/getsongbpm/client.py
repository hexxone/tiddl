"""
GetSongBPM API client for fetching BPM and musical key.

GetSongBPM provides a free API (with attribution requirement) for
getting tempo (BPM), key, time signature, and other musical data.

API documentation: https://getsongbpm.com/api
Attribution required: Must link back to getsongbpm.com
"""

import time
from dataclasses import dataclass
from logging import getLogger
from typing import Optional
import requests

log = getLogger(__name__)

# Be nice with rate limiting (no official limit, but be reasonable)
RATE_LIMIT_SECONDS = 0.5


@dataclass
class GetSongBPMTrackInfo:
    """Track information from GetSongBPM."""
    song_id: str = ""
    title: str = ""
    artist: str = ""
    bpm: Optional[int] = None
    key: str = ""  # Musical key (e.g., "C", "Am", "F#m")
    key_camelot: str = ""  # Camelot notation (e.g., "8B", "5A")
    time_signature: str = ""  # e.g., "4/4", "3/4"
    album: str = ""
    genres: list[str] = None  # Artist genres

    def __post_init__(self):
        if self.genres is None:
            self.genres = []


class GetSongBPMClient:
    """Client for GetSongBPM API."""

    BASE_URL = "https://api.getsongbpm.com"

    def __init__(self, api_key: str):
        """
        Initialize the client.

        Args:
            api_key: GetSongBPM API key (get from https://getsongbpm.com/api)
        """
        if not api_key:
            raise ValueError("GetSongBPM API key is required")

        self._api_key = api_key
        self._last_request_time: float = 0
        self._session = requests.Session()

    def _rate_limit(self):
        """Ensure we don't hit the API too fast."""
        elapsed = time.time() - self._last_request_time
        if elapsed < RATE_LIMIT_SECONDS:
            sleep_time = RATE_LIMIT_SECONDS - elapsed
            time.sleep(sleep_time)
        self._last_request_time = time.time()

    def _request(self, endpoint: str, params: Optional[dict] = None) -> Optional[dict]:
        """Make a rate-limited request to GetSongBPM API."""
        self._rate_limit()

        url = f"{self.BASE_URL}/{endpoint}"
        params = params or {}
        params["api_key"] = self._api_key

        try:
            response = self._session.get(url, params=params, timeout=10)

            if response.status_code == 401:
                log.error("GetSongBPM: Invalid API key")
                return None

            if response.status_code == 404:
                log.debug(f"Not found: {endpoint}")
                return None

            if response.status_code == 429:
                log.warning("GetSongBPM rate limit exceeded, waiting...")
                time.sleep(5)
                return self._request(endpoint, params)

            response.raise_for_status()
            return response.json()

        except requests.RequestException as e:
            log.warning(f"GetSongBPM API error: {e}")
            return None

    def search_track(
        self,
        title: str,
        artist: str,
    ) -> Optional[GetSongBPMTrackInfo]:
        """
        Search for a track by title and artist.

        Args:
            title: Track title
            artist: Artist name

        Returns:
            GetSongBPMTrackInfo if found, None otherwise
        """
        if not title:
            return None

        # Build search query
        query = title
        if artist:
            query = f"{title} {artist}"

        log.debug(f"Searching GetSongBPM: {query}")

        data = self._request("song/search/", params={
            "lookup": "song",
            "search": query,
        })

        if not data:
            return None

        # Handle different response structures
        search_results = data.get("search", [])
        if not search_results:
            return None

        # Find the best match
        best_match = None
        for result in search_results:
            result_title = result.get("title", "").lower()
            result_artist = result.get("artist", {}).get("name", "").lower()

            # Check if title matches
            if title.lower() in result_title or result_title in title.lower():
                # Check if artist matches (if provided)
                if not artist or artist.lower() in result_artist or result_artist in artist.lower():
                    best_match = result
                    break

        if not best_match and search_results:
            # Fall back to first result
            best_match = search_results[0]

        if not best_match:
            return None

        # Get song ID and fetch full details
        song_id = best_match.get("id")
        if song_id:
            return self.get_track_by_id(song_id)

        # Return basic info if no ID
        return self._parse_search_result(best_match)

    def get_track_by_id(self, song_id: str) -> Optional[GetSongBPMTrackInfo]:
        """
        Get track info by GetSongBPM song ID.

        Args:
            song_id: GetSongBPM song ID

        Returns:
            GetSongBPMTrackInfo if found, None otherwise
        """
        if not song_id:
            return None

        log.debug(f"Fetching GetSongBPM song: {song_id}")

        data = self._request(f"song/?id={song_id}")

        if not data or "song" not in data:
            return None

        return self._parse_song(data["song"])

    def _parse_search_result(self, result: dict) -> GetSongBPMTrackInfo:
        """Parse a search result into GetSongBPMTrackInfo."""
        artist_info = result.get("artist", {})

        return GetSongBPMTrackInfo(
            song_id=result.get("id", ""),
            title=result.get("title", ""),
            artist=artist_info.get("name", ""),
            bpm=self._parse_int(result.get("tempo")),
            key=result.get("key_of", ""),
            key_camelot=result.get("open_key", ""),
            time_signature=result.get("time_sig", ""),
            album=result.get("album", {}).get("title", "") if isinstance(result.get("album"), dict) else "",
        )

    def _parse_song(self, song: dict) -> GetSongBPMTrackInfo:
        """Parse a song response into GetSongBPMTrackInfo."""
        artist_info = song.get("artist", {})

        # Extract genres from artist info
        genres = []
        artist_genres = artist_info.get("genres", [])
        if isinstance(artist_genres, list):
            for g in artist_genres:
                if isinstance(g, dict):
                    genre_name = g.get("name", "")
                elif isinstance(g, str):
                    genre_name = g
                else:
                    continue
                if genre_name:
                    genres.append(genre_name)

        album_info = song.get("album", {})
        album_title = ""
        if isinstance(album_info, dict):
            album_title = album_info.get("title", "")

        return GetSongBPMTrackInfo(
            song_id=song.get("id", ""),
            title=song.get("title", ""),
            artist=artist_info.get("name", ""),
            bpm=self._parse_int(song.get("tempo")),
            key=song.get("key_of", ""),
            key_camelot=song.get("open_key", ""),
            time_signature=song.get("time_sig", ""),
            album=album_title,
            genres=genres,
        )

    def _parse_int(self, value) -> Optional[int]:
        """Safely parse an integer value."""
        if value is None:
            return None
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return None


def is_configured() -> bool:
    """Check if GetSongBPM API is configured."""
    from tiddl.cli.utils.getsongbpm import load_getsongbpm_credentials
    credentials = load_getsongbpm_credentials()
    return bool(credentials.api_key)


def create_client() -> Optional[GetSongBPMClient]:
    """Create a GetSongBPM client using stored credentials."""
    from tiddl.cli.utils.getsongbpm import load_getsongbpm_credentials
    credentials = load_getsongbpm_credentials()

    if not credentials.api_key:
        return None

    return GetSongBPMClient(credentials.api_key)
