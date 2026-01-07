from logging import getLogger
from pathlib import Path
from typing import Optional

from rich.console import Console

from tiddl.cli.ctx import Context

console = Console()
log = getLogger(__name__)


def find_or_reuse_tidal_playlist(
    ctx: Context,
    playlist_name: str,
) -> tuple[str, set[str], list[dict]]:
    """
    Find existing Tidal playlist by name or create a new one.
    Returns tuple of (playlist_uuid, set of existing track IDs, list of existing track metadata).
    Track metadata contains: id, title, artists (list of names), duration (seconds).
    """

    api = ctx.obj.api

    # Get user's own playlists (not favorites)
    existing_playlist_uuid = None
    existing_track_ids = set()
    existing_tracks_metadata = []  # Store full track info for metadata matching

    try:
        offset = 0
        limit = 50
        while True:
            user_playlists = api.get_user_playlists(limit=limit, offset=offset)
            items = user_playlists.get('items', [])

            if not items:
                break

            for playlist_data in items:
                playlist_title = playlist_data.get('title', '')
                if playlist_title == playlist_name:
                    existing_playlist_uuid = playlist_data.get('uuid')
                    num_tracks = playlist_data.get('numberOfTracks', 0)
                    console.print(f"    Found existing playlist with {num_tracks} track(s)")

                    # Fetch ALL existing tracks with pagination
                    if num_tracks > 0:
                        new_track_ids, new_tracks_metadata = _fetch_all_playlist_tracks(
                            api=api,
                            playlist_uuid=existing_playlist_uuid,
                            expected_count=num_tracks,
                        )

                        existing_track_ids.update(new_track_ids)
                        existing_tracks_metadata.extend(new_tracks_metadata)

                    break

            if existing_playlist_uuid:
                break

            # Check if there are more playlists to fetch
            total = user_playlists.get('totalNumberOfItems', 0)
            offset += limit
            if offset >= total:
                break

    except Exception as e:
        log.warning(f"Error fetching user playlists: {e}")
        # Fall through to create new playlist

    if existing_playlist_uuid:
        console.print(f"    Reusing existing playlist")
        return existing_playlist_uuid, existing_track_ids, existing_tracks_metadata

    # Create new playlist
    console.print(f"    Creating new playlist...")

    try:
        result = api.create_playlist(
            title=playlist_name,
            description="Migrated from Spotify via tiddl"
        )

        # The response should contain the playlist UUID
        if 'uuid' in result:
            playlist_uuid = result['uuid']
        elif 'data' in result and 'uuid' in result['data']:
            playlist_uuid = result['data']['uuid']
        else:
            # Fallback: try to find the newly created playlist in user's playlists
            user_playlists = api.get_user_playlists(limit=50, offset=0)
            items = user_playlists.get('items', [])
            # Find the playlist by name (most recently created with this name)
            for pl in items:
                if pl.get('title') == playlist_name:
                    playlist_uuid = pl.get('uuid')
                    break
            else:
                raise Exception("Could not determine created playlist UUID")

        console.print(f"    Created new playlist")
        return playlist_uuid, set(), []  # Empty set and list for new playlist

    except Exception as e:
        log.error(f"Error creating playlist: {e}", exc_info=True)
        raise Exception(f"Failed to create playlist: {e}")


def _fetch_all_playlist_tracks(
    api,
    playlist_uuid: str,
    expected_count: int,
) -> tuple[set[str], list[dict]]:
    """
    Fetch all tracks from a Tidal playlist with proper pagination.
    Returns (set of track IDs, list of track metadata dicts).
    """
    track_ids = set()
    tracks_metadata = []

    track_offset = 0
    track_limit = 100  # Tidal's max per request

    log.debug(f"Starting pagination for playlist {playlist_uuid}, expecting {expected_count} tracks")

    while track_offset < expected_count:
        log.debug(f"Fetching tracks offset={track_offset}, limit={track_limit}, expected={expected_count}")

        try:
            items_resp = api.get_playlist_items(
                playlist_uuid=playlist_uuid,
                limit=track_limit,
                offset=track_offset
            )
        except Exception as e:
            log.error(f"Error fetching playlist items at offset {track_offset}: {e}")
            break

        # Debug: log response type and structure
        log.debug(f"Response type: {type(items_resp)}")
        if hasattr(items_resp, 'items'):
            items_list = items_resp.items
            log.debug(f"items_resp.items type: {type(items_list)}, length: {len(items_list) if items_list else 'None'}")
        else:
            log.debug(f"items_resp has no 'items' attribute, attrs: {dir(items_resp)}")

        # Count items in this page
        page_count = 0

        # Handle Pydantic model response
        if hasattr(items_resp, 'items') and items_resp.items is not None:
            for item in items_resp.items:
                if hasattr(item, 'item') and item.item is not None:
                    track = item.item
                    if hasattr(track, 'id'):
                        track_id = str(track.id)
                        track_ids.add(track_id)
                        page_count += 1

                        # Store metadata for matching
                        artist_names = []
                        if hasattr(track, 'artists') and track.artists:
                            artist_names = [a.name for a in track.artists if hasattr(a, 'name')]

                        tracks_metadata.append({
                            'id': track_id,
                            'title': getattr(track, 'title', ''),
                            'artists': artist_names,
                            'duration': getattr(track, 'duration', 0),
                        })
                else:
                    log.debug(f"Item has no valid 'item' attribute: {type(item)}")
        else:
            log.warning(f"Response has no valid items at offset {track_offset}")

        log.debug(f"Fetched {page_count} tracks from offset {track_offset}, total so far: {len(track_ids)}")

        # Always increment offset, even if we got fewer items than expected
        track_offset += track_limit

        # If we got no items on this page, the API might have returned everything
        if page_count == 0:
            log.debug(f"No more items returned, stopping pagination at offset {track_offset}")
            break

    log.debug(f"Pagination complete. Total tracks fetched: {len(track_ids)} (expected: {expected_count})")

    if len(track_ids) != expected_count:
        log.warning(
            f"Track count mismatch: fetched {len(track_ids)}, expected {expected_count}. "
            f"This may indicate some tracks are unavailable in your region."
        )

    return track_ids, tracks_metadata


