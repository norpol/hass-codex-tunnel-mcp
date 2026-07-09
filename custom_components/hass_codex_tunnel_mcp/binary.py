"""Download and install the pinned tunnel-client binary."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import platform
import stat
import tempfile
from urllib.request import urlopen
from zipfile import ZipFile

from .const import TUNNEL_CLIENT_VERSION

BASE_DOWNLOAD_URL = (
    "https://github.com/openai/tunnel-client/releases/download/"
    f"{TUNNEL_CLIENT_VERSION}"
)


class TunnelClientError(Exception):
    """Base class for tunnel-client install errors."""


class UnsupportedPlatformError(TunnelClientError):
    """Raised when no pinned asset exists for this host."""


class ChecksumError(TunnelClientError):
    """Raised when a downloaded asset does not match the pinned digest."""


@dataclass(frozen=True)
class TunnelClientAsset:
    """A pinned release asset."""

    os_name: str
    arch: str
    filename: str
    sha256: str

    @property
    def url(self) -> str:
        """Return the public release URL for the asset."""
        return f"{BASE_DOWNLOAD_URL}/{self.filename}"


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


def sha256_bytes(data: bytes) -> str:
    """Return the SHA256 hex digest for bytes."""
    return hashlib.sha256(data).hexdigest()


def verify_sha256(data: bytes, expected: str) -> None:
    """Verify bytes against a pinned SHA256 digest."""
    actual = sha256_bytes(data)
    if actual != expected:
        raise ChecksumError(f"expected {expected}, got {actual}")


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


async def ensure_tunnel_client(
    install_root: Path,
    *,
    system: str | None = None,
    machine: str | None = None,
    force: bool = False,
) -> Path:
    """Download, verify, and install the host-specific tunnel-client binary."""
    asset = select_asset(system, machine)
    install_dir = install_root / TUNNEL_CLIENT_VERSION
    executable = install_dir / "tunnel-client"
    marker = install_dir / f".{asset.sha256}.complete"
    if not force and marker.exists() and executable.exists() and os.access(executable, os.X_OK):
        return executable

    data = await asyncio.to_thread(_download_bytes, asset.url)
    return await asyncio.to_thread(install_from_zip_bytes, data, asset, install_dir)
