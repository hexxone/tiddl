import time
import requests
from typing import Optional
from logging import getLogger

log = getLogger(__name__)


class OdesliClient:
    """
    Client for Odesli (song.link) API with rate limiting support.
    API docs: https://linktree.notion.site/API-d0ebe08a5e304a55928405eb682f6741
    Rate limit: 10 requests per minute
    """

    def __init__(self, rate_limit_per_minute: int = 10):
        self.base_url = "https://api.song.link/v1-alpha.1/links"
        self.rate_limit = rate_limit_per_minute
        self.request_times: list[float] = []

    def _wait_for_rate_limit(self):
        """Implement rate limiting by tracking request times"""
        now = time.time()
        
        # Remove requests older than 1 minute
        self.request_times = [t for t in self.request_times if now - t < 60]
        
        # If we've hit the rate limit, wait
        if len(self.request_times) >= self.rate_limit:
            oldest_request = self.request_times[0]
            wait_time = 60 - (now - oldest_request) + 0.1  # Add small buffer
            
            if wait_time > 0:
                log.debug(f"Rate limit reached, waiting {wait_time:.1f} seconds")
                time.sleep(wait_time)
                # Clean up old requests after waiting
                now = time.time()
                self.request_times = [t for t in self.request_times if now - t < 60]
        
        # Record this request
        self.request_times.append(time.time())

    def convert_spotify_to_tidal(self, spotify_track_id: str) -> Optional[str]:
        """
        Convert a Spotify track ID to a Tidal track ID.
        Returns None if no match is found.
        """
        self._wait_for_rate_limit()
        
        spotify_url = f"https://open.spotify.com/track/{spotify_track_id}"
        
        try:
            response = requests.get(
                self.base_url,
                params={
                    "url": spotify_url,
                    "userCountry": "US",  # Can be made configurable
                },
                timeout=10,
            )
            
            if response.status_code == 404:
                log.debug(f"Track not found on Odesli: {spotify_track_id}")
                return None
            
            response.raise_for_status()
            data = response.json()
            
            # Extract Tidal track ID from the response
            if 'linksByPlatform' in data and 'tidal' in data['linksByPlatform']:
                tidal_data = data['linksByPlatform']['tidal']
                if 'entityUniqueId' in tidal_data:
                    # entityUniqueId format: "TIDAL_TRACK::123456"
                    entity_id = tidal_data['entityUniqueId']
                    if '::' in entity_id:
                        tidal_id = entity_id.split('::')[1]
                        log.debug(f"Converted Spotify track {spotify_track_id} to Tidal track {tidal_id}")
                        return tidal_id
            
            log.debug(f"No Tidal match found for Spotify track {spotify_track_id}")
            return None
            
        except requests.exceptions.RequestException as e:
            log.error(f"Error converting track {spotify_track_id}: {e}")
            return None

    def convert_spotify_url_to_tidal(self, spotify_url: str) -> Optional[str]:
        """
        Convert a Spotify URL to a Tidal track ID.
        Returns None if no match is found.
        """
        self._wait_for_rate_limit()
        
        try:
            response = requests.get(
                self.base_url,
                params={
                    "url": spotify_url,
                    "userCountry": "US",
                },
                timeout=10,
            )
            
            if response.status_code == 404:
                log.debug(f"Track not found on Odesli: {spotify_url}")
                return None
            
            response.raise_for_status()
            data = response.json()
            
            # Extract Tidal track ID from the response
            if 'linksByPlatform' in data and 'tidal' in data['linksByPlatform']:
                tidal_data = data['linksByPlatform']['tidal']
                if 'entityUniqueId' in tidal_data:
                    entity_id = tidal_data['entityUniqueId']
                    if '::' in entity_id:
                        tidal_id = entity_id.split('::')[1]
                        log.debug(f"Converted Spotify URL to Tidal track {tidal_id}")
                        return tidal_id
            
            log.debug(f"No Tidal match found for Spotify URL {spotify_url}")
            return None
            
        except requests.exceptions.RequestException as e:
            log.error(f"Error converting URL {spotify_url}: {e}")
            return None
