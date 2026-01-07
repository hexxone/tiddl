import typer
from rich.console import Console
from rich.table import Table
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeRemainingColumn,
    TimeElapsedColumn,
)
from logging import getLogger
from typing import Optional
from datetime import datetime
from pathlib import Path

from typing_extensions import Annotated

from tiddl.cli.utils.spotify import load_spotify_credentials
from tiddl.core.spotify import SpotifyClient, SpotifyAPI
from tiddl.core.odesli import OdesliClient
from tiddl.cli.ctx import Context

from .downloader import PlaylistDownloader
from .playlist import (
    find_or_reuse_tidal_playlist,
    write_log_file,
    remove_duplicates_from_playlist,
)
from .tracks import (
    match_spotify_to_existing_tidal,
    search_tidal_track,
    add_single_track_to_playlist,
)
from .report import PlaylistReportCollector

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
    PARALLEL_DOWNLOAD: Annotated[
        bool,
        typer.Option(
            "--parallel-download/--sequential-download",
            help="Download playlists in parallel as they complete (default) or all at end.",
        ),
    ] = True,
    CLEANUP: Annotated[
        bool,
        typer.Option(
            "--cleanup/--no-cleanup",
            help="Remove duplicate tracks from playlists after migration.",
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

    # Get download path from config for report scanning
    from tiddl.cli.config import CONFIG
    download_path = CONFIG.download.download_path

    # Set up report collector for CSV generation
    report_collector = PlaylistReportCollector(
        log_dir=log_dir,
        download_path=download_path,
    )

    # Set up playlist downloader for downloading after migration
    playlist_downloader = PlaylistDownloader(
        enabled=DOWNLOAD,
        parallel=PARALLEL_DOWNLOAD,
        max_workers=2,  # Download up to 2 playlists concurrently
    )

    if DOWNLOAD:
        if PARALLEL_DOWNLOAD:
            console.print("[dim]Playlists will be downloaded in parallel as they complete[/]\n")
        else:
            console.print("[dim]Playlists will be downloaded after all migrations complete[/]\n")

    # Track playlist names for download reporting
    playlist_names: dict[str, str] = {}

    for playlist in selected_playlists:
        playlist_name = playlist['name']
        result = migrate_playlist(
            ctx=ctx,
            spotify_api=spotify_api,
            odesli_client=odesli_client,
            playlist=playlist,
            dry_run=DRY_RUN,
            log_dir=log_dir,
            report_collector=report_collector,
        )

        if result:
            playlist_names[result] = playlist_name

            # Cleanup duplicates if enabled (before downloading)
            if CLEANUP and not DRY_RUN:
                console.print(f"  [dim]Cleaning up duplicates...[/]")
                try:
                    _, removed = remove_duplicates_from_playlist(
                        ctx=ctx,
                        playlist_uuid=result,
                        dry_run=False,
                    )
                    if removed > 0:
                        console.print(f"  [green]Cleaned up {removed} duplicate(s)[/]\n")
                except Exception as e:
                    log.warning(f"Failed to cleanup duplicates: {e}")
                    console.print(f"  [yellow]Warning: Cleanup failed: {e}[/]\n")

            migrated_playlist_ids.append(result)
            # Queue/start playlist download with name and track count for timeout calculation
            track_count = playlist.get('tracks', {}).get('total', 0)
            playlist_downloader.add_playlist(result, playlist_name, track_count)

    console.print("\n[bold green]Migration complete!")

    # Handle downloads
    if DOWNLOAD and migrated_playlist_ids:
        # Calculate total tracks across all playlists for better progress display
        total_tracks = sum(p.get('tracks', {}).get('total', 0) for p in selected_playlists if p['name'] in playlist_names.values())
        console.print(f"\n[cyan]Downloading {len(migrated_playlist_ids)} playlist(s) ({total_tracks} tracks total)...[/]")
        console.print("[dim]Using --skip-errors to skip unavailable tracks[/]")

        if PARALLEL_DOWNLOAD:
            # Wait for parallel downloads to complete
            completed, failed, pending = playlist_downloader.stats
            if pending > 0 or completed > 0:
                console.print(f"[dim]Waiting for {pending} pending download(s)...[/]")

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("Downloading playlists...", total=len(migrated_playlist_ids))
                results = playlist_downloader.wait_for_completion()
                progress.update(task, completed=len(results))

            completed, failed, _ = playlist_downloader.stats
            if completed > 0:
                console.print(f"[green]✓ Downloaded {completed} playlist(s)[/]")
            if failed > 0:
                console.print(f"[yellow]✗ Failed to download {failed} playlist(s)[/]")
                # Show details of failed playlists
                console.print("\n[bold yellow]Failed playlists:[/]")
                for uuid, name, error in playlist_downloader.failed_playlists:
                    console.print(f"  [red]✗[/] {name}")
                    console.print(f"    [dim]{error}[/]")
        else:
            # Download sequentially at the end
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("Downloading playlists...", total=len(migrated_playlist_ids))
                for i, (uuid, name, success, message) in enumerate(playlist_downloader.download_queued()):
                    progress.update(task, completed=i + 1)
                    if success:
                        console.print(f"  [green]✓ Downloaded: {name}[/]")
                    else:
                        console.print(f"  [yellow]✗ Failed: {name} - {message[:80]}[/]")

            completed, failed, _ = playlist_downloader.stats
            console.print(f"\n[green]Downloaded {completed} playlist(s), {failed} failed[/]")

            # Show details of failed playlists
            if failed > 0:
                console.print("\n[bold yellow]Failed playlists:[/]")
                for uuid, name, error in playlist_downloader.failed_playlists:
                    console.print(f"  [red]✗[/] {name}")
                    console.print(f"    [dim]{error}[/]")

        playlist_downloader.shutdown()

        # Mark playlists as downloaded in the report collector
        for uuid, name, success, _ in results if PARALLEL_DOWNLOAD else []:
            report_collector.mark_playlist_downloaded(name, success)

    # Generate CSV reports for all playlists
    if not DRY_RUN:
        report_collector.finalize_and_write_reports(scan_downloads=DOWNLOAD)


def migrate_playlist(
    ctx: Context,
    spotify_api: SpotifyAPI,
    odesli_client: OdesliClient,
    playlist: dict,
    dry_run: bool = False,
    log_dir: Optional[Path] = None,
    report_collector: Optional[PlaylistReportCollector] = None,
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
            write_log_file(log_dir, playlist_name, log_lines)
        return None

    console.print(f"  Found {len(spotify_tracks)} track(s)")

    if dry_run:
        console.print(f"  [yellow]Would migrate {len(spotify_tracks)} tracks[/]\n")
        log_lines.append(f"DRY RUN: Would migrate {len(spotify_tracks)} tracks")
        if log_dir:
            write_log_file(log_dir, playlist_name, log_lines)
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
            write_log_file(log_dir, playlist_name, log_lines)
        return None

    # Start collecting track reports for this playlist
    if report_collector:
        report_collector.start_playlist(playlist_name, tidal_playlist_uuid)

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
                if report_collector:
                    report_collector.add_track(
                        playlist_name=playlist_name,
                        spotify_track=spotify_track,
                        tidal_id=matched_id,
                        migration_status="skipped",
                        migration_source="metadata_match",
                    )
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
                    if report_collector:
                        report_collector.add_track(
                            playlist_name=playlist_name,
                            spotify_track=spotify_track,
                            tidal_id=tidal_id,
                            migration_status="skipped",
                            migration_source=source or "existing",
                        )
                else:
                    # Try to add immediately
                    try:
                        add_single_track_to_playlist(api, tidal_playlist_uuid, tidal_id)
                        added = True
                        added_tracks.append(tidal_id)
                        existing_track_ids.add(tidal_id)  # Mark as added
                        log_lines.append(f"ADDED ({source}): {track_info}")
                        if report_collector:
                            report_collector.add_track(
                                playlist_name=playlist_name,
                                spotify_track=spotify_track,
                                tidal_id=tidal_id,
                                migration_status="added",
                                migration_source=source or "unknown",
                            )
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
                                        fallback_found += 1
                                        tidal_id = fallback_id  # Update for report
                                        source = "tidal_search"
                                        log_lines.append(f"ADDED (tidal_search fallback): {track_info}")
                                        if report_collector:
                                            report_collector.add_track(
                                                playlist_name=playlist_name,
                                                spotify_track=spotify_track,
                                                tidal_id=fallback_id,
                                                migration_status="added",
                                                migration_source="tidal_search_fallback",
                                            )
                                    except Exception as e2:
                                        log.debug(f"Fallback add also failed: {e2}")
                            except Exception as e:
                                log.debug(f"Fallback search error: {e}")

                        if not added:
                            failed_tracks.append(track_info)
                            log_lines.append(f"FAILED (could not add to playlist): {track_info}")
                            if report_collector:
                                report_collector.add_track(
                                    playlist_name=playlist_name,
                                    spotify_track=spotify_track,
                                    tidal_id=tidal_id,
                                    migration_status="failed_to_add",
                                    migration_source=source or "unknown",
                                )
            else:
                failed_tracks.append(track_info)
                log_lines.append(f"FAILED (not found on Tidal): {track_info}")
                if report_collector:
                    report_collector.add_track(
                        playlist_name=playlist_name,
                        spotify_track=spotify_track,
                        tidal_id=None,
                        migration_status="not_found",
                        migration_source="",
                    )

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
        if log_dir:
            write_log_file(log_dir, playlist_name, log_lines)
        return None
    else:
        console.print(f"  [yellow]Playlist partially migrated[/]")
        console.print(f"  [dim]Tidal Playlist UUID: {tidal_playlist_uuid}[/]")

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
        write_log_file(log_dir, playlist_name, log_lines)

    return tidal_playlist_uuid


@migrate_command.command(help="Remove duplicate tracks from Tidal playlists.")
def cleanup_duplicates(
    ctx: Context,
    DRY_RUN: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Show what would be removed without actually doing it.",
        ),
    ] = False,
    ALL_PLAYLISTS: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Clean up all playlists without prompting.",
        ),
    ] = False,
):
    """
    Remove duplicate tracks from your Tidal playlists.

    This is useful to clean up playlists that have duplicate tracks
    due to migration issues or other reasons.
    """

    api = ctx.obj.api

    # Fetch user's playlists
    console.print("[cyan]Fetching your Tidal playlists...[/]")

    playlists = []
    offset = 0
    limit = 50

    while True:
        try:
            user_playlists = api.get_user_playlists(limit=limit, offset=offset)
            items = user_playlists.get('items', [])

            if not items:
                break

            playlists.extend(items)

            total = user_playlists.get('totalNumberOfItems', 0)
            offset += limit
            if offset >= total:
                break

        except Exception as e:
            console.print(f"[bold red]Error fetching playlists: {e}")
            log.error(f"Failed to fetch playlists: {e}", exc_info=True)
            raise typer.Exit()

    if not playlists:
        console.print("[yellow]No playlists found.")
        raise typer.Exit()

    console.print(f"[green]Found {len(playlists)} playlist(s)[/]\n")

    # Display playlists in a table
    table = Table(title="Your Tidal Playlists", show_header=True, header_style="bold magenta")
    table.add_column("#", style="dim", width=6)
    table.add_column("Name", style="cyan")
    table.add_column("Tracks", justify="right", style="green")

    for idx, playlist in enumerate(playlists, 1):
        table.add_row(
            str(idx),
            playlist.get('title', 'Unknown'),
            str(playlist.get('numberOfTracks', 0))
        )

    console.print(table)
    console.print()

    if DRY_RUN:
        console.print("[yellow]DRY RUN - No changes will be made[/]\n")

    # Playlist selection
    if ALL_PLAYLISTS:
        selected_playlists = playlists
        console.print(f"[cyan]Processing all {len(playlists)} playlist(s)...[/]\n")
    else:
        console.print("[bold]Select playlists to clean up:[/]")
        console.print("Enter playlist numbers separated by commas (e.g., 1,3,5)")
        console.print("Or enter 'all' to clean up all playlists")
        console.print("Or enter 'none' to cancel\n")

        selection = typer.prompt("Your selection", default="all")

        if selection.lower() == 'none':
            console.print("[yellow]Cleanup cancelled.")
            raise typer.Exit()

        selected_playlists = []

        if selection.lower() == 'all':
            selected_playlists = playlists
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

    console.print(f"[green]Selected {len(selected_playlists)} playlist(s) for cleanup[/]\n")

    # Process each playlist
    total_duplicates_removed = 0

    for playlist in selected_playlists:
        playlist_name = playlist.get('title', 'Unknown')
        playlist_uuid = playlist.get('uuid')

        if not playlist_uuid:
            console.print(f"[yellow]Skipping playlist without UUID: {playlist_name}[/]")
            continue

        console.print(f"[bold cyan]Processing: {playlist_name}[/]")

        try:
            total_tracks, duplicates_removed = remove_duplicates_from_playlist(
                ctx=ctx,
                playlist_uuid=playlist_uuid,
                dry_run=DRY_RUN,
            )
            total_duplicates_removed += duplicates_removed
        except Exception as e:
            console.print(f"    [bold red]Error: {e}[/]")
            log.error(f"Error cleaning playlist {playlist_name}: {e}", exc_info=True)

        console.print()

    # Summary
    if DRY_RUN:
        console.print(f"\n[bold yellow]DRY RUN SUMMARY: Would remove {total_duplicates_removed} duplicate(s) total[/]")
    else:
        console.print(f"\n[bold green]Cleanup complete! Removed {total_duplicates_removed} duplicate(s) total[/]")
