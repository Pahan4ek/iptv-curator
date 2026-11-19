import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checker import (  # noqa: E402
    DEFAULT_USER_AGENT,
    check_single_stream,
    is_mpegts,
    is_valid_media_binary,
    parse_hls_stream_info,
)


class MockStreamResponse:
    """Mock aiohttp response yielding predefined chunks via content.read()."""

    def __init__(
        self,
        chunks: list[bytes],
        status: int = 200,
        content_type: str = "video/mp2t",
        extra_headers: dict[str, str] | None = None,
    ):
        self._chunks = list(chunks)
        self.status = status
        self.headers = {"Content-Type": content_type}
        if extra_headers:
            self.headers.update(extra_headers)
        self.content = self

    async def read(self, n: int = -1) -> bytes:
        if not self._chunks:
            return b""
        if n <= 0 or len(self._chunks[0]) <= n:
            return self._chunks.pop(0)
        chunk = self._chunks[0][:n]
        self._chunks[0] = self._chunks[0][n:]
        return chunk

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass


class MockClientSession:
    """Mock ClientSession capturing request parameters and returning MockStreamResponse."""

    def __init__(self, response: MockStreamResponse):
        self.response = response
        self.called_url = None
        self.called_headers = None
        self.called_timeout = None

    def get(self, url, headers=None, timeout=None, allow_redirects=True):
        self.called_url = url
        self.called_headers = headers
        self.called_timeout = timeout
        return self.response


def test_is_mpegts_false_positive():
    # ASCII 'G' is 0x47, but this is not MPEG-TS
    assert is_mpegts(b"Geo-blocked stream: your IP is not allowed") is False
    assert is_mpegts(b"Gone: resource has been deleted") is False
    assert is_mpegts(b"Gateway timeout error from server") is False
    assert is_mpegts(b"") is False
    assert is_mpegts(b"\x47" * 100) is False  # too short (< 189)


def test_is_mpegts_valid_buffer():
    # Valid TS buffer with sync byte 0x47 at offsets 0, 188, 376
    valid_ts = bytearray(380)
    valid_ts[0] = 0x47
    valid_ts[188] = 0x47
    valid_ts[376] = 0x47
    assert is_mpegts(bytes(valid_ts)) is True

    # Two packets (189..376 bytes)
    valid_2_packets = bytearray(200)
    valid_2_packets[0] = 0x47
    valid_2_packets[188] = 0x47
    assert is_mpegts(bytes(valid_2_packets)) is True

    # Corrupted 3rd packet sync byte
    corrupted = bytearray(valid_ts)
    corrupted[376] = 0x00
    assert is_mpegts(bytes(corrupted)) is False


def test_is_valid_media_binary():
    # MPEG-TS
    valid_ts = bytearray(380)
    valid_ts[0] = 0x47
    valid_ts[188] = 0x47
    valid_ts[376] = 0x47
    assert is_valid_media_binary(bytes(valid_ts)) is True

    # MP4 / ISO BMFF
    mp4 = b"\x00\x00\x00\x20ftypmp42\x00\x00\x00\x00"
    assert is_valid_media_binary(mp4) is True

    # WebM / MKV EBML
    webm = b"\x1a\x45\xdf\xa3\x93\x42\x82\x88matroska"
    assert is_valid_media_binary(webm) is True

    # FLV
    flv = b"FLV\x01\x05\x00\x00\x00\x09"
    assert is_valid_media_binary(flv) is True

    # Ogg
    ogg = b"OggS\x00\x02\x00\x00\x00\x00"
    assert is_valid_media_binary(ogg) is True

    # ID3 / MP3 / ADTS
    id3 = b"ID3\x04\x00\x00\x00\x00\x00\x00"
    assert is_valid_media_binary(id3) is True
    adts = b"\xff\xf1\x50\x80"
    assert is_valid_media_binary(adts) is True

    # Rejection of text / JSON / HTML
    assert is_valid_media_binary(b'{"error": "offline"}') is False
    assert is_valid_media_binary(b"   [500 Internal Error]") is False
    assert is_valid_media_binary(b"<!DOCTYPE html><html><body>Error</body></html>") is False
    assert is_valid_media_binary(b"<html lang='en'>") is False
    assert is_valid_media_binary(b"Access Denied") is False
    assert is_valid_media_binary(b"") is False


def test_parse_hls_stream_info_resolution_and_bandwidth():
    manifest = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:AVERAGE-BANDWIDTH=9000000,BANDWIDTH=3500000,RESOLUTION=1280x720\n720p.m3u8\n"
        "#EXT-X-STREAM-INF:AVERAGE-BANDWIDTH=15000000,BANDWIDTH=6000000,RESOLUTION=1920x1080\n1080p.m3u8\n"
        "#EXT-X-STREAM-INF:AVERAGE-BANDWIDTH=25000000,BANDWIDTH=18000000,RESOLUTION=3840x2160\n4k.m3u8\n"
    )
    info = parse_hls_stream_info(manifest)
    assert info["max_height"] == 2160
    assert info["max_width"] == 3840
    assert info["resolution_str"] == "4K"
    # Bandwidth must match BANDWIDTH, NOT AVERAGE-BANDWIDTH
    assert info["bandwidth"] == 18000000

    # 1440p resolution
    info_1440 = parse_hls_stream_info("#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=2560x1440\n")
    assert info_1440["max_height"] == 1440
    assert info_1440["resolution_str"] == "1440p"


