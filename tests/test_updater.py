import asyncio
import json
import os
import sys

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checker import CheckResult  # noqa: E402
from iptv_updater import DEFAULT_CONFIG, check_all_streams  # noqa: E402


def test_concurrency_normalization_negative_and_zero(monkeypatch):
    captured_kwargs = {}
    orig_connector = aiohttp.TCPConnector

    def connector_spy(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return orig_connector(*args, **kwargs)

    monkeypatch.setattr(aiohttp, "TCPConnector", connector_spy)

    # 1. Negative concurrency
    captured_kwargs.clear()
    cfg_neg = {"max_concurrent_checks": -10, "stream_check_timeout_seconds": 1.0}
    asyncio.run(check_all_streams([], cfg_neg))
    assert captured_kwargs.get("limit") == 1

    # 2. Zero concurrency
    captured_kwargs.clear()
    cfg_zero = {"max_concurrent_checks": 0, "stream_check_timeout_seconds": 1.0}
    asyncio.run(check_all_streams([], cfg_zero))
    assert captured_kwargs.get("limit") == 1

    # 3. Invalid non-integer string
    captured_kwargs.clear()
    cfg_inv = {"max_concurrent_checks": "invalid", "stream_check_timeout_seconds": 1.0}
    asyncio.run(check_all_streams([], cfg_inv))
    assert captured_kwargs.get("limit") == 40


def test_limit_per_host_configured(monkeypatch):
    captured_kwargs = {}
    orig_connector = aiohttp.TCPConnector

    def connector_spy(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return orig_connector(*args, **kwargs)

    monkeypatch.setattr(aiohttp, "TCPConnector", connector_spy)

    # Verify default config has limit_per_host
    assert DEFAULT_CONFIG.get("max_concurrent_per_host") == 6

    cfg = {"max_concurrent_per_host": 8, "max_concurrent_checks": 20}
    asyncio.run(check_all_streams([], cfg))
    assert captured_kwargs.get("limit_per_host") == 8


def test_check_all_streams_summary_and_drop_reasons(monkeypatch):
    channels = [
        {"name": "Live 1", "url": "https://stream.example/1.m3u8"},
        {"name": "Live 2", "url": "https://stream.example/2.m3u8"},
        {"name": "Dead 403", "url": "https://stream.example/3.m3u8"},
        {"name": "Dead Timeout", "url": "https://stream.example/4.m3u8"},
        {"name": "Dead Empty HLS", "url": "https://stream.example/5.m3u8"},
    ]

    async def mock_check_single_stream(session, ch, semaphore=None, timeout_sec=4.0):
        url = ch["url"]
        if "1.m3u8" in url:
            return CheckResult(
                alive=True, latency_ms=10.0, stream_info={"max_height": 1080, "bandwidth": 5000000}, reason="ok"
            )
        elif "2.m3u8" in url:
            return CheckResult(
                alive=True, latency_ms=20.0, stream_info={"max_height": 720, "bandwidth": 2500000}, reason="ok"
            )
        elif "3.m3u8" in url:
            return CheckResult(alive=False, latency_ms=0.0, reason="http_403", status_code=403)
        elif "4.m3u8" in url:
            return CheckResult(alive=False, latency_ms=0.0, reason="timeout")
        else:
            return CheckResult(alive=False, latency_ms=0.0, reason="empty_hls")

    import iptv_updater

    monkeypatch.setattr(iptv_updater, "check_single_stream", mock_check_single_stream)

    summary_stats = {}
    cfg = {"max_concurrent_checks": 5, "stream_check_timeout_seconds": 1.0}
    alive_channels = asyncio.run(check_all_streams(channels, cfg, summary_out=summary_stats))

    assert len(alive_channels) == 2
    assert summary_stats["total"] == 5
    assert summary_stats["alive"] == 2
    assert summary_stats["dropped"] == 3
    assert summary_stats["drop_reasons"] == {
        "http_403": 1,
        "timeout": 1,
        "empty_hls": 1,
    }


def test_summary_json_output(tmp_path, monkeypatch):
    channels = [
        {"name": "Channel 1", "url": "https://stream.example/1.m3u8"},
        {"name": "Channel 2", "url": "https://stream.example/2.m3u8"},
    ]

    async def mock_check_single_stream(session, ch, semaphore=None, timeout_sec=4.0):
        if "1.m3u8" in ch["url"]:
            return CheckResult(alive=True, latency_ms=15.0, reason="ok")
        return CheckResult(alive=False, latency_ms=0.0, reason="http_404", status_code=404)

    import iptv_updater

    monkeypatch.setattr(iptv_updater, "check_single_stream", mock_check_single_stream)

    summary_stats = {}
    summary_file = tmp_path / "test_summary.json"
    cfg = {
        "max_concurrent_checks": 2,
        "stream_check_timeout_seconds": 1.0,
        "summary_json": str(summary_file),
    }

    alive_channels = asyncio.run(check_all_streams(channels, cfg, summary_out=summary_stats))
    assert len(alive_channels) == 1

    # Simulate saving summary file as done in main()
    summary_data = {
        "total_candidates": len(channels),
        "alive_streams": len(alive_channels),
        "dropped_streams": len(channels) - len(alive_channels),
        "check_duration_seconds": summary_stats.get("duration_seconds", 0.0),
        "drop_reasons": summary_stats.get("drop_reasons", {}),
        "final_channels_count": len(alive_channels),
    }
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2)

    assert summary_file.exists()
    with open(summary_file, encoding="utf-8") as f:
        loaded = json.load(f)

    assert loaded["total_candidates"] == 2
    assert loaded["alive_streams"] == 1
    assert loaded["dropped_streams"] == 1
    assert loaded["drop_reasons"]["http_404"] == 1
