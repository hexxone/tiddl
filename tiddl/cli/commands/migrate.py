import typer
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn, TimeElapsedColumn
from logging import getLogger
from typing import Optional
from datetime import datetime
from pathlib import Path
import threading
import queue
import subprocess
import sys

from tiddl.cli.utils.spotify import load_spotify_credentials
from tiddl.core.spotify import SpotifyClient, SpotifyAPI
from tiddl.core.odesli import OdesliClient
from tiddl.cli.ctx import Context
from typing_extensions import Annotated

console = Console()
log = getLogger(__name__)

migrate_command = typer.Typer(
    name="migrate", help="Migrate playlists from Spotify to Tidal.", no_args_is_help=True
)


@migrate_command.command(help="Migrate and download all Spotify playlists to Tidal.")
def spotify_to_tidal(
    ctx: Context,
    DRY_RUN: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Show what would be migrated without actually doing it.",
        ),
    ] = False,
    DOWNLOAD: Annotated[
        bool,
        typer.Option(
            "--download/--no-download",
            help="Automatically download playlists after migration.",
        ),
    ] = True,
):
    """
    Migrate playlists from Spotify to Tidal and optionally download them.
    
    This command will:
    1. Fetch all your Spotify playlists
    2. Let you select which ones to migrate
    3. Convert tracks from Spotify to Tidal using Odesli API
    4. Create/update playlists in Tidal
    5. Optionally download the migrated playlists
    """
    
    # Load Spotify credentials and check authentication
    credentials = load_spotify_credentials()
    
    if not credentials.client_id or not credentials.client_secret:
        console.print("[bold red]Spotify credentials not found!")
        console.print("Please run 'tiddl auth spotify-setup' first.")
        raise typer.Exit()
    
    spotify_client = SpotifyClient(
        client_id=credentials.client_id,
        client_secret=credentials.client_secret,
    )
    
    if not spotify_client.is_authenticated():
        console.print("[bold red]Not logged in to Spotify!")
        console.print("Please run 'tiddl auth spotify-login' first.")
        raise typer.Exit()
    
    spotify_api = SpotifyAPI(spotify_client)
    
    # Fetch user's playlists
    console.print("[cyan]Fetching your Spotify playlists...[/]")

    try:
        playlists = spotify_api.get_user_playlists()
        log.debug(f"Fetched {len(playlists)} playlists from Spotify")
    except Exception as e:
        console.print(f"[bold red]Error fetching playlists: {e}")
        log.error(f"Failed to fetch playlists: {e}", exc_info=True)
        raise typer.Exit()

    if not playlists:
        console.print("[yellow]No playlists found.")
        console.print("[dim]This could mean:")
        console.print("  - You have no playlists in your Spotify account")
        console.print("  - The authentication token doesn't have the right permissions")
        console.print("  - Try logging out and back in: tiddl auth spotify-logout && tiddl auth spotify-login")
        raise typer.Exit()

    # Get current user info to identify owned playlists
    try:
        current_user = spotify_api.get_current_user()
        user_id = current_user['id']
        console.print(f"[dim]Logged in as: {current_user.get('display_name', user_id)}[/]")
    except Exception as e:
        log.warning(f"Could not fetch current user info: {e}")
        user_id = None

    # Sort playlists: owned ones first, then others
    def sort_key(playlist):
        is_owner = user_id and playlist['owner']['id'] == user_id
        return (0 if is_owner else 1, playlist['name'].lower())

    playlists.sort(key=sort_key)

    # Count owned playlists
    owned_count = sum(1 for p in playlists if user_id and p['owner']['id'] == user_id)

    console.print(f"[green]Found {len(playlists)} playlist(s)[/] ([cyan]{owned_count} owned by you[/])\n")

    # Display playlists in a table
    table = Table(title="Your Spotify Playlists", show_header=True, header_style="bold magenta")
    table.add_column("#", style="dim", width=6)
    table.add_column("Name", style="cyan")
    table.add_column("Tracks", justify="right", style="green")
    table.add_column("Owner", style="yellow")

    for idx, playlist in enumerate(playlists, 1):
        owner_name = playlist['owner']['display_name'] or playlist['owner']['id']
        is_owner = user_id and playlist['owner']['id'] == user_id

        # Mark owned playlists
        if is_owner:
            owner_name = f"[bold green]★ {owner_name}[/]"

        table.add_row(
            str(idx),
            playlist['name'],
            str(playlist['tracks']['total']),
            owner_name
        )

    console.print(table)
    console.print()

    # Build default selection (owned playlists)
    default_indices = [str(i+1) for i, p in enumerate(playlists) if user_id and p['owner']['id'] == user_id]
    default_selection = ','.join(default_indices) if default_indices else 'none'

    # Playlist selection
    console.print("[bold]Select playlists to migrate:[/]")
    console.print("Enter playlist numbers separated by commas (e.g., 1,3,5)")
    console.print("Or enter 'all' to migrate all playlists")
    console.print("Or enter 'mine' to migrate only your own playlists (default)")
    console.print("Or enter 'none' to cancel")
    console.print(f"[dim]★ = Owned by you[/]\n")

    selection = typer.prompt("Your selection", default=default_selection if default_selection != 'none' else 'mine')

    if selection.lower() == 'none':
        console.print("[yellow]Migration cancelled.")
        raise typer.Exit()

    # Parse selection
    selected_playlists = []

    if selection.lower() == 'all':
        selected_playlists = playlists
    elif selection.lower() == 'mine':
        selected_playlists = [p for p in playlists if user_id and p['owner']['id'] == user_id]
        if not selected_playlists:
            console.print("[yellow]You don't own any playlists.")
            raise typer.Exit()
    else:
        try:
            indices = [int(x.strip()) for x in selection.split(',')]
            for idx in indices:
                if 1 <= idx <= len(playlists):
                    selected_playlists.append(playlists[idx - 1])
                else:
                    console.print(f"[yellow]Warning: Invalid playlist number {idx}, skipping.")
        except ValueError:
            console.print("[bold red]Invalid selection format!")
            raise typer.Exit()
    
    if not selected_playlists:
        console.print("[yellow]No playlists selected.")
        raise typer.Exit()
    
    console.print(f"\n[green]Selected {len(selected_playlists)} playlist(s) for migration[/]\n")
    
    if DRY_RUN:
        console.print("[yellow]DRY RUN - No changes will be made[/]\n")
    
    # Create log directory for this run
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_dir = Path(f"/tmp/tiddl/{timestamp}-runlog")
    log_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"[dim]Migration logs will be saved to: {log_dir}[/]\n")

    # Migrate playlists
    odesli_client = OdesliClient()
    migrated_playlist_ids = []

    for playlist in selected_playlists:
        result = migrate_playlist(
            ctx=ctx,
            spotify_api=spotify_api,
            odesli_client=odesli_client,
            playlist=playlist,
            dry_run=DRY_RUN,
            log_dir=log_dir,
            background_download=DOWNLOAD,  # Enable background downloads if download flag is set
        )

        if result:
            migrated_playlist_ids.append(result)

    console.print("\n[bold green]Migration complete!")


