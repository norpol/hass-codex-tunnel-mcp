from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from custom_components.hass_codex_tunnel_mcp.binary import (
    ChecksumError,
    PINNED_ASSETS,
    TunnelClientAsset,
    install_from_zip_bytes,
    select_asset,
    sha256_bytes,
)


def _zip_with_client() -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as zip_file:
        zip_file.writestr("release/tunnel-client", "#!/bin/sh\nexit 0\n")
    return buffer.getvalue()


def test_select_asset_for_linux_amd64_and_arm64() -> None:
    amd64 = select_asset("Linux", "x86_64")
    arm64 = select_asset("Linux", "aarch64")

    assert amd64 == PINNED_ASSETS[("linux", "amd64")]
    assert arm64 == PINNED_ASSETS[("linux", "arm64")]
    assert amd64.sha256 == "b9e0388a343f2d7adeff3992f411a0bd3d916a64bc56534aac5fd15ac1b20cd5"
    assert arm64.sha256 == "b842a9b2352eebd80514cf01a1fbb1c0d400a7d24a4015e85a7ea5f1aeaa5b30"


def test_install_from_zip_bytes_verifies_and_extracts(tmp_path: Path) -> None:
    data = _zip_with_client()
    asset = TunnelClientAsset("linux", "amd64", "fake.zip", sha256_bytes(data))

    executable = install_from_zip_bytes(data, asset, tmp_path)

    assert executable == tmp_path / "tunnel-client"
    assert executable.exists()
    assert executable.stat().st_mode & 0o111


def test_install_from_zip_bytes_rejects_bad_checksum(tmp_path: Path) -> None:
    asset = TunnelClientAsset("linux", "amd64", "fake.zip", "0" * 64)

    with pytest.raises(ChecksumError):
        install_from_zip_bytes(_zip_with_client(), asset, tmp_path)
