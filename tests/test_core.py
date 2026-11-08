import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checker import parse_hls_stream_info  # noqa: E402
from iptv_updater import (  # noqa: E402
    DEFAULT_CONFIG,
    build_m3u,
    deduplicate_channels,
    get_channel_quality_score,
    load_config,
    normalize_channel_name,
    parse_m3u,
    passes_content_filters,
)

SAMPLE_M3U = """#EXTM3U
#EXTINF:-1 tvg-id="CNN.us" tvg-logo="https://x/cnn.png" group-title="News",CNN (720p)
#EXTVLCOPT:http-user-agent=TestAgent/1.0
https://example.com/cnn.m3u8
#EXTINF:-1 group-title="Movies;Series" http-referrer="https://ref.example",Movie Box HD
https://example.com/movies.m3u8

# random comment
#EXTINF:-1,No Group Channel
https://example.com/nogroup.ts
"""


def test_parse_m3u_basic_fields():
    chans = parse_m3u(SAMPLE_M3U, lang="eng")
    assert len(chans) == 3

    cnn = chans[0]
    assert cnn["name"] == "CNN (720p)"
    assert cnn["tvg_id"] == "CNN.us"
    assert cnn["raw_group"] == "News"
    assert cnn["headers"]["User-Agent"] == "TestAgent/1.0"
    assert cnn["group"] == "[EN] News"

    movies = chans[1]
    assert movies["headers"]["Referer"] == "https://ref.example"
    assert movies["group"] == "[EN] Movies HD"

    assert chans[2]["raw_group"] == "General"


def test_parse_hls_master_manifest():
    manifest = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\nlow.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080\nhigh.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720\nmid.m3u8\n"
    )
    info = parse_hls_stream_info(manifest)
    assert info["max_height"] == 1080
    assert info["max_width"] == 1920
    assert info["bandwidth"] == 5000000
    assert info["resolution_str"] == "1080p"


def test_parse_hls_4k_label():
    info = parse_hls_stream_info("#EXT-X-STREAM-INF:BANDWIDTH=1,RESOLUTION=3840x2160\n")
    assert info["resolution_str"] == "4K"


def test_quality_score_prefers_detected_height():
    assert get_channel_quality_score({"name": "Foo 4K", "detected_height": 576}) == 576
    assert get_channel_quality_score({"name": "Foo 1080p"}) == 1080
    assert get_channel_quality_score({"name": "Foo"}) == 540


def test_normalize_channel_name_strips_quality_tags():
    assert normalize_channel_name("CNN HD (720p)") == normalize_channel_name("CNN")
    assert normalize_channel_name("BBC One [Geo-blocked]") == "bbc one"


def test_dedup_keeps_best_stream():
    chans = [
        {"name": "CNN (480p)", "lang": "eng", "detected_height": 480, "bandwidth": 0},
        {"name": "CNN HD", "lang": "eng", "detected_height": 1080, "bandwidth": 100},
        {"name": "CNN", "lang": "eng", "detected_height": 1080, "bandwidth": 900},
        {"name": "CNN", "lang": "rus", "detected_height": 720, "bandwidth": 0},
    ]
    out = deduplicate_channels(chans)
    assert len(out) == 2
    eng = next(c for c in out if c["lang"] == "eng")
    assert eng["bandwidth"] == 900


def test_content_filters():
    filters = {
        "enable_antitrash": True,
        "antitrash_keywords": ["shop"],
        "blacklist_keywords": ["casino"],
        "whitelist_keywords": [],
        "custom_regex": "",
    }
    assert passes_content_filters({"name": "Home Shop TV"}, filters)[0] is False
    assert passes_content_filters({"name": "Casino 24"}, filters)[0] is False
    assert passes_content_filters({"name": "Discovery"}, filters)[0] is True

    filters["whitelist_keywords"] = ["news"]
    assert passes_content_filters({"name": "Discovery"}, filters)[0] is False
    assert passes_content_filters({"name": "Euronews"}, filters)[0] is True

    filters["custom_regex"] = "[unclosed"  # broken regex must not crash
    assert passes_content_filters({"name": "Euronews"}, filters)[0] is True


def test_build_m3u_roundtrip_and_order():
    chans = parse_m3u(SAMPLE_M3U, lang="eng")
    uhd = {
        "name": "NASA TV UHD", "lang": "uhd", "url": "https://example.com/nasa.m3u8",
        "tvg_id": "", "tvg_logo": "", "raw_group": "Documentary", "headers": {},
    }
    text = build_m3u(chans + [uhd])
    assert text.startswith("#EXTM3U")
    reparsed = parse_m3u(text, lang="eng")
    assert len(reparsed) == 4
    assert reparsed[0]["name"] == "NASA TV UHD"  # UHD group goes first
    assert "http-user-agent" not in text or "TestAgent/1.0" in text


def test_load_config_does_not_mutate_defaults(tmp_path, monkeypatch):
    import iptv_updater

    cfg_file = tmp_path / "config.json"
    cfg_file.write_text('{"content_filters": {"enable_antitrash": false}}', encoding="utf-8")
    monkeypatch.setattr(iptv_updater, "CONFIG_FILE", str(cfg_file))

    cfg = load_config()
    assert cfg["content_filters"]["enable_antitrash"] is False
    assert DEFAULT_CONFIG["content_filters"]["enable_antitrash"] is True
