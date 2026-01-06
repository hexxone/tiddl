import requests
import time
from pathlib import Path
from typing import Any, Optional
import json
import logging

from tiddl.cli.const import APP_PATH

log = logging.getLogger(__name__)


class SpotifyClient:
    """Spotify API client using OAuth 2.0 with authorization code grant"""

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str = "https://example.com/callback"):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.cache_path = APP_PATH / ".spotify_cache"
        self.token_info: Optional[dict[str, Any]] = None

        # Load cached token if available
        if self.cache_path.exists():
            try:
                self.token_info = json.loads(self.cache_path.read_text())
            except Exception:
                pass

    def get_auth_url(self) -> tuple[str, str]:
        """
        Get the authorization URL for user to login.
        Returns (auth_url, state) tuple.
        """
        import secrets
        state = secrets.token_urlsafe(16)

        scope = "playlist-read-private playlist-read-collaborative user-library-read"

        auth_url = (
            f"https://accounts.spotify.com/authorize?"
            f"client_id={self.client_id}&"
            f"response_type=code&"
            f"redirect_uri={self.redirect_uri}&"
            f"scope={scope.replace(' ', '%20')}&"
            f"state={state}"
        )

        return auth_url, state

    def get_access_token_from_code(self, code: str) -> dict[str, Any]:
        """Exchange authorization code for access token"""
        response = requests.post(
            "https://accounts.spotify.com/api/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )

        response.raise_for_status()
        token_info = response.json()

        # Add expiry timestamp
        token_info['expires_at'] = int(time.time()) + token_info['expires_in']

        # Cache the token
        self._save_token(token_info)
        self.token_info = token_info

        return token_info

    def _save_token(self, token_info: dict[str, Any]):
        """Save token to cache file"""
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(token_info, indent=2))

    def _refresh_token(self) -> dict[str, Any]:
        """Refresh the access token"""
        if not self.token_info or 'refresh_token' not in self.token_info:
            raise Exception("No refresh token available")

        response = requests.post(
            "https://accounts.spotify.com/api/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.token_info['refresh_token'],
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )

        response.raise_for_status()
        token_info = response.json()

        # Add expiry timestamp
        token_info['expires_at'] = int(time.time()) + token_info['expires_in']

        # Keep the refresh token if not provided in response
        if 'refresh_token' not in token_info and self.token_info:
            token_info['refresh_token'] = self.token_info['refresh_token']

        # Cache the token
        self._save_token(token_info)
        self.token_info = token_info

        return token_info

    def get_valid_token(self) -> str:
        """Get a valid access token, refreshing if necessary"""
        if not self.token_info:
            raise Exception("Not authenticated. Please run 'tiddl auth spotify-login' first.")

        # Check if token is expired (with 60 second buffer)
        if self.token_info.get('expires_at', 0) < (int(time.time()) + 60):
            self._refresh_token()

        return self.token_info['access_token']

    def make_request(self, endpoint: str, method: str = "GET", params: dict = None) -> dict[str, Any]:
        """Make an authenticated request to Spotify API"""
        token = self.get_valid_token()

        url = f"https://api.spotify.com/v1/{endpoint}"
        headers = {
            "Authorization": f"Bearer {token}",
        }

        log.debug(f"Making Spotify API request: {method} {url} with params={params}")
        response = requests.request(method, url, headers=headers, params=params)

        log.debug(f"Spotify API response status: {response.status_code}")

        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            log.error(f"Spotify API error: {response.status_code} - {response.text}")
            raise

        result = response.json()
        log.debug(f"Spotify API response keys: {result.keys() if isinstance(result, dict) else type(result)}")

        return result

    def is_authenticated(self) -> bool:
        """Check if user is authenticated"""
        return self.token_info is not None and 'access_token' in self.token_info
