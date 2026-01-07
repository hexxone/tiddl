from logging import getLogger
from typing import Optional

from tiddl.cli.ctx import Context

log = getLogger(__name__)


def simplify_name(name: str) -> str:
    """
    Simplify a track/artist name for matching by removing version info.
    Strips content after hyphens, parentheses, and brackets.
    """
    return name.split('-')[0].strip().split('(')[0].strip().split('[')[0].strip().lower()


def duration_match(tidal_duration: int, spotify_duration_ms: int, tolerance_sec: int = 2) -> bool:
    """Check if durations match within tolerance (Spotify is in ms, Tidal in seconds)"""
    spotify_duration_sec = spotify_duration_ms / 1000
    return abs(tidal_duration - spotify_duration_sec) <= tolerance_sec


def name_match(tidal_name: str, spotify_name: str) -> bool:
    """Check if track names match (simplified comparison)"""
    simple_tidal = simplify_name(tidal_name)
    simple_spotify = simplify_name(spotify_name)

    # Check if one contains the other
    return simple_spotify in simple_tidal or simple_tidal in simple_spotify


def artist_match(tidal_artists: list, spotify_artists: list) -> bool:
    """Check if at least one artist matches"""
    # Simplify and split artist names (handle "Artist1 & Artist2" format)
    def get_artist_names(artists):
        names = set()
        for artist in artists:
            name = artist.get('name', '') if isinstance(artist, dict) else getattr(artist, 'name', str(artist))
            # Split by common separators
            for part in name.replace('&', ',').replace(' x ', ',').replace(' X ', ',').split(','):
                names.add(simplify_name(part.strip()))
        return names

    tidal_names = get_artist_names(tidal_artists)
    spotify_names = get_artist_names(spotify_artists)

    return bool(tidal_names & spotify_names)


def match_spotify_to_existing_tidal(spotify_track: dict, existing_tracks: list[dict]) -> Optional[str]:
    """
    Try to match a Spotify track to an existing Tidal track in the playlist by metadata.
    Returns the Tidal track ID if a match is found, None otherwise.
    """
    if not existing_tracks:
        return None

    spotify_name = spotify_track.get('name', '')
    spotify_artists = spotify_track.get('artists', [])
    spotify_duration_ms = spotify_track.get('duration_ms', 0)

    for tidal_track in existing_tracks:
        tidal_name = tidal_track.get('title', '')
        tidal_artists = tidal_track.get('artists', [])
        tidal_duration = tidal_track.get('duration', 0)

        # Check duration match (within 2 seconds)
        if not duration_match(tidal_duration, spotify_duration_ms, tolerance_sec=2):
            continue

        # Check name match
        if not name_match(tidal_name, spotify_name):
            continue

        # Check artist match
        # Convert tidal_artists (list of strings) to format expected by artist_match
        tidal_artists_dicts = [{'name': name} for name in tidal_artists]
        if not artist_match(tidal_artists_dicts, spotify_artists):
            continue

        # All criteria match
        return tidal_track.get('id')

    return None


def search_tidal_track(ctx: Context, spotify_track: dict) -> Optional[str]:
    """
    Search for a track on Tidal using track name and artist.
    Returns the Tidal track ID if found, None otherwise.
    """
    api = ctx.obj.api

    track_name = spotify_track.get('name', '')
    artists = spotify_track.get('artists', [])
    duration_ms = spotify_track.get('duration_ms', 0)
    isrc = spotify_track.get('external_ids', {}).get('isrc')

    if not track_name or not artists:
        return None

    first_artist = artists[0].get('name', '') if artists else ''

    # Build search query: simplified track name + first artist
    query = f"{simplify_name(track_name)} {simplify_name(first_artist)}"

    try:
        search_result = api.get_search(query)

        # Check tracks in search results
        if hasattr(search_result, 'tracks') and hasattr(search_result.tracks, 'items'):
            for tidal_track in search_result.tracks.items[:10]:  # Check top 10 results
                # Try ISRC match first (most reliable)
                if isrc and hasattr(tidal_track, 'isrc') and tidal_track.isrc == isrc:
                    log.debug(f"Found ISRC match for '{track_name}': {tidal_track.id}")
                    return str(tidal_track.id)

                # Fall back to fuzzy matching
                tidal_artists = tidal_track.artists if hasattr(tidal_track, 'artists') else []
                tidal_duration = tidal_track.duration if hasattr(tidal_track, 'duration') else 0
                tidal_name = tidal_track.title if hasattr(tidal_track, 'title') else ''

                if (duration_match(tidal_duration, duration_ms) and
                    name_match(tidal_name, track_name) and
                    artist_match(tidal_artists, artists)):
                    log.debug(f"Found fuzzy match for '{track_name}': {tidal_track.id}")
                    return str(tidal_track.id)

        log.debug(f"No Tidal match found for '{track_name}'")
        return None

    except Exception as e:
        log.error(f"Error searching Tidal for '{track_name}': {e}")
        return None


def add_single_track_to_playlist(api, playlist_uuid: str, track_id: str):
    """Add a single track to a Tidal playlist"""
    api.add_tracks_to_playlist(
        playlist_uuid=playlist_uuid,
        track_ids=[track_id],
        on_duplicate="SKIP"
    )
