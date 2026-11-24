import argparse
import io
import json
import os
import sys
import urllib.error
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gist_sync import GistSync, extract_gist_id, is_valid_gist_id  # noqa: E402
from iptv_updater import (  # noqa: E402
    DEFAULT_CONFIG,
    get_gist_id,
    get_github_token,
    main,
    resolve_config,
    sync_to_gist,
)


class MockHTTPResponse:
    def __init__(self, data: dict, status: int = 200):
        self.data = json.dumps(data).encode("utf-8")
        self.status = status

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


def test_gist_id_normalization_and_validation():
    # 1. Extraction from various URL formats
    assert extract_gist_id("https://gist.github.com/user/abc123def") == "abc123def"
    assert extract_gist_id("https://gist.github.com/user/abc123def/") == "abc123def"
    assert extract_gist_id("https://gist.github.com/abc123def") == "abc123def"
    assert extract_gist_id("https://gist.github.com/user/abc123def.git") == "abc123def"
    assert extract_gist_id("https://gist.githubusercontent.com/user/abc123def/raw/playlist.m3u") == "abc123def"
    assert extract_gist_id("abc123def") == "abc123def"
    assert extract_gist_id("  abc123def  ") == "abc123def"
    assert extract_gist_id("") == ""
    assert extract_gist_id(None) == ""

    # 2. Validation
    assert is_valid_gist_id("abc123def") is True
    assert is_valid_gist_id("0123456789abcdef0123456789abcdef") is True
    assert is_valid_gist_id("invalid-id!") is False
    assert is_valid_gist_id("") is False

    # 3. GistSync init normalizes URL to ID
    sync = GistSync(token="dummy_token", gist_id="https://gist.github.com/username/gist789xyz")
    assert sync.gist_id == "gist789xyz"

    # 4. Invalid gist ID stops sync before network request
    sync_invalid = GistSync(token="dummy_token", gist_id="invalid!gist!id")
    success, err, new_id = sync_invalid.sync_playlist("#EXTM3U\n#EXTINF:-1,Chan\nhttp://stream")
    assert success is False
    assert "Invalid Gist ID" in err
    assert new_id is None


def test_empty_m3u_content_blocks_request(monkeypatch):
    mock_urlopen = MagicMock()
    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    sync = GistSync(token="dummy_token", gist_id="abc123def")

    # Empty string
    success, err, new_id = sync.sync_playlist("")
    assert success is False
    assert "empty" in err.lower()
    assert new_id is None

    # Whitespace only
    success, err, new_id = sync.sync_playlist("   \n\t  ")
    assert success is False
    assert "empty" in err.lower()
    assert new_id is None

    # Network was never called
    mock_urlopen.assert_not_called()


def test_owner_null_does_not_crash(monkeypatch):
    # GitHub API can return "owner": null for certain gists (e.g. anonymous/ghost owner)
    payload_response = {
        "id": "gist_null_owner_123",
        "owner": None,
        "files": {
            "playlist.m3u": {
                "raw_url": "https://gist.githubusercontent.com/raw/gist_null_owner_123/playlist.m3u",
            }
        },
    }

    def mock_urlopen(req, timeout=15):
        return MockHTTPResponse(payload_response)

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    sync = GistSync(token="dummy_token", gist_id="gist_null_owner_123")
    success, raw_url, new_id = sync.sync_playlist("#EXTM3U\n#EXTINF:-1,Channel\nhttp://example.com/live.m3u8")

    assert success is True
    assert new_id == "gist_null_owner_123"
    assert raw_url == "https://gist.githubusercontent.com/raw/gist_null_owner_123/playlist.m3u"


