import argparse
import asyncio
import copy
import json
import os
import sys

import aiohttp
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checker import CheckResult  # noqa: E402
from iptv_updater import (  # noqa: E402
    DEFAULT_CONFIG,
    UHD_4K_CHANNELS,
    build_m3u,
    check_all_streams,
    compile_custom_regex,
    get_channel_quality_score,
    is_high_def,
    mask_proxy_url,
    parse_args,
    parse_m3u,
    passes_content_filters,
    resolve_config,
)


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


def test_parse_m3u_channel_name_with_comma_and_logo_quality():
    raw_m3u = (
        '#EXTINF:-1 tvg-logo="https://img.example/hd4k.png",NBC 4, New York\n'
        "https://example.com/nbc.m3u8\n"
        '#EXTINF:-1 group-title="News",News, Sport & Weather\n'
        "https://example.com/nsw.m3u8\n"
    )
    chans = parse_m3u(raw_m3u, lang="eng")
    assert len(chans) == 2

    nbc = chans[0]
    assert nbc["name"] == "NBC 4, New York"
    score = get_channel_quality_score(nbc)
    # The logo URL contains 'hd4k', but tvg_logo / extinf logo must NOT affect quality score
    assert score < 720
    assert score != 2160
    assert not is_high_def(nbc)

    nsw = chans[1]
    assert nsw["name"] == "News, Sport & Weather"


def test_build_m3u_deduplicates_headers_and_vlcopt():
    raw_m3u = (
        '#EXTINF:-1 http-user-agent="CustomUA/1.0" http-referrer="https://ref.example",NBC 4, New York\n'
        "#EXTVLCOPT:http-user-agent=CustomUA/1.0\n"
        "#EXTVLCOPT:http-referrer=https://ref.example\n"
        "#EXTVLCOPT:http-user-agent=CustomUA/1.0\n"
        "https://example.com/nbc.m3u8\n"
    )
    chans = parse_m3u(raw_m3u, lang="eng")
    out = build_m3u(chans, primary_lang="eng")
    assert out.count("http-user-agent") == 1
    assert out.count("http-referrer") == 1
    assert "#EXTVLCOPT" not in out


def test_quality_score_word_boundaries_no_false_positives():
    # Baghdad, Newsdesk, USD, query parameters must not be misdetected as hd/sd/4k
    ch_baghdad = {"name": "Baghdad TV", "url": "https://example.com/stream.m3u8"}
    assert get_channel_quality_score(ch_baghdad) == 540

    ch_newsdesk = {"name": "Newsdesk Live", "url": "https://example.com/stream.m3u8"}
    assert get_channel_quality_score(ch_newsdesk) == 540

    ch_usd = {"name": "USD Market", "url": "https://example.com/stream.m3u8"}
    assert get_channel_quality_score(ch_usd) == 540

    ch_query = {"name": "Clean Stream", "url": "https://example.com/stream.m3u8?token=4k&quality=uhd"}
    assert get_channel_quality_score(ch_query) == 540

    # Legitimate quality tokens
    assert get_channel_quality_score({"name": "Travel 4K", "url": "https://example.com/stream.m3u8"}) == 2160
    assert get_channel_quality_score({"name": "Sports FHD", "url": "https://example.com/stream.m3u8"}) == 1080
    assert get_channel_quality_score({"name": "Channel HD", "url": "https://example.com/stream.m3u8"}) == 720
    assert get_channel_quality_score({"name": "Old TV SD", "url": "https://example.com/stream.m3u8"}) == 480


def test_workshop_tv_not_blocked_by_shop_filter():
    filters = {
        "enable_antitrash": True,
        "antitrash_keywords": ["shop", "casino"],
        "blacklist_keywords": [],
        "whitelist_keywords": [],
        "custom_regex": "",
    }
    # Workshop TV should NOT be blocked by shop
    passed, reason = passes_content_filters({"name": "Workshop TV"}, filters)
    assert passed is True, f"Workshop TV was unexpectedly blocked: {reason}"

    # Genuine shop channel must be blocked
    passed_shop, reason_shop = passes_content_filters({"name": "Home Shop TV"}, filters)
    assert passed_shop is False
    assert "shop" in reason_shop.lower()

    # Casino channel blocked
    passed_casino, _ = passes_content_filters({"name": "Casino 24"}, filters)
    assert passed_casino is False


