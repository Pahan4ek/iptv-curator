import argparse
import asyncio
import copy
import functools
import json
import os
import re
import sys
import time
import warnings
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from checker import check_single_stream, is_socks_proxy
from gist_sync import GistSync

try:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

RE_QUALITY_4K = re.compile(r"\b(4k|2160p|uhd)\b", re.IGNORECASE)
RE_QUALITY_1080 = re.compile(r"\b(1080p|fhd)\b", re.IGNORECASE)
RE_QUALITY_720 = re.compile(r"\b(720p|hd)\b", re.IGNORECASE)
RE_QUALITY_576 = re.compile(r"\b576p\b", re.IGNORECASE)
RE_QUALITY_480 = re.compile(r"\b(480p|sd)\b", re.IGNORECASE)

_INVALID_REGEX = object()

DEFAULT_CONFIG = {
    # Active profile: "balanced", "cinema", "sports", "kids", "news", "custom"
    "profile": "balanced",
    # Target languages: English is primary, Russian is included
    "languages": ["eng", "rus"],
    "primary_language": "eng",
    # Source settings
    "sources": {"iptv_org": True, "custom_m3u_urls": [], "local_m3u_files": []},
    # Resolution & quality filter: "all", "720p", "1080p", "4k"
    "min_resolution": "all",
    # Deduplicate channels and pick highest resolution/bitrate
    "deduplicate": True,
    # Categories filter
    "categories": [
        "News",
        "Sports",
        "Movies",
        "Music",
        "Animation",
        "Kids",
        "Entertainment",
        "Documentary",
        "General",
        "Series",
        "Comedy",
    ],
    "exclude_categories": ["XXX"],
    # Content & anti-trash filters
    "content_filters": {
        "enable_antitrash": True,
        "antitrash_keywords": [
            "shop",
            "магазин",
            "ювелир",
            "телемагазин",
            "commercial",
            "infomercial",
            "casino",
            "казино",
            "лотерея",
            "религия",
            "church",
            "test card",
        ],
        "blacklist_keywords": [],
        "whitelist_keywords": [],
        "custom_regex": "",
    },
    # Curated 4K list inclusion
    "include_curated_4k": True,
    # Limits per language
    "max_channels_per_language": {"eng": 200, "rus": 150},
    # Stream checker
    "check_streams": True,
    "deep_check": False,
    "proxy": "",
    "stream_check_timeout_seconds": 4.0,
    "max_concurrent_checks": 40,
    "max_concurrent_per_host": 6,
    # Output & Gist sync
    "github_token": "",  # prefer GIST_TOKEN or GITHUB_TOKEN env var
    "gist_id": "",
    "output_file": "playlist.m3u",
    "min_channels_to_sync": 10,
}

