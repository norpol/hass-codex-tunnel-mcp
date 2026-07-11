from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from custom_components.hass_codex_tunnel_mcp.binary import (
    ChecksumError,
    PINNED_ASSETS,
    TunnelClientAsset,
    eligible_clean_releases,
    install_from_zip_bytes,
    latest_seen_clean_version,
    parse_github_releases,
    select_latest_eligible_release,
    select_release_asset,
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


def test_release_selection_skips_young_and_suffix_versions() -> None:
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    releases = parse_github_releases(
        [
            _release_payload("v0.0.12", now - timedelta(days=2)),
            _release_payload("v0.0.11--patched", now - timedelta(days=20)),
            _release_payload("v0.0.11", now - timedelta(days=9)),
            _release_payload("v0.0.9", now - timedelta(days=30), prerelease=True),
        ]
    )

    assert latest_seen_clean_version(releases) == "v0.0.12"
    assert [release.version for release in eligible_clean_releases(
        releases, now=now, minimum_age_days=8
    )] == ["v0.0.11"]
    selected = select_latest_eligible_release(
        releases,
        current_version="v0.0.10",
        now=now,
        minimum_age_days=8,
    )

    assert selected is not None
    assert selected.version == "v0.0.11"


def test_release_selection_can_skip_failed_versions() -> None:
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    releases = parse_github_releases(
        [
            _release_payload("v0.0.12", now - timedelta(days=9)),
            _release_payload("v0.0.11", now - timedelta(days=20)),
        ]
    )

    selected = select_latest_eligible_release(
        releases,
        current_version="v0.0.10",
        now=now,
        minimum_age_days=8,
        failed_versions={"v0.0.12"},
    )

    assert selected is not None
    assert selected.version == "v0.0.11"


def test_select_release_asset_uses_github_digest() -> None:
    release = parse_github_releases(
        [
            _release_payload(
                "v0.0.11",
                datetime(2026, 7, 1, tzinfo=timezone.utc),
                sha256="a" * 64,
            )
        ]
    )[0]

    asset = select_release_asset(release, system="Linux", machine="x86_64")

    assert asset.version == "v0.0.11"
    assert asset.filename == "tunnel-client-v0.0.11-linux-amd64.zip"
    assert asset.sha256 == "a" * 64
    assert asset.url.endswith("/v0.0.11/tunnel-client-v0.0.11-linux-amd64.zip")


def test_select_release_asset_can_use_sha256s_fallback() -> None:
    release = parse_github_releases(
        [
            _release_payload(
                "v0.0.11",
                datetime(2026, 7, 1, tzinfo=timezone.utc),
                sha256=None,
            )
        ]
    )[0]

    asset = select_release_asset(
        release,
        system="Linux",
        machine="aarch64",
        sha256_sums_text=(
            f"{'b' * 64}  tunnel-client-v0.0.11-linux-arm64.zip\n"
        ),
    )

    assert asset.arch == "arm64"
    assert asset.sha256 == "b" * 64


def _release_payload(
    version: str,
    published_at: datetime,
    *,
    prerelease: bool = False,
    draft: bool = False,
    sha256: str | None = "f" * 64,
) -> dict:
    digest = f"sha256:{sha256}" if sha256 else None
    assets = [
        {
            "name": f"tunnel-client-{version}-linux-amd64.zip",
            "digest": digest,
            "browser_download_url": (
                "https://github.com/openai/tunnel-client/releases/download/"
                f"{version}/tunnel-client-{version}-linux-amd64.zip"
            ),
        },
        {
            "name": f"tunnel-client-{version}-linux-arm64.zip",
            "digest": digest,
            "browser_download_url": (
                "https://github.com/openai/tunnel-client/releases/download/"
                f"{version}/tunnel-client-{version}-linux-arm64.zip"
            ),
        },
    ]
    return {
        "tag_name": version,
        "published_at": published_at.isoformat().replace("+00:00", "Z"),
        "draft": draft,
        "prerelease": prerelease,
        "assets": assets,
    }