def test_custom_regex_warning_on_invalid_pattern():
    filters = {
        "enable_antitrash": False,
        "custom_regex": "[invalid(pattern",
    }
    with pytest.warns(UserWarning, match="Invalid custom_regex pattern"):
        passed, _ = passes_content_filters({"name": "Test TV"}, filters)
    assert passed is True

    # compile_custom_regex directly warns and returns None
    with pytest.warns(UserWarning, match="Invalid custom_regex pattern"):
        compiled = compile_custom_regex("[invalid(pattern")
        assert compiled is None


def test_cli_flags_precedence_and_lang():
    # 1. --profile cinema --min-res all -> min_resolution == 'all'
    args_cinema = argparse.Namespace(
        profile="cinema",
        min_res="all",
        lang=None,
        no_check=False,
        no_antitrash=False,
        summary_json=None,
    )
    resolved_cinema = resolve_config(DEFAULT_CONFIG, args_cinema)
    assert resolved_cinema["profile"] == "cinema"
    assert resolved_cinema["min_resolution"] == "all"

    # 2. --lang ukr -> primary_language == 'ukr'
    args_ukr = argparse.Namespace(
        profile=None,
        min_res=None,
        lang="ukr",
        no_check=False,
        no_antitrash=False,
        summary_json=None,
    )
    resolved_ukr = resolve_config(DEFAULT_CONFIG, args_ukr)
    assert resolved_ukr["languages"] == ["ukr"]
    assert resolved_ukr["primary_language"] == "ukr"

    # 3. --lang eng,eng -> deduplicated, no duplicate channels in output
    args_dedup = argparse.Namespace(
        profile=None,
        min_res=None,
        lang="eng,eng",
        no_check=False,
        no_antitrash=False,
        summary_json=None,
    )
    resolved_dedup = resolve_config(DEFAULT_CONFIG, args_dedup)
    assert resolved_dedup["languages"] == ["eng"]
    assert resolved_dedup["primary_language"] == "eng"


def test_lang_order_deduplication_no_duplicate_channels():
    channels = [
        {"name": "BBC News", "lang": "eng", "url": "https://example.com/bbc.m3u8", "headers": {}},
    ]
    primary_lang = "eng"
    languages = ["eng"]
    lang_order = list(
        dict.fromkeys(
            ["uhd", primary_lang] + [code for code in languages if code != primary_lang] + ["custom", "local"]
        )
    )
    final_channels = []
    for lang in lang_order:
        lang_chans = [c for c in channels if c.get("lang") == lang]
        final_channels.extend(lang_chans)
    assert len(final_channels) == 1


def test_uhd_4k_channels_not_mutated_and_profile_filtering():
    orig_snapshot = copy.deepcopy(UHD_4K_CHANNELS)

    # 1. Profile filtering: kids & news disable curated 4K channels
    args_kids = argparse.Namespace(
        profile="kids",
        min_res=None,
        lang=None,
        no_check=False,
        no_antitrash=False,
        summary_json=None,
    )
    resolved_kids = resolve_config(DEFAULT_CONFIG, args_kids)
    assert resolved_kids["include_curated_4k"] is False

    args_news = argparse.Namespace(
        profile="news",
        min_res=None,
        lang=None,
        no_check=False,
        no_antitrash=False,
        summary_json=None,
    )
    resolved_news = resolve_config(DEFAULT_CONFIG, args_news)
    assert resolved_news["include_curated_4k"] is False

    args_cinema = argparse.Namespace(
        profile="cinema",
        min_res=None,
        lang=None,
        no_check=False,
        no_antitrash=False,
        summary_json=None,
    )
    resolved_cinema = resolve_config(DEFAULT_CONFIG, args_cinema)
    assert resolved_cinema["include_curated_4k"] is True

    # 2. Mutating copies or formatting does not mutate global list
    ch_copies = [copy.deepcopy(c) for c in UHD_4K_CHANNELS]
    for ch in ch_copies:
        ch["latency"] = 15.0
        ch["detected_height"] = 2160
        ch["bandwidth"] = 5000000

    build_m3u(ch_copies)

    # Verify global UHD_4K_CHANNELS snapshot is strictly unchanged
    assert orig_snapshot == UHD_4K_CHANNELS


