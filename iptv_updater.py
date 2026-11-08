import argparse
import asyncio
import copy
import json
import os
import re
import sys
import time
from typing import Any

import aiohttp

from checker import check_single_stream
from gist_sync import GistSync

try:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG = {
    # Active profile: "balanced", "cinema", "sports", "kids", "news", "custom"
    "profile": "balanced",

    # Target languages: English is primary, Russian is included
    "languages": ["eng", "rus"],
    "primary_language": "eng",

    # Source settings
    "sources": {
        "iptv_org": True,
        "custom_m3u_urls": [],
        "local_m3u_files": []
    },

    # Resolution & quality filter: "all", "720p", "1080p", "4k"
    "min_resolution": "all",

    # Deduplicate channels and pick highest resolution/bitrate
    "deduplicate": True,

    # Categories filter
    "categories": [
        "News", "Sports", "Movies", "Music", "Animation",
        "Kids", "Entertainment", "Documentary", "General", "Series", "Comedy"
    ],
    "exclude_categories": ["XXX"],

    # Content & anti-trash filters
    "content_filters": {
        "enable_antitrash": True,
        "antitrash_keywords": [
            "shop", "магазин", "ювелир", "телемагазин", "commercial", "infomercial",
            "casino", "казино", "лотерея", "религия", "church", "test card"
        ],
        "blacklist_keywords": [],
        "whitelist_keywords": [],
        "custom_regex": ""
    },

    # Limits per language
    "max_channels_per_language": {
        "eng": 200,
        "rus": 150
    },

    # Stream checker
    "check_streams": True,
    "stream_check_timeout_seconds": 4.0,
    "max_concurrent_checks": 40,

    # Output & Gist sync
    "github_token": "",  # prefer the GITHUB_TOKEN env var
    "gist_id": "",
    "output_file": "playlist.m3u"
}

PRESETS = {
    "balanced": {
        "description": "Balanced: all main categories (news, movies, sports, kids, docs)",
        "categories": ["News", "Sports", "Movies", "Music", "Animation", "Kids", "Entertainment",
                       "Documentary", "General", "Series", "Comedy"],
        "min_resolution": "all"
    },
    "cinema": {
        "description": "Cinema: movies and series only, 720p and up",
        "categories": ["Movies", "Series"],
        "min_resolution": "720p"
    },
    "sports": {
        "description": "Sports: sports channels, 720p and up",
        "categories": ["Sports"],
        "min_resolution": "720p"
    },
    "kids": {
        "description": "Kids: cartoons, animation, kids shows",
        "categories": ["Animation", "Kids"],
        "min_resolution": "all"
    },
    "news": {
        "description": "News: news channels only",
        "categories": ["News"],
        "min_resolution": "all"
    }
}

CATEGORY_MAP_RU = {
    "general": "[RU] Общие",
    "news": "[RU] Новости",
    "sports": "[RU] Спорт",
    "movies": "[RU] Кино",
    "animation": "[RU] Мультфильмы",
    "kids": "[RU] Детские",
    "music": "[RU] Музыка",
    "entertainment": "[RU] Развлекательные",
    "documentary": "[RU] Познавательные",
    "series": "[RU] Сериалы",
    "comedy": "[RU] Юмор",
    "education": "[RU] Образование",
    "culture": "[RU] Культура",
    "classic": "[RU] Классика"
}

CATEGORY_MAP_UA = {
    "general": "[UA] Загальні",
    "news": "[UA] Новини",
    "sports": "[UA] Спорт",
    "movies": "[UA] Кіно",
    "animation": "[UA] Мультфільми",
    "kids": "[UA] Дитячі",
    "music": "[UA] Музика",
    "entertainment": "[UA] Розважальні",
    "documentary": "[UA] Пізнавальні",
    "series": "[UA] Серіали",
    "comedy": "[UA] Гумор"
}