def test_check_stream_ts_false_positive():
    resp = MockStreamResponse(
        chunks=[b"Geo-blocked stream: not allowed in your area"],
        content_type="video/mp2t",
    )
    session = MockClientSession(resp)
    is_alive, latency, info = asyncio.run(check_single_stream(session, {"url": "https://tv.example/live.ts"}))
    assert is_alive is False


def test_check_stream_valid_ts():
    valid_ts = bytearray(380)
    valid_ts[0] = 0x47
    valid_ts[188] = 0x47
    valid_ts[376] = 0x47

    resp = MockStreamResponse(chunks=[bytes(valid_ts)], content_type="video/mp2t")
    session = MockClientSession(resp)
    is_alive, latency, info = asyncio.run(check_single_stream(session, {"url": "https://tv.example/live.ts"}))
    assert is_alive is True
    assert latency >= 0.0


def test_check_stream_empty_hls():
    empty_hls = b"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-ENDLIST\n"
    resp = MockStreamResponse(chunks=[empty_hls], content_type="application/vnd.apple.mpegurl")
    session = MockClientSession(resp)
    is_alive, latency, info = asyncio.run(check_single_stream(session, {"url": "https://tv.example/live.m3u8"}))
    assert is_alive is False


def test_check_stream_json_error_octet_stream():
    json_err = b'{"error": "offline"}'
    resp = MockStreamResponse(chunks=[json_err], content_type="application/octet-stream")
    session = MockClientSession(resp)
    is_alive, latency, info = asyncio.run(check_single_stream(session, {"url": "https://tv.example/live.ts"}))
    assert is_alive is False


def test_check_stream_large_master_manifest():
    # Construct a master playlist > 4 KB where 4K stream is located after 4096 bytes
    chunk1_lines = ["#EXTM3U\n", "#EXT-X-VERSION:3\n"]
    # Pad to > 4096 bytes with comments and 720p stream
    chunk1_lines.append(
        "#EXT-X-STREAM-INF:AVERAGE-BANDWIDTH=5000000,BANDWIDTH=2000000,RESOLUTION=1280x720\n720p.m3u8\n"
    )
    while sum(len(line.encode()) for line in chunk1_lines) < 4200:
        chunk1_lines.append("# Comment line with filler data to test chunk reading boundaries\n")

    chunk1 = "".join(chunk1_lines).encode("utf-8")
    assert len(chunk1) > 4096

    chunk2 = b"#EXT-X-STREAM-INF:AVERAGE-BANDWIDTH=30000000,BANDWIDTH=18000000,RESOLUTION=3840x2160\n4k.m3u8\n"

    resp = MockStreamResponse(
        chunks=[chunk1[:4096], chunk1[4096:] + chunk2],
        content_type="application/vnd.apple.mpegurl",
    )
    session = MockClientSession(resp)
    is_alive, latency, info = asyncio.run(check_single_stream(session, {"url": "https://tv.example/master.m3u8"}))

    assert is_alive is True
    assert info["max_height"] == 2160
    assert info["resolution_str"] == "4K"
    assert info["bandwidth"] == 18000000


def test_check_stream_forwarding_headers():
    valid_ts = bytearray(380)
    valid_ts[0] = 0x47
    valid_ts[188] = 0x47
    valid_ts[376] = 0x47

    resp = MockStreamResponse(chunks=[bytes(valid_ts)], content_type="video/mp2t")
    session = MockClientSession(resp)

    channel = {
        "url": "https://tv.example/live.ts",
        "headers": {
            "user-agent": "MyCustomBot/2.0",
            "referer": "https://player.example.com",
            "Origin": "https://player.example.com",
            "Cookie": "auth_token=secret123",
            "X-Custom-Header": "value42",
        },
    }

    is_alive, latency, info = asyncio.run(check_single_stream(session, channel))
    assert is_alive is True

    sent_headers = session.called_headers
    assert sent_headers["User-Agent"] == "MyCustomBot/2.0"
    assert sent_headers["Referer"] == "https://player.example.com"
    assert sent_headers["Origin"] == "https://player.example.com"
    assert sent_headers["Cookie"] == "auth_token=secret123"
    assert sent_headers["X-Custom-Header"] == "value42"
    assert sent_headers["Accept"] == "*/*"


def test_check_stream_default_headers():
    valid_ts = bytearray(380)
    valid_ts[0] = 0x47
    valid_ts[188] = 0x47
    valid_ts[376] = 0x47

    resp = MockStreamResponse(chunks=[bytes(valid_ts)], content_type="video/mp2t")
    session = MockClientSession(resp)

    channel = {"url": "https://tv.example/live.ts"}
    is_alive, latency, info = asyncio.run(check_single_stream(session, channel))
    assert is_alive is True

    sent_headers = session.called_headers
    assert sent_headers["User-Agent"] == DEFAULT_USER_AGENT
    assert "Referer" not in sent_headers


def test_check_stream_semaphore():
    valid_ts = bytearray(380)
    valid_ts[0] = 0x47
    valid_ts[188] = 0x47
    valid_ts[376] = 0x47

    resp = MockStreamResponse(chunks=[bytes(valid_ts)], content_type="video/mp2t")
    session = MockClientSession(resp)

    async def run_with_sem():
        sem = asyncio.Semaphore(1)
        # Lock semaphore
        await sem.acquire()
        # check_single_stream should wait or if semaphore is 0 cannot enter immediately
        assert sem.locked()
        sem.release()
        return await check_single_stream(session, {"url": "https://tv.example/live.ts"}, semaphore=sem)

    is_alive, latency, info = asyncio.run(run_with_sem())
    assert is_alive is True