def migrate_playlist(
    ctx: Context,
    spotify_api: SpotifyAPI,
    odesli_client: OdesliClient,
    playlist: dict,
    dry_run: bool = False,
    log_dir: Optional[Path] = None,
    background_download: bool = False,
) -> Optional[str]:
    """
    Migrate a single playlist from Spotify to Tidal.
    Returns the Tidal playlist ID if successful, None otherwise.
    """

    playlist_name = playlist['name']
    playlist_id = playlist['id']
    spotify_url = f"https://open.spotify.com/playlist/{playlist_id}"

    console.print(f"[bold cyan]Processing playlist: {playlist_name}[/]")

    # Set up logging for this playlist
    log_lines = []
    log_lines.append(f"Playlist Migration Log")
    log_lines.append(f"=" * 80)
    log_lines.append(f"Playlist Name: {playlist_name}")
    log_lines.append(f"Origin URL: {spotify_url}")
    log_lines.append(f"Migration Time: {datetime.now().isoformat()}")
    log_lines.append("")

    # Fetch tracks from Spotify
    console.print("  Fetching tracks from Spotify...")

    try:
        spotify_tracks = spotify_api.get_playlist_tracks(playlist_id)
        log_lines.append(f"Fetched {len(spotify_tracks)} tracks from Spotify")
    except Exception as e:
        console.print(f"  [bold red]Error fetching tracks: {e}[/]")
        log_lines.append(f"ERROR: Failed to fetch tracks: {e}")
        if log_dir:
            _write_log_file(log_dir, playlist_name, log_lines)
        return None

    console.print(f"  Found {len(spotify_tracks)} track(s)")

    if dry_run:
        console.print(f"  [yellow]Would migrate {len(spotify_tracks)} tracks[/]\n")
        log_lines.append(f"DRY RUN: Would migrate {len(spotify_tracks)} tracks")
        if log_dir:
            _write_log_file(log_dir, playlist_name, log_lines)
        return None

    # Create or find playlist in Tidal FIRST (before converting tracks)
    console.print("  Finding or creating playlist in Tidal...")

    try:
        tidal_playlist_uuid, existing_track_ids, existing_tracks_metadata = find_or_reuse_tidal_playlist(
            ctx=ctx,
            playlist_name=playlist_name,
        )
        tidal_url = f"https://listen.tidal.com/playlist/{tidal_playlist_uuid}"
        log_lines.append(f"Target URL: {tidal_url}")
        log_lines.append(f"Found/created Tidal playlist with {len(existing_track_ids)} existing tracks")
        console.print(f"  [green]✓ Playlist ready in Tidal ({len(existing_track_ids)} existing tracks)[/]")
    except Exception as e:
        console.print(f"  [bold red]Error with Tidal playlist: {e}[/]")
        log.error(f"Failed to create/find Tidal playlist: {e}", exc_info=True)
        log_lines.append(f"ERROR: Failed to create/find Tidal playlist: {e}")
        if log_dir:
            _write_log_file(log_dir, playlist_name, log_lines)
        return None

    # Convert and add tracks to Tidal (immediately, one by one)
    # Calculate worst-case ETA based on Odesli rate limit (10 req/min = 6 sec/track)
    worst_case_minutes = (len(spotify_tracks) * 6) / 60
    console.print(f"  Converting & adding tracks... (max ~{worst_case_minutes:.0f} min due to rate limiting)")
    log_lines.append("")
    log_lines.append("Track Conversion Results:")
    log_lines.append("-" * 80)

    added_tracks = []
    failed_tracks = []
    skipped_tracks = []
    skipped_by_metadata = 0  # Track how many were skipped via metadata match
    fallback_found = 0  # Track how many were found via Tidal search

    api = ctx.obj.api

    # Initialize background downloader if enabled
    downloader = BackgroundDownloader(enabled=background_download)
    if background_download:
        downloader.start()
        console.print(f"  [dim]Background downloads enabled[/]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TextColumn("ETA:"),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("  Processing...", total=len(spotify_tracks))

        for spotify_track in spotify_tracks:
            track_name = spotify_track['name']
            artists = ', '.join([artist['name'] for artist in spotify_track['artists']])
            track_info = f"{track_name} - {artists}"

            tidal_id = None
            source = None
            added = False

            # Step 0: Check if track already exists via metadata matching (skip conversion entirely)
            matched_id = match_spotify_to_existing_tidal(spotify_track, existing_tracks_metadata)
            if matched_id:
                skipped_tracks.append(track_info)
                skipped_by_metadata += 1
                log_lines.append(f"SKIPPED (metadata match): {track_info}")
                progress.update(task, advance=1)
                continue

            # Step 1: Try Odesli first
            try:
                tidal_id = odesli_client.convert_spotify_to_tidal(spotify_track['id'])
                if tidal_id:
                    source = "odesli"
            except Exception as e:
                log.debug(f"Odesli error for {spotify_track['id']}: {e}")

            # Step 2: Fallback to Tidal search if Odesli failed
            if not tidal_id:
                try:
                    tidal_id = search_tidal_track(ctx, spotify_track)
                    if tidal_id:
                        source = "tidal_search"
                        fallback_found += 1
                except Exception as e:
                    log.debug(f"Tidal search error for '{track_name}': {e}")

            # Step 3: Try to add to playlist if we found a track
            if tidal_id:
                # Check if track already exists in playlist
                if tidal_id in existing_track_ids:
                    skipped_tracks.append(track_info)
                    log_lines.append(f"SKIPPED (already in playlist): {track_info}")
                else:
                    # Try to add immediately
                    try:
                        add_single_track_to_playlist(api, tidal_playlist_uuid, tidal_id)
                        added = True
                        added_tracks.append(tidal_id)
                        existing_track_ids.add(tidal_id)  # Mark as added
                        downloader.add_track(tidal_id)  # Queue for background download
                        log_lines.append(f"ADDED ({source}): {track_info}")
                    except Exception as add_error:
                        log.debug(f"Failed to add track {tidal_id}: {add_error}")

                        # Step 4: If add failed and we used Odesli, try Tidal search as fallback
                        if source == "odesli":
                            try:
                                fallback_id = search_tidal_track(ctx, spotify_track)
                                if fallback_id and fallback_id != tidal_id:
                                    try:
                                        add_single_track_to_playlist(api, tidal_playlist_uuid, fallback_id)
                                        added = True
                                        added_tracks.append(fallback_id)
                                        existing_track_ids.add(fallback_id)
                                        downloader.add_track(fallback_id)  # Queue for background download
                                        fallback_found += 1
                                        log_lines.append(f"ADDED (tidal_search fallback): {track_info}")
                                    except Exception as e2:
                                        log.debug(f"Fallback add also failed: {e2}")
                            except Exception as e:
                                log.debug(f"Fallback search error: {e}")

                        if not added:
                            failed_tracks.append(track_info)
                            log_lines.append(f"FAILED (could not add to playlist): {track_info}")
            else:
                failed_tracks.append(track_info)
                log_lines.append(f"FAILED (not found on Tidal): {track_info}")

            progress.update(task, advance=1)

    console.print(f"  [green]Successfully added {len(added_tracks)}/{len(spotify_tracks)} tracks[/]")
    if fallback_found > 0:
        console.print(f"  [dim]({fallback_found} found via Tidal search fallback)[/]")

    if skipped_tracks:
        console.print(f"  [cyan]Skipped {len(skipped_tracks)} track(s) already in playlist[/]")
        if skipped_by_metadata > 0:
            console.print(f"  [dim]({skipped_by_metadata} matched by metadata - no conversion needed)[/]")

    if failed_tracks:
        console.print(f"  [yellow]Failed {len(failed_tracks)} track(s):[/]")
        for track in failed_tracks[:5]:  # Show first 5
            console.print(f"    - {track}")
        if len(failed_tracks) > 5:
            console.print(f"    ... and {len(failed_tracks) - 5} more")

    # Add summary to log
    log_lines.append("")
    log_lines.append("=" * 80)
    log_lines.append(f"SUMMARY:")
    log_lines.append(f"  Total tracks in Spotify playlist: {len(spotify_tracks)}")
    log_lines.append(f"  Successfully added: {len(added_tracks)}")
    if fallback_found > 0:
        log_lines.append(f"    - via Odesli: {len(added_tracks) - fallback_found}")
        log_lines.append(f"    - via Tidal search: {fallback_found}")
    log_lines.append(f"  Skipped (already existed): {len(skipped_tracks)}")
    if skipped_by_metadata > 0:
        log_lines.append(f"    - via metadata match (no conversion): {skipped_by_metadata}")
        log_lines.append(f"    - via ID match: {len(skipped_tracks) - skipped_by_metadata}")
    log_lines.append(f"  Failed: {len(failed_tracks)}")
    log_lines.append("")

    if added_tracks:
        console.print(f"  [bold green]✓ Playlist migrated successfully![/]")
        console.print(f"  [dim]Tidal Playlist UUID: {tidal_playlist_uuid}[/]")
        log_lines.append(f"Successfully added {len(added_tracks)} tracks to Tidal playlist")
    elif skipped_tracks and not failed_tracks:
        console.print(f"  [bold green]✓ Playlist already up to date![/]")
        console.print(f"  [dim]Tidal Playlist UUID: {tidal_playlist_uuid}[/]")
        log_lines.append("No new tracks needed to be added")
    elif not existing_track_ids and not added_tracks:
        console.print(f"  [bold red]Playlist is empty and no tracks could be added.[/]\n")
        downloader.stop()
        if log_dir:
            _write_log_file(log_dir, playlist_name, log_lines)
        return None
    else:
        console.print(f"  [yellow]Playlist partially migrated[/]")
        console.print(f"  [dim]Tidal Playlist UUID: {tidal_playlist_uuid}[/]")

    # Wait for background downloads to complete
    if background_download and added_tracks:
        downloaded, dl_failed, pending = downloader.stats
        if pending > 0 or downloaded > 0:
            console.print(f"  [dim]Waiting for {pending + (len(added_tracks) - downloaded - dl_failed)} background downloads...[/]")
            downloader.wait_for_completion()
            downloaded, dl_failed, _ = downloader.stats
            if downloaded > 0:
                console.print(f"  [green]Downloaded {downloaded} track(s)[/]")
            if dl_failed > 0:
                console.print(f"  [yellow]Failed to download {dl_failed} track(s)[/]")
            log_lines.append(f"Downloads: {downloaded} succeeded, {dl_failed} failed")
    downloader.stop()
    console.print()  # Add newline

    # Update playlist description with last sync timestamp
    try:
        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_description = f"Migrated from Spotify via tiddl | Last sync: {update_time}"
        ctx.obj.api.update_playlist(
            playlist_uuid=tidal_playlist_uuid,
            description=new_description,
        )
        log_lines.append(f"Updated playlist description with sync timestamp")
    except Exception as e:
        log.warning(f"Failed to update playlist description: {e}")
        # Don't fail the migration if description update fails

    # Write log file
    if log_dir:
        _write_log_file(log_dir, playlist_name, log_lines)

    return tidal_playlist_uuid


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

                    # Fetch existing tracks with metadata
                    if num_tracks > 0:
                        track_offset = 0
                        track_limit = 100
                        while track_offset < num_tracks:
                            items_resp = api.get_playlist_items(
                                playlist_uuid=existing_playlist_uuid,
                                limit=track_limit,
                                offset=track_offset
                            )
                            if hasattr(items_resp, 'items') and items_resp.items:
                                for item in items_resp.items:
                                    if hasattr(item, 'item') and hasattr(item.item, 'id'):
                                        track = item.item
                                        track_id = str(track.id)
                                        existing_track_ids.add(track_id)

                                        # Store metadata for matching
                                        artist_names = []
                                        if hasattr(track, 'artists') and track.artists:
                                            artist_names = [a.name for a in track.artists if hasattr(a, 'name')]
                                        existing_tracks_metadata.append({
                                            'id': track_id,
                                            'title': getattr(track, 'title', ''),
                                            'artists': artist_names,
                                            'duration': getattr(track, 'duration', 0),
                                        })
                            track_offset += track_limit

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


def _write_log_file(log_dir: Path, playlist_name: str, log_lines: list[str]):
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


def add_tracks_to_tidal_playlist(
    ctx: Context,
    playlist_uuid: str,
    track_ids: list[str],
):
    """Add tracks to a Tidal playlist in batches"""
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
                import re
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
        if not _duration_match(tidal_duration, spotify_duration_ms, tolerance_sec=2):
            continue

        # Check name match
        if not _name_match(tidal_name, spotify_name):
            continue

        # Check artist match
        # Convert tidal_artists (list of strings) to format expected by _artist_match
        tidal_artists_dicts = [{'name': name} for name in tidal_artists]
        if not _artist_match(tidal_artists_dicts, spotify_artists):
            continue

        # All criteria match
        return tidal_track.get('id')

    return None


def add_single_track_to_playlist(api, playlist_uuid: str, track_id: str):
    """Add a single track to a Tidal playlist"""
    api.add_tracks_to_playlist(
        playlist_uuid=playlist_uuid,
        track_ids=[track_id],
        on_duplicate="SKIP"
    )


class BackgroundDownloader:
    """Background downloader that processes tracks while conversion continues"""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.queue: queue.Queue = queue.Queue()
        self.downloaded = 0
        self.failed = 0
        self.stop_signal = threading.Event()
        self.worker_thread = None

    def start(self):
        """Start the background download worker"""
        if not self.enabled:
            return
        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.worker_thread.start()

    def add_track(self, track_id: str):
        """Queue a track for download"""
        if self.enabled:
            self.queue.put(track_id)

    def stop(self):
        """Signal the worker to stop after processing remaining items"""
        self.stop_signal.set()
        self.queue.put(None)  # Sentinel to wake up the worker
        if self.worker_thread:
            self.worker_thread.join(timeout=5)

    def wait_for_completion(self):
        """Wait for all queued downloads to complete"""
        self.queue.join()

    def _worker(self):
        """Worker thread that downloads tracks from the queue"""
        while not self.stop_signal.is_set():
            try:
                track_id = self.queue.get(timeout=1)
                if track_id is None:
                    self.queue.task_done()
                    break

                try:
                    # Download the track using tiddl CLI
                    result = subprocess.run(
                        [sys.executable, "-m", "tiddl.cli.app", "download", "url", f"track/{track_id}"],
                        capture_output=True,
                        timeout=300,  # 5 min timeout per track
                    )
                    if result.returncode == 0:
                        self.downloaded += 1
                    else:
                        self.failed += 1
                        log.debug(f"Download failed for track {track_id}: {result.stderr.decode()[:200]}")
                except subprocess.TimeoutExpired:
                    self.failed += 1
                    log.warning(f"Download timeout for track {track_id}")
                except Exception as e:
                    self.failed += 1
                    log.debug(f"Download error for track {track_id}: {e}")

                self.queue.task_done()

            except queue.Empty:
                continue

    @property
    def stats(self) -> tuple[int, int, int]:
        """Return (downloaded, failed, pending) counts"""
        return self.downloaded, self.failed, self.queue.qsize()


def _simplify_name(name: str) -> str:
    """
    Simplify a track/artist name for matching by removing version info.
    Strips content after hyphens, parentheses, and brackets.
    """
    return name.split('-')[0].strip().split('(')[0].strip().split('[')[0].strip().lower()


def _duration_match(tidal_duration: int, spotify_duration_ms: int, tolerance_sec: int = 2) -> bool:
    """Check if durations match within tolerance (Spotify is in ms, Tidal in seconds)"""
    spotify_duration_sec = spotify_duration_ms / 1000
    return abs(tidal_duration - spotify_duration_sec) <= tolerance_sec


def _name_match(tidal_name: str, spotify_name: str) -> bool:
    """Check if track names match (simplified comparison)"""
    simple_tidal = _simplify_name(tidal_name)
    simple_spotify = _simplify_name(spotify_name)

    # Check if one contains the other
    return simple_spotify in simple_tidal or simple_tidal in simple_spotify


def _artist_match(tidal_artists: list, spotify_artists: list) -> bool:
    """Check if at least one artist matches"""
    # Simplify and split artist names (handle "Artist1 & Artist2" format)
    def get_artist_names(artists):
        names = set()
        for artist in artists:
            name = artist.get('name', '') if isinstance(artist, dict) else getattr(artist, 'name', str(artist))
            # Split by common separators
            for part in name.replace('&', ',').replace(' x ', ',').replace(' X ', ',').split(','):
                names.add(_simplify_name(part.strip()))
        return names

    tidal_names = get_artist_names(tidal_artists)
    spotify_names = get_artist_names(spotify_artists)

    return bool(tidal_names & spotify_names)


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
    query = f"{_simplify_name(track_name)} {_simplify_name(first_artist)}"

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

                if (_duration_match(tidal_duration, duration_ms) and
                    _name_match(tidal_name, track_name) and
                    _artist_match(tidal_artists, artists)):
                    log.debug(f"Found fuzzy match for '{track_name}': {tidal_track.id}")
                    return str(tidal_track.id)

        log.debug(f"No Tidal match found for '{track_name}'")
        return None

    except Exception as e:
        log.error(f"Error searching Tidal for '{track_name}': {e}")
        return None