UHD_4K_CHANNELS = [
    {
        "name": "Love Nature 4K (2160p)",
        "url": "https://pb-ehs1glsha1juy.akamaized.net/Love_Nature_4K.m3u8",
        "group": "[UHD] 4K Channels",
        "raw_group": "4K UHD",
        "lang": "uhd",
        "tvg_logo": "https://i.imgur.com/xT0gWnL.png",
        "tvg_id": "LoveNature.ca@4K",
        "detected_height": 2160
    },
    {
        "name": "Eurosport 4K (2160p)",
        "url": "http://88.212.15.19/live/esp_4k_atk/playlist.m3u8",
        "group": "[UHD] 4K Channels",
        "raw_group": "4K UHD",
        "lang": "uhd",
        "tvg_logo": "https://i.imgur.com/u3l9s1x.png",
        "tvg_id": "Eurosport4K.fr@4K",
        "detected_height": 2160
    },
    {
        "name": "Bloomberg TV+ 4K (2160p)",
        "url": "https://d2d98ykcmmhgd4.cloudfront.net/v1/bloomberg_bloombergtv_2/samsungheadend_us/latest/main/hls/playlist.m3u8",
        "group": "[UHD] 4K Channels",
        "raw_group": "4K UHD",
        "lang": "uhd",
        "tvg_logo": "https://i.imgur.com/k9vjUeE.png",
        "tvg_id": "BloombergTVPlus.us@4K",
        "detected_height": 2160
    },
    {
        "name": "Ocean Vibes 4K (2160p)",
        "url": "https://pb-vt93jy06rfty1.akamaized.net/SONOS45.m3u8",
        "group": "[UHD] 4K Channels",
        "raw_group": "4K UHD",
        "lang": "uhd",
        "tvg_logo": "https://i.imgur.com/gK9qC5D.png",
        "tvg_id": "OceanVibes.us@4K",
        "detected_height": 2160
    },
    {
        "name": "Fireplace Vibes 4K (2160p)",
        "url": "https://d21izs34d0y5tl.cloudfront.net/Fireplace_4K_SONO20.m3u8",
        "group": "[UHD] 4K Channels",
        "raw_group": "4K UHD",
        "lang": "uhd",
        "tvg_logo": "https://i.imgur.com/gK9qC5D.png",
        "tvg_id": "FireplaceVibes.us@4K",
        "detected_height": 2160
    },
    {
        "name": "NASA TV UHD",
        "url": "https://ntv1.akamaized.net/hls/live/2014075/NASA-NTV1-HLS/master.m3u8",
        "group": "[UHD] 4K Channels",
        "raw_group": "4K UHD",
        "lang": "uhd",
        "tvg_logo": "https://upload.wikimedia.org/wikipedia/commons/e/e5/NASA_logo.svg",
        "tvg_id": "NASATVUHD.us@UHD",
        "detected_height": 2160
    },
    {
        "name": "4K Travel TV",
        "url": "https://streams2.sofast.tv/sofastplayout/33c31ac4-51fa-46ae-afd0-0d1fe5e60a80_0_HLS/master.m3u8",
        "group": "[UHD] 4K Channels",
        "raw_group": "4K UHD",
        "lang": "uhd",
        "tvg_logo": "https://i.imgur.com/J3tG9t9.png",
        "tvg_id": "4KTravelTV.us@4K",
        "detected_height": 2160
    }
]

def load_config() -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                user_cfg = json.load(f)
                # Deep merge content_filters and sources
                for k, v in user_cfg.items():
                    if isinstance(v, dict) and k in cfg and isinstance(cfg[k], dict):
                        cfg[k].update(v)
                    else:
                        cfg[k] = v
        except Exception as e:
            print(f"[!] Failed to read {CONFIG_FILE}: {e}. Falling back to defaults.")
    return cfg

def save_gist_id(gist_id: str):
    """Persist only the new gist_id, without dumping CLI overrides or the token into config.json."""
    try:
        data = {}
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, encoding="utf-8") as f:
                data = json.load(f)
        data["gist_id"] = gist_id
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[!] Failed to save {CONFIG_FILE}: {e}")

def get_channel_quality_score(ch: dict[str, Any]) -> int:
    """
    Returns numerical height: 2160, 1080, 720, 576, 480, etc.
    Prioritizes real HLS detected height from stream manifest,
    falls back to string parsing tags.
    """
    detected_h = ch.get("detected_height", 0)
    if detected_h > 0:
        return detected_h

    name = ch.get("name", "").lower()
    url = ch.get("url", "").lower()
    meta = f"{name} {url} {ch.get('tvg_id', '')} {ch.get('extinf', '')}".lower()

    if any(x in meta for x in ["4k", "2160p", "uhd"]):
        return 2160
    if any(x in meta for x in ["1080p", "fhd"]):
        return 1080
    if any(x in meta for x in ["720p", "hd"]):
        return 720
    if "576p" in meta:
        return 576
    if any(x in meta for x in ["480p", "sd"]):
        return 480
    return 540

