from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from asset_catalogue import updater


@pytest.mark.parametrize(
    "latest, current, expected",
    [
        ("1.2.3", "1.2.2", True),
        ("v1.2.3", "1.2.3", False),
        ("1.2.3", "1.2.3", False),
        ("2.0.0", "1.9.9", True),
        ("1.10.0", "1.9.0", True),  # numeric, not lexicographic, comparison
        ("1.0.0", "1.0.1", False),
    ],
)
def test_is_newer(latest: str, current: str, expected: bool) -> None:
    assert updater.is_newer(latest, current) is expected


def _fake_response(payload: dict):
    mock = MagicMock()
    mock.read.return_value = json.dumps(payload).encode("utf-8")
    mock.__enter__.return_value = mock
    mock.__exit__.return_value = False
    return mock


def test_check_for_update_returns_none_when_already_current() -> None:
    from asset_catalogue.version import __version__

    payload = {"tag_name": f"v{__version__}", "html_url": "https://example.com"}
    with patch("urllib.request.urlopen", return_value=_fake_response(payload)):
        assert updater.check_for_update() is None


def test_check_for_update_returns_info_when_newer_release_exists() -> None:
    from asset_catalogue.version import __version__

    newer = ".".join(str(int(part) + 1) if i == 0 else part for i, part in enumerate(__version__.split(".")))
    payload = {
        "tag_name": f"v{newer}",
        "html_url": "https://github.com/AnthraxDelta7/Asset-Catalog/releases/tag/v" + newer,
        "body": "Some release notes",
    }
    with patch("urllib.request.urlopen", return_value=_fake_response(payload)):
        info = updater.check_for_update()
    assert info is not None
    assert info.latest_version == newer
    assert info.current_version == __version__
    assert info.release_notes == "Some release notes"


def test_check_for_update_raises_on_network_failure() -> None:
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no network")):
        with pytest.raises(updater.UpdateCheckError):
            updater.check_for_update()


def test_check_for_update_raises_on_malformed_response() -> None:
    with patch("urllib.request.urlopen", return_value=_fake_response({"unexpected": "shape"})):
        with pytest.raises(updater.UpdateCheckError):
            updater.check_for_update()
