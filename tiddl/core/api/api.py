from typing import Literal, TypeAlias

from requests_cache import DO_NOT_CACHE, EXPIRE_IMMEDIATELY

from .client import TidalClient
from .models.base import (
    AlbumItems,
    AlbumItemsCredits,
    ArtistAlbumsItems,
    ArtistVideosItems,
    Favorites,
    MixItems,
    PlaylistItems,
    Search,
    SessionResponse,
    TrackLyrics,
    TrackStream,
    VideoStream,
)
from .models.resources import (
    Album,
    Artist,
    Playlist,
    StreamVideoQuality,
    Track,
    TrackQuality,
    Video,
)
from .models.review import AlbumReview

ID: TypeAlias = str | int


class Limits:
    # TODO test every max limit

    ARTIST_ALBUMS = 10
    ARTIST_ALBUMS_MAX = 100

    ARTIST_VIDEOS = 10
    ARTIST_VIDEOS_MAX = 100

    ALBUM_ITEMS = 20
    ALBUM_ITEMS_MAX = 100

    PLAYLIST_ITEMS = 20
    PLAYLIST_ITEMS_MAX = 100

    MIX_ITEMS = 20
    MIX_ITEMS_MAX = 100


class TidalAPI:
    client: TidalClient
    user_id: str
    country_code: str

    def __init__(self, client: TidalClient, user_id: str, country_code: str) -> None:
        self.client = client
        self.user_id = user_id
        self.country_code = country_code

    def get_album(self, album_id: ID):
        return self.client.fetch(
            Album,
            f"albums/{album_id}",
            {"countryCode": self.country_code},
            expire_after=3600,
        )

    def get_album_items(
        self, album_id: ID, limit: int = Limits.ALBUM_ITEMS, offset: int = 0
    ):
        return self.client.fetch(
            AlbumItems,
            f"albums/{album_id}/items",
            {
                "countryCode": self.country_code,
                "limit": min(limit, Limits.ALBUM_ITEMS_MAX),
                "offset": offset,
            },
            expire_after=3600,
        )

    def get_album_items_credits(
        self, album_id: ID, limit: int = Limits.ALBUM_ITEMS, offset: int = 0
    ):
        return self.client.fetch(
            AlbumItemsCredits,
            f"albums/{album_id}/items/credits",
            {
                "countryCode": self.country_code,
                "limit": min(limit, Limits.ALBUM_ITEMS_MAX),
                "offset": offset,
            },
            expire_after=3600,
        )

    def get_album_review(self, album_id: ID):
        return self.client.fetch(
            AlbumReview,
            f"albums/{album_id}/review",
            {"countryCode": self.country_code},
            expire_after=3600,
        )

    def get_artist(self, artist_id: ID):
        return self.client.fetch(
            Artist,
            f"artists/{artist_id}",
            {"countryCode": self.country_code},
            expire_after=3600,
        )

    def get_artist_videos(
        self,
        artist_id: ID,
        limit: int = Limits.ARTIST_VIDEOS,
        offset: int = 0,
    ):
        return self.client.fetch(
            ArtistVideosItems,
            f"artists/{artist_id}/videos",
            {
                "countryCode": self.country_code,
                "limit": limit,
                "offset": offset,
            },
            expire_after=3600,
        )

    def get_artist_albums(
        self,
        artist_id: ID,
        limit: int = Limits.ARTIST_ALBUMS,
        offset: int = 0,
        filter: Literal["ALBUMS", "EPSANDSINGLES"] = "ALBUMS",
    ):
        return self.client.fetch(
            ArtistAlbumsItems,
            f"artists/{artist_id}/albums",
            {
                "countryCode": self.country_code,
                "limit": min(limit, Limits.ARTIST_ALBUMS_MAX),
                "offset": offset,
                "filter": filter,
            },
            expire_after=3600,
        )

    def get_mix_items(
        self,
        mix_id: str,
        limit: int = Limits.MIX_ITEMS,
        offset: int = 0,
    ):
        return self.client.fetch(
            MixItems,
            f"mixes/{mix_id}/items",
            {
                "countryCode": self.country_code,
                "limit": min(limit, Limits.MIX_ITEMS_MAX),
                "offset": offset,
            },
            expire_after=3600,
        )

    def get_favorites(self):
        return self.client.fetch(
            Favorites,
            f"users/{self.user_id}/favorites/ids",
            {"countryCode": self.country_code},
            expire_after=EXPIRE_IMMEDIATELY,
        )

    def get_playlist(self, playlist_uuid: str):
        return self.client.fetch(
            Playlist,
            f"playlists/{playlist_uuid}",
            {"countryCode": self.country_code},
            expire_after=EXPIRE_IMMEDIATELY,
        )

    def get_playlist_items(
        self, playlist_uuid: str, limit: int = Limits.PLAYLIST_ITEMS, offset: int = 0
    ):
        return self.client.fetch(
            PlaylistItems,
            f"playlists/{playlist_uuid}/items",
            {
                "countryCode": self.country_code,
                "limit": min(limit, Limits.PLAYLIST_ITEMS_MAX),
                "offset": offset,
            },
            expire_after=EXPIRE_IMMEDIATELY,
        )

    def get_search(self, query: str):
        return self.client.fetch(
            Search,
            "search",
            {"countryCode": self.country_code, "query": query},
            expire_after=DO_NOT_CACHE,
        )

    def get_session(self):
        return self.client.fetch(SessionResponse, "sessions", expire_after=DO_NOT_CACHE)

    def get_track_lyrics(self, track_id: ID):
        return self.client.fetch(
            TrackLyrics,
            f"tracks/{track_id}/lyrics",
            {"countryCode": self.country_code},
            expire_after=3600,
        )

    def get_track(self, track_id: ID):
        return self.client.fetch(
            Track,
            f"tracks/{track_id}",
            {"countryCode": self.country_code},
            expire_after=3600,
        )

    def get_track_stream(self, track_id: ID, quality: TrackQuality):
        return self.client.fetch(
            TrackStream,
            f"tracks/{track_id}/playbackinfopostpaywall",
            {
                "audioquality": quality,
                "playbackmode": "STREAM",
                "assetpresentation": "FULL",
            },
            expire_after=DO_NOT_CACHE,
        )

    def get_video(self, video_id: ID):
        return self.client.fetch(
            Video,
            f"videos/{video_id}",
            {"countryCode": self.country_code},
            expire_after=3600,
        )

    def get_video_stream(self, video_id: ID, quality: StreamVideoQuality):
        return self.client.fetch(
            VideoStream,
            f"videos/{video_id}/playbackinfopostpaywall",
            {
                "videoquality": quality,
                "playbackmode": "STREAM",
                "assetpresentation": "FULL",
            },
            expire_after=DO_NOT_CACHE,
        )

    def _request(self, method: str, endpoint: str, **kwargs) -> "requests.Response":
        """Make a request with automatic token refresh on 401"""
        import requests

        url = f"https://api.tidal.com/v1/{endpoint}"
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.client.token}"

        response = requests.request(method, url, headers=headers, **kwargs)

        # Handle token expiry
        if response.status_code == 401 and self.client.on_token_expiry:
            new_token = self.client.on_token_expiry()
            if new_token:
                self.client.token = new_token
                headers["Authorization"] = f"Bearer {self.client.token}"
                response = requests.request(method, url, headers=headers, **kwargs)

        return response

    def get_user_playlists(self, limit: int = 50, offset: int = 0) -> dict:
        """Get playlists created by the current user"""
        response = self._request(
            "GET",
            f"users/{self.user_id}/playlists",
            params={
                "countryCode": self.country_code,
                "limit": limit,
                "offset": offset,
            },
        )

        if response.status_code != 200:
            from .exceptions import ApiError
            try:
                error_data = response.json()
                raise ApiError(**error_data)
            except Exception:
                raise ApiError(
                    status=response.status_code,
                    subStatus="0",
                    userMessage=f"Failed to get user playlists: {response.text}"
                )

        return response.json()

    def create_playlist(self, title: str, description: str = "") -> dict:
        """Create a new playlist"""
        response = self._request(
            "POST",
            f"users/{self.user_id}/playlists",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "title": title,
                "description": description,
                "countryCode": self.country_code,
            },
        )

        if response.status_code not in [200, 201]:
            from .exceptions import ApiError
            try:
                error_data = response.json()
                raise ApiError(**error_data)
            except Exception:
                raise ApiError(
                    status=response.status_code,
                    subStatus="0",
                    userMessage=f"Failed to create playlist: {response.text}"
                )

        return response.json()

    def add_tracks_to_playlist(self, playlist_uuid: str, track_ids: list[str], on_duplicate: str = "FAIL"):
        """Add tracks to a playlist. If batch addition fails, tries adding tracks one by one."""
        import logging
        import time
        from .exceptions import ApiError

        logger = logging.getLogger(__name__)

        def get_playlist_etag():
            """Get the ETag from the playlist response headers"""
            response = self._request(
                "GET",
                f"playlists/{playlist_uuid}",
                params={"countryCode": self.country_code},
            )
            return response.headers.get('ETag') or response.headers.get('etag')

        def add_tracks_request(track_ids_to_add: list[str], etag: str = None):
            """Make the actual API request to add tracks"""
            track_ids_str = ",".join(str(tid) for tid in track_ids_to_add)

            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            if etag:
                headers["If-None-Match"] = etag

            response = self._request(
                "POST",
                f"playlists/{playlist_uuid}/items",
                headers=headers,
                data={
                    "trackIds": track_ids_str,
                    "onDuplicates": on_duplicate,
                    "countryCode": self.country_code,
                },
            )
            return response

        # Get initial ETag and try to add all tracks at once
        etag = get_playlist_etag()
        response = add_tracks_request(track_ids, etag)

        if response.status_code in [200, 201]:
            return response.json() if response.text else {}

        # Log the failure details
        error_detail = response.text[:500] if response.text else 'empty'
        logger.warning(f"Batch addition failed: status={response.status_code}, response={error_detail}")

        # Batch addition failed - try adding tracks one by one
        failed_tracks = []
        success_count = 0

        for i, track_id in enumerate(track_ids):
            # Refresh ETag for each track (playlist changes after each successful add)
            etag = get_playlist_etag()
            response = add_tracks_request([track_id], etag)

            if response.status_code in [200, 201]:
                success_count += 1
            else:
                logger.debug(f"Failed to add track {track_id}: status={response.status_code}")
                failed_tracks.append(track_id)

            # Small delay every 10 tracks to avoid rate limiting
            if (i + 1) % 10 == 0:
                time.sleep(0.5)

        logger.info(f"One-by-one addition: {success_count} succeeded, {len(failed_tracks)} failed")

        if failed_tracks:
            raise ApiError(
                status=404,
                subStatus="2001",
                userMessage=f"Failed to add {len(failed_tracks)} track(s). Track IDs: {', '.join(failed_tracks)}"
            )

        return {}

    def delete_playlist_tracks(self, playlist_uuid: str, indices: list[int]):
        """Delete tracks from playlist by their indices"""
        # Get current ETag from response headers
        response = self._request(
            "GET",
            f"playlists/{playlist_uuid}",
            params={"countryCode": self.country_code},
        )
        etag = response.headers.get('ETag') or response.headers.get('etag')

        # Join indices as comma-separated string
        indices_str = ",".join(str(i) for i in indices)

        headers = {}
        if etag:
            headers["If-None-Match"] = str(etag)

        response = self._request(
            "DELETE",
            f"playlists/{playlist_uuid}/items/{indices_str}",
            headers=headers,
            params={"countryCode": self.country_code},
        )

        if response.status_code not in [200, 201, 204]:
            from .exceptions import ApiError
            try:
                error_data = response.json()
                raise ApiError(**error_data)
            except Exception:
                raise ApiError(
                    status=response.status_code,
                    subStatus="0",
                    userMessage=f"Failed to delete tracks: {response.text}"
                )

        return {}

    def update_playlist(self, playlist_uuid: str, title: str = None, description: str = None) -> dict:
        """Update playlist title and/or description"""
        # Get current ETag from response headers
        response = self._request(
            "GET",
            f"playlists/{playlist_uuid}",
            params={"countryCode": self.country_code},
        )
        etag = response.headers.get('ETag') or response.headers.get('etag')

        # Build update data - only include fields that are provided
        data = {}
        if title is not None:
            data["title"] = title
        if description is not None:
            data["description"] = description

        if not data:
            return {}  # Nothing to update

        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if etag:
            headers["If-None-Match"] = str(etag)

        response = self._request(
            "POST",
            f"playlists/{playlist_uuid}",
            headers=headers,
            data=data,
        )

        if response.status_code not in [200, 201]:
            from .exceptions import ApiError
            try:
                error_data = response.json()
                raise ApiError(**error_data)
            except Exception:
                raise ApiError(
                    status=response.status_code,
                    subStatus="0",
                    userMessage=f"Failed to update playlist: {response.text}"
                )

        return response.json() if response.text else {}
