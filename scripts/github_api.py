"""Minimal GitHub REST API client (stdlib only) for listing and downloading workflow artifacts."""

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
RETRIES = 3
TIMEOUT_S = 60


class GitHubApiError(Exception):
    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Response:
    def __init__(self, status: int, headers: dict, body: bytes) -> None:
        self.status = status
        self.headers = {key.lower(): value for key, value in headers.items()}
        self.body = body


def urllib_transport(url: str, headers: dict, follow_redirects: bool) -> Response:
    """Performs a GET request; returns non-2xx responses (incl. redirects when not followed) instead of raising."""
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener() if follow_redirects else urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=TIMEOUT_S) as response:  # nosec B310 - https API/storage URLs
            return Response(response.status, dict(response.headers), _read_capped(response))
    except urllib.error.HTTPError as error:
        body = error.read(64 * 1024) if error.fp else b""
        return Response(error.code, dict(error.headers or {}), body)


def _read_capped(response) -> bytes:
    data = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise GitHubApiError(f"Response larger than {MAX_DOWNLOAD_BYTES} bytes")
    return data


class GitHubApi:
    def __init__(
        self,
        token: Optional[str],
        api_url: str = "https://api.github.com",
        transport: Callable[[str, dict, bool], Response] = urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
        user_agent: str = "notebooks-dashboard",
    ) -> None:
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.transport = transport
        self.sleep = sleep
        self.user_agent = user_agent

    def _headers(self, authorized: bool = True) -> dict:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": self.user_agent}
        if authorized and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(self, url: str, headers: dict, follow_redirects: bool) -> Response:
        last_error = None
        for attempt in range(RETRIES):
            try:
                response = self.transport(url, headers, follow_redirects)
            except (OSError, http.client.HTTPException) as error:
                last_error = GitHubApiError(f"GET {url} failed: {error}")
            else:
                if response.status < 500 and response.status != 429:
                    return response
                last_error = GitHubApiError(f"GET {url} returned HTTP {response.status}", response.status)
            if attempt < RETRIES - 1:
                self.sleep(2**attempt)
        raise last_error

    def _url(self, path_or_url: str, params: Optional[dict] = None) -> str:
        url = path_or_url if path_or_url.startswith("http") else f"{self.api_url}/{path_or_url.lstrip('/')}"
        return url + ("?" + urllib.parse.urlencode(params) if params else "")

    def get_json(self, path_or_url: str, params: Optional[dict] = None) -> dict:
        url = self._url(path_or_url, params)
        response = self._request(url, self._headers(), follow_redirects=True)
        if response.status != 200:
            raise GitHubApiError(f"GET {url} returned HTTP {response.status}: {response.body[:300].decode('utf-8', 'replace')}", response.status)
        try:
            return json.loads(response.body.decode("utf-8"))
        except ValueError as error:
            raise GitHubApiError(f"GET {url} returned invalid JSON: {error}") from error

    def list_artifacts(self, repository: str, name: str) -> list[dict]:
        artifacts: list[dict] = []
        page = 1
        while True:
            data = self.get_json(f"repos/{repository}/actions/artifacts", {"name": name, "per_page": 100, "page": page})
            batch = data.get("artifacts") or []
            artifacts.extend(batch)
            if len(batch) < 100 or len(artifacts) >= int(data.get("total_count") or 0):
                return artifacts
            page += 1

    def download_artifact_zip(self, repository: str, artifact_id: int) -> bytes:
        """The API answers with a redirect to blob storage, which must be requested without the Authorization header."""
        url = self._url(f"repos/{repository}/actions/artifacts/{artifact_id}/zip")
        response = self._request(url, self._headers(), follow_redirects=False)
        if response.status in (301, 302, 303, 307, 308):
            location = response.headers.get("location")
            if not location:
                raise GitHubApiError(f"GET {url} returned a redirect without location", response.status)
            response = self._request(location, {"User-Agent": self.user_agent}, follow_redirects=True)
        if response.status != 200:
            raise GitHubApiError(f"Downloading artifact {artifact_id} returned HTTP {response.status}", response.status)
        return response.body
