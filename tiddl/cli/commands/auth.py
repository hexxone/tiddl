import typer
from datetime import datetime
from time import time, sleep
from rich.console import Console

from tiddl.cli.utils.auth.core import load_auth_data, save_auth_data, AuthData
from tiddl.cli.utils.spotify import load_spotify_credentials, save_spotify_credentials, SpotifyCredentials
from tiddl.core.auth import AuthAPI, AuthClientError
from tiddl.core.spotify import SpotifyClient

from typing_extensions import Annotated

console = Console()

auth_command = typer.Typer(
    name="auth", help="Manage Tidal authentication.", no_args_is_help=True
)


# TODO add context and load auth data from ctx
@auth_command.command(help="Login with your Tidal account.")
def login():
    loaded_auth_data = load_auth_data()

    if loaded_auth_data.token:
        console.print("[cyan bold]Already logged in.")
        raise typer.Exit()

    auth_api = AuthAPI()
    device_auth = auth_api.get_device_auth()

    uri = f"https://{device_auth.verificationUriComplete}"
    typer.launch(uri)

    console.print(f"Go to '{uri}' and complete authentication!")

    auth_end_at = time() + device_auth.expiresIn

    status_text = "Authenticating..."

    with console.status(status_text) as status:
        while True:
            sleep(device_auth.interval)

            try:
                auth = auth_api.get_auth(device_auth.deviceCode)
                auth_data = AuthData(
                    token=auth.access_token,
                    refresh_token=auth.refresh_token,
                    expires_at=auth.expires_in + int(time()),
                    user_id=str(auth.user_id),
                    country_code=auth.user.countryCode,
                )
                save_auth_data(auth_data)
                status.console.print("[bold green]Logged in!")
                break

            except AuthClientError as e:
                if e.error == "authorization_pending":
                    time_left = auth_end_at - time()
                    minutes, seconds = time_left // 60, int(time_left % 60)
                    status.update(
                        f"{status_text} time left: {minutes:.0f}:{seconds:02d}"
                    )
                    continue

                if e.error == "expired_token":
                    status.console.print(
                        "\n[bold red]Time for authentication has expired."
                    )
                    break


@auth_command.command(help="Logout and remove token from app.")
def logout():
    loaded_auth_data = load_auth_data()

    if loaded_auth_data.token:
        auth_api = AuthAPI()
        auth_api.logout_token(loaded_auth_data.token)

    save_auth_data(AuthData())

    console.print("[bold green]Logged out!")


@auth_command.command(help="Refreshes your token in app.")
def refresh(
    FORCE: Annotated[
        bool,
        typer.Option(
            "--force", "-f", help="Refresh token even when it is still valid."
        ),
    ] = False,
    EARLY_EXPIRE_TIME: Annotated[
        int,
        typer.Option(
            "--early-expire",
            "-e",
            help="Time to expire the token earlier",
            metavar="seconds",
        ),
    ] = 0,
):
    loaded_auth_data = load_auth_data()

    if loaded_auth_data.refresh_token is None:
        console.print("[bold red]Not logged in.")
        raise typer.Exit()

    if time() < (loaded_auth_data.expires_at - EARLY_EXPIRE_TIME) and not FORCE:
        expiry_time = datetime.fromtimestamp(loaded_auth_data.expires_at)
        remaining = expiry_time - datetime.now()
        hours, remainder = divmod(remaining.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        console.print(
            f"[green]Auth token expires in {remaining.days}d {hours}h {minutes}m"
        )
        return

    auth_api = AuthAPI()
    auth_data = auth_api.refresh_token(loaded_auth_data.refresh_token)

    loaded_auth_data.token = auth_data.access_token
    loaded_auth_data.expires_at = auth_data.expires_in + int(time())

    save_auth_data(loaded_auth_data)

    console.print("[bold green]Auth token has been refreshed!")


@auth_command.command(help="Setup Spotify API credentials.")
def spotify_setup(
    CLIENT_ID: Annotated[
        str,
        typer.Option(
            "--client-id",
            prompt="Spotify Client ID",
            help="Your Spotify application client ID",
        ),
    ],
    CLIENT_SECRET: Annotated[
        str,
        typer.Option(
            "--client-secret",
            prompt="Spotify Client Secret",
            help="Your Spotify application client secret",
        ),
    ],
):
    credentials = SpotifyCredentials(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
    )
    save_spotify_credentials(credentials)
    console.print("[bold green]Spotify credentials saved!")
    console.print("[cyan]You can now run 'tiddl auth spotify-login' to authenticate.")


@auth_command.command(help="Login with your Spotify account.")
def spotify_login():
    credentials = load_spotify_credentials()

    if not credentials.client_id or not credentials.client_secret:
        console.print("[bold red]Spotify credentials not found!")
        console.print("Please run 'tiddl auth spotify-setup' first.")
        console.print("\nTo get credentials:")
        console.print("1. Go to https://developer.spotify.com/dashboard")
        console.print("2. Create an app (or use existing)")
        console.print("3. Add 'https://example.com/callback' to Redirect URIs")
        console.print("   (Note: Spotify requires HTTPS and doesn't accept localhost)")
        console.print("4. Copy Client ID and Client Secret")
        raise typer.Exit()

    client = SpotifyClient(
        client_id=credentials.client_id,
        client_secret=credentials.client_secret,
    )

    if client.is_authenticated():
        console.print("[cyan bold]Already logged in to Spotify.")
        raise typer.Exit()

    auth_url, state = client.get_auth_url()
    console.print(f"\n[bold]Opening browser for Spotify authentication...[/]")
    console.print(f"[dim]URL: {auth_url}[/]\n")

    typer.launch(auth_url)

    console.print("[yellow]After authorizing:[/]")
    console.print("1. You'll be redirected to a page that won't load (example.com/callback)")
    console.print("2. That's OK! Copy the FULL URL from your browser's address bar")
    console.print("3. The URL will look like: https://example.com/callback?code=AQB...&state=...")
    console.print("4. Paste it below\n")

    redirect_response = typer.prompt("Paste the full redirect URL")

    # Extract code from URL
    try:
        if "code=" in redirect_response:
            code = redirect_response.split("code=")[1].split("&")[0]
            client.get_access_token_from_code(code)
            console.print("[bold green]Successfully logged in to Spotify!")
        else:
            console.print("[bold red]Invalid URL. No authorization code found.")
            console.print("Make sure you copied the full URL from the browser.")
    except Exception as e:
        console.print(f"[bold red]Authentication failed: {e}")


@auth_command.command(help="Logout from Spotify.")
def spotify_logout():
    from tiddl.cli.const import APP_PATH
    cache_path = APP_PATH / ".spotify_cache"

    if cache_path.exists():
        cache_path.unlink()
        console.print("[bold green]Logged out from Spotify!")
    else:
        console.print("[yellow]Not logged in to Spotify.")
