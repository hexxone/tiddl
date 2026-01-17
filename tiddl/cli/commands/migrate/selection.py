"""
Interactive playlist selection with toggle support.
"""

from typing import Optional
from rich.console import Console
from rich.table import Table


def get_owner_name(playlist: dict) -> str:
    """Extract owner display name from playlist."""
    return playlist['owner'].get('display_name') or playlist['owner'].get('id', 'Unknown')


def get_unique_owners(playlists: list[dict]) -> dict[str, list[int]]:
    """Get unique owners and their playlist indices (1-based)."""
    owners: dict[str, list[int]] = {}
    for idx, playlist in enumerate(playlists, 1):
        owner = get_owner_name(playlist).lower()
        if owner not in owners:
            owners[owner] = []
        owners[owner].append(idx)
    return owners


def display_playlists_with_selection(
    console: Console,
    playlists: list[dict],
    selected: set[int],
    user_id: Optional[str] = None,
):
    """Display playlists table with selection checkboxes."""
    table = Table(title="Your Spotify Playlists", show_header=True, header_style="bold magenta")
    table.add_column("", width=3)  # Checkbox column
    table.add_column("#", style="dim", width=4)
    table.add_column("Name", style="cyan", max_width=40)
    table.add_column("Tracks", justify="right", style="green", width=6)
    table.add_column("Owner", style="yellow")

    for idx, playlist in enumerate(playlists, 1):
        is_liked_songs = playlist.get('_is_liked_songs', False)
        owner_name = get_owner_name(playlist)
        is_owner = user_id and playlist['owner']['id'] == user_id

        # Selection checkbox
        checkbox = "[green]✓[/]" if idx in selected else "[dim]○[/]"

        # Special display for Liked Songs
        if is_liked_songs:
            name = "[bold magenta]♥ Liked Songs[/]"
            owner_display = f"[bold green]★ {owner_name}[/]"
        else:
            # Mark owned playlists
            if is_owner:
                owner_display = f"[bold green]★ {owner_name}[/]"
            else:
                owner_display = owner_name

            # Highlight selected rows
            name = playlist['name']
            if idx in selected:
                name = f"[bold]{name}[/]"

        table.add_row(
            checkbox,
            str(idx),
            name,
            str(playlist['tracks']['total']),
            owner_display
        )

    console.print(table)

    # Show selection summary
    total_tracks = sum(p['tracks']['total'] for i, p in enumerate(playlists, 1) if i in selected)
    console.print(f"\n[cyan]Selected:[/] {len(selected)} playlist(s), {total_tracks} total tracks")


def display_help(console: Console, owners: dict[str, list[int]]):
    """Display help for selection commands."""
    console.print("\n[bold]Selection Commands:[/]")
    console.print("  [cyan]1,2,3[/]     Toggle specific playlists by number")
    console.print("  [cyan]1-5[/]       Toggle a range of playlists")
    console.print("  [cyan]@owner[/]    Toggle all playlists by owner name")
    console.print("  [cyan]all[/]       Select all playlists")
    console.print("  [cyan]none[/]      Deselect all playlists")
    console.print("  [cyan]mine[/]      Select only your own playlists")
    console.print("  [cyan]liked[/]     Select only Liked Songs")
    console.print("  [cyan]invert[/]    Invert current selection")
    console.print("  [cyan]done[/]      Confirm selection and continue")
    console.print("  [cyan]help[/]      Show this help")
    console.print("  [dim]Press Enter with empty input to confirm[/]")

    if owners:
        console.print("\n[bold]Available owners:[/]")
        for owner, indices in sorted(owners.items()):
            console.print(f"  [yellow]@{owner}[/] ({len(indices)} playlists)")


