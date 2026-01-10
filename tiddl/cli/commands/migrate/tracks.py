import re
import unicodedata
from logging import getLogger
from typing import Optional

from tiddl.cli.ctx import Context

log = getLogger(__name__)

# Common suffixes to remove from track names when searching
# Note: We deliberately DON'T remove "remix" from REMOVE_SUFFIXES
# because remixes are different tracks that should match by name.
# Instead, we handle remix matching specially in is_remix_match().
REMOVE_SUFFIXES = [
    # Mix types (but NOT "remix" - those are different songs)
    r'\boriginal\s*mix\b',
    r'\bradio\s*edit\b',
    r'\bradio\s*mix\b',
    r'\bextended\s*mix\b',
    r'\bextended\s*version\b',
    r'\bclub\s*mix\b',
    r'\bdub\s*mix\b',
    r'\bvip\s*mix\b',
    r'\bbootleg\b',
    # Remasters
    r'\bremaster(ed)?\b',
    r'\b\d{4}\s*remaster(ed)?\b',
    # Versions
    r'\bdeluxe(\s*edition)?\b',
    r'\bbonus\s*track\b',
    r'\balbum\s*version\b',
    r'\bsingle\s*version\b',
    r'\blive(\s*version)?\b',
    r'\bacoustic(\s*version)?\b',
    r'\binstrumental\b',
    # Features (but keep for artist matching)
    r'\bfeat\.?\b',
    r'\bft\.?\b',
    r'\bfeaturing\b',
]

# Compile the pattern once
SUFFIX_PATTERN = re.compile('|'.join(REMOVE_SUFFIXES), re.IGNORECASE)

# Pattern to detect remixes
REMIX_PATTERN = re.compile(r'\bremix\b', re.IGNORECASE)


def is_remix(name: str) -> bool:
    """Check if a track name indicates it's a remix."""
    return bool(REMIX_PATTERN.search(name))


def remix_status_matches(name1: str, name2: str) -> bool:
    """
    Check if two track names have matching remix status.

    Both must be remixes, or neither must be a remix.
    This prevents matching "Song (Artist Remix)" with "Song".
    """
    return is_remix(name1) == is_remix(name2)


def simplify_name(name: str) -> str:
    """
    Simplify a track/artist name for matching by removing version info.
    Strips content after hyphens, parentheses, and brackets.
    """
    return name.split('-')[0].strip().split('(')[0].strip().split('[')[0].strip().lower()


def normalize_for_search(text: str, keep_non_ascii: bool = True) -> str:
    """
    Normalize text for search query building.

    - Removes common suffixes (Original Mix, Remaster, etc.)
    - Removes content in parentheses/brackets
    - Removes special characters except alphanumerics and spaces
    - Optionally keeps non-ASCII characters (for Japanese, etc.)

    Args:
        text: The text to normalize
        keep_non_ascii: If True, keep non-ASCII letters (Japanese, etc.)

    Returns:
        Normalized text suitable for search queries
    """
    if not text:
        return ""

    # First, remove content in parentheses and brackets
    result = re.sub(r'\([^)]*\)', ' ', text)
    result = re.sub(r'\[[^\]]*\]', ' ', result)

    # Remove common suffixes
    result = SUFFIX_PATTERN.sub(' ', result)

    # Remove content after " - " (often version info)
    if ' - ' in result:
        result = result.split(' - ')[0]

    # Normalize unicode (decompose accented characters)
    result = unicodedata.normalize('NFKD', result)

    if keep_non_ascii:
        # Keep letters (any script), numbers, and spaces
        # This preserves Japanese, Chinese, Korean, etc.
        result = ''.join(c for c in result if c.isalnum() or c.isspace())
    else:
        # Only keep ASCII alphanumerics and spaces
        result = ''.join(c for c in result if c.isascii() and (c.isalnum() or c.isspace()))

    # Collapse multiple spaces and strip
    result = re.sub(r'\s+', ' ', result).strip()

    return result.lower()


