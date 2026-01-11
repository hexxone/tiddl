# Tidal Downloader

Download tracks and videos from Tidal with max quality! `tiddl` is CLI app written in Python.

> [!WARNING]
> `This app is for personal use only and is not affiliated with Tidal. Users must ensure their use complies with Tidal's terms of service and local copyright laws. Downloaded tracks are for personal use and may not be shared or redistributed. The developer assumes no responsibility for misuse of this app.`

![PyPI - Downloads](https://img.shields.io/pypi/dm/tiddl?style=for-the-badge&color=%2332af64)
![PyPI - Version](https://img.shields.io/pypi/v/tiddl?style=for-the-badge)
[<img src="https://img.shields.io/badge/gitmoji-%20😜%20😍-FFDD67.svg?style=for-the-badge" />](https://gitmoji.dev)

# Installation

`tiddl` is available at [python package index](https://pypi.org/project/tiddl/) and you can install it with your favorite Python package manager.

> [!IMPORTANT]
> Also make sure you have installed  [`ffmpeg`](https://ffmpeg.org/download.html) - it is used to convert downloaded tracks to proper format.

## uv

We recommend using [uv](https://docs.astral.sh/uv/)

```bash
uv tool install tiddl
```

## pip

You can also use [pip](https://packaging.python.org/en/latest/tutorials/installing-packages/)

```bash
pip install tiddl
```

## docker

**coming soon**

# Usage

Run the app with `tiddl`

```bash
$ tiddl
 Usage: tiddl [OPTIONS] COMMAND [ARGS]...

 tiddl - download tidal tracks ♫

╭─ Options ───────────────────────────────────────────────────────────────────────────────────────────────────╮
│ --omit-cache            --no-omit-cache      [default: no-omit-cache]                                       │
│ --debug                 --no-debug           [default: no-debug]                                            │
│ --install-completion                         Install completion for the current shell.                      │
│ --show-completion                            Show completion for the current shell, to copy it or customize │
│                                              the installation.                                              │
│ --help                                       Show this message and exit.                                    │
╰─────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ──────────────────────────────────────────────────────────────────────────────────────────────────╮
│ auth       Manage Tidal authentication.                                                                     │
│ download   Download Tidal resources.                                                                        │
│ migrate    Migrate playlists from Spotify to Tidal.                                                         │
╰─────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
```

## Features

- 🎵 Download tracks, videos, albums, artists, playlists, and mixes from Tidal
- 🎧 Support for maximum audio quality (up to 24-bit, 192 kHz FLAC)
- 🔄 **Migrate playlists from Spotify to Tidal** with interactive selection
- 📝 Automatic metadata tagging with lyrics support
- 🎹 **Extended metadata**: BPM, musical key, Camelot notation, genres, mood
- 🎨 Cover art embedding and saving
- 🔍 Smart file organization with custom templates
- ⚡ Concurrent downloads with configurable thread count
- 🚫 Skip existing files with automatic integrity verification
- 📋 M3U playlist generation with portable relative paths

## Authentication

### Tidal Authentication

Login to app with your Tidal account: run the command below and follow instructions.

```bash
tiddl auth login
```

### Spotify Authentication (for Migration)

To migrate playlists from Spotify, you need to set up Spotify API credentials:

1. Get Spotify API credentials from [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
   - Add `https://example.com/callback` to Redirect URIs
2. Configure credentials:
   ```bash
   tiddl auth spotify-setup
   ```
3. Login to Spotify (opens browser, copy/paste URL):
   ```bash
   tiddl auth spotify-login
   ```

See [Spotify Migration Guide](docs/spotify_migration.md) for detailed instructions.

### GetSongBPM API (Optional)

To enable musical key detection (e.g., "Am", "C#") and Camelot notation (e.g., "8A", "5B") in your downloaded files, set up a GetSongBPM API key:

1. Get a free API key from [GetSongBPM](https://getsongbpm.com/api)
2. Configure the API key:
   ```bash
   tiddl auth getsongbpm-setup
   ```

> [!NOTE]
> GetSongBPM is free but requires attribution. MusicBrainz (for genres/tags) requires no API key.

### Check Authentication Status

View the status of all configured services:

```bash
tiddl auth status
```

## Migrating from Spotify

Migrate your Spotify playlists to Tidal and automatically download them:

```bash
tiddl migrate spotify-to-tidal
```

This will:
1. Fetch all your Spotify playlists
2. Let you interactively select which ones to migrate (toggle on/off)
3. Convert tracks from Spotify to Tidal
4. Create/update playlists in Tidal (Spotify is source of truth)
5. Download the migrated playlists

**Interactive Selection:**
- Toggle playlists by number: `1,2,3` or `1-5`
- Toggle by owner: `@username` (selects all playlists by that user)
- Quick commands: `all`, `none`, `mine`, `invert`

**Options:**
- `--dry-run`: Preview without making changes
- `--no-download`: Migrate without downloading
- `--select <selection>`: Non-interactive mode (e.g., `--select mine`)

See the [Spotify Migration Guide](docs/spotify_migration.md) for complete details.

## Downloading

You can download tracks / videos / albums / artists / playlists / mixes.

```bash
$ tiddl download url <url>
```

> [!TIP]
> You don't have to paste full urls, track/103805726, album/103805723 etc. will also work

Run `tiddl download` to see available download options.

### Error Handling

By default, tiddl stops when encountering unavailable items in collections such as playlists, albums, artists, or mixes (e.g., removed or region-locked tracks).

Use `--skip-errors` to automatically skip these items and continue downloading:

```bash
tiddl download url <url> --skip-errors
```

Skipped items are logged with track/album name and IDs for reference.

### File Integrity Verification

By default, tiddl verifies existing files using ffprobe before skipping them. If a file is corrupted or incomplete, it will be automatically deleted and redownloaded:

```bash
# Default behavior - verifies existing files
tiddl download url <url>

# Disable verification for faster skipping (not recommended)
tiddl download url <url> --no-verify
```

The verification checks:
- File exists and has content
- ffprobe can parse the file (valid audio format)
- File contains an audio stream
- Duration matches expected length

### Metadata Enrichment

tiddl can fetch additional metadata from external APIs and embed it in your audio files:

```bash
# Enable metadata enrichment
tiddl download url <url> --enrich
```

**Sources:**
- **Tidal API**: BPM (when available)
- **MusicBrainz**: Genres and tags (lookup by ISRC, no API key needed)
- **GetSongBPM**: Musical key and Camelot notation (requires free API key)

**Embedded tags:**
| Tag | FLAC | M4A | Description |
|-----|------|-----|-------------|
| BPM | `BPM` | `bpm` | Tempo in beats per minute |
| Key | `INITIALKEY` | iTunes atom | Musical key (e.g., "Am", "C#") |
| Camelot | `KEY` | iTunes atom | Camelot notation (e.g., "8A", "5B") |
| Genre | `GENRE` | `genre` | Music genres |
| Mood | `MOOD` | iTunes atom | Mood tag (e.g., "Energetic") |

To enable enrichment by default, add to your `config.toml`:

```toml
[metadata.enrichment]
enable = true
musicbrainz = true   # Genres/tags (free)
getsongbpm = true    # Key/BPM (requires API key)
```

### Quality

| Quality | File extension |        Details        |
| :-----: | :------------: | :-------------------: |
|   LOW   |      .m4a      |        96 kbps        |
| NORMAL  |      .m4a      |       320 kbps        |
|  HIGH   |     .flac      |   16-bit, 44.1 kHz    |
|   MAX   |     .flac      | Up to 24-bit, 192 kHz |

### Output

You can format filenames of your downloaded resources and put them in different directories.

For example, setting output flag to `"{album.artist}/{album.title}/{item.number:02d}. {item.title}"`
will download tracks like following:

```
Music
└── Kanye West
    └── Graduation
        ├── 01. Good Morning.flac
        ├── 02. Champion.flac
        ├── 03. Stronger.flac
        ├── 04. I Wonder.flac
        ├── 05. Good Life.flac
        ├── 06. Can't Tell Me Nothing.flac
        ├── 07. Barry Bonds.flac
        ├── 08. Drunk and Hot Girls.flac
        ├── 09. Flashing Lights.flac
        ├── 10. Everything I Am.flac
        ├── 11. The Glory.flac
        ├── 12. Homecoming.flac
        ├── 13. Big Brother.flac
        └── 14. Good Night.flac
```

> [!NOTE]
> Learn more about [file templating](/docs/templating.md)

### M3U Playlists

tiddl can generate M3U playlist files for downloaded albums and playlists. These files use **relative paths** by default, making them portable - you can move your music folder and the playlists will still work.

To fix existing M3U files that use absolute paths:

```bash
# Preview what would be changed
tiddl migrate fix-m3u --dry-run

# Fix all M3U files in the default directory
tiddl migrate fix-m3u

# Fix M3U files in a custom directory
tiddl migrate fix-m3u --m3u-dir /path/to/m3u
```

## Configuration files

Files of the app are created in your home directory. By default, the app is located at `~/.tiddl`.

You can (and should) create the `config.toml` file to configure the app how you want.

You can copy example config from docs [config.example.toml](/docs/config.example.toml)

## Environment variables

### Custom app path

You can set `TIDDL_PATH` environment variable to use custom path for `tiddl` app.

Example CLI usage:

```sh
TIDDL_PATH=~/custom/tiddl tiddl auth login
```

### Auth stopped working?

Set `TIDDL_AUTH` environment variable to use another credentials.

TIDDL_AUTH=<CLIENT_ID>;<CLIENT_SECRET>

# Development

Clone the repository

```bash
git clone https://github.com/oskvr37/tiddl
cd tiddl
```

You should create virtual environment and activate it

```bash
uv venv
source .venv/Scripts/activate
```

Install package with `--editable` flag

```bash
uv pip install -e .
```

# Resources

[Tidal API wiki (api endpoints)](https://github.com/Fokka-Engineering/TIDAL)

[Tidal-Media-Downloader (inspiration)](https://github.com/yaronzz/Tidal-Media-Downloader)
