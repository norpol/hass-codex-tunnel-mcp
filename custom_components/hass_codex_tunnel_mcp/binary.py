"""Download and install verified tunnel-client binaries."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import tempfile
from urllib.request import urlopen
from zipfile import ZipFile

from .const import TUNNEL_CLIENT_VERSION

GITHUB_RELEASES_API = "https://api.github.com/repos/openai/tunnel-client/releases"
GITHUB_RELEASE_DOWNLOAD_BASE = "https://github.com/openai/tunnel-client/releases/download"
CLEAN_VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


class TunnelClientError(Exception):
    """Base class for tunnel-client install errors."""


class UnsupportedPlatformError(TunnelClientError):
    """Raised when no pinned asset exists for this host."""


class ChecksumError(TunnelClientError):
    """Raised when a downloaded asset does not match the pinned digest."""


@dataclass(frozen=True)
class TunnelClientAsset:
    """A verified release asset."""

    os_name: str
    arch: str
    filename: str
    sha256: str
    version: str = TUNNEL_CLIENT_VERSION
    download_url: str | None = None

    @property
    def url(self) -> str:
        """Return the public release URL for the asset."""
        if self.download_url:
            return self.download_url
        return f"{GITHUB_RELEASE_DOWNLOAD_BASE}/{self.version}/{self.filename}"

    def to_dict(self) -> dict[str, str]:
        """Serialize asset metadata for Home Assistant storage."""
        data = {
            "os_name": self.os_name,
            "arch": self.arch,
            "filename": self.filename,
            "sha256": self.sha256,
            "version": self.version,
        }
        if self.download_url:
            data["download_url"] = self.download_url
        return data

    @classmethod
    def from_dict(cls, data: dict[str, object] | None) -> "TunnelClientAsset | None":
        """Deserialize stored asset metadata."""
        if not isinstance(data, dict):
            return None
        try:
            return cls(
                os_name=str(data["os_name"]),
                arch=str(data["arch"]),
                filename=str(data["filename"]),
                sha256=str(data["sha256"]),
                version=str(data.get("version") or TUNNEL_CLIENT_VERSION),
                download_url=str(data["download_url"])
                if data.get("download_url")
                else None,
            )
        except KeyError:
            return None


@dataclass(frozen=True)
class ReleaseAssetMetadata:
    """Release asset metadata returned by the GitHub API."""

    name: str
    browser_download_url: str
    digest: str | None = None


@dataclass(frozen=True)
class TunnelClientRelease:
    """A tunnel-client release returned by the GitHub API."""

    version: str
    published_at: datetime
    draft: bool
    prerelease: bool
    assets: tuple[ReleaseAssetMetadata, ...]


PINNED_ASSETS: dict[tuple[str, str], TunnelClientAsset] = {
    ("linux", "amd64"): TunnelClientAsset(
        os_name="linux",
        arch="amd64",
        filename="tunnel-client-v0.0.10-linux-amd64.zip",
        sha256="b9e0388a343f2d7adeff3992f411a0bd3d916a64bc56534aac5fd15ac1b20cd5",
    ),
    ("linux", "arm64"): TunnelClientAsset(
        os_name="linux",
        arch="arm64",
        filename="tunnel-client-v0.0.10-linux-arm64.zip",
        sha256="b842a9b2352eebd80514cf01a1fbb1c0d400a7d24a4015e85a7ea5f1aeaa5b30",
    ),
}

_ARCH_ALIASES = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
}


def normalize_arch(machine: str | None = None) -> str:
    """Normalize a machine architecture to tunnel-client asset naming."""
    raw = (machine or platform.machine()).lower()
    return _ARCH_ALIASES.get(raw, raw)


def select_asset(
    system: str | None = None, machine: str | None = None
) -> TunnelClientAsset:
    """Select the pinned tunnel-client asset for a host."""
    os_name = (system or platform.system()).lower()
    arch = normalize_arch(machine)
    try:
        return PINNED_ASSETS[(os_name, arch)]
    except KeyError as err:
        raise UnsupportedPlatformError(f"{os_name}/{arch}") from err


def parse_clean_version(version: str) -> tuple[int, int, int] | None:
    """Parse a clean vX.Y.Z tag, rejecting suffix builds."""
    match = CLEAN_VERSION_RE.fullmatch(version)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def is_clean_version(version: str) -> bool:
    """Return true for clean semver release tags."""
    return parse_clean_version(version) is not None


def version_newer_than(candidate: str, current: str) -> bool:
    """Return true when candidate is a newer clean version than current."""
    candidate_parts = parse_clean_version(candidate)
    current_parts = parse_clean_version(current)
    if candidate_parts is None:
        return False
    if current_parts is None:
        return True
    return candidate_parts > current_parts


def parse_github_datetime(value: str) -> datetime:
    """Parse a GitHub API timestamp as an aware UTC datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def release_available_after(release: TunnelClientRelease, delay_days: int) -> datetime:
    """Return when a release becomes eligible for automatic updates."""
    from datetime import timedelta

    return release.published_at + timedelta(days=delay_days)


