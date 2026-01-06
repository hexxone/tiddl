# Spotify to Tidal Migration

This guide explains how to migrate your Spotify playlists to Tidal using tiddl.

## Prerequisites

1. A Tidal account with active subscription
2. A Spotify account
3. Spotify API credentials (Client ID and Client Secret)

## Getting Spotify API Credentials

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Log in with your Spotify account
3. Click "Create an App"
4. Fill in the app name and description (e.g., "Tiddl Migration")
5. Accept the terms and conditions
6. Click "Create"
7. In your app settings, click "Edit Settings"
8. Add `https://example.com/callback` to the "Redirect URIs" field
   - **Important:** Spotify requires HTTPS and does not accept localhost URLs
   - You can use `https://example.com/callback` - it doesn't need to be a real website
9. Click "Save"
10. Copy your **Client ID** and **Client Secret** from the app dashboard

## Setup

### 1. Configure Spotify Credentials

```bash
tiddl auth spotify-setup
```

You'll be prompted to enter your Spotify Client ID and Client Secret.

### 2. Login to Spotify

```bash
tiddl auth spotify-login
```

This will open your browser for Spotify authentication. After authorizing:

1. You'll be redirected to a page that won't load (`https://example.com/callback?code=...`)
2. **That's expected!** The page doesn't need to load since example.com doesn't handle the callback
3. Copy the **FULL URL** from your browser's address bar
   - It will look like: `https://example.com/callback?code=AQB...&state=...`
4. Paste the URL into the terminal when prompted

The app will extract the authorization code and complete the login automatically.

### 3. Login to Tidal

Make sure you're also logged into Tidal:

```bash
tiddl auth login
```

## Migrating Playlists

### Basic Migration

To migrate and download all playlists:

```bash
tiddl migrate spotify-to-tidal
```

This will:
1. Fetch all your Spotify playlists
2. Display them in a table
3. Let you select which ones to migrate
4. Convert tracks from Spotify to Tidal using the Odesli API
5. Create/update playlists in Tidal
6. Download the migrated playlists

### Migration Options

#### Dry Run

Preview what would be migrated without making any changes:

```bash
tiddl migrate spotify-to-tidal --dry-run
```

#### Skip Download

Migrate playlists without downloading them:

```bash
tiddl migrate spotify-to-tidal --no-download
```

## Playlist Selection

When prompted, you can select playlists in several ways:

- **All playlists**: Type `all`
- **Specific playlists**: Type comma-separated numbers (e.g., `1,3,5`)
- **Cancel**: Type `none`

Example:
```
Select playlists to migrate:
Enter playlist numbers separated by commas (e.g., 1,3,5)
Or enter 'all' to migrate all playlists
Or enter 'none' to cancel

Your selection: 1,2,5
```

## How It Works

### Track Conversion

The migration uses the [Odesli API](https://odesli.co/) (also known as song.link) to convert Spotify tracks to Tidal tracks. This service:

- Matches tracks across streaming platforms
- Respects rate limits (10 requests per minute by default)
- May not find matches for all tracks (rare, region-locked, or very new tracks)

### Playlist Handling

When a playlist with the same name already exists in Tidal:

- **Spotify is treated as the source of truth**
- The existing Tidal playlist is cleared
- All tracks from Spotify are added in the same order

This ensures that if you re-run the migration, your Tidal playlists will be updated to match Spotify exactly.

### Parallel Processing

The migration processes playlists **sequentially** but converts tracks **track-by-track** with progress indicators. This ensures:

- Proper ordering is maintained
- Rate limits are respected
- You can see progress in real-time

## Troubleshooting

### "Spotify credentials not found"

Run `tiddl auth spotify-setup` to configure your credentials.

### "Not logged in to Spotify"

Run `tiddl auth spotify-login` to authenticate.

### Track conversion failures

Some tracks may not be available on Tidal or might not have a match in the Odesli database. The migration will:

- Continue with other tracks
- Show a summary of failed conversions
- List the first 5 failed tracks

### Rate limiting

The Odesli API has a rate limit of 10 requests per minute. The migration tool automatically handles this by:

- Tracking request times
- Waiting when necessary
- Showing progress indicators during waits

### Authentication expired

If your Spotify authentication expires:

```bash
# Logout and login again
tiddl auth spotify-logout
tiddl auth spotify-login
```

## Tips

1. **Test with a few playlists first**: Use the selection feature to migrate a couple of playlists before doing all of them
2. **Use dry-run**: Check what would be migrated with `--dry-run` before committing
3. **Check your Tidal library**: After migration, verify the playlists in your Tidal app
4. **Re-run anytime**: You can re-run the migration to update your Tidal playlists with any changes from Spotify

## Logout

To logout from Spotify:

```bash
tiddl auth spotify-logout
```

This removes the Spotify authentication cache.
