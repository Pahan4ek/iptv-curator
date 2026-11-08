import json
import urllib.error
import urllib.request


class GistSync:
    def __init__(self, token: str, gist_id: str | None = None):
        self.token = token.strip() if token else ""
        self.gist_id = gist_id.strip() if gist_id else ""

    def is_configured(self) -> bool:
        return bool(self.token)

    def sync_playlist(self, m3u_content: str, filename: str = "playlist.m3u") -> tuple[bool, str, str | None]:
        """
        Uploads or updates the M3U content in a GitHub Gist.
        Returns (success: bool, direct_raw_url_or_error: str, new_gist_id: Optional[str])
        """
        if not self.is_configured():
            return False, "GitHub token is not configured.", None

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "IPTV-Updater-Script"
        }

        payload = {
            "description": "Custom IPTV Playlist (Auto-updated)",
            "files": {
                filename: {
                    "content": m3u_content
                }
            }
        }

        try:
            if self.gist_id:
                # Update existing Gist
                url = f"https://api.github.com/gists/{self.gist_id}"
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="PATCH"
                )
            else:
                # Create a secret (unlisted) Gist
                payload["public"] = False
                url = "https://api.github.com/gists"
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="POST"
                )

            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                new_id = data.get("id")
                owner = data.get("owner", {}).get("login", "")

                # Permanent raw URL without commit SHA
                if owner and new_id:
                    permanent_raw_url = f"https://gist.githubusercontent.com/{owner}/{new_id}/raw/{filename}"
                else:
                    files = data.get("files", {})
                    file_info = files.get(filename, {})
                    permanent_raw_url = file_info.get("raw_url", "")

                return True, permanent_raw_url, new_id

        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="ignore")
            return False, f"HTTP Error {e.code}: {err_msg}", None
        except Exception as e:
            return False, f"Error: {str(e)}", None
