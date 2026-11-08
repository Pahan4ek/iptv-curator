import asyncio
import re
import time
from typing import Any

import aiohttp

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

def parse_hls_stream_info(manifest_text: str) -> dict[str, Any]:
    """
    Parses HLS master manifest for stream quality parameters:
    max resolution (height, width), max bandwidth.
    """
    info = {
        "max_height": 0,
        "max_width": 0,
        "bandwidth": 0,
        "resolution_str": ""
    }

    # Match RESOLUTION=1920x1080
    res_matches = re.findall(r'RESOLUTION=(\d+)x(\d+)', manifest_text, re.IGNORECASE)
    for w_str, h_str in res_matches:
        try:
            w, h = int(w_str), int(h_str)
            if h > info["max_height"]:
                info["max_height"] = h
                info["max_width"] = w
                info["resolution_str"] = f"{h}p" if h not in (2160, 1080, 720) else ("4K" if h >= 2160 else f"{h}p")
        except ValueError:
            continue

    # Match BANDWIDTH=3500000
    bw_matches = re.findall(r'BANDWIDTH=(\d+)', manifest_text, re.IGNORECASE)
    for bw_str in bw_matches:
        try:
            bw = int(bw_str)
            if bw > info["bandwidth"]:
                info["bandwidth"] = bw
        except ValueError:
            continue

    return info

async def check_single_stream(
    session: aiohttp.ClientSession,
    channel: dict[str, Any],
    semaphore: asyncio.Semaphore | None = None,
    timeout_sec: float = 4.0
) -> tuple[bool, float, dict[str, Any]]:
    """
    Checks if a stream URL is reachable and returns valid media/HLS data.
    Returns (is_alive: bool, latency_ms: float, stream_info: dict).
    """
    url = channel["url"]
    custom_headers = channel.get("headers", {})

    headers = {
        "User-Agent": custom_headers.get("User-Agent", DEFAULT_USER_AGENT),
        "Accept": "*/*"
    }
    if "Referer" in custom_headers:
        headers["Referer"] = custom_headers["Referer"]

    t0 = time.monotonic()
    stream_info = {
        "max_height": 0,
        "max_width": 0,
        "bandwidth": 0,
        "resolution_str": ""
    }

    try:
        timeout = aiohttp.ClientTimeout(
            total=timeout_sec,
            connect=timeout_sec * 0.7,
            sock_read=timeout_sec * 0.7
        )
        async with session.get(url, headers=headers, timeout=timeout, allow_redirects=True) as resp:
            if resp.status != 200:
                return False, 0.0, stream_info

            content_type = resp.headers.get("Content-Type", "").lower()

            # Read first chunk up to 4KB to inspect payload & stream-inf
            chunk = await resp.content.read(4096)
            latency_ms = (time.monotonic() - t0) * 1000

            # Reject HTML error pages returning HTTP 200
            lower_chunk = chunk.lower()
            if b"<!doctype html" in lower_chunk or b"<html" in lower_chunk:
                return False, 0.0, stream_info

            # Inspect HLS Master Manifest
            if b"#EXT-X-STREAM-INF" in chunk or b"#EXTM3U" in chunk:
                try:
                    text_chunk = chunk.decode("utf-8", errors="ignore")
                    stream_info = parse_hls_stream_info(text_chunk)
                except Exception:
                    pass
                return True, latency_ms, stream_info

            # Valid HLS media playlist or version tag
            if b"#EXT-X-VERSION" in chunk or b"#EXTINF" in chunk:
                return True, latency_ms, stream_info

            # Valid MPEG-TS (starts with sync byte 0x47)
            if chunk and chunk[0] == 0x47:
                return True, latency_ms, stream_info

            # Content-Type matching known media types
            media_types = [
                "application/vnd.apple.mpegurl",
                "application/x-mpegurl",
                "audio/mpegurl",
                "video/mp2t",
                "video/mp4",
                "video/webm",
                "audio/aac",
                "audio/mp3",
                "application/octet-stream"
            ]
            if any(m in content_type for m in media_types) and len(chunk) > 0:
                return True, latency_ms, stream_info

            return False, 0.0, stream_info

    except Exception:
        return False, 0.0, stream_info
