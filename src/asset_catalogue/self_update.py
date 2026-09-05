"""Downloads a new release and hands off to a detached helper script that
waits for this process to exit, swaps the install folder, and relaunches
-- the part update_available_dialog's "Download && Install" button
actually does. Only meaningful for a packaged .exe (see is_frozen): a
dev-mode run has no "own install folder" to replace.

Windows locks a running process's own .exe/.dll files, so this process
can never do the actual file swap itself -- every self-updater works
around that the same way, by handing off to a second process that starts
only after the first one is gone. Real added complexity and risk versus
the previous "just open the release page" design: a failed swap can't
just be retried by re-running this app (it's not running anymore), so
the helper script backs up the old install rather than deleting it
outright, and a downloaded-but-not-yet-applied update never touches the
live install at all if anything goes wrong before the handoff.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

ProgressCallback = Callable[[int, int], None]  # (bytes_so_far, total_bytes)

DOWNLOAD_CHUNK_SIZE = 1024 * 256
REQUEST_TIMEOUT_SECONDS = 30


class SelfUpdateError(Exception):
    """The download, extraction, or handoff itself failed -- distinct from
    UpdateCheckError (checking whether an update exists at all). The
    caller's job on this one is just to show the message and leave the
    current install completely untouched, since every failure mode here
    is caught before the point of no return (launching the relauncher).
    """


def is_frozen() -> bool:
    """Whether this is a packaged .exe, not a dev-mode run from source --
    self-update only makes sense for the former, since a dev checkout has
    no single "install folder" to download a build over.
    """
    return getattr(sys, "frozen", False)


def install_dir() -> Path:
    """The folder holding this running .exe and its bundled files -- what
    gets replaced by an applied update. Only meaningful when is_frozen().
    """
    return Path(sys.executable).resolve().parent


def download_update(url: str, on_progress: ProgressCallback | None = None) -> Path:
    """Downloads the release zip to a temp file, reporting (bytes_so_far,
    total_bytes) as it goes -- this project's builds are 100+MB, so a
    real progress indicator matters, not just an indeterminate spinner.
    total_bytes is 0 if the server doesn't report Content-Length; callers
    should treat that as "show bytes downloaded, not a percentage."
    """
    report = on_progress or (lambda _done, _total: None)
    fd, temp_path = tempfile.mkstemp(suffix=".zip", prefix="AssetCatalogue-update-")
    dest = Path(temp_path)
    try:
        # Open (and, via the `with`, guarantee closing) the destination
        # file BEFORE the network request -- otherwise a request that
        # fails before ever reaching os.fdopen() leaks the raw fd from
        # mkstemp, which on Windows (unlike POSIX) blocks deleting the
        # file at all: unlink() on a still-open handle raises
        # PermissionError instead of silently succeeding. Caught by a
        # real test, not a hypothetical.
        with os.fdopen(fd, "wb") as f:
            request = urllib.request.Request(url, headers={"Accept": "application/octet-stream"})
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                total = int(response.headers.get("Content-Length") or 0)
                downloaded = 0
                while True:
                    chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    report(downloaded, total)
        return dest
    except Exception as exc:  # noqa: BLE001 -- surfaced as SelfUpdateError, original file cleaned up
        dest.unlink(missing_ok=True)
        raise SelfUpdateError(f"Couldn't download the update: {exc}") from exc


def extract_update(zip_path: Path, expected_exe_name: str) -> Path:
    """Extracts the downloaded zip to a fresh temp directory and verifies
    it actually contains expected_exe_name before reporting success --
    a corrupt or unexpectedly-shaped zip should never make it as far as
    the actual install swap. Returns the path to the extracted app folder
    (the zip's own top-level "AssetCatalogue/" entry), not the temp
    directory itself.
    """
    extract_root = Path(tempfile.mkdtemp(prefix="AssetCatalogue-update-extracted-"))
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_root)
    except zipfile.BadZipFile as exc:
        shutil.rmtree(extract_root, ignore_errors=True)
        raise SelfUpdateError(f"Downloaded update is not a valid zip file: {exc}") from exc

    # The zip's own top-level entry (see the release-cutting steps in the
    # README) is the app folder itself, e.g. "AssetCatalogue/" -- find
    # whichever extracted top-level folder actually contains the exe,
    # rather than assuming a specific name.
    for candidate in extract_root.iterdir():
        if candidate.is_dir() and (candidate / expected_exe_name).is_file():
            return candidate

    shutil.rmtree(extract_root, ignore_errors=True)
    raise SelfUpdateError(
        f"Downloaded update doesn't contain {expected_exe_name} where expected -- "
        "not applying it."
    )


_RELAUNCH_SCRIPT_TEMPLATE = r"""
param(
    [int]$ProcessId,
    [string]$InstallDir,
    [string]$NewVersionDir,
    [string]$ExeName,
    [string]$ExtractRoot
)

try { Wait-Process -Id $ProcessId -Timeout 30 -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Seconds 1

$backupDir = "$InstallDir.old"
$succeeded = $false
for ($attempt = 1; $attempt -le 5; $attempt++) {
    try {
        if (Test-Path $backupDir) { Remove-Item -Recurse -Force $backupDir -ErrorAction Stop }
        Rename-Item -Path $InstallDir -NewName (Split-Path -Leaf $backupDir) -ErrorAction Stop
        Move-Item -Path $NewVersionDir -Destination $InstallDir -ErrorAction Stop
        $succeeded = $true
        break
    } catch {
        Start-Sleep -Seconds 2
    }
}

if ($succeeded) {
    Start-Process -FilePath (Join-Path $InstallDir $ExeName)
    Start-Sleep -Seconds 2
    Remove-Item -Recurse -Force $backupDir -ErrorAction SilentlyContinue
} else {
    Add-Type -AssemblyName System.Windows.Forms
    $failureMessage = "Asset Catalogue couldn't finish installing the update (the old version's files " +
        "may still have been in use). Your existing install was left in place at:`n$InstallDir`n`n" +
        "You can install the update manually from the same release page."
    [System.Windows.Forms.MessageBox]::Show($failureMessage, "Asset Catalogue Update Failed") | Out-Null
    if (Test-Path $backupDir) {
        # Roll the rename back so the app is at least left runnable.
        if (-not (Test-Path $InstallDir)) {
            Rename-Item -Path $backupDir -NewName (Split-Path -Leaf $InstallDir) -ErrorAction SilentlyContinue
        }
    }
}

Remove-Item -Recurse -Force $ExtractRoot -ErrorAction SilentlyContinue
"""


def apply_update_and_exit(extracted_app_dir: Path, exe_name: str) -> None:
    """The point of no return: launches a detached PowerShell script that
    waits for THIS process to exit, then swaps install_dir()'s contents
    for extracted_app_dir's and relaunches. Never returns -- calls
    sys.exit() itself once the helper is launched, since there's nothing
    left for this process to safely do (its own files are about to be
    replaced out from under it).
    """
    script_fd = tempfile.NamedTemporaryFile(
        mode="w", suffix=".ps1", prefix="AssetCatalogue-relaunch-", delete=False, encoding="utf-8"
    )
    with script_fd as f:
        f.write(_RELAUNCH_SCRIPT_TEMPLATE)
        script_path = Path(f.name)

    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden",
            "-File", str(script_path),
            "-ProcessId", str(os.getpid()),
            "-InstallDir", str(install_dir()),
            "-NewVersionDir", str(extracted_app_dir),
            "-ExeName", exe_name,
            "-ExtractRoot", str(extracted_app_dir.parent),
        ],
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        close_fds=True,
    )
    sys.exit(0)