PRESETS = {
    "balanced": {
        "description": "Balanced: all main categories (news, movies, sports, kids, docs)",
        "categories": [
            "News",
            "Sports",
            "Movies",
            "Music",
            "Animation",
            "Kids",
            "Entertainment",
            "Documentary",
            "General",
            "Series",
            "Comedy",
        ],
        "min_resolution": "all",
        "include_curated_4k": True,
    },
    "cinema": {
        "description": "Cinema: movies and series only, 720p and up",
        "categories": ["Movies", "Series"],
        "min_resolution": "720p",
        "include_curated_4k": True,
    },
    "sports": {
        "description": "Sports: sports channels, 720p and up",
        "categories": ["Sports"],
        "min_resolution": "720p",
        "include_curated_4k": True,
    },
    "kids": {
        "description": "Kids: cartoons, animation, kids shows",
        "categories": ["Animation", "Kids"],
        "min_resolution": "all",
        "include_curated_4k": False,
    },
    "news": {
        "description": "News: news channels only",
        "categories": ["News"],
        "min_resolution": "all",
        "include_curated_4k": False,
    },
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
    "classic": "[RU] Классика",
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
    "comedy": "[UA] Гумор",
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
        "detected_height": 2160,
    },
    {
        "name": "Eurosport 4K (2160p)",
        "url": "http://88.212.15.19/live/esp_4k_atk/playlist.m3u8",
        "group": "[UHD] 4K Channels",
        "raw_group": "4K UHD",
        "lang": "uhd",
        "tvg_logo": "https://i.imgur.com/u3l9s1x.png",
        "tvg_id": "Eurosport4K.fr@4K",
        "detected_height": 2160,
    },
    {
        "name": "Bloomberg TV+ 4K (2160p)",
        "url": "https://d2d98ykcmmhgd4.cloudfront.net/v1/bloomberg_bloombergtv_2/samsungheadend_us/latest/main/hls/playlist.m3u8",
        "group": "[UHD] 4K Channels",
        "raw_group": "4K UHD",
        "lang": "uhd",
        "tvg_logo": "https://i.imgur.com/k9vjUeE.png",
        "tvg_id": "BloombergTVPlus.us@4K",
        "detected_height": 2160,
    },
    {
        "name": "Ocean Vibes 4K (2160p)",
        "url": "https://pb-vt93jy06rfty1.akamaized.net/SONOS45.m3u8",
        "group": "[UHD] 4K Channels",
        "raw_group": "4K UHD",
        "lang": "uhd",
        "tvg_logo": "https://i.imgur.com/gK9qC5D.png",
        "tvg_id": "OceanVibes.us@4K",
        "detected_height": 2160,
    },
    {
        "name": "Fireplace Vibes 4K (2160p)",
        "url": "https://d21izs34d0y5tl.cloudfront.net/Fireplace_4K_SONO20.m3u8",
        "group": "[UHD] 4K Channels",
        "raw_group": "4K UHD",
        "lang": "uhd",
        "tvg_logo": "https://i.imgur.com/gK9qC5D.png",
        "tvg_id": "FireplaceVibes.us@4K",
        "detected_height": 2160,
    },
    {
        "name": "NASA TV UHD",
        "url": "https://ntv1.akamaized.net/hls/live/2014075/NASA-NTV1-HLS/master.m3u8",
        "group": "[UHD] 4K Channels",
        "raw_group": "4K UHD",
        "lang": "uhd",
        "tvg_logo": "https://upload.wikimedia.org/wikipedia/commons/e/e5/NASA_logo.svg",
        "tvg_id": "NASATVUHD.us@UHD",
        "detected_height": 2160,
    },
    {
        "name": "4K Travel TV",
        "url": "https://streams2.sofast.tv/sofastplayout/33c31ac4-51fa-46ae-afd0-0d1fe5e60a80_0_HLS/master.m3u8",
        "group": "[UHD] 4K Channels",
        "raw_group": "4K UHD",
        "lang": "uhd",
        "tvg_logo": "https://i.imgur.com/J3tG9t9.png",
        "tvg_id": "4KTravelTV.us@4K",
        "detected_height": 2160,
    },
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
    falls back to string parsing tags with word boundary matching.
    Excludes tvg_logo, logo URLs, and URL query tokens from keywords.
    """
    detected_h = ch.get("detected_height", 0)
    if detected_h > 0:
        return detected_h

    name = ch.get("name", "")
    url = ch.get("url", "")
    # Exclude URL query parameters and fragments; replace underscores with spaces for word boundaries
    url_path = url.split("?", 1)[0].split("#", 1)[0].replace("_", " ")

    tvg_id = ch.get("tvg_id", "")
    raw_group = ch.get("raw_group", "")

    # Strip tvg-logo attribute completely from extinf to prevent logo URLs or image hashes from polluting score
    extinf = ch.get("extinf", "")
    extinf_clean = re.sub(r'tvg-logo=(?:"[^"]*"|\'[^\']*\'|\S+)', "", extinf, flags=re.IGNORECASE) if extinf else ""

    meta = f"{name} {url_path} {tvg_id} {raw_group} {extinf_clean}"

    if RE_QUALITY_4K.search(meta):
        return 2160
    if RE_QUALITY_1080.search(meta):
        return 1080
    if RE_QUALITY_720.search(meta):
        return 720
    if RE_QUALITY_576.search(meta):
        return 576
    if RE_QUALITY_480.search(meta):
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
    n = re.sub(r"\b(FHD|HD|SD|4K|UHD|2K|HEVC|H\.265)\b", "", n, flags=re.IGNORECASE)
    n = re.sub(
        r"[\(\[\{]\s*(\d{3,4}p|4k|not 24/7|geo-blocked|[A-Z]{2})\s*[\)\]\}]",
        "",
        n,
        flags=re.IGNORECASE,
    )
    n = re.sub(r"\s+", " ", n).strip()
    return n.lower()


@functools.lru_cache(maxsize=512)
def _compile_keyword_pattern(kw: str) -> re.Pattern:
    clean = kw.strip()
    leading = r"\b" if re.match(r"^\w", clean) else ""
    trailing = r"\b" if re.search(r"\w$", clean) else ""
    return re.compile(rf"{leading}{re.escape(clean)}{trailing}", re.IGNORECASE)


def matches_keyword(kw: str, text: str) -> bool:
    clean = kw.strip()
    if not clean:
        return False
    return bool(_compile_keyword_pattern(clean).search(text))


def compile_custom_regex(pattern: str) -> re.Pattern | None:
    """
    Compiles custom regex pattern.
    Emits an explicit warning and returns None on re.error.
    """
    clean = pattern.strip()
    if not clean:
        return None
    try:
        return re.compile(clean, re.IGNORECASE)
    except re.error as e:
        warnings.warn(f"Invalid custom_regex pattern '{clean}': {e}", UserWarning, stacklevel=2)
        print(f"[!] Warning: Invalid custom_regex pattern '{clean}': {e}", file=sys.stderr)
        return None


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
            if matches_keyword(kw, text):
                return False, f"Anti-trash keyword match: '{kw}'"

    # 2. Blacklist filter
    for kw in filters.get("blacklist_keywords", []):
        if matches_keyword(kw, text):
            return False, f"Blacklist match: '{kw}'"

    # 3. Whitelist filter (if defined)
    whitelist = filters.get("whitelist_keywords", [])
    if whitelist and not any(kw.lower() in text for kw in whitelist):
        return False, "Not in whitelist"

    # 4. Custom regex
    pattern = filters.get("custom_regex", "").strip()
    if pattern:
        compiled = filters.get("_compiled_regex")
        if compiled is None or getattr(compiled, "pattern", None) != pattern:
            compiled_res = compile_custom_regex(pattern)
            compiled = compiled_res if compiled_res is not None else _INVALID_REGEX
            filters["_compiled_regex"] = compiled

        if compiled is not _INVALID_REGEX and compiled is not None and not compiled.search(text):
            return False, f"Regex mismatch: {pattern}"

    return True, "OK"


def parse_extinf_line(extinf_line: str) -> tuple[str, str]:
    """
    Splits an #EXTINF line into (attributes_part, channel_name).
    Correctly handles commas inside quoted attributes and channel names containing commas.
    """
    quote_char = None
    comma_idx = -1
    for idx, char in enumerate(extinf_line):
        if quote_char is None and char in ('"', "'"):
            quote_char = char
        elif quote_char is not None and char == quote_char and (idx == 0 or extinf_line[idx - 1] != "\\"):
            quote_char = None
        elif char == "," and quote_char is None:
            comma_idx = idx
            break

    if comma_idx != -1:
        attrs_part = extinf_line[:comma_idx]
        name = extinf_line[comma_idx + 1 :].strip()
    else:
        attrs_part = extinf_line
        name = ""

    return attrs_part, name or "Unknown Channel"


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

                attrs_part, name = parse_extinf_line(current_extinf)

                group_match = re.search(r'group-title="([^"]+)"', attrs_part, re.IGNORECASE)
                raw_group = group_match.group(1).strip() if group_match else "General"

                tvg_id_match = re.search(r'tvg-id="([^"]+)"', attrs_part, re.IGNORECASE)
                tvg_id = tvg_id_match.group(1).strip() if tvg_id_match else ""

                tvg_logo_match = re.search(r'tvg-logo="([^"]+)"', attrs_part, re.IGNORECASE)
                tvg_logo = tvg_logo_match.group(1).strip() if tvg_logo_match else ""

                headers = {}
                ua_match = re.search(r'http-user-agent="([^"]+)"', attrs_part, re.IGNORECASE)
                if ua_match:
                    headers["User-Agent"] = ua_match.group(1).strip()
                ref_match = re.search(r'http-referrer="([^"]+)"', attrs_part, re.IGNORECASE)
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
                    "bandwidth": 0,
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


def mask_proxy_url(proxy: str) -> str:
    """Redacts credentials from a proxy URL before logging."""
    try:
        parts = urlsplit(proxy)
        if parts.password is not None or parts.username is not None:
            host_port = parts.hostname or ""
            if parts.port:
                host_port = f"{host_port}:{parts.port}"
            user = parts.username or ""
            netloc = f"{user}:***@{host_port}" if user else f"***@{host_port}"
            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception:
        pass
    return proxy


def get_proxy_url(cfg: dict[str, Any]) -> str:
    """
    Retrieves stream checker proxy URL in priority order:
    1. cfg['proxy'] (already populated from --proxy > IPTV_PROXY > config.json in resolve_config)
    2. IPTV_PROXY env var (fallback when check_all_streams is called directly)
    """
    cfg_proxy = str(cfg.get("proxy", "") or "").strip()
    if cfg_proxy:
        return cfg_proxy
    return os.environ.get("IPTV_PROXY", "").strip()


async def check_all_streams(
    channels: list[dict[str, Any]],
    cfg: dict,
    summary_out: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    try:
        raw_concurrency = int(cfg.get("max_concurrent_checks", 40))
    except (ValueError, TypeError):
        raw_concurrency = 40
    concurrency = max(1, raw_concurrency)

    try:
        raw_limit_per_host = int(cfg.get("max_concurrent_per_host", 6))
    except (ValueError, TypeError):
        raw_limit_per_host = 6
    limit_per_host = max(1, raw_limit_per_host)

    timeout = float(cfg.get("stream_check_timeout_seconds", 4.0))
    deep_check = bool(cfg.get("deep_check", False))
    proxy = get_proxy_url(cfg) or None

    supported_schemes = ("http://", "https://", "socks4://", "socks4a://", "socks5://", "socks5h://")
    if proxy and not proxy.lower().startswith(supported_schemes):
        raise ValueError(f"Unsupported proxy URL scheme: {mask_proxy_url(proxy)}")

    total = len(channels)
    extras = []
    if deep_check:
        extras.append("deep-check")
    if proxy:
        extras.append(f"proxy={mask_proxy_url(proxy)}")
    extra_info = f", {', '.join(extras)}" if extras else ""
    print(f"\n[*] Checking streams ({total} channels, concurrency {concurrency}, timeout {timeout}s{extra_info})...")

    queue = asyncio.Queue()
    for ch in channels:
        queue.put_nowait(ch)

    use_socks = is_socks_proxy(proxy)
    if use_socks:
        try:
            from aiohttp_socks import ProxyConnector
        except ImportError as exc:
            raise RuntimeError(
                "SOCKS proxy support requires 'aiohttp-socks'. Install it with: pip install aiohttp-socks"
            ) from exc
        connector = ProxyConnector.from_url(
            proxy,
            ssl=False,
            limit=concurrency,
            limit_per_host=limit_per_host,
            ttl_dns_cache=300,
        )
    else:
        resolver = aiohttp.ThreadedResolver()
        # ssl=False on purpose: lots of free IPTV origins run expired/self-signed certs,
        # and we only read the first 4 KB of a public stream here, nothing sensitive is sent.
        connector = aiohttp.TCPConnector(
            resolver=resolver,
            ssl=False,
            limit=concurrency,
            limit_per_host=limit_per_host,
            ttl_dns_cache=300,
        )

    tested = 0
    alive_channels = []
    drop_reasons: dict[str, int] = {}
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
                    check_kwargs: dict[str, Any] = {"timeout_sec": timeout}
                    if deep_check:
                        check_kwargs["deep_check"] = True
                    if proxy and not use_socks:
                        check_kwargs["proxy"] = proxy
                    res = await check_single_stream(session, ch, **check_kwargs)
                    async with lock:
                        tested += 1
                        if res.alive:
                            ch["latency"] = res.latency_ms
                            if res.stream_info.get("max_height"):
                                ch["detected_height"] = res.stream_info["max_height"]
                            if res.stream_info.get("bandwidth"):
                                ch["bandwidth"] = res.stream_info["bandwidth"]
                            alive_channels.append(ch)
                        else:
                            drop_reasons[res.reason] = drop_reasons.get(res.reason, 0) + 1

                        if tested % 25 == 0 or tested == total:
                            percent = (tested / total) * 100
                            print(
                                f"\r  -> Checked: {tested}/{total} ({percent:.1f}%) | alive: {len(alive_channels)}",
                                end="",
                                flush=True,
                            )
                except Exception:
                    async with lock:
                        tested += 1
                        drop_reasons["network_error"] = drop_reasons.get("network_error", 0) + 1
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
        await asyncio.gather(*workers)

    duration = time.time() - t_start
    print(f"\n[+] Done in {duration:.1f}s: {len(alive_channels)} of {total} streams alive.\n")

    failures = {k: v for k, v in drop_reasons.items() if k != "ok"}
    if failures:
        top_failures = sorted(failures.items(), key=lambda item: item[1], reverse=True)[:5]
        summary_str = ", ".join(f"{reason}: {count}" for reason, count in top_failures)
        print(f"[*] Top failure reasons: {summary_str}\n")

    if summary_out is not None:
        summary_out["total"] = total
        summary_out["alive"] = len(alive_channels)
        summary_out["dropped"] = total - len(alive_channels)
        summary_out["duration_seconds"] = round(duration, 2)
        summary_out["drop_reasons"] = dict(drop_reasons)

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
    epg_guides = (
        "https://iptv-org.github.io/epg/guides/us.xml,"
        "https://iptv-org.github.io/epg/guides/uk.xml,"
        "https://iptv-org.github.io/epg/guides/ru.xml,"
        "https://iptv-org.github.io/epg/guides/ua.xml"
    )
    lines = [f'#EXTM3U url-tvg="{epg_guides}"']

    # Languages ordering: UHD at top, then primary (ENG), then secondary (RUS), then others
    lang_priority = {
        "uhd": -2,
        primary_lang: 0,
        "eng": 1 if primary_lang != "eng" else 0,
        "rus": 2 if primary_lang != "rus" else 0,
        "ukr": 3 if primary_lang != "ukr" else 0,
    }

    sorted_channels = sorted(
        channels,
        key=lambda x: (
            lang_priority.get(x.get("lang", ""), 99),
            CAT_ORDER.get(x.get("group") or format_group(x), 99),
            (x.get("group") or format_group(x)).lower(),
            x.get("name", "").lower(),
        ),
    )

    for ch in sorted_channels:
        group_val = ch.get("group") or format_group(ch)
        tvg_id_part = f' tvg-id="{ch["tvg_id"]}"' if ch.get("tvg_id") else ""
        tvg_logo_part = f' tvg-logo="{ch["tvg_logo"]}"' if ch.get("tvg_logo") else ""
        group_part = f' group-title="{group_val}"' if group_val else ""

        headers_part = ""
        if "User-Agent" in ch.get("headers", {}):
            headers_part += f' http-user-agent="{ch["headers"]["User-Agent"]}"'
        if "Referer" in ch.get("headers", {}):
            headers_part += f' http-referrer="{ch["headers"]["Referer"]}"'

        extinf_line = f"#EXTINF:-1{tvg_id_part}{tvg_logo_part}{group_part}{headers_part},{ch['name']}"
        lines.append(extinf_line)

        seen_extras = set()
        for extra in ch.get("extra_lines", []):
            stripped_extra = extra.strip()
            if not stripped_extra:
                continue
            extra_lower = stripped_extra.lower()
            if extra_lower.startswith("#extvlcopt:http-user-agent=") and "http-user-agent=" in headers_part:
                continue
            if (
                extra_lower.startswith("#extvlcopt:http-referrer=")
                or extra_lower.startswith("#extvlcopt:http-referer=")
            ) and "http-referrer=" in headers_part:
                continue
            if extra_lower in seen_extras:
                continue
            seen_extras.add(extra_lower)
            lines.append(stripped_extra)

        lines.append(ch["url"])

    return "\n".join(lines) + "\n"


def get_github_token(cfg: dict[str, Any]) -> str:
    """
    Retrieves GitHub token in priority order:
    1. GIST_TOKEN env var
    2. GITHUB_TOKEN env var
    3. config.json 'github_token'
    """
    env_gist = os.environ.get("GIST_TOKEN", "").strip()
    if env_gist:
        return env_gist
    env_gh = os.environ.get("GITHUB_TOKEN", "").strip()
    if env_gh:
        return env_gh
    return str(cfg.get("github_token", "")).strip()


def get_gist_id(cfg: dict[str, Any]) -> str:
    """
    Retrieves Gist ID in priority order:
    1. GIST_ID env var
    2. config.json 'gist_id'
    """
    env_id = os.environ.get("GIST_ID", "").strip()
    if env_id:
        return env_id
    return str(cfg.get("gist_id", "")).strip()


def sync_to_gist(
    m3u_text: str,
    channel_count: int,
    cfg: dict[str, Any],
    sync: GistSync | None = None,
    output_filename: str = "playlist.m3u",
) -> tuple[bool, str, str | None]:
    """
    Validates safety threshold and uploads/updates playlist on GitHub Gist.
    Returns (success: bool, url_or_error: str, new_gist_id: str | None).
    """
    min_channels = max(0, int(cfg.get("min_channels_to_sync", 10)))
    force_sync = bool(cfg.get("force_sync", False))

    if channel_count < min_channels and not force_sync:
        msg = (
            f"Safety check failed: live channels ({channel_count}) below threshold "
            f"min_channels_to_sync={min_channels}. Aborting Gist sync to protect remote playlist. "
            "Use --force-sync to override."
        )
        return False, msg, None

    if sync is None:
        token = get_github_token(cfg)
        gist_id = get_gist_id(cfg)
        sync = GistSync(token=token, gist_id=gist_id)

    if not sync.is_configured():
        return False, "GitHub token is not configured.", None

    return sync.sync_playlist(m3u_text, filename=output_filename)


def parse_args():
    parser = argparse.ArgumentParser(description="Smart IPTV Stream Curator & Parser")
    parser.add_argument(
        "--profile",
        choices=list(PRESETS.keys()) + ["custom"],
        help="Select profile preset (balanced, cinema, sports, kids, news, custom)",
    )
    parser.add_argument("--lang", help="Comma-separated languages (e.g. 'eng,rus' or 'eng')")
    parser.add_argument("--min-res", choices=["all", "720p", "1080p", "4k"], help="Minimum resolution filter")
    parser.add_argument("--no-check", action="store_true", help="Skip online stream checks (faster)")
    parser.add_argument(
        "--deep-check",
        action="store_true",
        help="Verify HLS variant playlists return 200 OK (catches geo-blocks/expired tokens; slower)",
    )
    parser.add_argument(
        "--proxy",
        help="HTTP/SOCKS proxy URL for stream checks (overrides IPTV_PROXY env var and config)",
    )
    parser.add_argument("--no-antitrash", action="store_true", help="Disable anti-trash keyword filter")
    parser.add_argument("--summary-json", help="Path to write run summary/diagnostics JSON file")
    parser.add_argument(
        "--min-channels",
        type=int,
        help="Minimum number of live channels required to sync playlist to Gist (default: 10)",
    )
    parser.add_argument(
        "--force-sync",
        action="store_true",
        help="Force Gist sync even if live channel count is below threshold",
    )
    return parser.parse_args()


def resolve_config(cfg: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """
    Applies profile presets and explicit CLI argument overrides to cfg.
    Explicit CLI flags take precedence over profile presets.
    """
    cfg = copy.deepcopy(cfg)

    # 1. Profile selection
    if getattr(args, "profile", None):
        cfg["profile"] = args.profile

    profile_name = cfg.get("profile", "balanced")
    if profile_name in PRESETS:
        preset = PRESETS[profile_name]
        if profile_name != "custom":
            cfg["categories"] = list(preset.get("categories", cfg.get("categories", [])))
            cfg["min_resolution"] = preset.get("min_resolution", cfg.get("min_resolution", "all"))
            if "include_curated_4k" in preset:
                cfg["include_curated_4k"] = preset["include_curated_4k"]

    # 2. Explicit CLI Overrides take precedence over profile presets
    if getattr(args, "min_res", None):
        cfg["min_resolution"] = args.min_res
    if getattr(args, "lang", None):
        raw_langs = [code.strip().lower() for code in args.lang.split(",") if code.strip()]
        dedup_langs = list(dict.fromkeys(raw_langs))
        if dedup_langs:
            cfg["languages"] = dedup_langs
            cfg["primary_language"] = dedup_langs[0]
    if getattr(args, "no_check", False):
        cfg["check_streams"] = False
    if getattr(args, "deep_check", False):
        cfg["deep_check"] = True
    cli_proxy = (getattr(args, "proxy", None) or "").strip()
    if cli_proxy:
        cfg["proxy"] = cli_proxy
    else:
        env_proxy = os.environ.get("IPTV_PROXY", "").strip()
        if env_proxy:
            cfg["proxy"] = env_proxy
    if getattr(args, "no_antitrash", False):
        cfg.setdefault("content_filters", {})["enable_antitrash"] = False
    if getattr(args, "summary_json", None):
        cfg["summary_json"] = args.summary_json
    if getattr(args, "min_channels", None) is not None:
        cfg["min_channels_to_sync"] = max(0, args.min_channels)
    if getattr(args, "force_sync", False):
        cfg["force_sync"] = True

    # Deduplicate languages and ensure consistent primary_language
    cfg["languages"] = list(dict.fromkeys(cfg.get("languages", ["eng", "rus"])))
    if "primary_language" not in cfg or cfg["primary_language"] not in cfg["languages"]:
        cfg["primary_language"] = cfg["languages"][0] if cfg["languages"] else "eng"

    return cfg


async def main():
    args = parse_args()
    base_cfg = load_config()
    cfg = resolve_config(base_cfg, args)

    profile_name = cfg.get("profile", "balanced")
    if profile_name in PRESETS:
        preset = PRESETS[profile_name]
        print("=" * 65)
        print(f"  Smart IPTV Stream Curator - profile: [{profile_name.upper()}]")
        print(f"  {preset['description']}")
        print("=" * 65)
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

    # Precompile custom_regex once at startup
    if "custom_regex" in content_filters and content_filters["custom_regex"].strip():
        pat = content_filters["custom_regex"].strip()
        compiled = compile_custom_regex(pat)
        content_filters["_compiled_regex"] = compiled if compiled is not None else _INVALID_REGEX

    print(f"[*] Primary language: [{primary_lang.upper()}], languages: {', '.join(languages)}")
    print(f"[*] Min resolution: [{min_res.upper()}]")
    print(f"[*] Categories: {', '.join(cfg.get('categories', [])) if allowed_categories else 'all'}")
    print(f"[*] Anti-trash filter: {'on' if content_filters.get('enable_antitrash', True) else 'off'}")

    # 1. Fetch playlists from sources (parallel via asyncio.gather with TLS enabled)
    all_channels = []
    seen_urls = set()
    trash_count = 0

    resolver = aiohttp.ThreadedResolver()
    connector = aiohttp.TCPConnector(resolver=resolver)  # TLS enabled for trusted playlist downloads
    async with aiohttp.ClientSession(connector=connector) as session:
        fetch_tasks = []
        task_meta = []

        # A. Official iptv-org sources
        if sources_cfg.get("iptv_org", True):
            for lang in languages:
                url = f"https://iptv-org.github.io/iptv/languages/{lang}.m3u"
                fetch_tasks.append(fetch_playlist(session, url, lang=lang))
                task_meta.append(("iptv_org", lang, url))

        # B. Custom M3U URLs (Multi-source)
        for custom_url in sources_cfg.get("custom_m3u_urls", []):
            url = custom_url.strip()
            if url:
                fetch_tasks.append(fetch_playlist(session, url, lang="custom"))
                task_meta.append(("custom", "custom", url))

        if fetch_tasks:
            results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
            for (source_type, lang, url), res in zip(task_meta, results, strict=True):
                if isinstance(res, Exception):
                    print(f"[!] Error fetching {url}: {res}")
                    continue
                chans = res
                label = lang.upper() if source_type == "iptv_org" else "CUSTOM"
                print(f"[*] Fetched {len(chans)} channels from {url} [{label}].")

                for ch in chans:
                    url_val = ch["url"]
                    if url_val in seen_urls:
                        continue
                    seen_urls.add(url_val)

                    raw_grp = ch.get("raw_group", "").lower()
                    if any(ex in raw_grp for ex in exclude_categories):
                        continue
                    if allowed_categories and not any(allowed in raw_grp for allowed in allowed_categories):
                        continue

                    passed, _ = passes_content_filters(ch, content_filters)
                    if not passed:
                        trash_count += 1
                        continue

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

    # D. Built-in free 4K channels (unless filtered out by profile)
    if cfg.get("include_curated_4k", True):
        for ch4k in UHD_4K_CHANNELS:
            ch_copy = copy.deepcopy(ch4k)
            if ch_copy["url"] not in seen_urls:
                seen_urls.add(ch_copy["url"])
                passed, _ = passes_content_filters(ch_copy, content_filters)
                if passed:
                    all_channels.append(ch_copy)

    print(f"\n[+] Candidates: {len(all_channels)} (dropped by anti-trash/blacklist: {trash_count})")
    summary_path = (cfg.get("summary_json") or getattr(args, "summary_json", None) or "").strip()
    check_stats: dict[str, Any] = {}

    def write_summary_json(final_count: int, alive_count: int):
        if not summary_path:
            return
        summary_data = {
            "total_candidates": len(all_channels),
            "alive_streams": alive_count,
            "dropped_streams": len(all_channels) - alive_count,
            "check_duration_seconds": check_stats.get("duration_seconds", 0.0),
            "drop_reasons": check_stats.get("drop_reasons", {}),
            "final_channels_count": final_count,
        }
        try:
            with open(summary_path, "w", encoding="utf-8") as f:
                json.dump(summary_data, f, indent=2, ensure_ascii=False)
            print(f"[+] Summary JSON saved: {os.path.abspath(summary_path)}")
        except Exception as e:
            print(f"[!] Failed to save summary JSON to {summary_path}: {e}")

    if not all_channels:
        write_summary_json(0, 0)
        print("[!] No channels match the current filters.")
        return

    # 2. Check stream availability & probe HLS resolution
    if cfg.get("check_streams", True):
        working_channels = await check_all_streams(all_channels, cfg, summary_out=check_stats)
    else:
        print("[*] Stream check skipped (--no-check).")
        working_channels = all_channels

    if not working_channels:
        write_summary_json(0, 0)
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
    lang_order = list(
        dict.fromkeys(
            ["uhd", primary_lang] + [code for code in languages if code != primary_lang] + ["custom", "local"]
        )
    )
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
    token = get_github_token(cfg)
    gist_id = get_gist_id(cfg)
    sync = GistSync(token=token, gist_id=gist_id)

    if sync.is_configured():
        print("\n[*] Syncing to GitHub Gist...")
        output_filename = os.path.basename(output_file) or "playlist.m3u"
        success, url_or_err, new_id = sync_to_gist(
            m3u_text=m3u_text,
            channel_count=len(final_channels),
            cfg=cfg,
            sync=sync,
            output_filename=output_filename,
        )
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
            print(f"[!] Gist sync failed: {url_or_err}")
            write_summary_json(len(final_channels), len(working_channels))
            sys.exit(1)
    else:
        print("\n[*] Gist sync not configured, playlist is local only.")

    write_summary_json(len(final_channels), len(working_channels))


if __name__ == "__main__":
    asyncio.run(main())
