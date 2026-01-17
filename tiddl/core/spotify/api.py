from typing import Any
from .client import SpotifyClient
import logging

log = logging.getLogger(__name__)


class SpotifyAPI:
    """High-level Spotify API interface"""

    def __init__(self, client: SpotifyClient):
        self.client = client

    def get_user_playlists(self) -> list[dict[str, Any]]:
        """Get all playlists for the current user"""
        playlists = []
        offset = 0
        limit = 50

        while True:
            results = self.client.make_request(
                "me/playlists",
                params={"limit": limit, "offset": offset}
            )

            items = results.get('items', [])
            playlists.extend(items)

            log.debug(f"Fetched {len(items)} playlists (offset={offset}, total so far={len(playlists)})")
            log.debug(f"Response has 'next': {results.get('next')}, total in response: {results.get('total')}")

            # Check if there are more playlists to fetch
            if not results.get('next'):
                break

            # Also check if we've fetched all items
            total = results.get('total', 0)
            if len(playlists) >= total:
                break

            offset += limit

        log.info(f"Fetched total of {len(playlists)} playlists from Spotify")
        return playlists

    def get_playlist_tracks(self, playlist_id: str) -> list[dict[str, Any]]:
        """Get all tracks from a playlist"""
        tracks = []
        offset = 0
        limit = 100

        while True:
            results = self.client.make_request(
                f"playlists/{playlist_id}/tracks",
                params={"limit": limit, "offset": offset}
            )

            for item in results['items']:
                if item['track'] is not None:  # Skip local files and unavailable tracks
                    tracks.append(item['track'])

            if results['next'] is None:
                break

            offset += limit

        return tracks

    def get_track_info(self, track_id: str) -> dict[str, Any]:
        """Get detailed information about a track"""
        return self.client.make_request(f"tracks/{track_id}")

    def get_current_user(self) -> dict[str, Any]:
        """Get current user's profile"""
        return self.client.make_request("me")

    def get_saved_tracks(self) -> list[dict[str, Any]]:
        """
        Get all saved/liked tracks for the current user.

        Returns a list of track dicts (same format as playlist tracks).
        """
        tracks = []
        offset = 0
        limit = 50  # Spotify max for saved tracks endpoint

        while True:
            results = self.client.make_request(
                "me/tracks",
                params={"limit": limit, "offset": offset}
            )

            for item in results.get('items', []):
                if item.get('track') is not None:  # Skip unavailable tracks
                    tracks.append(item['track'])

            log.debug(f"Fetched {len(results.get('items', []))} saved tracks (offset={offset}, total so far={len(tracks)})")

            # Check if there are more tracks to fetch
            if not results.get('next'):
                break

            total = results.get('total', 0)
            if len(tracks) >= total:
                break

            offset += limit

        log.info(f"Fetched total of {len(tracks)} saved/liked tracks from Spotify")
        return tracks

    def get_saved_tracks_count(self) -> int:
        """Get the count of saved/liked tracks without fetching all of them."""
        results = self.client.make_request("me/tracks", params={"limit": 1, "offset": 0})
        return results.get('total', 0)
