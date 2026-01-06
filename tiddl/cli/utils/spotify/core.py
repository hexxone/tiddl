from pathlib import Path
from logging import getLogger
from pydantic import BaseModel

from tiddl.cli.config import APP_PATH


SPOTIFY_CREDENTIALS_FILE = APP_PATH / "spotify_credentials.json"


log = getLogger(__name__)


class SpotifyCredentials(BaseModel):
    client_id: str = ""
    client_secret: str = ""


def load_spotify_credentials(file: Path = SPOTIFY_CREDENTIALS_FILE) -> SpotifyCredentials:
    log.debug(f"loading from '{SPOTIFY_CREDENTIALS_FILE}'")

    try:
        file_content = file.read_text()
    except FileNotFoundError:
        return SpotifyCredentials()

    credentials = SpotifyCredentials.model_validate_json(file_content)

    return credentials


def save_spotify_credentials(credentials: SpotifyCredentials, file: Path = SPOTIFY_CREDENTIALS_FILE):
    log.debug(f"saving to '{file}'")

    with file.open("w") as f:
        f.write(credentials.model_dump_json())