def build_search_queries(track_name: str, artist_name: str) -> list[str]:
    """
    Build multiple search query variations to try.

    Returns a list of queries in order of preference:
    1. Normalized track name + artist (keeps non-ASCII)
    2. ASCII-only track name + artist
    3. Artist + first significant word of track
    4. Just artist name (last resort)

    For tracks with CJK characters that normalize to empty,
    keeps the original characters.
    """
    queries = []

    # Normalize with non-ASCII kept (for Japanese, etc.)
    norm_track = normalize_for_search(track_name, keep_non_ascii=True)
    norm_artist = normalize_for_search(artist_name, keep_non_ascii=True)

    # ASCII-only versions
    ascii_track = normalize_for_search(track_name, keep_non_ascii=False)
    ascii_artist = normalize_for_search(artist_name, keep_non_ascii=False)

    # If track name normalizes to empty but has content, use original
    # (handles tracks that are entirely special chars like Japanese)
    if not norm_track and track_name.strip():
        norm_track = track_name.strip().lower()
    if not norm_artist and artist_name.strip():
        norm_artist = artist_name.strip().lower()

    # Query 1: Full normalized query (keeps non-ASCII)
    if norm_track and norm_artist:
        queries.append(f"{norm_track} {norm_artist}")
    elif norm_track:
        queries.append(norm_track)
    elif norm_artist:
        queries.append(norm_artist)

    # Query 2: ASCII-only (may help for transliterated titles on Tidal)
    if ascii_track and ascii_artist:
        ascii_query = f"{ascii_track} {ascii_artist}"
        if ascii_query not in queries:
            queries.append(ascii_query)
    elif ascii_artist and ascii_artist not in queries:
        # Just artist if track has no ASCII
        queries.append(ascii_artist)

    # Query 3: Artist + first word of track (for partial matches)
    if norm_track and norm_artist:
        first_word = norm_track.split()[0] if norm_track.split() else ""
        if first_word and len(first_word) > 2:
            partial_query = f"{first_word} {norm_artist}"
            if partial_query not in queries:
                queries.append(partial_query)

    # Query 4: Just the artist (very last resort)
    if norm_artist and norm_artist not in queries:
        queries.append(norm_artist)

    return queries


def duration_match(tidal_duration: int, spotify_duration_ms: int, tolerance_sec: int = 2) -> bool:
    """Check if durations match within tolerance (Spotify is in ms, Tidal in seconds)"""
    spotify_duration_sec = spotify_duration_ms / 1000
    return abs(tidal_duration - spotify_duration_sec) <= tolerance_sec


def name_match(tidal_name: str, spotify_name: str) -> bool:
    """
    Check if track names match using improved normalization.

    Uses both ASCII-only and full unicode matching to handle:
    - Japanese/CJK characters with transliterated Tidal titles
    - Common suffixes like "Original Mix", "Remaster"
    - Special characters and version info
    - Remix vs original track distinction
    """
    # First, check remix status - a remix should only match another remix
    if not remix_status_matches(tidal_name, spotify_name):
        return False

    # Try full normalized comparison (keeps non-ASCII)
    norm_tidal = normalize_for_search(tidal_name, keep_non_ascii=True)
    norm_spotify = normalize_for_search(spotify_name, keep_non_ascii=True)

    if norm_tidal and norm_spotify:
        # Check if one contains the other
        if norm_spotify in norm_tidal or norm_tidal in norm_spotify:
            return True

    # Try ASCII-only comparison (helps when Tidal has transliterated title)
    ascii_tidal = normalize_for_search(tidal_name, keep_non_ascii=False)
    ascii_spotify = normalize_for_search(spotify_name, keep_non_ascii=False)

    if ascii_tidal and ascii_spotify:
        if ascii_spotify in ascii_tidal or ascii_tidal in ascii_spotify:
            return True

    # Cross-comparison: ASCII Tidal vs full Spotify (e.g., "70cm" in both)
    if ascii_tidal and norm_spotify:
        # Check if ASCII version appears in the other
        if ascii_tidal in norm_spotify or norm_spotify in ascii_tidal:
            return True

    # Fallback to simple name matching (original behavior)
    simple_tidal = simplify_name(tidal_name)
    simple_spotify = simplify_name(spotify_name)

    return simple_spotify in simple_tidal or simple_tidal in simple_spotify