def write_log_file(log_dir: Path, playlist_name: str, log_lines: list[str]):
    """Write log lines to a file in the log directory"""
    # Sanitize playlist name for filename
    safe_name = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in playlist_name)
    safe_name = safe_name.strip().replace(' ', '-')[:100]  # Limit length

    log_file = log_dir / f"pl-{safe_name}.txt"

    try:
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(log_lines))
        log.debug(f"Wrote migration log to {log_file}")
    except Exception as e:
        log.error(f"Failed to write log file {log_file}: {e}")
        console.print(f"[yellow]Warning: Could not write log file: {e}[/]")


def fetch_playlist_tracks_with_indices(
    api,
    playlist_uuid: str,
) -> list[dict]:
    """
    Fetch all tracks from a Tidal playlist with their indices and metadata.
    Returns list of dicts with keys: index, track_id, title, artists.
    """
    tracks_with_indices = []
    offset = 0
    limit = 100
    total_items = None  # Will be set from first response

    while True:
        try:
            items_resp = api.get_playlist_items(
                playlist_uuid=playlist_uuid,
                limit=limit,
                offset=offset
            )
        except Exception as e:
            log.error(f"Error fetching playlist items at offset {offset}: {e}")
            break

        # Get total items count from response (for reliable pagination)
        if total_items is None:
            if hasattr(items_resp, 'totalNumberOfItems'):
                total_items = items_resp.totalNumberOfItems
            log.debug(f"Playlist has {total_items} total items (from API response)")

        page_count = 0

        if hasattr(items_resp, 'items') and items_resp.items is not None:
            for item in items_resp.items:
                if hasattr(item, 'item') and item.item is not None:
                    track = item.item
                    if hasattr(track, 'id'):
                        track_id = str(track.id)
                        title = getattr(track, 'title', 'Unknown')
                        artists = []
                        if hasattr(track, 'artists') and track.artists:
                            artists = [a.name for a in track.artists if hasattr(a, 'name')]

                        # The index is the position in the playlist
                        index = offset + page_count
                        tracks_with_indices.append({
                            'index': index,
                            'track_id': track_id,
                            'title': title,
                            'artists': artists,
                        })
                        page_count += 1

        offset += limit

        # Use totalNumberOfItems for reliable pagination
        if total_items is not None:
            if offset >= total_items:
                break
        elif page_count == 0:
            # Fallback: stop if no items returned and we don't know total
            break

    log.debug(f"Fetched {len(tracks_with_indices)} tracks from playlist")
    return tracks_with_indices


def find_duplicate_indices(tracks_with_indices: list[dict]) -> list[dict]:
    """
    Find duplicate tracks (keeping the first occurrence).
    Returns list of track dicts that should be removed.
    """
    seen_track_ids = set()
    duplicates = []

    for track in tracks_with_indices:
        track_id = track['track_id']
        if track_id in seen_track_ids:
            duplicates.append(track)
        else:
            seen_track_ids.add(track_id)

    return duplicates


