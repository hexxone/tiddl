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
from tiddl.core.utils.format import format_template
from tiddl.cli.ctx import Context
from tiddl.cli.config import CONFIG

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
from .ui import MigrationUI
from .selection import interactive_playlist_selection

console = Console()
log = getLogger(__name__)


def _compute_csv_path_for_playlist(ctx: Context, tidal_playlist_uuid: str, download_path: Path) -> Path | None:
    """
    Compute the CSV path based on the M3U template.
    Returns a path like: download_path/m3u/playlist/{creator_name}/{playlist_title}.csv
    """
    try:
        # Fetch the Tidal playlist
        playlist = ctx.obj.api.get_playlist(tidal_playlist_uuid)

        # Get creator name
        from tiddl.cli.commands.download import get_playlist_creator_name
        creator_name = get_playlist_creator_name(ctx.obj.api, playlist)

        # Use the M3U playlist template to compute the path
        m3u_path = format_template(
            template=CONFIG.m3u.templates.playlist,
            playlist=playlist,
            creator_name=creator_name,
            with_asterisk_ext=False,
        )

        # Replace .m3u extension with .csv (or just add .csv if no extension)
        csv_path = download_path / (m3u_path + ".csv")
        return csv_path
    except Exception as e:
        log.warning(f"Could not compute CSV path from M3U template: {e}")
        return None


migrate_command = typer.Typer(
    name="migrate", help="Migrate playlists from Spotify to Tidal.", no_args_is_help=True
)


