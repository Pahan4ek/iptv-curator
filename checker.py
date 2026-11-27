import asyncio
import contextlib
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import aiohttp

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


@dataclass
class CheckResult:
    """
    Structured outcome of a single stream probe.
    Supports backwards-compatible 3-tuple unpacking: (alive, latency_ms, stream_info).
    """

    alive: bool
    latency_ms: float = 0.0
    stream_info: dict[str, Any] = field(default_factory=dict)
    reason: str = "ok"
    status_code: int | None = None

    def __iter__(self):
        yield self.alive
        yield self.latency_ms
        yield self.stream_info

    def __getitem__(self, index: int):
        return (self.alive, self.latency_ms, self.stream_info)[index]


def parse_hls_stream_info(manifest_text: str) -> dict[str, Any]:
    """
    Parses HLS master manifest for stream quality parameters:
    max resolution (height, width), max bandwidth.
    """
    info = {
        "max_height": 0,
        "max_width": 0,
        "bandwidth": 0,
        "resolution_str": "",
    }

    # Match RESOLUTION=1920x1080
    res_matches = re.findall(r"RESOLUTION=(\d+)x(\d+)", manifest_text, re.IGNORECASE)
    for w_str, h_str in res_matches:
        try:
            w, h = int(w_str), int(h_str)
            if h > info["max_height"]:
                info["max_height"] = h
                info["max_width"] = w
                info["resolution_str"] = "4K" if h >= 2160 else f"{h}p"
        except ValueError:
            continue

    # Match BANDWIDTH=3500000 without matching AVERAGE-BANDWIDTH
    bw_matches = re.findall(r"(?<![A-Z-])BANDWIDTH=(\d+)", manifest_text, re.IGNORECASE)
    for bw_str in bw_matches:
        try:
            bw = int(bw_str)
            if bw > info["bandwidth"]:
                info["bandwidth"] = bw
        except ValueError:
            continue

    return info


def extract_hls_variant_url(manifest_text: str, base_url: str) -> str | None:
    """
    Extracts the first variant playlist URL following an #EXT-X-STREAM-INF tag
    and resolves it against base_url using urllib.parse.urljoin.
    """
    expect_uri = False
    for raw_line in manifest_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.upper().startswith("#EXT-X-STREAM-INF"):
            expect_uri = True
            continue
        if expect_uri:
            if line.startswith("#"):
                continue
            return urljoin(base_url, line)
    return None


def is_socks_proxy(proxy: str | None) -> bool:
    """Returns True if proxy URL uses a SOCKS scheme (socks4/socks4a/socks5/socks5h)."""
    if not proxy:
        return False
    return proxy.strip().lower().startswith(("socks4://", "socks4a://", "socks5://", "socks5h://"))


def _status_failure_reason(status_code: int, prefix: str = "http") -> str:
    if 500 <= status_code < 600:
        return f"{prefix}_5xx"
    return f"{prefix}_{status_code}"


def is_mpegts(chunk: bytes) -> bool:
    """
    Checks for MPEG-TS sync byte 0x47 repeated every 188 bytes across at least 2-3 packets.
    Avoids false positives from ASCII strings starting with 'G' (0x47).
    """
    if len(chunk) >= 377:
        return chunk[0] == 0x47 and chunk[188] == 0x47 and chunk[376] == 0x47
    if len(chunk) >= 189:
        return chunk[0] == 0x47 and chunk[188] == 0x47
    return False


def is_valid_media_binary(chunk: bytes) -> bool:
    """
    Validates known binary media stream signatures and explicitly rejects
    text, JSON, and HTML error payloads.
    Supported signatures:
    - MPEG-TS (sync byte 0x47 with 188-byte stride)
    - MP4 / ISO BMFF ('ftyp' box)
    - WebM / MKV (EBML \x1a\x45\xdf\xa3)
    - FLV (b'FLV')
    - Ogg (b'OggS')
    - ADTS / ID3 / MP3 (b'ID3' or sync word 0xFFE0 / 0xFFF0)
    """
    if not chunk:
        return False

    stripped = chunk.lstrip()
    if (
        stripped.startswith((b"{", b"[", b"<", b"error", b"Error", b"ERROR"))
        or b"<html" in stripped.lower()
        or b"<!doctype" in stripped.lower()
    ):
        return False

    if is_mpegts(chunk):
        return True
    if len(chunk) >= 8 and (chunk[4:8] == b"ftyp" or b"ftyp" in chunk[:32]):
        return True
    if chunk.startswith(b"\x1a\x45\xdf\xa3"):
        return True
    if chunk.startswith(b"FLV"):
        return True
    if chunk.startswith(b"OggS"):
        return True
    if chunk.startswith(b"ID3"):
        return True
    return len(chunk) >= 2 and chunk[0] == 0xFF and (chunk[1] & 0xE0) == 0xE0