def test_cli_flags_deep_check_and_proxy_priority(monkeypatch):
    assert DEFAULT_CONFIG["deep_check"] is False
    assert DEFAULT_CONFIG["proxy"] == ""

    # 1. parse_args supports --deep-check and --proxy
    monkeypatch.setattr(
        sys,
        "argv",
        ["iptv_updater.py", "--deep-check", "--proxy", "http://cli-proxy:8080"],
    )
    parsed = parse_args()
    assert parsed.deep_check is True
    assert parsed.proxy == "http://cli-proxy:8080"

    # 2. Config proxy used when env and CLI are unset
    monkeypatch.delenv("IPTV_PROXY", raising=False)
    base_cfg = copy.deepcopy(DEFAULT_CONFIG)
    base_cfg["proxy"] = "http://cfg-proxy:3128"
    args_empty = argparse.Namespace(profile=None, deep_check=False, proxy=None)
    resolved_cfg = resolve_config(base_cfg, args_empty)
    assert resolved_cfg["deep_check"] is False
    assert resolved_cfg["proxy"] == "http://cfg-proxy:3128"

    # 3. IPTV_PROXY overrides config.json
    monkeypatch.setenv("IPTV_PROXY", "socks5://127.0.0.1:1080")
    resolved_env = resolve_config(base_cfg, args_empty)
    assert resolved_env["proxy"] == "socks5://127.0.0.1:1080"

    # 4. CLI --proxy overrides IPTV_PROXY and config.json; --deep-check enables deep_check
    resolved_cli = resolve_config(base_cfg, parsed)
    assert resolved_cli["deep_check"] is True
    assert resolved_cli["proxy"] == "http://cli-proxy:8080"


def test_check_all_streams_passes_deep_check_and_http_proxy(monkeypatch, capsys):
    channels = [{"name": "Live 1", "url": "https://stream.example/master.m3u8"}]
    captured_calls = []

    async def mock_check_single_stream(session, ch, semaphore=None, timeout_sec=4.0, **kwargs):
        captured_calls.append({"ch": ch, "timeout_sec": timeout_sec, **kwargs})
        return CheckResult(alive=True, latency_ms=12.0, reason="ok")

    import iptv_updater

    monkeypatch.setattr(iptv_updater, "check_single_stream", mock_check_single_stream)

    cfg = {
        "max_concurrent_checks": 2,
        "stream_check_timeout_seconds": 2.0,
        "deep_check": True,
        "proxy": "http://proxyuser:supersecret@proxy.example:8080",
    }
    alive = asyncio.run(check_all_streams(channels, cfg))
    assert len(alive) == 1
    assert len(captured_calls) == 1
    assert captured_calls[0]["deep_check"] is True
    assert captured_calls[0]["proxy"] == "http://proxyuser:supersecret@proxy.example:8080"

    out = capsys.readouterr().out
    assert "deep-check" in out
    assert "supersecret" not in out
    assert mask_proxy_url(cfg["proxy"]) == "http://proxyuser:***@proxy.example:8080"


def test_check_all_streams_socks_proxy_uses_proxy_connector(monkeypatch):
    import types

    import iptv_updater

    created_connectors = []

    class FakeProxyConnector(aiohttp.TCPConnector):
        @classmethod
        def from_url(cls, url, **kwargs):
            created_connectors.append((url, kwargs))
            return cls(**kwargs)

    fake_socks_mod = types.ModuleType("aiohttp_socks")
    fake_socks_mod.ProxyConnector = FakeProxyConnector
    monkeypatch.setitem(sys.modules, "aiohttp_socks", fake_socks_mod)

    captured_calls = []

    async def mock_check_single_stream(session, ch, semaphore=None, timeout_sec=4.0, **kwargs):
        captured_calls.append(kwargs)
        return CheckResult(alive=True, latency_ms=5.0, reason="ok")

    monkeypatch.setattr(iptv_updater, "check_single_stream", mock_check_single_stream)

    monkeypatch.setenv("IPTV_PROXY", "socks5://127.0.0.1:1080")
    cfg = {"max_concurrent_checks": 2, "stream_check_timeout_seconds": 1.0}
    alive = asyncio.run(check_all_streams([{"name": "Ch1", "url": "https://stream.example/1.ts"}], cfg))

    assert len(alive) == 1
    assert len(created_connectors) == 1
    assert created_connectors[0][0] == "socks5://127.0.0.1:1080"
    assert "proxy" not in captured_calls[0]

    # Unsupported proxy scheme fails cleanly
    with pytest.raises(ValueError, match="Unsupported proxy URL scheme"):
        asyncio.run(check_all_streams([], {"proxy": "ftp://proxy.example:21"}))