def _run_migration_loop(
    ctx: Context,
    spotify_api,
    odesli_client,
    selected_playlists: list,
    playlist_downloader,
    report_collector,
    playlist_names: dict,
    migrated_playlist_ids: list,
    log_dir: Path,
    dry_run: bool,
    cleanup: bool,
    download: bool,
    parallel_download: bool,
    parallel_migration: bool,
    migration_workers: int,
    ui: Optional[MigrationUI],
):
    """
    Run the main migration loop over selected playlists.

    This is extracted to work with both fancy UI and simple console modes.
    Supports both sequential and parallel migration.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    total_playlists = len(selected_playlists)
    total_tracks_all = sum(p.get('tracks', {}).get('total', 0) for p in selected_playlists)
    _playlist_counter = [0]  # Use list for thread-safe incrementing
    _counter_lock = threading.Lock()

    # Set up migration config for UI
    if ui:
        effective_workers = migration_workers if parallel_migration else 1
        ui.set_migration_config(total_playlists, effective_workers, total_tracks_all)

    def process_single_playlist(playlist_idx_tuple):
        """Process a single playlist. Used for both sequential and parallel modes."""
        i, playlist = playlist_idx_tuple
        playlist_name = playlist['name']
        track_count = playlist.get('tracks', {}).get('total', 0)

        with _counter_lock:
            _playlist_counter[0] += 1
            current_num = _playlist_counter[0]

        # Update UI with current playlist
        if ui:
            ui.start_playlist(current_num, total_playlists, playlist_name, track_count)
        else:
            console.print(f"[bold cyan]Processing playlist: {playlist_name}[/]")

        result = migrate_playlist(
            ctx=ctx,
            spotify_api=spotify_api,
            odesli_client=odesli_client,
            playlist=playlist,
            dry_run=dry_run,
            log_dir=log_dir,
            report_collector=report_collector,
            ui=ui,
        )

        if result:
            with _counter_lock:
                playlist_names[result] = playlist_name

            # Cleanup duplicates if enabled (before downloading)
            if cleanup and not dry_run:
                if not ui:
                    console.print(f"  [dim]Cleaning up duplicates...[/]")
                try:
                    _, removed = remove_duplicates_from_playlist(
                        ctx=ctx,
                        playlist_uuid=result,
                        dry_run=False,
                        quiet=ui is not None,  # Suppress output when using fancy UI
                    )
                    if removed > 0 and not ui:
                        console.print(f"  [green]Cleaned up {removed} duplicate(s)[/]\n")
                except Exception as e:
                    log.warning(f"Failed to cleanup duplicates: {e}")
                    if not ui:
                        console.print(f"  [yellow]Warning: Cleanup failed: {e}[/]\n")

            with _counter_lock:
                migrated_playlist_ids.append(result)

            # Queue/start playlist download with name and track count for timeout calculation
            if ui:
                ui.queue_download(playlist_name, track_count)
            playlist_downloader.add_playlist(result, playlist_name, track_count)

            # Show download status in simple mode
            if not ui and download and parallel_download:
                completed, failed, pending = playlist_downloader.stats
                total_queued = completed + failed + pending
                if total_queued > 0:
                    status_parts = []
                    if completed > 0:
                        status_parts.append(f"[green]{completed} +[/]")
                    if failed > 0:
                        status_parts.append(f"[red]{failed} x[/]")
                    if pending > 0:
                        status_parts.append(f"[dim]{pending} pending[/]")
                    console.print(f"  [dim]Downloads: {' '.join(status_parts)}[/]")

        # Always mark playlist as finished in UI (even if migration failed)
        if ui:
            ui.finish_playlist(i)

        return result

    def process_with_cleanup(playlist_idx_tuple):
        """Wrapper to ensure finish_playlist is called even on exceptions."""
        try:
            return process_single_playlist(playlist_idx_tuple)
        except Exception as e:
            # Still mark as finished on exception
            if ui:
                ui.finish_playlist(playlist_idx_tuple[0])
            raise

    # Run migrations either sequentially or in parallel
    if parallel_migration and migration_workers > 1:
        # Parallel migration mode
        log.info(f"Running parallel migration with {migration_workers} workers")
        with ThreadPoolExecutor(max_workers=migration_workers) as executor:
            # Submit all playlists to the executor
            futures = {
                executor.submit(process_with_cleanup, (i, playlist)): playlist
                for i, playlist in enumerate(selected_playlists, 1)
            }

            # Wait for all to complete
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    playlist = futures[future]
                    log.error(f"Error migrating playlist {playlist.get('name', 'unknown')}: {e}")
    else:
        # Sequential migration mode (original behavior)
        for i, playlist in enumerate(selected_playlists, 1):
            process_with_cleanup((i, playlist))

    # If using fancy UI with parallel downloads, wait for them to complete within the UI context
    if ui and download and parallel_download:
        # Keep UI running while downloads complete, with periodic refresh for responsiveness
        playlist_downloader.wait_for_completion(
            on_progress=ui.refresh,
            poll_interval=0.5,
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
    PARALLEL_MIGRATION: Annotated[
        bool,
        typer.Option(
            "--parallel-migration/--sequential-migration",
            help="Migrate multiple playlists in parallel (default) or sequentially.",
        ),
    ] = True,
    MIGRATION_WORKERS: Annotated[
        int,
        typer.Option(
            "--migration-workers",
            "-w",
            help="Number of parallel migration workers (default: 4).",
        ),
    ] = 4,
    FANCY_UI: Annotated[
        bool,
        typer.Option(
            "--fancy-ui/--simple-ui",
            help="Use split-screen UI showing migration and download progress side-by-side.",
        ),
    ] = True,
    INTERACTIVE: Annotated[
        bool,
        typer.Option(
            "--interactive/--no-interactive",
            "-i",
            help="Interactive playlist selection with toggle support. Use @owner to select by owner.",
        ),
    ] = True,
    SELECT: Annotated[
        Optional[str],
        typer.Option(
            "--select",
            "-s",
            help="Pre-select playlists (non-interactive). Use: 'all', 'mine', '1,2,3', or '1-5'.",
        ),
    ] = None,
):
    """
    Migrate playlists from Spotify to Tidal and optionally download them.

    This command will:
    1. Fetch all your Spotify playlists
    2. Let you select which ones to migrate (interactive toggle or --select)
    3. Convert tracks from Spotify to Tidal using Odesli API
    4. Create/update playlists in Tidal
    5. Optionally download the migrated playlists

    Interactive selection commands:
      - Numbers (1,2,3) to toggle specific playlists
      - Ranges (1-5) to toggle a range
      - @owner to toggle all playlists by that owner
      - 'all', 'none', 'mine', 'invert' for bulk operations
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

    # Fetch Liked Songs count and create a pseudo-playlist for it
    try:
        liked_songs_count = spotify_api.get_saved_tracks_count()
        log.debug(f"User has {liked_songs_count} liked songs")
    except Exception as e:
        log.warning(f"Could not fetch liked songs count: {e}")
        liked_songs_count = 0

    if not playlists and liked_songs_count == 0:
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
        user_display_name = current_user.get('display_name', user_id)
        console.print(f"[dim]Logged in as: {user_display_name}[/]")
    except Exception as e:
        log.warning(f"Could not fetch current user info: {e}")
        user_id = None
        user_display_name = "Unknown"

    # Add Liked Songs as a special pseudo-playlist at the beginning
    if liked_songs_count > 0:
        liked_songs_playlist = {
            'id': '__liked_songs__',  # Special ID to identify this pseudo-playlist
            'name': 'Liked Songs',
            'tracks': {'total': liked_songs_count},
            'owner': {
                'id': user_id or '__self__',
                'display_name': user_display_name,
            },
            '_is_liked_songs': True,  # Flag to identify this as the Liked Songs pseudo-playlist
        }
        playlists.insert(0, liked_songs_playlist)

    # Sort playlists: owned ones first, then others (but keep Liked Songs at position 0)
    def sort_key(playlist):
        # Liked Songs always first
        if playlist.get('_is_liked_songs'):
            return (-1, '')
        is_owner = user_id and playlist['owner']['id'] == user_id
        return (0 if is_owner else 1, playlist['name'].lower())

    playlists.sort(key=sort_key)

    # Count owned playlists (including Liked Songs)
    owned_count = sum(1 for p in playlists if user_id and p['owner']['id'] == user_id)

    liked_msg = f" + Liked Songs ({liked_songs_count} tracks)" if liked_songs_count > 0 else ""
    console.print(f"[green]Found {len(playlists) - (1 if liked_songs_count > 0 else 0)} playlist(s){liked_msg}[/] ([cyan]{owned_count} owned by you[/])\n")

    # Playlist selection
    selected_playlists = []

    if SELECT:
        # Non-interactive mode with --select option
        selection = SELECT.lower().strip()

        # Display playlists in a simple table first
        table = Table(title="Your Spotify Playlists", show_header=True, header_style="bold magenta")
        table.add_column("#", style="dim", width=6)
        table.add_column("Name", style="cyan")
        table.add_column("Tracks", justify="right", style="green")
        table.add_column("Owner", style="yellow")

        for idx, playlist in enumerate(playlists, 1):
            owner_name = playlist['owner']['display_name'] or playlist['owner']['id']
            is_owner = user_id and playlist['owner']['id'] == user_id
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

        if selection == 'all':
            selected_playlists = playlists
        elif selection == 'mine':
            selected_playlists = [p for p in playlists if user_id and p['owner']['id'] == user_id]
            if not selected_playlists:
                console.print("[yellow]You don't own any playlists.")
                raise typer.Exit()
        elif selection == 'none':
            console.print("[yellow]Migration cancelled.")
            raise typer.Exit()
        else:
            # Parse numbers and ranges
            try:
                parts = [p.strip() for p in selection.replace(' ', ',').split(',') if p.strip()]
                indices = set()
                for part in parts:
                    if '-' in part:
                        start, end = part.split('-', 1)
                        for i in range(int(start), int(end) + 1):
                            indices.add(i)
                    else:
                        indices.add(int(part))

                for idx in sorted(indices):
                    if 1 <= idx <= len(playlists):
                        selected_playlists.append(playlists[idx - 1])
                    else:
                        console.print(f"[yellow]Warning: Invalid playlist number {idx}, skipping.")
            except ValueError:
                console.print("[bold red]Invalid selection format!")
                raise typer.Exit()

    elif INTERACTIVE:
        # Interactive selection mode (default)
        selected_playlists = interactive_playlist_selection(
            console=console,
            playlists=playlists,
            user_id=user_id,
            default_mine=True,
        )
    else:
        # Fallback: simple prompt (when --no-interactive and no --select)
        table = Table(title="Your Spotify Playlists", show_header=True, header_style="bold magenta")
        table.add_column("#", style="dim", width=6)
        table.add_column("Name", style="cyan")
        table.add_column("Tracks", justify="right", style="green")
        table.add_column("Owner", style="yellow")

        for idx, playlist in enumerate(playlists, 1):
            owner_name = playlist['owner']['display_name'] or playlist['owner']['id']
            is_owner = user_id and playlist['owner']['id'] == user_id
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

        default_indices = [str(i+1) for i, p in enumerate(playlists) if user_id and p['owner']['id'] == user_id]
        default_selection = ','.join(default_indices) if default_indices else 'mine'

        console.print("[bold]Select playlists to migrate:[/]")
        console.print("Enter playlist numbers separated by commas (e.g., 1,3,5)")
        console.print("Or enter 'all', 'mine', or 'none'")
        console.print(f"[dim]★ = Owned by you[/]\n")

        selection = typer.prompt("Your selection", default=default_selection)
        selection = selection.lower().strip()

        if selection == 'none':
            console.print("[yellow]Migration cancelled.")
            raise typer.Exit()
        elif selection == 'all':
            selected_playlists = playlists
        elif selection == 'mine':
            selected_playlists = [p for p in playlists if user_id and p['owner']['id'] == user_id]
        else:
            try:
                indices = [int(x.strip()) for x in selection.split(',')]
                for idx in indices:
                    if 1 <= idx <= len(playlists):
                        selected_playlists.append(playlists[idx - 1])
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

    # Create the migration UI (fancy or None for simple mode)
    ui = MigrationUI(console=console) if FANCY_UI and not DRY_RUN else None

    # Set up playlist downloader for downloading after migration
    playlist_downloader = PlaylistDownloader(
        enabled=DOWNLOAD,
        parallel=PARALLEL_DOWNLOAD,
        max_workers=2,  # Download up to 2 playlists concurrently
        on_complete=ui.get_download_callback() if ui else None,
        on_start=ui.get_download_start_callback() if ui else None,
    )

    if not FANCY_UI:
        if PARALLEL_MIGRATION:
            console.print(f"[dim]Playlists will be migrated in parallel ({MIGRATION_WORKERS} workers)[/]")
        if DOWNLOAD:
            if PARALLEL_DOWNLOAD:
                console.print("[dim]Playlists will be downloaded in parallel as they complete[/]\n")
            else:
                console.print("[dim]Playlists will be downloaded after all migrations complete[/]\n")

    # Track playlist names for download reporting
    playlist_names: dict[str, str] = {}
    total_playlists = len(selected_playlists)

    # Use the fancy UI context if enabled
    if ui:
        with ui:
            _run_migration_loop(
                ctx=ctx,
                spotify_api=spotify_api,
                odesli_client=odesli_client,
                selected_playlists=selected_playlists,
                playlist_downloader=playlist_downloader,
                report_collector=report_collector,
                playlist_names=playlist_names,
                migrated_playlist_ids=migrated_playlist_ids,
                log_dir=log_dir,
                dry_run=DRY_RUN,
                cleanup=CLEANUP,
                download=DOWNLOAD,
                parallel_download=PARALLEL_DOWNLOAD,
                parallel_migration=PARALLEL_MIGRATION,
                migration_workers=MIGRATION_WORKERS,
                ui=ui,
            )
    else:
        _run_migration_loop(
            ctx=ctx,
            spotify_api=spotify_api,
            odesli_client=odesli_client,
            selected_playlists=selected_playlists,
            playlist_downloader=playlist_downloader,
            report_collector=report_collector,
            playlist_names=playlist_names,
            migrated_playlist_ids=migrated_playlist_ids,
            log_dir=log_dir,
            dry_run=DRY_RUN,
            cleanup=CLEANUP,
            download=DOWNLOAD,
            parallel_download=PARALLEL_DOWNLOAD,
            parallel_migration=PARALLEL_MIGRATION,
            migration_workers=MIGRATION_WORKERS,
            ui=None,
        )

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
    ui: Optional[MigrationUI] = None,
) -> Optional[str]:
    """
    Migrate a single playlist from Spotify to Tidal.
    Returns the Tidal playlist ID if successful, None otherwise.
    """

    playlist_name = playlist['name']
    playlist_id = playlist['id']
    is_liked_songs = playlist.get('_is_liked_songs', False)

    if is_liked_songs:
        spotify_url = "https://open.spotify.com/collection/tracks"
    else:
        spotify_url = f"https://open.spotify.com/playlist/{playlist_id}"

    # Only print if not using fancy UI (the loop already shows this)
    if not ui:
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
    if not ui:
        if is_liked_songs:
            console.print("  Fetching Liked Songs from Spotify...")
        else:
            console.print("  Fetching tracks from Spotify...")

    try:
        if is_liked_songs:
            spotify_tracks = spotify_api.get_saved_tracks()
        else:
            spotify_tracks = spotify_api.get_playlist_tracks(playlist_id)
        log_lines.append(f"Fetched {len(spotify_tracks)} tracks from Spotify")
    except Exception as e:
        if not ui:
            console.print(f"  [bold red]Error fetching tracks: {e}[/]")
        log_lines.append(f"ERROR: Failed to fetch tracks: {e}")
        if log_dir:
            write_log_file(log_dir, playlist_name, log_lines)
        return None

    if not ui:
        console.print(f"  Found {len(spotify_tracks)} track(s)")

    if dry_run:
        console.print(f"  [yellow]Would migrate {len(spotify_tracks)} tracks[/]\n")
        log_lines.append(f"DRY RUN: Would migrate {len(spotify_tracks)} tracks")
        if log_dir:
            write_log_file(log_dir, playlist_name, log_lines)
        return None

    # Create or find playlist in Tidal FIRST (before converting tracks)
    if not ui:
        console.print("  Finding or creating playlist in Tidal...")

    try:
        tidal_playlist_uuid, existing_track_ids, existing_tracks_metadata = find_or_reuse_tidal_playlist(
            ctx=ctx,
            playlist_name=playlist_name,
            quiet=ui is not None,  # Suppress output when using fancy UI
        )
        tidal_url = f"https://listen.tidal.com/playlist/{tidal_playlist_uuid}"
        log_lines.append(f"Target URL: {tidal_url}")
        log_lines.append(f"Found/created Tidal playlist with {len(existing_track_ids)} existing tracks")
        if not ui:
            console.print(f"  [green]✓ Playlist ready in Tidal ({len(existing_track_ids)} existing tracks)[/]")
    except Exception as e:
        if not ui:
            console.print(f"  [bold red]Error with Tidal playlist: {e}[/]")
        log.error(f"Failed to create/find Tidal playlist: {e}", exc_info=True)
        log_lines.append(f"ERROR: Failed to create/find Tidal playlist: {e}")
        if log_dir:
            write_log_file(log_dir, playlist_name, log_lines)
        return None

    # Start collecting track reports for this playlist
    if report_collector:
        # Compute CSV path based on M3U template (saves CSV next to M3U file)
        csv_path = _compute_csv_path_for_playlist(
            ctx=ctx,
            tidal_playlist_uuid=tidal_playlist_uuid,
            download_path=CONFIG.download.download_path,
        )
        report_collector.start_playlist(playlist_name, tidal_playlist_uuid, csv_path=csv_path)

    # Convert and add tracks to Tidal (immediately, one by one)
    log_lines.append("")
    log_lines.append("Track Conversion Results:")
    log_lines.append("-" * 80)

    added_tracks = []
    failed_tracks = []
    skipped_tracks = []
    skipped_by_metadata = 0  # Track how many were skipped via metadata match
    fallback_found = 0  # Track how many were found via Tidal search

    api = ctx.obj.api

    # Use Progress bar only when not using fancy UI
    if not ui:
        worst_case_minutes = (len(spotify_tracks) * 6) / 60
        console.print(f"  Converting & adding tracks... (max ~{worst_case_minutes:.0f} min due to rate limiting)")

    # Create progress context - only active when not using fancy UI
    progress_context = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TextColumn("ETA:"),
        TimeRemainingColumn(),
        console=console,
        disable=ui is not None,  # Disable progress bar when using fancy UI
    )

    with progress_context as progress:
        task = progress.add_task("  Processing...", total=len(spotify_tracks)) if not ui else None

        for track_idx, spotify_track in enumerate(spotify_tracks, 1):
            track_name = spotify_track['name']
            artists = ', '.join([artist['name'] for artist in spotify_track['artists']])
            track_info = f"{track_name} - {artists}"

            # Update UI with current track
            if ui:
                ui.update_track(track_idx, track_info[:50])

            tidal_id = None
            source = None
            added = False

            # Step 0: Check if track already exists via metadata matching (skip conversion entirely)
            matched_id = match_spotify_to_existing_tidal(spotify_track, existing_tracks_metadata)
            if matched_id:
                skipped_tracks.append(track_info)
                skipped_by_metadata += 1
                log_lines.append(f"SKIPPED (metadata match): {track_info}")
                if ui:
                    ui.track_skipped(track_info[:40])
                if report_collector:
                    report_collector.add_track(
                        playlist_name=playlist_name,
                        spotify_track=spotify_track,
                        tidal_id=matched_id,
                        migration_status="skipped",
                        migration_source="metadata_match",
                    )
                if task is not None:
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
                    if ui:
                        ui.track_skipped(track_info[:40])
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
                        if ui:
                            ui.track_added(track_info[:40])
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
                                        if ui:
                                            ui.track_added(track_info[:40])
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
                            if ui:
                                ui.track_failed(track_info[:30], "add failed")
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
                if ui:
                    ui.track_failed(track_info[:30], "not found")
                if report_collector:
                    report_collector.add_track(
                        playlist_name=playlist_name,
                        spotify_track=spotify_track,
                        tidal_id=None,
                        migration_status="not_found",
                        migration_source="",
                    )

            if task is not None:
                progress.update(task, advance=1)

    # Only print summary if not using fancy UI
    if not ui:
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
        if not ui:
            console.print(f"  [bold green]✓ Playlist migrated successfully![/]")
            console.print(f"  [dim]Tidal Playlist UUID: {tidal_playlist_uuid}[/]")
        log_lines.append(f"Successfully added {len(added_tracks)} tracks to Tidal playlist")
    elif skipped_tracks and not failed_tracks:
        if not ui:
            console.print(f"  [bold green]✓ Playlist already up to date![/]")
            console.print(f"  [dim]Tidal Playlist UUID: {tidal_playlist_uuid}[/]")
        log_lines.append("No new tracks needed to be added")
    elif not existing_track_ids and not added_tracks:
        if not ui:
            console.print(f"  [bold red]Playlist is empty and no tracks could be added.[/]\n")
        if log_dir:
            write_log_file(log_dir, playlist_name, log_lines)
        return None
    else:
        if not ui:
            console.print(f"  [yellow]Playlist partially migrated[/]")
            console.print(f"  [dim]Tidal Playlist UUID: {tidal_playlist_uuid}[/]")

    if not ui:
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