def remove_duplicates_from_playlist(
    ctx: Context,
    playlist_uuid: str,
    dry_run: bool = False,
) -> tuple[int, int]:
    """
    Remove duplicate tracks from a Tidal playlist.
    Returns tuple of (total_tracks, duplicates_removed).
    """
    api = ctx.obj.api

    # Fetch all tracks with their indices
    console.print("    Fetching playlist tracks...")
    tracks_with_indices = fetch_playlist_tracks_with_indices(api, playlist_uuid)

    if not tracks_with_indices:
        console.print("    [yellow]No tracks found in playlist[/]")
        return 0, 0

    # Find duplicates
    duplicates = find_duplicate_indices(tracks_with_indices)

    if not duplicates:
        console.print(f"    [green]No duplicates found in {len(tracks_with_indices)} track(s)[/]")
        return len(tracks_with_indices), 0

    console.print(f"    Found {len(duplicates)} duplicate(s) in {len(tracks_with_indices)} track(s)")

    # Log detailed info about each duplicate
    for dup in duplicates:
        artists_str = ', '.join(dup['artists']) if dup['artists'] else 'Unknown Artist'
        log.debug(
            f"Duplicate at index {dup['index']}: "
            f"{dup['title']} - {artists_str} (ID: {dup['track_id']})"
        )

    if dry_run:
        console.print(f"    [yellow]Would remove {len(duplicates)} duplicate(s)[/]")
        return len(tracks_with_indices), len(duplicates)

    # Remove duplicates in batches, starting from the highest index
    # (to avoid index shifting issues)
    duplicates.sort(key=lambda x: x['index'], reverse=True)

    # Tidal API may have limits on how many indices can be deleted at once
    batch_size = 50
    total_removed = 0

    for i in range(0, len(duplicates), batch_size):
        batch = duplicates[i:i + batch_size]
        batch_indices = [d['index'] for d in batch]
        try:
            api.delete_playlist_tracks(playlist_uuid, batch_indices)
            total_removed += len(batch)

            # Log each removed track
            for dup in batch:
                artists_str = ', '.join(dup['artists']) if dup['artists'] else 'Unknown Artist'
                log.debug(
                    f"Removed duplicate: {dup['title']} - {artists_str} "
                    f"(index: {dup['index']}, ID: {dup['track_id']})"
                )

            log.debug(f"Removed batch of {len(batch)} duplicates")
        except Exception as e:
            log.error(f"Error removing duplicates batch: {e}")
            console.print(f"    [yellow]Warning: Failed to remove some duplicates: {e}[/]")

    console.print(f"    [green]Removed {total_removed} duplicate(s)[/]")
    return len(tracks_with_indices), total_removed


def add_tracks_to_tidal_playlist(
    ctx: Context,
    playlist_uuid: str,
    track_ids: list[str],
):
    """Add tracks to a Tidal playlist in batches"""
    import re
    from tiddl.core.api.exceptions import ApiError

    api = ctx.obj.api

    # Tidal API has a limit on how many tracks can be added at once
    # Using 50 as a safe batch size (100 may fail for some accounts)
    batch_size = 50
    total_batches = (len(track_ids) + batch_size - 1) // batch_size

    if total_batches > 1:
        console.print(f"    Adding tracks in {total_batches} batches of up to {batch_size}...")

    total_failed = 0
    total_added = 0

    for batch_num, i in enumerate(range(0, len(track_ids), batch_size), 1):
        batch = track_ids[i:i + batch_size]
        try:
            api.add_tracks_to_playlist(
                playlist_uuid=playlist_uuid,
                track_ids=batch,
                on_duplicate="SKIP"  # Skip duplicates instead of failing
            )
            total_added += len(batch)
            if total_batches > 1:
                console.print(f"    Batch {batch_num}/{total_batches} complete ({len(batch)} tracks)")
        except ApiError as e:
            # ApiError from one-by-one fallback contains count of failed tracks
            # Some tracks may have succeeded even if the batch failed
            error_msg = str(e.userMessage) if hasattr(e, 'userMessage') else str(e)
            if "Failed to add" in error_msg and "track(s)" in error_msg:
                # Extract the number of failed tracks from the error message
                match = re.search(r'Failed to add (\d+) track\(s\)', error_msg)
                if match:
                    batch_failed = int(match.group(1))
                    batch_succeeded = len(batch) - batch_failed
                    total_failed += batch_failed
                    total_added += batch_succeeded
                    console.print(f"    [yellow]Batch {batch_num}/{total_batches}: {batch_succeeded} added, {batch_failed} failed[/]")
                else:
                    total_failed += len(batch)
                    console.print(f"    [yellow]Warning: Batch {batch_num} failed ({len(batch)} tracks)[/]")
            else:
                total_failed += len(batch)
                console.print(f"    [yellow]Warning: Batch {batch_num} failed ({len(batch)} tracks)[/]")
            log.error(f"Error adding batch {batch_num}: {e}")
        except Exception as e:
            log.error(f"Error adding batch {batch_num}: {e}", exc_info=True)
            total_failed += len(batch)
            console.print(f"    [yellow]Warning: Batch {batch_num} failed ({len(batch)} tracks)[/]")

    if total_failed > 0:
        console.print(f"    [yellow]Warning: {total_failed} track(s) could not be added[/]")
        console.print(f"    Successfully added {total_added} track(s) to playlist")
    else:
        console.print(f"    Successfully added all {total_added} tracks to playlist")