def is_high_def(ch: dict[str, Any]) -> bool:
    return get_channel_quality_score(ch) >= 720

def format_group(ch: dict[str, Any]) -> str:
    if ch.get("lang") == "uhd":
        return "[UHD] 4K Channels"

    lang = ch.get("lang", "eng")
    raw_group = ch.get("raw_group", "General")
    name = ch.get("name", "").lower()
    primary = raw_group.split(";")[0].strip().lower()

    is_movie = primary in ["movies", "film", "kino"] or any(
        k in name for k in ["kino", "кино", "фильм", "cinema", "tv1000", "viasatkino"]
    )

    if lang == "rus":
        if is_movie:
            return "[RU] Фильмы HD" if is_high_def(ch) else "[RU] Фильмы"
        return CATEGORY_MAP_RU.get(primary, f"[RU] {raw_group.title()}")

    elif lang == "ukr":
        if is_movie:
            return "[UA] Кіно HD" if is_high_def(ch) else "[UA] Кіно"
        return CATEGORY_MAP_UA.get(primary, f"[UA] {raw_group.title()}")

    elif lang == "eng":
        if is_movie:
            return "[EN] Movies HD" if is_high_def(ch) else "[EN] Movies"
        return f"[EN] {primary.title()}"

    return f"[{lang.upper()}] {raw_group.title()}"

def normalize_channel_name(name: str) -> str:
    n = name
    n = re.sub(r'\b(FHD|HD|SD|4K|UHD|2K|HEVC|H\.265)\b', '', n, flags=re.IGNORECASE)
    n = re.sub(r'[\(\[\{]\s*(\d{3,4}p|4k|not 24/7|geo-blocked|[A-Z]{2})\s*[\)\]\}]', '', n, flags=re.IGNORECASE)
    n = re.sub(r'\s+', ' ', n).strip()
    return n.lower()

def passes_content_filters(ch: dict[str, Any], filters: dict[str, Any]) -> tuple[bool, str]:
    """
    Checks if a channel passes the anti-trash, blacklist, whitelist, and regex filters.
    """
    name = ch.get("name", "")
    group = ch.get("raw_group", "")
    url = ch.get("url", "")
    text = f"{name} {group} {url}".lower()

    # 1. Anti-trash filter
    if filters.get("enable_antitrash", True):
        for kw in filters.get("antitrash_keywords", []):
            if kw.lower() in text:
                return False, f"Anti-trash keyword match: '{kw}'"

    # 2. Blacklist filter
    for kw in filters.get("blacklist_keywords", []):
        if kw.lower() in text:
            return False, f"Blacklist match: '{kw}'"

    # 3. Whitelist filter (if defined)
    whitelist = filters.get("whitelist_keywords", [])
    if whitelist and not any(kw.lower() in text for kw in whitelist):
        return False, "Not in whitelist"

    # 4. Custom regex
    pattern = filters.get("custom_regex", "").strip()
    if pattern:
        try:
            if not re.search(pattern, text, re.IGNORECASE):
                return False, f"Regex mismatch: {pattern}"
        except re.error:
            pass

    return True, "OK"

