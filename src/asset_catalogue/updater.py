from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from asset_catalogue.version import __version__

GITHUB_REPO = "srgreiick/Asset-Catalog"
RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
REQUEST_TIMEOUT_SECONDS = 10


class UpdateCheckError(Exception):
    """The check itself couldn't complete -- no network, GitHub API issue,
    rate limiting, or no releases published yet. Distinct from "checked
    successfully and you're already up to date" (see check_for_update),
    since a caller doing a manual, user-requested check wants to tell
    those two outcomes apart; a silent background check can just treat
    this the same as "nothing to report" and stay quiet either way.
    """


@dataclass
class UpdateInfo:
    current_version: str
    latest_version: str
    release_url: str
    release_notes: str


def _parse_version(version_string: str) -> tuple[int, ...]:
    """Parses a version string like "1.2.3" or "v1.2.3" into a comparable
    tuple. Deliberately simple -- no pre-release/build-metadata handling --
    since this project's own versions are plain MAJOR.MINOR.PATCH; a small
    hand-rolled parser avoids a new dependency (the `packaging` library)
    just for this one comparison.
    """
    cleaned = version_string.strip().lstrip("vV")
    parts = []
    for part in cleaned.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


def fetch_latest_release() -> dict:
    """Raises UpdateCheckError on any failure -- network, timeout, a
    malformed/unexpected response. GitHub's public releases API needs no
    auth for a public repo and has a generous rate limit for this kind of
    occasional, low-volume check.
    """
    request = urllib.request.Request(
        RELEASES_API_URL, headers={"Accept": "application/vnd.github+json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise UpdateCheckError(str(exc)) from exc


def check_for_update() -> UpdateInfo | None:
    """Returns UpdateInfo if GitHub's latest release is newer than this
    running build, or None if already up to date. Raises UpdateCheckError
    if the check itself couldn't complete -- callers doing a silent
    background check should catch that and simply do nothing; a manual,
    user-requested check should surface it instead of reporting a false
    "up to date".
    """
    data = fetch_latest_release()
    latest_tag = data.get("tag_name")
    release_url = data.get("html_url")
    if not latest_tag or not release_url:
        raise UpdateCheckError("Unexpected response from GitHub (missing tag_name/html_url)")

    if not is_newer(latest_tag, __version__):
        return None

    return UpdateInfo(
        current_version=__version__,
        latest_version=latest_tag.lstrip("vV"),
        release_url=release_url,
        release_notes=(data.get("body") or "").strip(),
    )
