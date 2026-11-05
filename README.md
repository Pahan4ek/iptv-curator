# iptv-curator

[![tests](https://github.com/Pahan4ek/iptv-curator/actions/workflows/ci.yml/badge.svg)](https://github.com/Pahan4ek/iptv-curator/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-yellow)

Builds a clean, working IPTV playlist out of the public [iptv-org](https://github.com/iptv-org/iptv) feeds and pushes it to a GitHub Gist, so your TV box (TiviMate, OTT Navigator, VLC, Kodi) always points at one URL that keeps itself fresh.

Public IPTV lists are mostly dead links, duplicates and teleshopping. This script pulls them, actually opens every stream, keeps the ones that respond, picks the best copy of each channel and writes a sorted M3U.

On a typical run (eng + rus, home connection) it fetches ~3.7k entries, checks ~2.6k streams in about 50 seconds and ends up with ~1.8k alive, which then get trimmed to per-language quotas.

## How it works

1. **Fetch** language playlists from iptv-org, plus any extra M3U URLs or local files from the config.
2. **Filter** by category, anti-trash keywords (shops, casinos, test cards), blacklist/whitelist and an optional regex.
3. **Check** every stream with `aiohttp` (40 concurrent requests by default, 4s timeout). Reads only the first 4 KB: rejects HTML error pages served with HTTP 200, accepts HLS playlists, MPEG-TS (0x47 sync byte) and known media content types.
4. **Probe quality** by parsing `#EXT-X-STREAM-INF` in HLS master manifests to get the real resolution and bitrate instead of trusting the channel name.
5. **Deduplicate** by normalized channel name: highest resolution wins, then bitrate, then latency.
6. **Apply quotas** per language and write the playlist with EPG links, UHD channels on top.
7. **Sync** to a secret gist. The raw URL doesn't change between updates, so the TV picks up new versions on its own.

## Quick start

```bash
git clone https://github.com/Pahan4ek/iptv-curator.git
cd iptv-curator
pip install -r requirements.txt
python iptv_updater.py
```

On Windows you can just double-click `run.bat`.

Presets:

```bash
python iptv_updater.py --profile cinema      # movies & series, 720p+
python iptv_updater.py --profile sports      # sports, 720p+
python iptv_updater.py --profile kids        # cartoons and kids channels
python iptv_updater.py --profile news        # news only
python iptv_updater.py --lang eng --min-res 1080p
python iptv_updater.py --no-check            # skip stream checks, fast dry run
```

## Config

Copy `config.example.json` to `config.json` and edit. The interesting bits:

| key | what it does |
| --- | --- |
| `languages` / `primary_language` | iptv-org language codes (`eng`, `rus`, `ukr`, ...) and which one goes first |
| `content_filters` | anti-trash keywords, blacklist, whitelist, custom regex |
| `sources.custom_m3u_urls` / `local_m3u_files` | extra playlists to merge in |
| `max_channels_per_language` | quota per language after dedup |
| `max_concurrent_checks` / `stream_check_timeout_seconds` | checker tuning |

`config.json` is gitignored.

## Gist sync

1. Create a classic token with only the `gist` scope at [github.com/settings/tokens](https://github.com/settings/tokens).
2. Put it in an env var (don't put it in a file you might commit):
   ```bash
   export GITHUB_TOKEN=ghp_...        # PowerShell: $env:GITHUB_TOKEN="ghp_..."
   ```
3. Run the script. The first run creates a secret gist and stores its id in `config.json`; later runs update the same gist. It prints a URL like
   `https://gist.githubusercontent.com/<user>/<gist_id>/raw/playlist.m3u`
4. Add that URL to TiviMate / OTT Navigator as an M3U playlist.

"Secret" here is GitHub's term for unlisted: the gist doesn't show up in search or on your profile, but anyone with the link can open it.

`GIST_ID` can also be passed as an env var, it overrides the config.

### Auto-update with GitHub Actions

`.github/workflows/update-playlist.yml` rebuilds the playlist every Monday. Add two repository secrets:

- `GIST_TOKEN`: the token from above
- `GIST_ID`: id of the gist created on your first local run

You can also trigger it by hand from the Actions tab.

## Web player

`player.html` is a small local player on top of hls.js. Open it in a browser, drop `playlist.m3u` on it, pick a channel. Useful for checking a playlist before you put it on the TV.

## Tests

```bash
pip install pytest
pytest -q
```

They cover M3U parsing, HLS manifest probing, filters, dedup and playlist generation. CI runs them on Python 3.10 to 3.13.

## Note on content

The script doesn't host or bundle any streams. It only reads public playlists maintained by iptv-org and checks whether the links they list are reachable. What ends up in the playlist depends on those sources and your config.

## License

MIT