@migrate_command.command(help="Fix M3U playlists to use relative paths.")
def fix_m3u(
    M3U_DIR: Annotated[
        Path,
        typer.Option(
            "--m3u-dir",
            "-d",
            help="Directory containing M3U files to fix.",
        ),
    ] = Path.home() / "Music" / "tiddl" / "m3u",
    RECURSIVE: Annotated[
        bool,
        typer.Option(
            "--recursive/--no-recursive",
            "-r",
            help="Search for M3U files recursively in subdirectories.",
        ),
    ] = True,
    DRY_RUN: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Show what would be changed without actually modifying files.",
        ),
    ] = False,
):
    """
    Fix existing M3U playlist files to use relative paths.

    This command scans for M3U files and rewrites them to use paths
    relative to the M3U file location instead of absolute paths.
    This makes playlists portable and usable when moved to different locations.

    Example:
        Before: /Users/john/Music/tiddl/Artist/Album/Song.flac
        After:  ../../Artist/Album/Song.flac
    """
    from tiddl.core.utils.m3u import regenerate_m3u_with_relative_paths

    if not M3U_DIR.exists():
        console.print(f"[red]Directory does not exist: {M3U_DIR}[/]")
        raise typer.Exit(1)

    # Find all M3U files
    if RECURSIVE:
        m3u_files = list(M3U_DIR.rglob("*.m3u"))
    else:
        m3u_files = list(M3U_DIR.glob("*.m3u"))

    if not m3u_files:
        console.print(f"[yellow]No M3U files found in {M3U_DIR}[/]")
        raise typer.Exit()

    console.print(f"[cyan]Found {len(m3u_files)} M3U file(s)[/]\n")

    if DRY_RUN:
        console.print("[yellow]DRY RUN - No changes will be made[/]\n")

    fixed_count = 0
    error_count = 0

    for m3u_file in m3u_files:
        relative_path = m3u_file.relative_to(M3U_DIR) if m3u_file.is_relative_to(M3U_DIR) else m3u_file.name

        if DRY_RUN:
            # Just check if file has absolute paths
            try:
                content = m3u_file.read_text(encoding="utf-8")
                has_absolute = any(
                    line.startswith("/") or (len(line) > 2 and line[1] == ":")
                    for line in content.splitlines()
                    if line and not line.startswith("#")
                )
                if has_absolute:
                    console.print(f"  [yellow]Would fix:[/] {relative_path}")
                    fixed_count += 1
                else:
                    console.print(f"  [dim]Already relative:[/] {relative_path}")
            except Exception as e:
                console.print(f"  [red]Error reading:[/] {relative_path} - {e}")
                error_count += 1
        else:
            try:
                success = regenerate_m3u_with_relative_paths(m3u_file)
                if success:
                    console.print(f"  [green]Fixed:[/] {relative_path}")
                    fixed_count += 1
                else:
                    console.print(f"  [red]Failed:[/] {relative_path}")
                    error_count += 1
            except Exception as e:
                console.print(f"  [red]Error:[/] {relative_path} - {e}")
                error_count += 1

    console.print()
    if DRY_RUN:
        console.print(f"[bold yellow]DRY RUN: Would fix {fixed_count} file(s), {error_count} error(s)[/]")
    else:
        console.print(f"[bold green]Fixed {fixed_count} file(s), {error_count} error(s)[/]")
