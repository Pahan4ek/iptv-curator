import contextlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def extract_gist_id(raw_id: str | None) -> str:
    """
    Extracts Gist ID from a raw ID or full Gist URL.
    Examples:
        'abc123def' -> 'abc123def'
        'https://gist.github.com/user/abc123def' -> 'abc123def'
        'https://gist.github.com/abc123def' -> 'abc123def'
        'https://gist.githubusercontent.com/user/abc123def/raw/playlist.m3u' -> 'abc123def'
    """
    if not raw_id:
        return ""
    cleaned = raw_id.strip()
    if not cleaned:
        return ""

    if "://" in cleaned or "/" in cleaned:
        try:
            parsed = urllib.parse.urlparse(cleaned)
            path = parsed.path.strip("/")
            parts = [p for p in path.split("/") if p]
            if "raw" in parts:
                idx = parts.index("raw")
                cleaned = parts[idx - 1] if idx > 0 else parts[-1]
            elif parts:
                cleaned = parts[-1]
            else:
                cleaned = ""
        except Exception:
            cleaned = cleaned.rstrip("/").split("/")[-1]

    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]

    return cleaned


def is_valid_gist_id(gist_id: str) -> bool:
    """
    Validates whether the Gist ID looks like a valid GitHub Gist ID (alphanumeric, dashes, underscores).
    """
    if not gist_id:
        return False
    return bool(re.fullmatch(r"[0-9a-zA-Z_-]+", gist_id))


class GistSync:
    def __init__(self, token: str, gist_id: str | None = None):
        self.token = token.strip() if token else ""
        self.raw_gist_id = gist_id.strip() if gist_id else ""
        self.gist_id = extract_gist_id(self.raw_gist_id)

    def is_configured(self) -> bool:
        return bool(self.token)

    def sync_playlist(
        self,
        m3u_content: str,
        filename: str = "playlist.m3u",
        retry_delays: tuple[float, ...] = (1.0, 2.0),
    ) -> tuple[bool, str, str | None]:
        """
        Uploads or updates the M3U content in a GitHub Gist.
        Returns (success: bool, direct_raw_url_or_error: str, new_gist_id: Optional[str])
        """
        if not self.is_configured():
            return False, "GitHub token is not configured.", None

        if not m3u_content or not m3u_content.strip():
            return False, "M3U content is empty, aborting Gist sync to protect existing playlist.", None

        if self.raw_gist_id and not is_valid_gist_id(self.gist_id):
            return False, f"Invalid Gist ID: {self.raw_gist_id}", None

        filename = os.path.basename(filename).strip() or "playlist.m3u"
        quoted_filename = urllib.parse.quote(filename)

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "IPTV-Updater-Script",
        }

        payload: dict[str, Any] = {
            "description": "Custom IPTV Playlist (Auto-updated)",
            "files": {filename: {"content": m3u_content}},
        }

        if self.gist_id:
            # Update existing Gist
            url = f"https://api.github.com/gists/{self.gist_id}"
            req_method = "PATCH"
        else:
            # Create a secret (unlisted) Gist
            payload["public"] = False
            url = "https://api.github.com/gists"
            req_method = "POST"

        req_data = json.dumps(payload).encode("utf-8")
        max_attempts = 1 + len(retry_delays)

        last_error = "Unknown error"
        for attempt in range(max_attempts):
            try:
                req = urllib.request.Request(
                    url,
                    data=req_data,
                    headers=headers,
                    method=req_method,
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    new_id = data.get("id")
                    owner_info = data.get("owner")
                    owner = owner_info.get("login", "") if isinstance(owner_info, dict) else ""

                    # Permanent raw URL without commit SHA
                    if owner and new_id:
                        permanent_raw_url = f"https://gist.githubusercontent.com/{owner}/{new_id}/raw/{quoted_filename}"
                    else:
                        files = data.get("files")
                        file_info = files.get(filename) if isinstance(files, dict) else None
                        permanent_raw_url = file_info.get("raw_url", "") if isinstance(file_info, dict) else ""

                    return True, permanent_raw_url, new_id

            except urllib.error.HTTPError as e:
                err_body = ""
                with contextlib.suppress(Exception):
                    err_body = e.read().decode("utf-8", errors="ignore")

                msg = ""
                with contextlib.suppress(Exception):
                    parsed_json = json.loads(err_body)
                    if isinstance(parsed_json, dict) and "message" in parsed_json:
                        msg = str(parsed_json["message"])

                if msg:
                    err_str = f"HTTP Error {e.code}: {msg}"
                elif err_body.strip():
                    err_str = f"HTTP Error {e.code}: {err_body.strip()}"
                else:
                    err_str = f"HTTP Error {e.code}"

                last_error = err_str

                # Retry on 5xx errors
                if e.code >= 500 and attempt < len(retry_delays):
                    delay = retry_delays[attempt]
                    if delay > 0:
                        time.sleep(delay)
                    continue

                return False, last_error, None

            except urllib.error.URLError as e:
                last_error = f"Network Error: {e.reason}"
                if attempt < len(retry_delays):
                    delay = retry_delays[attempt]
                    if delay > 0:
                        time.sleep(delay)
                    continue
                return False, last_error, None

            except TimeoutError as e:
                last_error = f"Timeout Error: {e}"
                if attempt < len(retry_delays):
                    delay = retry_delays[attempt]
                    if delay > 0:
                        time.sleep(delay)
                    continue
                return False, last_error, None

            except Exception as e:
                return False, f"Error: {str(e)}", None

        return False, last_error, None