def parse_m3u(content: str, lang: str = "eng") -> list[dict[str, Any]]:
    channels = []
    lines = content.splitlines()

    current_extinf = None
    current_extra_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("#EXTINF:"):
            current_extinf = stripped
            current_extra_lines = []
        elif stripped.startswith("#EXT"):
            if current_extinf:
                current_extra_lines.append(stripped)
        elif not stripped.startswith("#"):
            if current_extinf:
                url = stripped

                group_match = re.search(r'group-title="([^"]+)"', current_extinf, re.IGNORECASE)
                raw_group = group_match.group(1).strip() if group_match else "General"

                name_match = re.search(r',([^,]+)$', current_extinf)
                name = name_match.group(1).strip() if name_match else "Unknown Channel"

                tvg_id_match = re.search(r'tvg-id="([^"]+)"', current_extinf, re.IGNORECASE)
                tvg_id = tvg_id_match.group(1).strip() if tvg_id_match else ""

                tvg_logo_match = re.search(r'tvg-logo="([^"]+)"', current_extinf, re.IGNORECASE)
                tvg_logo = tvg_logo_match.group(1).strip() if tvg_logo_match else ""

                headers = {}
                ua_match = re.search(r'http-user-agent="([^"]+)"', current_extinf, re.IGNORECASE)
                if ua_match:
                    headers["User-Agent"] = ua_match.group(1).strip()
                ref_match = re.search(r'http-referrer="([^"]+)"', current_extinf, re.IGNORECASE)
                if ref_match:
                    headers["Referer"] = ref_match.group(1).strip()

                for extra in current_extra_lines:
                    if extra.startswith("#EXTVLCOPT:http-user-agent="):
                        headers["User-Agent"] = extra.split("=", 1)[1].strip()
                    elif extra.startswith("#EXTVLCOPT:http-referrer="):
                        headers["Referer"] = extra.split("=", 1)[1].strip()

                ch_obj = {
                    "name": name,
                    "lang": lang,
                    "raw_group": raw_group,
                    "url": url,
                    "tvg_id": tvg_id,
                    "tvg_logo": tvg_logo,
                    "extinf": current_extinf,
                    "extra_lines": current_extra_lines,
                    "headers": headers,
                    "detected_height": 0,
                    "bandwidth": 0
                }
                ch_obj["group"] = format_group(ch_obj)
                channels.append(ch_obj)

                current_extinf = None
                current_extra_lines = []

    return channels

async def fetch_playlist(session: aiohttp.ClientSession, url: str, lang: str = "eng") -> list[dict[str, Any]]:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                text = await resp.text(encoding="utf-8", errors="ignore")
                return parse_m3u(text, lang=lang)
            else:
                print(f"[!] Failed to download {url}: HTTP {resp.status}")
    except Exception as e:
        print(f"[!] Error fetching {url}: {e}")
    return []

async def check_all_streams(channels: list[dict[str, Any]], cfg: dict) -> list[dict[str, Any]]:
    concurrency = min(cfg.get("max_concurrent_checks", 40), 60)
    timeout = cfg.get("stream_check_timeout_seconds", 4.0)

    total = len(channels)
    print(f"\n[*] Checking streams ({total} channels, concurrency {concurrency}, timeout {timeout}s)...")

    queue = asyncio.Queue()
    for ch in channels:
        queue.put_nowait(ch)

    resolver = aiohttp.ThreadedResolver()
    # ssl=False on purpose: lots of free IPTV origins run expired/self-signed certs,
    # and we only read the first 4 KB of a public stream here, nothing sensitive is sent.
    connector = aiohttp.TCPConnector(resolver=resolver, ssl=False, limit=concurrency, ttl_dns_cache=300)

    tested = 0
    alive_channels = []
    t_start = time.time()
    lock = asyncio.Lock()

    async with aiohttp.ClientSession(connector=connector) as session:
        async def worker():
            nonlocal tested
            while True:
                try:
                    ch = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                try:
                    is_alive, latency, stream_info = await check_single_stream(session, ch, timeout_sec=timeout)
                    async with lock:
                        tested += 1
                        if tested % 25 == 0 or tested == total:
                            percent = (tested / total) * 100
                            print(f"\r  -> Checked: {tested}/{total} ({percent:.1f}%) | alive: {len(alive_channels)}",
                                  end="", flush=True)
                        if is_alive:
                            ch["latency"] = latency
                            if stream_info.get("max_height"):
                                ch["detected_height"] = stream_info["max_height"]
                            if stream_info.get("bandwidth"):
                                ch["bandwidth"] = stream_info["bandwidth"]
                            alive_channels.append(ch)
                except Exception:
                    async with lock:
                        tested += 1
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
        await asyncio.gather(*workers)

    duration = time.time() - t_start
    print(f"\n[+] Done in {duration:.1f}s: {len(alive_channels)} of {total} streams alive.\n")
    return alive_channels