def parse_selection_input(
    input_str: str,
    playlists: list[dict],
    current_selection: set[int],
    user_id: Optional[str],
    owners: dict[str, list[int]],
) -> tuple[set[int], str]:
    """
    Parse user input and return updated selection.

    Returns:
        (new_selection, message) - message is empty on success, error message on failure
    """
    input_str = input_str.strip()

    if not input_str or input_str.lower() == 'done':
        return current_selection, ""

    new_selection = current_selection.copy()

    # Handle special commands
    cmd = input_str.lower()

    if cmd == 'all':
        return set(range(1, len(playlists) + 1)), "Selected all playlists"

    if cmd == 'none':
        return set(), "Deselected all playlists"

    if cmd == 'mine':
        mine = {i for i, p in enumerate(playlists, 1)
                if user_id and p['owner']['id'] == user_id}
        return mine, f"Selected {len(mine)} owned playlists"

    if cmd == 'liked':
        liked = {i for i, p in enumerate(playlists, 1)
                 if p.get('_is_liked_songs', False)}
        if liked:
            return liked, "Selected Liked Songs"
        return current_selection, "No Liked Songs available"

    if cmd == 'invert':
        all_indices = set(range(1, len(playlists) + 1))
        return all_indices - current_selection, "Inverted selection"

    if cmd == 'help':
        return current_selection, "help"

    # Handle @owner selection
    if input_str.startswith('@'):
        owner_query = input_str[1:].lower().strip()
        if not owner_query:
            return current_selection, "Please specify an owner name after @"

        # Find matching owners (partial match)
        matching_indices = []
        matched_owners = []
        for owner, indices in owners.items():
            if owner_query in owner:
                matching_indices.extend(indices)
                matched_owners.append(owner)

        if not matching_indices:
            return current_selection, f"No owner found matching '{owner_query}'"

        # Toggle all matching playlists
        for idx in matching_indices:
            if idx in new_selection:
                new_selection.discard(idx)
            else:
                new_selection.add(idx)

        action = "toggled"
        return new_selection, f"Toggled {len(matching_indices)} playlists by {', '.join(matched_owners)}"

    # Handle number/range input
    try:
        parts = [p.strip() for p in input_str.replace(' ', ',').split(',') if p.strip()]

        toggled = []
        for part in parts:
            if '-' in part:
                # Range like "1-5"
                start, end = part.split('-', 1)
                start_idx = int(start.strip())
                end_idx = int(end.strip())
                if start_idx > end_idx:
                    start_idx, end_idx = end_idx, start_idx
                for idx in range(start_idx, end_idx + 1):
                    if 1 <= idx <= len(playlists):
                        if idx in new_selection:
                            new_selection.discard(idx)
                        else:
                            new_selection.add(idx)
                        toggled.append(idx)
            else:
                # Single number
                idx = int(part)
                if 1 <= idx <= len(playlists):
                    if idx in new_selection:
                        new_selection.discard(idx)
                    else:
                        new_selection.add(idx)
                    toggled.append(idx)
                else:
                    return current_selection, f"Invalid playlist number: {idx}"

        if toggled:
            return new_selection, f"Toggled playlist(s): {', '.join(map(str, sorted(set(toggled))))}"
        return current_selection, "No valid playlist numbers provided"

    except ValueError:
        return current_selection, f"Invalid input: '{input_str}'. Type 'help' for commands."


def interactive_playlist_selection(
    console: Console,
    playlists: list[dict],
    user_id: Optional[str] = None,
    default_mine: bool = True,
) -> list[dict]:
    """
    Interactive playlist selection with toggle support.

    Args:
        console: Rich console for output
        playlists: List of Spotify playlist dicts
        user_id: Current user's Spotify ID (to identify owned playlists)
        default_mine: If True, pre-select owned playlists

    Returns:
        List of selected playlist dicts
    """
    # Initialize selection
    if default_mine and user_id:
        selected = {i for i, p in enumerate(playlists, 1)
                   if p['owner']['id'] == user_id}
    else:
        selected = set()

    owners = get_unique_owners(playlists)

    # Initial display
    display_playlists_with_selection(console, playlists, selected, user_id)

    console.print("\n[bold]Toggle playlists:[/] Enter numbers (1,2,3), ranges (1-5), @owner, or commands")
    console.print("[dim]Type 'help' for all commands. Press Enter when done.[/]\n")

    while True:
        try:
            user_input = console.input("[bold cyan]Selection>[/] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Selection cancelled.")
            return []

        if not user_input or user_input.lower() == 'done':
            break

        new_selection, message = parse_selection_input(
            user_input, playlists, selected, user_id, owners
        )

        if message == "help":
            display_help(console, owners)
            continue

        if message and not message.startswith("Toggled") and not message.startswith("Selected") and not message.startswith("Deselected") and not message.startswith("Inverted"):
            # Error message
            console.print(f"[red]{message}[/]")
            continue

        selected = new_selection

        # Refresh display
        console.print()  # Add spacing
        display_playlists_with_selection(console, playlists, selected, user_id)

        if message:
            console.print(f"[green]{message}[/]\n")

    # Return selected playlists
    return [p for i, p in enumerate(playlists, 1) if i in selected]