def parse_github_releases(payload: object) -> list[TunnelClientRelease]:
    """Parse GitHub release API JSON into release metadata."""
    if not isinstance(payload, list):
        raise TunnelClientError("GitHub releases response was not a list")
    releases: list[TunnelClientRelease] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        version = str(item.get("tag_name") or "")
        published_at = str(item.get("published_at") or "")
        if not version or not published_at:
            continue
        assets = []
        for asset in item.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "")
            browser_download_url = str(asset.get("browser_download_url") or "")
            if not name or not browser_download_url:
                continue
            digest = asset.get("digest")
            assets.append(
                ReleaseAssetMetadata(
                    name=name,
                    browser_download_url=browser_download_url,
                    digest=str(digest) if digest else None,
                )
            )
        releases.append(
            TunnelClientRelease(
                version=version,
                published_at=parse_github_datetime(published_at),
                draft=bool(item.get("draft")),
                prerelease=bool(item.get("prerelease")),
                assets=tuple(assets),
            )
        )
    return releases


def stable_clean_releases(
    releases: list[TunnelClientRelease],
) -> list[TunnelClientRelease]:
    """Return non-draft, non-prerelease, clean-version releases newest first."""
    return sorted(
        [
            release
            for release in releases
            if not release.draft
            and not release.prerelease
            and is_clean_version(release.version)
        ],
        key=lambda release: parse_clean_version(release.version) or (0, 0, 0),
        reverse=True,
    )


def latest_seen_clean_version(releases: list[TunnelClientRelease]) -> str | None:
    """Return the latest stable clean release version."""
    stable = stable_clean_releases(releases)
    return stable[0].version if stable else None


def eligible_clean_releases(
    releases: list[TunnelClientRelease],
    *,
    now: datetime,
    minimum_age_days: int,
) -> list[TunnelClientRelease]:
    """Return stable clean releases old enough for update."""
    now = now.astimezone(timezone.utc)
    return [
        release
        for release in stable_clean_releases(releases)
        if release_available_after(release, minimum_age_days) <= now
    ]


def select_latest_eligible_release(
    releases: list[TunnelClientRelease],
    *,
    current_version: str,
    now: datetime,
    minimum_age_days: int,
    failed_versions: set[str] | None = None,
) -> TunnelClientRelease | None:
    """Select the latest eligible update newer than current."""
    failed_versions = failed_versions or set()
    for release in eligible_clean_releases(
        releases, now=now, minimum_age_days=minimum_age_days
    ):
        if release.version in failed_versions:
            continue
        if version_newer_than(release.version, current_version):
            return release
    return None


def select_release_asset(
    release: TunnelClientRelease,
    *,
    system: str | None = None,
    machine: str | None = None,
    sha256_sums_text: str | None = None,
) -> TunnelClientAsset:
    """Select and verify the host-specific asset metadata for a release."""
    os_name = (system or platform.system()).lower()
    arch = normalize_arch(machine)
    filename = f"tunnel-client-{release.version}-{os_name}-{arch}.zip"
    for asset in release.assets:
        if asset.name != filename:
            continue
        sha256 = _sha256_from_digest(asset.digest) or _sha256_from_sums(
            sha256_sums_text, filename
        )
        if sha256 is None:
            raise ChecksumError(f"no SHA256 digest found for {filename}")
        return TunnelClientAsset(
            os_name=os_name,
            arch=arch,
            filename=filename,
            sha256=sha256,
            version=release.version,
            download_url=asset.browser_download_url,
        )
    raise UnsupportedPlatformError(f"{os_name}/{arch} for {release.version}")


def checksum_asset(release: TunnelClientRelease) -> ReleaseAssetMetadata | None:
    """Return the SHA256SUMS.txt asset for a release, if present."""
    for asset in release.assets:
        if asset.name == "SHA256SUMS.txt":
            return asset
    return None