async def _check_single_stream_impl(
    session: aiohttp.ClientSession,
    channel: dict[str, Any],
    timeout_sec: float = 4.0,
    deep_check: bool = False,
    proxy: str | None = None,
) -> CheckResult:
    url = channel.get("url", "")
    stream_info = {
        "max_height": 0,
        "max_width": 0,
        "bandwidth": 0,
        "resolution_str": "",
    }

    if not url.startswith(("http://", "https://")):
        return CheckResult(
            alive=False,
            latency_ms=0.0,
            stream_info=stream_info,
            reason="unsupported_scheme",
            status_code=None,
        )

    custom_headers = channel.get("headers", {})

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "*/*",
    }
    for k, v in custom_headers.items():
        k_lower = k.lower()
        if k_lower == "user-agent":
            headers["User-Agent"] = str(v)
        elif k_lower in ("referer", "referrer"):
            headers["Referer"] = str(v)
        else:
            headers[k] = str(v)

    t0 = time.monotonic()

    try:
        timeout = aiohttp.ClientTimeout(
            total=timeout_sec,
            connect=timeout_sec * 0.7,
            sock_read=timeout_sec * 0.7,
        )
        request_kwargs: dict[str, Any] = {
            "headers": headers,
            "timeout": timeout,
            "allow_redirects": True,
        }
        clean_proxy = proxy.strip() if isinstance(proxy, str) and proxy.strip() else None
        if clean_proxy and not is_socks_proxy(clean_proxy):
            request_kwargs["proxy"] = clean_proxy

        variant_url: str | None = None
        async with session.get(url, **request_kwargs) as resp:
            status_code = resp.status
            if status_code != 200:
                return CheckResult(
                    alive=False,
                    latency_ms=0.0,
                    stream_info=stream_info,
                    reason=_status_failure_reason(status_code),
                    status_code=status_code,
                )

            content_type = resp.headers.get("Content-Type", "").lower()
            if "text/html" in content_type:
                return CheckResult(
                    alive=False,
                    latency_ms=0.0,
                    stream_info=stream_info,
                    reason="html_response",
                    status_code=status_code,
                )

            # Read first chunk up to 4KB to inspect payload & stream-inf
            chunk = await resp.content.read(4096)
            latency_ms = (time.monotonic() - t0) * 1000

            # Reject HTML error pages returning HTTP 200
            lower_chunk = chunk.lower()
            if b"<!doctype html" in lower_chunk or b"<html" in lower_chunk:
                return CheckResult(
                    alive=False,
                    latency_ms=0.0,
                    stream_info=stream_info,
                    reason="html_response",
                    status_code=status_code,
                )

            # Check for HLS Master or Media Manifest
            if b"#EXTM3U" in chunk or b"#EXT-X-STREAM-INF" in chunk:
                manifest_bytes = chunk
                remaining = 65536 - len(manifest_bytes)
                while remaining > 0:
                    more = await resp.content.read(min(remaining, 8192))
                    if not more:
                        break
                    manifest_bytes += more
                    remaining -= len(more)

                has_stream_inf = b"#EXT-X-STREAM-INF" in manifest_bytes
                has_extinf = b"#EXTINF" in manifest_bytes

                if has_stream_inf or has_extinf:
                    text_manifest = manifest_bytes.decode("utf-8", errors="ignore")
                    with contextlib.suppress(Exception):
                        stream_info = parse_hls_stream_info(text_manifest)

                    if not deep_check or not has_stream_inf:
                        return CheckResult(
                            alive=True,
                            latency_ms=latency_ms,
                            stream_info=stream_info,
                            reason="ok",
                            status_code=status_code,
                        )

                    final_url = str(getattr(resp, "url", None) or url)
                    variant_url = extract_hls_variant_url(text_manifest, final_url)
                else:
                    return CheckResult(
                        alive=False,
                        latency_ms=0.0,
                        stream_info=stream_info,
                        reason="empty_hls",
                        status_code=status_code,
                    )

            # HLS media fragment without #EXTM3U, or valid binary media payload
            elif b"#EXTINF" in chunk or is_valid_media_binary(chunk):
                return CheckResult(
                    alive=True,
                    latency_ms=latency_ms,
                    stream_info=stream_info,
                    reason="ok",
                    status_code=status_code,
                )

            else:
                return CheckResult(
                    alive=False,
                    latency_ms=0.0,
                    stream_info=stream_info,
                    reason="invalid_binary",
                    status_code=status_code,
                )

        # Deep check: verify the extracted HLS variant playlist returns 200 OK
        if not variant_url:
            return CheckResult(
                alive=False,
                latency_ms=0.0,
                stream_info=stream_info,
                reason="empty_hls",
                status_code=status_code,
            )

        if not variant_url.startswith(("http://", "https://")):
            return CheckResult(
                alive=False,
                latency_ms=0.0,
                stream_info=stream_info,
                reason="unsupported_scheme",
                status_code=None,
            )

        async with session.get(variant_url, **request_kwargs) as vresp:
            vstatus = vresp.status
            if vstatus != 200:
                return CheckResult(
                    alive=False,
                    latency_ms=0.0,
                    stream_info=stream_info,
                    reason=_status_failure_reason(vstatus, prefix="hls_variant"),
                    status_code=vstatus,
                )

            v_ct = vresp.headers.get("Content-Type", "").lower()
            if "text/html" in v_ct:
                return CheckResult(
                    alive=False,
                    latency_ms=0.0,
                    stream_info=stream_info,
                    reason="hls_variant_html",
                    status_code=vstatus,
                )

            vchunk = await vresp.content.read(4096)
            latency_ms = (time.monotonic() - t0) * 1000
            if not vchunk:
                return CheckResult(
                    alive=False,
                    latency_ms=0.0,
                    stream_info=stream_info,
                    reason="hls_variant_empty",
                    status_code=vstatus,
                )

            lower_vchunk = vchunk.lower()
            if b"<!doctype html" in lower_vchunk or b"<html" in lower_vchunk:
                return CheckResult(
                    alive=False,
                    latency_ms=0.0,
                    stream_info=stream_info,
                    reason="hls_variant_html",
                    status_code=vstatus,
                )

            if b"#EXT" in vchunk or is_valid_media_binary(vchunk):
                return CheckResult(
                    alive=True,
                    latency_ms=latency_ms,
                    stream_info=stream_info,
                    reason="ok",
                    status_code=vstatus,
                )

            return CheckResult(
                alive=False,
                latency_ms=0.0,
                stream_info=stream_info,
                reason="hls_variant_invalid",
                status_code=vstatus,
            )

    except (asyncio.TimeoutError, TimeoutError):
        return CheckResult(
            alive=False,
            latency_ms=0.0,
            stream_info=stream_info,
            reason="timeout",
            status_code=None,
        )
    except Exception:
        return CheckResult(
            alive=False,
            latency_ms=0.0,
            stream_info=stream_info,
            reason="network_error",
            status_code=None,
        )


async def check_single_stream(
    session: aiohttp.ClientSession,
    channel: dict[str, Any],
    semaphore: asyncio.Semaphore | None = None,
    timeout_sec: float = 4.0,
    deep_check: bool = False,
    proxy: str | None = None,
) -> CheckResult:
    """
    Checks if a stream URL is reachable and returns valid media/HLS data.
    Returns CheckResult(alive, latency_ms, stream_info, reason, status_code).
    """
    if semaphore is not None:
        async with semaphore:
            return await _check_single_stream_impl(session, channel, timeout_sec, deep_check=deep_check, proxy=proxy)
    return await _check_single_stream_impl(session, channel, timeout_sec, deep_check=deep_check, proxy=proxy)