def test_filename_normalization_and_url_quoting(monkeypatch):
    captured_req = None

    def mock_urlopen(req, timeout=15):
        nonlocal captured_req
        captured_req = req
        return MockHTTPResponse(
            {
                "id": "gist_abc",
                "owner": {"login": "testowner"},
                "files": {
                    "my special playlist.m3u": {
                        "raw_url": "https://gist.githubusercontent.com/raw/gist_abc/my%20special%20playlist.m3u"
                    }
                },
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    sync = GistSync(token="dummy_token", gist_id="gist_abc")
    success, raw_url, new_id = sync.sync_playlist(
        "#EXTM3U\n#EXTINF:-1,Test\nhttp://stream",
        filename="/tmp/nested/path/my special playlist.m3u",
    )

    assert success is True
    assert new_id == "gist_abc"
    assert raw_url == "https://gist.githubusercontent.com/testowner/gist_abc/raw/my%20special%20playlist.m3u"

    # Verify request payload has normalized filename basename as file key
    data = json.loads(captured_req.data.decode("utf-8"))
    assert "my special playlist.m3u" in data["files"]
    assert "/tmp/nested/path" not in captured_req.data.decode("utf-8")


def test_http_error_readable_message_extraction(monkeypatch):
    error_body = json.dumps({"message": "Not Found", "documentation_url": "https://docs.github.com"}).encode("utf-8")

    def mock_urlopen(req, timeout=15):
        raise urllib.error.HTTPError(
            url="https://api.github.com/gists/nonexistent",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=io.BytesIO(error_body),
        )

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    sync = GistSync(token="dummy_token", gist_id="nonexistent")
    success, err, new_id = sync.sync_playlist(
        "#EXTM3U\n#EXTINF:-1,Chan\nhttp://stream",
        retry_delays=(0.0, 0.0),
    )

    assert success is False
    assert err == "HTTP Error 404: Not Found"
    assert new_id is None


def test_no_retry_on_4xx_client_error(monkeypatch):
    call_count = 0
    error_body = json.dumps({"message": "Bad credentials"}).encode("utf-8")

    def mock_urlopen(req, timeout=15):
        nonlocal call_count
        call_count += 1
        raise urllib.error.HTTPError(
            url="https://api.github.com/gists",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=io.BytesIO(error_body),
        )

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    sync = GistSync(token="bad_token")
    success, err, new_id = sync.sync_playlist(
        "#EXTM3U\n#EXTINF:-1,Chan\nhttp://stream",
        retry_delays=(0.0, 0.0),
    )

    assert success is False
    assert err == "HTTP Error 401: Bad credentials"
    assert call_count == 1  # 4xx client errors should not be retried


def test_retry_on_502_success_second_attempt(monkeypatch):
    call_count = 0
    error_body = b"Bad Gateway"

    def mock_urlopen(req, timeout=15):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise urllib.error.HTTPError(
                url="https://api.github.com/gists/gist123",
                code=502,
                msg="Bad Gateway",
                hdrs={},
                fp=io.BytesIO(error_body),
            )
        return MockHTTPResponse(
            {
                "id": "gist123",
                "owner": {"login": "octocat"},
                "files": {
                    "playlist.m3u": {"raw_url": "https://gist.githubusercontent.com/octocat/gist123/raw/playlist.m3u"}
                },
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    sync = GistSync(token="dummy_token", gist_id="gist123")
    success, raw_url, new_id = sync.sync_playlist(
        "#EXTM3U\n#EXTINF:-1,Chan\nhttp://stream",
        retry_delays=(0.0, 0.0),
    )

    assert success is True
    assert call_count == 2
    assert new_id == "gist123"
    assert raw_url == "https://gist.githubusercontent.com/octocat/gist123/raw/playlist.m3u"


def test_retry_exhausted_on_500(monkeypatch):
    call_count = 0
    error_body = b"Internal Server Error"

    def mock_urlopen(req, timeout=15):
        nonlocal call_count
        call_count += 1
        raise urllib.error.HTTPError(
            url="https://api.github.com/gists/gist123",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=io.BytesIO(error_body),
        )

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    sync = GistSync(token="dummy_token", gist_id="gist123")
    success, err, new_id = sync.sync_playlist(
        "#EXTM3U\n#EXTINF:-1,Chan\nhttp://stream",
        retry_delays=(0.0, 0.0),
    )

    assert success is False
    assert "HTTP Error 500" in err
    assert call_count == 3  # 1 initial + 2 retries = 3 attempts


def test_retry_on_urlerror_and_timeout(monkeypatch):
    call_count = 0

    def mock_urlopen(req, timeout=15):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise urllib.error.URLError("Connection refused")
        return MockHTTPResponse(
            {
                "id": "gist123",
                "owner": {"login": "octocat"},
                "files": {"playlist.m3u": {"raw_url": "https://raw.example/playlist.m3u"}},
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    sync = GistSync(token="dummy_token", gist_id="gist123")
    success, raw_url, new_id = sync.sync_playlist(
        "#EXTM3U\n#EXTINF:-1,Chan\nhttp://stream",
        retry_delays=(0.0, 0.0),
    )

    assert success is True
    assert call_count == 2
    assert new_id == "gist123"


def test_min_channels_to_sync_threshold_blocks():
    cfg = {"min_channels_to_sync": 10, "github_token": "token123", "gist_id": "gist123"}
    mock_sync = MagicMock()

    # 1. Below threshold (5 < 10) and not force_sync -> blocked
    success, msg, new_id = sync_to_gist(
        m3u_text="#EXTM3U\n",
        channel_count=5,
        cfg=cfg,
        sync=mock_sync,
    )
    assert success is False
    assert "below threshold" in msg.lower()
    assert new_id is None
    mock_sync.sync_playlist.assert_not_called()

    # 2. Below threshold with force_sync -> allowed
    cfg_force = {"min_channels_to_sync": 10, "force_sync": True, "github_token": "token123"}
    mock_sync.sync_playlist.return_value = (True, "https://raw.example/playlist.m3u", "gist123")
    success, msg, new_id = sync_to_gist(
        m3u_text="#EXTM3U\n",
        channel_count=5,
        cfg=cfg_force,
        sync=mock_sync,
    )
    assert success is True
    mock_sync.sync_playlist.assert_called_once()

    # 3. Equal or above threshold (10 >= 10) -> allowed
    mock_sync.reset_mock()
    success, msg, new_id = sync_to_gist(
        m3u_text="#EXTM3U\n",
        channel_count=10,
        cfg=cfg,
        sync=mock_sync,
    )
    assert success is True
    mock_sync.sync_playlist.assert_called_once()


def test_get_github_token_priority(monkeypatch):
    cfg = {"github_token": "config_token"}

    # 1. Both env vars set -> GIST_TOKEN wins
    monkeypatch.setenv("GIST_TOKEN", "gist_token_val")
    monkeypatch.setenv("GITHUB_TOKEN", "github_token_val")
    assert get_github_token(cfg) == "gist_token_val"

    # 2. Only GITHUB_TOKEN set -> GITHUB_TOKEN used
    monkeypatch.delenv("GIST_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "github_token_val")
    assert get_github_token(cfg) == "github_token_val"

    # 3. Neither env var set -> config.json used
    monkeypatch.delenv("GIST_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert get_github_token(cfg) == "config_token"

    # 4. Empty env vars fall through to config
    monkeypatch.setenv("GIST_TOKEN", "   ")
    monkeypatch.setenv("GITHUB_TOKEN", "")
    assert get_github_token(cfg) == "config_token"


def test_get_gist_id(monkeypatch):
    cfg = {"gist_id": "cfg_id"}

    monkeypatch.setenv("GIST_ID", "env_id")
    assert get_gist_id(cfg) == "env_id"

    monkeypatch.delenv("GIST_ID", raising=False)
    assert get_gist_id(cfg) == "cfg_id"


def test_cli_flags_min_channels_and_force_sync():
    args = argparse.Namespace(
        profile=None,
        min_res=None,
        lang=None,
        no_check=False,
        no_antitrash=False,
        summary_json=None,
        min_channels=25,
        force_sync=True,
    )
    resolved = resolve_config(DEFAULT_CONFIG, args)
    assert resolved["min_channels_to_sync"] == 25
    assert resolved["force_sync"] is True


def test_main_exits_1_on_sync_failure_and_threshold(monkeypatch, tmp_path):
    output_playlist = str(tmp_path / "test_out.m3u")

    # Test 1: Channel count below threshold triggers sys.exit(1)
    monkeypatch.setattr(
        "sys.argv",
        ["iptv_updater.py", "--no-check", "--min-channels", "100"],
    )
    monkeypatch.setenv("GIST_TOKEN", "fake_token")
    monkeypatch.setenv("GIST_ID", "fake_id")

    dummy_cfg = dict(DEFAULT_CONFIG)
    dummy_cfg["min_channels_to_sync"] = 100
    dummy_cfg["include_curated_4k"] = False
    dummy_cfg["output_file"] = output_playlist
    monkeypatch.setattr("iptv_updater.load_config", lambda: dummy_cfg)

    # Mock fetch_playlist to return 2 channels
    async def mock_fetch_playlist(*args, **kwargs):
        return [
            {"name": "Ch 1", "url": "http://example.com/1", "lang": "eng"},
            {"name": "Ch 2", "url": "http://example.com/2", "lang": "eng"},
        ]

    monkeypatch.setattr("iptv_updater.fetch_playlist", mock_fetch_playlist)

    with pytest.raises(SystemExit) as exc_info:
        import asyncio

        asyncio.run(main())

    assert exc_info.value.code == 1

    # Test 2: GistSync failure triggers sys.exit(1)
    monkeypatch.setattr(
        "sys.argv",
        ["iptv_updater.py", "--no-check", "--min-channels", "1"],
    )
    dummy_cfg2 = dict(DEFAULT_CONFIG)
    dummy_cfg2["min_channels_to_sync"] = 1
    dummy_cfg2["include_curated_4k"] = False
    dummy_cfg2["output_file"] = output_playlist
    monkeypatch.setattr("iptv_updater.load_config", lambda: dummy_cfg2)

    # Force GistSync.sync_playlist to return failure
    monkeypatch.setattr(
        GistSync,
        "sync_playlist",
        lambda self, *args, **kwargs: (False, "API Rate Limit Exceeded", None),
    )

    with pytest.raises(SystemExit) as exc_info:
        import asyncio

        asyncio.run(main())

    assert exc_info.value.code == 1