def sha256_bytes(data: bytes) -> str:
    """Return the SHA256 hex digest for bytes."""
    return hashlib.sha256(data).hexdigest()


def verify_sha256(data: bytes, expected: str) -> None:
    """Verify bytes against a pinned SHA256 digest."""
    actual = sha256_bytes(data)
    if actual != expected:
        raise ChecksumError(f"expected {expected}, got {actual}")


def _sha256_from_digest(digest: str | None) -> str | None:
    if not digest or not digest.startswith("sha256:"):
        return None
    value = digest.removeprefix("sha256:")
    if re.fullmatch(r"[0-9a-fA-F]{64}", value):
        return value.lower()
    return None


def _sha256_from_sums(text: str | None, filename: str) -> str | None:
    if not text:
        return None
    for raw_line in text.splitlines():
        parts = raw_line.strip().split()
        if len(parts) < 2:
            continue
        digest, name = parts[0], parts[-1].lstrip("*")
        if name == filename and re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            return digest.lower()
    return None


def _safe_extract_zip(data: bytes, destination: Path) -> None:
    """Extract zip data while preventing path traversal."""
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "tunnel-client.zip"
        archive.write_bytes(data)
        with ZipFile(archive) as zip_file:
            for member in zip_file.infolist():
                target = destination / member.filename
                resolved = target.resolve()
                if destination.resolve() not in resolved.parents and resolved != destination.resolve():
                    raise TunnelClientError(f"unsafe zip member: {member.filename}")
            zip_file.extractall(destination)


def find_executable(root: Path) -> Path:
    """Find the tunnel-client executable in an extracted release archive."""
    candidates = [
        path
        for path in root.rglob("tunnel-client*")
        if path.is_file() and not path.name.endswith(".zip")
    ]
    for path in candidates:
        if path.name == "tunnel-client":
            return path
    if candidates:
        return candidates[0]
    raise TunnelClientError("tunnel-client executable not found in archive")


def install_from_zip_bytes(data: bytes, asset: TunnelClientAsset, install_dir: Path) -> Path:
    """Verify and extract a tunnel-client zip archive."""
    verify_sha256(data, asset.sha256)
    install_dir.mkdir(parents=True, exist_ok=True)
    marker = install_dir / f".{asset.sha256}.complete"
    executable = install_dir / "tunnel-client"
    if marker.exists() and executable.exists():
        return executable

    for child in install_dir.iterdir():
        if child.is_file() or child.is_symlink():
            child.unlink()
        elif child.is_dir():
            import shutil

            shutil.rmtree(child)

    _safe_extract_zip(data, install_dir)
    extracted = find_executable(install_dir)
    if extracted != executable:
        extracted.replace(executable)
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    marker.write_text(asset.sha256, encoding="utf-8")
    return executable


def _download_bytes(url: str) -> bytes:
    with urlopen(url, timeout=120) as response:
        return response.read()


def _download_json(url: str) -> object:
    return json.loads(_download_bytes(url).decode("utf-8"))


async def fetch_github_releases() -> list[TunnelClientRelease]:
    """Fetch tunnel-client releases from GitHub."""
    payload = await asyncio.to_thread(_download_json, GITHUB_RELEASES_API)
    return parse_github_releases(payload)


async def download_text(url: str) -> str:
    """Download UTF-8 text."""
    data = await asyncio.to_thread(_download_bytes, url)
    return data.decode("utf-8")


def installed_executable(install_root: Path, version: str) -> Path:
    """Return the expected installed executable path for a version."""
    return install_root / version / "tunnel-client"


async def ensure_tunnel_client(
    install_root: Path,
    *,
    asset: TunnelClientAsset | None = None,
    system: str | None = None,
    machine: str | None = None,
    force: bool = False,
) -> Path:
    """Download, verify, and install the host-specific tunnel-client binary."""
    asset = asset or select_asset(system, machine)
    install_dir = install_root / asset.version
    executable = install_dir / "tunnel-client"
    marker = install_dir / f".{asset.sha256}.complete"
    if not force and marker.exists() and executable.exists() and os.access(executable, os.X_OK):
        return executable

    data = await asyncio.to_thread(_download_bytes, asset.url)
    return await asyncio.to_thread(install_from_zip_bytes, data, asset, install_dir)
