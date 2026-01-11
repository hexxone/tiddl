from pathlib import Path
from logging import getLogger
from pydantic import BaseModel

from tiddl.cli.config import APP_PATH


GETSONGBPM_CREDENTIALS_FILE = APP_PATH / "getsongbpm_credentials.json"


log = getLogger(__name__)


class GetSongBPMCredentials(BaseModel):
    api_key: str = ""


def load_getsongbpm_credentials(file: Path = GETSONGBPM_CREDENTIALS_FILE) -> GetSongBPMCredentials:
    log.debug(f"loading from '{GETSONGBPM_CREDENTIALS_FILE}'")

    try:
        file_content = file.read_text()
    except FileNotFoundError:
        return GetSongBPMCredentials()

    credentials = GetSongBPMCredentials.model_validate_json(file_content)

    return credentials


def save_getsongbpm_credentials(credentials: GetSongBPMCredentials, file: Path = GETSONGBPM_CREDENTIALS_FILE):
    log.debug(f"saving to '{file}'")

    with file.open("w") as f:
        f.write(credentials.model_dump_json())