def deduplicate_channels(channels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_channels = {}
    for ch in channels:
        key = (ch.get("lang", ""), normalize_channel_name(ch["name"]))
        current_best = best_channels.get(key)
        if current_best is None:
            best_channels[key] = ch
        else:
            score_new = get_channel_quality_score(ch)
            score_old = get_channel_quality_score(current_best)

            # 1. Compare real/inferred resolution
            if score_new > score_old:
                best_channels[key] = ch
            elif score_new == score_old:
                # 2. Compare detected bandwidth
                bw_new = ch.get("bandwidth", 0)
                bw_old = current_best.get("bandwidth", 0)
                if bw_new > bw_old:
                    best_channels[key] = ch
                elif bw_new == bw_old:
                    # 3. Prefer faster ping as fallback
                    lat_new = ch.get("latency", 9999)
                    lat_old = current_best.get("latency", 9999)
                    if lat_new < lat_old:
                        best_channels[key] = ch
    return list(best_channels.values())

# Category ordering in playlist (UHD on top, then primary language, then the rest)
CAT_ORDER = {
    # 4K Ultra HD
    "[UHD] 4K Channels": 1,

    # English (Primary)
    "[EN] News": 10,
    "[EN] Movies HD": 11,
    "[EN] Movies": 12,
    "[EN] Sports": 13,
    "[EN] Documentary": 14,
    "[EN] Animation": 15,
    "[EN] Kids": 16,
    "[EN] Entertainment": 17,
    "[EN] Music": 18,
    "[EN] General": 19,

    # Russian
    "[RU] Общие": 30,
    "[RU] Фильмы HD": 31,
    "[RU] Фильмы": 32,
    "[RU] Сериалы": 33,
    "[RU] Новости": 34,
    "[RU] Спорт": 35,
    "[RU] Познавательные": 36,
    "[RU] Мультфильмы": 37,
    "[RU] Детские": 38,
    "[RU] Музыка": 39,
    "[RU] Развлекательные": 40,

    # Ukrainian (if enabled)
    "[UA] Загальні": 50,
    "[UA] Кіно HD": 51,
    "[UA] Кіно": 52,
    "[UA] Новини": 54,
    "[UA] Спорт": 55,
}

def build_m3u(channels: list[dict[str, Any]], primary_lang: str = "eng") -> str:
    epg_guides = "https://iptv-org.github.io/epg/guides/us.xml,https://iptv-org.github.io/epg/guides/uk.xml,https://iptv-org.github.io/epg/guides/ru.xml,https://iptv-org.github.io/epg/guides/ua.xml"
    lines = [f'#EXTM3U url-tvg="{epg_guides}"']

    # Languages ordering: UHD at top, then primary (ENG), then secondary (RUS), then others
    lang_priority = {
        "uhd": -2,
        primary_lang: 0,
        "eng": 1 if primary_lang != "eng" else 0,
        "rus": 2 if primary_lang != "rus" else 0,
        "ukr": 3
    }

    for ch in channels:
        ch["group"] = format_group(ch)

    sorted_channels = sorted(
        channels,
        key=lambda x: (
            lang_priority.get(x.get("lang", ""), 99),
            CAT_ORDER.get(x.get("group", ""), 99),
            x.get("group", "").lower(),
            x.get("name", "").lower()
        )
    )

    for ch in sorted_channels:
        tvg_id_part = f' tvg-id="{ch["tvg_id"]}"' if ch.get("tvg_id") else ""
        tvg_logo_part = f' tvg-logo="{ch["tvg_logo"]}"' if ch.get("tvg_logo") else ""
        group_part = f' group-title="{ch["group"]}"' if ch.get("group") else ""

        headers_part = ""
        if "User-Agent" in ch.get("headers", {}):
            headers_part += f' http-user-agent="{ch["headers"]["User-Agent"]}"'
        if "Referer" in ch.get("headers", {}):
            headers_part += f' http-referrer="{ch["headers"]["Referer"]}"'

        extinf_line = f'#EXTINF:-1{tvg_id_part}{tvg_logo_part}{group_part}{headers_part},{ch["name"]}'
        lines.append(extinf_line)

        for extra in ch.get("extra_lines", []):
            lines.append(extra)

        lines.append(ch["url"])

    return "\n".join(lines) + "\n"

def parse_args():
    parser = argparse.ArgumentParser(description="Smart IPTV Stream Curator & Parser")
    parser.add_argument("--profile", choices=list(PRESETS.keys()) + ["custom"],
                        help="Select profile preset (balanced, cinema, sports, kids, news, custom)")
    parser.add_argument("--lang", help="Comma-separated languages (e.g. 'eng,rus' or 'eng')")
    parser.add_argument("--min-res", choices=["all", "720p", "1080p", "4k"], help="Minimum resolution filter")
    parser.add_argument("--no-check", action="store_true", help="Skip online stream checks (faster)")
    parser.add_argument("--no-antitrash", action="store_true", help="Disable anti-trash keyword filter")
    return parser.parse_args()

async def main():
    args = parse_args()
    cfg = load_config()

    # CLI Overrides
    if args.profile:
        cfg["profile"] = args.profile
    if args.lang:
        cfg["languages"] = [code.strip().lower() for code in args.lang.split(",")]
    if args.min_res:
        cfg["min_resolution"] = args.min_res
    if args.no_check:
        cfg["check_streams"] = False
    if args.no_antitrash:
        cfg["content_filters"]["enable_antitrash"] = False

    profile_name = cfg.get("profile", "balanced")
    if profile_name in PRESETS:
        preset = PRESETS[profile_name]
        print("=" * 65)
        print(f"  Smart IPTV Stream Curator - profile: [{profile_name.upper()}]")
        print(f"  {preset['description']}")
        print("=" * 65)
        if profile_name != "custom":
            cfg["categories"] = preset.get("categories", cfg["categories"])
            if cfg["min_resolution"] == "all" and preset.get("min_resolution") != "all":
                cfg["min_resolution"] = preset["min_resolution"]
    else:
        print("=" * 65)
        print("  Smart IPTV Stream Curator - custom profile")
        print("=" * 65)

    languages = cfg.get("languages", ["eng", "rus"])
    primary_lang = cfg.get("primary_language", "eng")
    allowed_categories = [c.lower() for c in cfg.get("categories", [])]
    exclude_categories = [c.lower() for c in cfg.get("exclude_categories", [])]
    content_filters = cfg.get("content_filters", {})
    sources_cfg = cfg.get("sources", {"iptv_org": True, "custom_m3u_urls": [], "local_m3u_files": []})
    output_file = cfg.get("output_file", "playlist.m3u")
    if not os.path.isabs(output_file):
        output_file = os.path.join(BASE_DIR, output_file)
    dedup = cfg.get("deduplicate", True)
    min_res = cfg.get("min_resolution", "all").lower()
    max_per_lang = cfg.get("max_channels_per_language", {"eng": 200, "rus": 150})

    print(f"[*] Primary language: [{primary_lang.upper()}], languages: {', '.join(languages)}")
    print(f"[*] Min resolution: [{min_res.upper()}]")
    print(f"[*] Categories: {', '.join(cfg.get('categories', [])) if allowed_categories else 'all'}")
    print(f"[*] Anti-trash filter: {'on' if content_filters.get('enable_antitrash', True) else 'off'}")

    # 1. Fetch playlists from sources
    all_channels = []
    seen_urls = set()
    trash_count = 0

    resolver = aiohttp.ThreadedResolver()
    connector = aiohttp.TCPConnector(resolver=resolver, ssl=False)  # see note in check_all_streams
    async with aiohttp.ClientSession(connector=connector) as session:
        # A. Official iptv-org sources
        if sources_cfg.get("iptv_org", True):
            for lang in languages:
                url = f"https://iptv-org.github.io/iptv/languages/{lang}.m3u"
                print(f"[*] Fetching [{lang.upper()}] from iptv-org...")
                chans = await fetch_playlist(session, url, lang=lang)
                print(f"    -> {len(chans)} channels.")

                for ch in chans:
                    url = ch["url"]
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)

                    raw_grp = ch.get("raw_group", "").lower()
                    if any(ex in raw_grp for ex in exclude_categories):
                        continue
                    if allowed_categories and not any(allowed in raw_grp for allowed in allowed_categories):
                        continue

                    # Content & anti-trash filter
                    passed, reason = passes_content_filters(ch, content_filters)
                    if not passed:
                        trash_count += 1
                        continue

                    all_channels.append(ch)

        # B. Custom M3U URLs (Multi-source)
        for custom_url in sources_cfg.get("custom_m3u_urls", []):
            if custom_url.strip():
                print(f"[*] Fetching custom playlist: {custom_url}...")
                custom_chans = await fetch_playlist(session, custom_url.strip(), lang="custom")
                print(f"    -> {len(custom_chans)} channels.")
                for ch in custom_chans:
                    if ch["url"] not in seen_urls:
                        seen_urls.add(ch["url"])
                        passed, reason = passes_content_filters(ch, content_filters)
                        if passed:
                            all_channels.append(ch)

    # C. Local M3U Files
    for local_file in sources_cfg.get("local_m3u_files", []):
        if os.path.exists(local_file):
            print(f"[*] Loading local file: {local_file}...")
            with open(local_file, encoding="utf-8", errors="ignore") as f:
                local_chans = parse_m3u(f.read(), lang="local")
                for ch in local_chans:
                    if ch["url"] not in seen_urls:
                        seen_urls.add(ch["url"])
                        passed, _ = passes_content_filters(ch, content_filters)
                        if passed:
                            all_channels.append(ch)

    # D. Built-in free 4K channels (unless filtered out)
    for ch4k in UHD_4K_CHANNELS:
        if ch4k["url"] not in seen_urls:
            seen_urls.add(ch4k["url"])
            passed, _ = passes_content_filters(ch4k, content_filters)
            if passed:
                all_channels.append(ch4k)

    print(f"\n[+] Candidates: {len(all_channels)} (dropped by anti-trash/blacklist: {trash_count})")
    if not all_channels:
        print("[!] No channels match the current filters.")
        return

    # 2. Check stream availability & probe HLS resolution
    if cfg.get("check_streams", True):
        working_channels = await check_all_streams(all_channels, cfg)
    else:
        print("[*] Stream check skipped (--no-check).")
        working_channels = all_channels

    if not working_channels:
        print("[!] No stream passed the check.")
        return

    # 3. Quality threshold filter
    if min_res in ("720p", "1080p", "4k"):
        min_h = 2160 if min_res == "4k" else (1080 if min_res == "1080p" else 720)
        before_res = len(working_channels)
        working_channels = [c for c in working_channels if get_channel_quality_score(c) >= min_h]
        dropped = before_res - len(working_channels)
        print(f"[*] Resolution filter [>={min_res.upper()}]: kept {len(working_channels)}, dropped {dropped}")

    # 4. Deduplication: Pick the highest quality stream for duplicate channels
    if dedup:
        print("[*] Deduplicating (keeping the best resolution/bitrate per channel)...")
        before_count = len(working_channels)
        working_channels = deduplicate_channels(working_channels)
        print(f"    -> Removed {before_count - len(working_channels)} duplicates, {len(working_channels)} unique left.")

    # 5. Apply per-language quotas
    final_channels = []
    lang_order = ["uhd", primary_lang] + [code for code in languages if code != primary_lang] + ["custom", "local"]
    for lang in lang_order:
        lang_chans = [c for c in working_channels if c.get("lang") == lang]
        limit = max_per_lang.get(lang)
        if limit and len(lang_chans) > limit:
            print(f"[*] Quota for [{lang}]: keeping top {limit} of {len(lang_chans)}.")
            lang_chans = sorted(
                lang_chans,
                key=lambda c: (-get_channel_quality_score(c), -c.get("bandwidth", 0), c.get("latency", 9999)),
            )[:limit]
        final_channels.extend(lang_chans)
        if not lang_chans and lang in ("custom", "local"):
            continue
        if lang == "uhd":
            print(f"    -> [UHD 4K]: {len(lang_chans)} channels.")
        else:
            print(f"    -> [{lang.upper()}]: {len(lang_chans)} channels.")

    # 6. Generate final playlist.m3u
    m3u_text = build_m3u(final_channels, primary_lang=primary_lang)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(m3u_text)
    print(f"\n[+] Playlist saved: {os.path.abspath(output_file)}")
    print(f"    Total channels: {len(final_channels)}")

    # 7. Gist Sync
    # Env vars win over config.json, so the token never has to live in a file
    token = (os.environ.get("GITHUB_TOKEN") or cfg.get("github_token", "")).strip()
    gist_id = (os.environ.get("GIST_ID") or cfg.get("gist_id", "")).strip()
    sync = GistSync(token=token, gist_id=gist_id)

    if sync.is_configured():
        print("\n[*] Syncing to GitHub Gist...")
        success, url_or_err, new_id = sync.sync_playlist(m3u_text, filename="playlist.m3u")
        if success:
            if new_id and new_id != gist_id:
                save_gist_id(new_id)
                print(f"[*] New gist created, id saved to config.json: {new_id}")

            print("\n" + "#" * 65)
            print(" Permanent playlist URL (TiviMate / OTT Navigator):")
            print(f" {url_or_err}")
            print("#" * 65)
            print(" Add it to your player once, it will pick up new versions automatically.\n")
        else:
            print(f"[!] Gist error: {url_or_err}")
    else:
        print("\n[*] Gist sync not configured, playlist is local only.")

if __name__ == "__main__":
    asyncio.run(main())