def artist_match(tidal_artists: list, spotify_artists: list) -> bool:
    """
    Check if at least one artist matches using improved normalization.

    Handles:
    - Multiple artist formats ("Artist1 & Artist2", "Artist1 x Artist2")
    - Non-ASCII artist names
    - Common variations and abbreviations
    """
    def get_artist_names(artists, keep_non_ascii: bool = True) -> set:
        names = set()
        for artist in artists:
            name = artist.get('name', '') if isinstance(artist, dict) else getattr(artist, 'name', str(artist))
            # Split by common separators
            for part in name.replace('&', ',').replace(' x ', ',').replace(' X ', ',').replace(' vs ', ',').replace(' vs. ', ',').split(','):
                normalized = normalize_for_search(part.strip(), keep_non_ascii=keep_non_ascii)
                if normalized:
                    names.add(normalized)
        return names

    # Try with non-ASCII preserved (for Japanese artist names, etc.)
    tidal_names = get_artist_names(tidal_artists, keep_non_ascii=True)
    spotify_names = get_artist_names(spotify_artists, keep_non_ascii=True)

    if tidal_names & spotify_names:
        return True

    # Try ASCII-only (for transliterated names)
    tidal_ascii = get_artist_names(tidal_artists, keep_non_ascii=False)
    spotify_ascii = get_artist_names(spotify_artists, keep_non_ascii=False)

    if tidal_ascii & spotify_ascii:
        return True

    # Check if any name is a substring of another (partial match)
    for tidal_name in tidal_names:
        for spotify_name in spotify_names:
            if len(tidal_name) > 3 and len(spotify_name) > 3:
                if tidal_name in spotify_name or spotify_name in tidal_name:
                    return True

    return False


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
    Search for a track on Tidal using multiple query strategies.

    Tries multiple search queries in order of specificity:
    1. Full normalized track name + artist
    2. ASCII-only query (helps for transliterated titles)
    3. Partial queries (first word + artist)
    4. Just artist name

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

    # Build multiple search queries to try
    queries = build_search_queries(track_name, first_artist)

    if not queries:
        log.debug(f"No valid search queries for '{track_name}'")
        return None

    tried_tracks = set()  # Avoid checking the same track multiple times

    for query in queries:
        try:
            log.debug(f"Searching Tidal with query: '{query}'")
            search_result = api.get_search(query)

            # Check tracks in search results
            if hasattr(search_result, 'tracks') and hasattr(search_result.tracks, 'items'):
                for tidal_track in search_result.tracks.items[:10]:  # Check top 10 results
                    # Skip if we've already checked this track
                    track_id = str(tidal_track.id)
                    if track_id in tried_tracks:
                        continue
                    tried_tracks.add(track_id)

                    # Try ISRC match first (most reliable)
                    if isrc and hasattr(tidal_track, 'isrc') and tidal_track.isrc == isrc:
                        log.debug(f"Found ISRC match for '{track_name}': {track_id}")
                        return track_id

                    # Fall back to fuzzy matching
                    tidal_artists = tidal_track.artists if hasattr(tidal_track, 'artists') else []
                    tidal_duration = tidal_track.duration if hasattr(tidal_track, 'duration') else 0
                    tidal_name = tidal_track.title if hasattr(tidal_track, 'title') else ''

                    if (duration_match(tidal_duration, duration_ms) and
                        name_match(tidal_name, track_name) and
                        artist_match(tidal_artists, artists)):
                        log.debug(f"Found fuzzy match for '{track_name}' with query '{query}': {track_id}")
                        return track_id

        except Exception as e:
            log.warning(f"Error searching Tidal with query '{query}': {e}")
            # Continue to next query
            continue

    log.debug(f"No Tidal match found for '{track_name}' after trying {len(queries)} queries")
    return None


def add_single_track_to_playlist(api, playlist_uuid: str, track_id: str):
    """Add a single track to a Tidal playlist"""
    api.add_tracks_to_playlist(
        playlist_uuid=playlist_uuid,
        track_ids=[track_id],
        on_duplicate="SKIP"
    )
