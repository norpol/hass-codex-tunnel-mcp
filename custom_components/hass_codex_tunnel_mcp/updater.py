"""Automatic verified tunnel-client updater."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from .binary import (
    ChecksumError,
    TunnelClientAsset,
    TunnelClientError,
    checksum_asset,
    download_text,
    eligible_clean_releases,
    ensure_tunnel_client,
    fetch_github_releases,
    installed_executable,
    latest_seen_clean_version,
    release_available_after,
    select_asset,
    select_latest_eligible_release,
    select_release_asset,
)
from .const import (
    CONF_AUTO_UPDATE_TUNNEL_CLIENT,
    DEFAULT_AUTO_UPDATE_TUNNEL_CLIENT,
    DOMAIN,
    TUNNEL_CLIENT_VERSION,
)
from .repairs import create_issue, delete_issue

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .tunnel import TunnelManager

_LOGGER = logging.getLogger(__name__)

UPDATE_CHECK_INTERVAL = timedelta(days=1)
UPDATE_BACKOFF_DAYS = 8
READINESS_TIMEOUT = 60
STORAGE_VERSION = 1


@dataclass
class TunnelClientUpdaterState:
    """Stored tunnel-client updater state."""

    active_version: str = TUNNEL_CLIENT_VERSION
    previous_version: str | None = None
    latest_seen_version: str | None = None
    latest_eligible_version: str | None = None
    deferred_until: str | None = None
    last_check: str | None = None
    last_update: str | None = None
    last_error: str | None = None
    failed_versions: dict[str, str] = field(default_factory=dict)
    active_asset: dict[str, str] | None = None
    previous_asset: dict[str, str] | None = None

    @classmethod
    def from_dict(cls, data: object) -> "TunnelClientUpdaterState":
        """Deserialize updater state."""
        if not isinstance(data, dict):
            return cls()
        failed_versions = data.get("failed_versions")
        return cls(
            active_version=str(data.get("active_version") or TUNNEL_CLIENT_VERSION),
            previous_version=_optional_string(data.get("previous_version")),
            latest_seen_version=_optional_string(data.get("latest_seen_version")),
            latest_eligible_version=_optional_string(data.get("latest_eligible_version")),
            deferred_until=_optional_string(data.get("deferred_until")),
            last_check=_optional_string(data.get("last_check")),
            last_update=_optional_string(data.get("last_update")),
            last_error=_optional_string(data.get("last_error")),
            failed_versions={
                str(version): str(error)
                for version, error in failed_versions.items()
            }
            if isinstance(failed_versions, dict)
            else {},
            active_asset=_string_dict(data.get("active_asset")),
            previous_asset=_string_dict(data.get("previous_asset")),
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize updater state."""
        return {
            "active_version": self.active_version,
            "previous_version": self.previous_version,
            "latest_seen_version": self.latest_seen_version,
            "latest_eligible_version": self.latest_eligible_version,
            "deferred_until": self.deferred_until,
            "last_check": self.last_check,
            "last_update": self.last_update,
            "last_error": self.last_error,
            "failed_versions": self.failed_versions,
            "active_asset": self.active_asset,
            "previous_asset": self.previous_asset,
        }

    def as_attributes(self) -> dict[str, object]:
        """Return Home Assistant-safe status attributes."""
        return {
            "active_version": self.active_version,
            "previous_known_good_version": self.previous_version,
            "latest_seen_version": self.latest_seen_version,
            "latest_eligible_version": self.latest_eligible_version,
            "deferred_until": self.deferred_until,
            "last_update_check": self.last_check,
            "last_successful_update": self.last_update,
            "last_update_error": self.last_error,
            "failed_update_versions": sorted(self.failed_versions),
        }


@dataclass(frozen=True)
class UpdateCheckResult:
    """Result of a tunnel-client update check."""

    latest_seen_version: str | None
    latest_eligible_version: str | None
    candidate_asset: TunnelClientAsset | None
    deferred_until: str | None

    @property
    def candidate_version(self) -> str | None:
        """Return candidate version if an update is available."""
        return self.candidate_asset.version if self.candidate_asset else None


class TunnelClientUpdater:
    """Manage verified tunnel-client updates and rollback."""

    def __init__(
        self,
        hass: "HomeAssistant",
        entry: "ConfigEntry",
        install_root: Path,
        notify: Callable[[], None],
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.install_root = install_root
        self._notify = notify
        self.state = TunnelClientUpdaterState()
        self._store = None
        self._lock = None

    async def async_load(self) -> None:
        """Load persisted updater state."""
        import asyncio
        from homeassistant.helpers.storage import Store

        self._lock = asyncio.Lock()
        self._store = Store(
            self.hass, STORAGE_VERSION, f"{DOMAIN}_{self.entry.entry_id}_updater"
        )
        self.state = TunnelClientUpdaterState.from_dict(await self._store.async_load())
        if not self.state.active_version:
            self.state.active_version = TUNNEL_CLIENT_VERSION

    async def async_save(self) -> None:
        """Persist updater state."""
        if self._store is not None:
            await self._store.async_save(self.state.to_dict())
        self._notify()

    async def ensure_active_executable(self, *, force: bool = False) -> Path:
        """Return the active tunnel-client executable, falling back if needed."""
        version = self.state.active_version or TUNNEL_CLIENT_VERSION
        asset = TunnelClientAsset.from_dict(self.state.active_asset)
        if version == TUNNEL_CLIENT_VERSION:
            return await ensure_tunnel_client(
                self.install_root,
                asset=asset or select_asset(),
                force=force,
            )

        executable = installed_executable(self.install_root, version)
        if not force and executable.exists() and os.access(executable, os.X_OK):
            return executable
        if asset is not None:
            try:
                return await ensure_tunnel_client(
                    self.install_root, asset=asset, force=force
                )
            except TunnelClientError as err:
                await self._fallback_to_bundled(
                    f"active tunnel-client {version} could not be installed: {err}"
                )
                return await ensure_tunnel_client(self.install_root)

        await self._fallback_to_bundled(
            f"active tunnel-client {version} is missing asset metadata"
        )
        return await ensure_tunnel_client(self.install_root)

    async def async_start_auto_update(
        self,
        tunnel: "TunnelManager",
        entry_data_factory: Callable[[], Mapping[str, object]],
    ) -> None:
        """Schedule daily automatic updates."""
        from homeassistant.helpers.event import async_track_time_interval

        async def _run_auto_update(now: datetime) -> None:
            try:
                await self.async_update_tunnel_client(
                    tunnel, entry_data_factory(), now=now, automatic=True
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Automatic tunnel-client update failed: %s", err)

        def _schedule_update(now: datetime) -> None:
            if not self.auto_update_enabled:
                return
            self.hass.async_create_task(_run_auto_update(now))

        remove = async_track_time_interval(
            self.hass, _schedule_update, UPDATE_CHECK_INTERVAL
        )
        self.entry.async_on_unload(remove)

    @property
    def auto_update_enabled(self) -> bool:
        """Return whether automatic updates are enabled for this entry."""
        data = {**self.entry.data, **self.entry.options}
        return bool(
            data.get(CONF_AUTO_UPDATE_TUNNEL_CLIENT, DEFAULT_AUTO_UPDATE_TUNNEL_CLIENT)
        )

    async def async_check_for_update(
        self,
        *,
        now: datetime | None = None,
        skip_failed_versions: bool = True,
    ) -> UpdateCheckResult:
        """Check GitHub for a verified, eligible tunnel-client update."""
        now = _utc_now(now)
        try:
            releases = await fetch_github_releases()
            latest_seen = latest_seen_clean_version(releases)
            eligible = eligible_clean_releases(
                releases, now=now, minimum_age_days=UPDATE_BACKOFF_DAYS
            )
            latest_eligible = eligible[0].version if eligible else None
            candidate = select_latest_eligible_release(
                releases,
                current_version=self.state.active_version,
                now=now,
                minimum_age_days=UPDATE_BACKOFF_DAYS,
                failed_versions=set(self.state.failed_versions)
                if skip_failed_versions
                else set(),
            )
            deferred_until = None
            if latest_seen and latest_seen != latest_eligible:
                for release in releases:
                    if release.version == latest_seen:
                        deferred_until = _iso(
                            release_available_after(release, UPDATE_BACKOFF_DAYS)
                        )
                        break
            candidate_asset = (
                await self._asset_for_release(candidate) if candidate is not None else None
            )
            self.state.latest_seen_version = latest_seen
            self.state.latest_eligible_version = latest_eligible
            self.state.deferred_until = deferred_until
            self.state.last_check = _iso(now)
            self.state.last_error = None
            await self.async_save()
            if not self.state.failed_versions:
                await delete_issue(self.hass, "tunnel_client_update_failed")
            await delete_issue(self.hass, "tunnel_client_unverified_release")
            return UpdateCheckResult(
                latest_seen_version=latest_seen,
                latest_eligible_version=latest_eligible,
                candidate_asset=candidate_asset,
                deferred_until=deferred_until,
            )
        except ChecksumError as err:
            await self._record_error("tunnel_client_unverified_release", str(err))
            raise
        except Exception as err:
            await self._record_error("tunnel_client_update_failed", str(err))
            raise

    async def async_update_tunnel_client(
        self,
        tunnel: "TunnelManager",
        entry_data: Mapping[str, object],
        *,
        now: datetime | None = None,
        automatic: bool = False,
    ) -> UpdateCheckResult:
        """Install the latest eligible update and rollback on readiness failure."""
        if self._lock is None:
            raise RuntimeError("updater was not loaded")
        async with self._lock:
            result = await self.async_check_for_update(
                now=now, skip_failed_versions=automatic
            )
            if result.candidate_asset is None:
                return result

            candidate = result.candidate_asset
            old_version = self.state.active_version
            old_asset = self.state.active_asset
            try:
                candidate_executable = await ensure_tunnel_client(
                    self.install_root, asset=candidate
                )
            except ChecksumError as err:
                error = str(err)
                self.state.failed_versions[candidate.version] = error
                await self._record_error("tunnel_client_unverified_release", error)
                raise
            except TunnelClientError as err:
                error = str(err)
                self.state.failed_versions[candidate.version] = error
                await self._record_error("tunnel_client_update_failed", error)
                raise

            try:
                await tunnel.start(entry_data, executable_override=candidate_executable)
                if not await tunnel.wait_until_healthy(READINESS_TIMEOUT):
                    raise TunnelClientError(
                        f"tunnel-client {candidate.version} did not become ready"
                    )
            except Exception as err:
                await self._rollback_after_failed_update(
                    tunnel,
                    entry_data,
                    old_version=old_version,
                    old_asset=old_asset,
                    failed_version=candidate.version,
                    error=str(err),
                )
                raise

            update_time = _utc_now(now)
            self.state.active_version = candidate.version
            self.state.active_asset = candidate.to_dict()
            self.state.previous_version = old_version
            self.state.previous_asset = old_asset
            self.state.last_update = _iso(update_time)
            self.state.last_error = None
            self.state.failed_versions.pop(candidate.version, None)
            await self.async_save()
            await delete_issue(self.hass, "tunnel_client_update_failed")
            _LOGGER.info(
                "Updated tunnel-client to %s%s",
                candidate.version,
                " automatically" if automatic else "",
            )
            return result

    async def async_rollback(
        self,
        tunnel: "TunnelManager",
        entry_data: Mapping[str, object],
    ) -> bool:
        """Rollback to the previous known-good tunnel-client version."""
        if not self.state.previous_version:
            self.state.last_error = "no previous tunnel-client version is available"
            await self.async_save()
            return False
        return await self._switch_to_version(
            tunnel,
            entry_data,
            target_version=self.state.previous_version,
            target_asset=self.state.previous_asset,
            issue_id="tunnel_client_rollback_failed",
        )

    async def _switch_to_version(
        self,
        tunnel: "TunnelManager",
        entry_data: Mapping[str, object],
        *,
        target_version: str,
        target_asset: dict[str, str] | None,
        issue_id: str,
    ) -> bool:
        current_version = self.state.active_version
        current_asset = self.state.active_asset
        asset = TunnelClientAsset.from_dict(target_asset)
        try:
            if asset is None and target_version == TUNNEL_CLIENT_VERSION:
                executable = await ensure_tunnel_client(
                    self.install_root, asset=select_asset()
                )
            elif asset is not None:
                executable = await ensure_tunnel_client(self.install_root, asset=asset)
            else:
                raise TunnelClientError(
                    f"no asset metadata is available for tunnel-client {target_version}"
                )
            await tunnel.start(entry_data, executable_override=executable)
            if not await tunnel.wait_until_healthy(READINESS_TIMEOUT):
                raise TunnelClientError(
                    f"tunnel-client {target_version} did not become ready"
                )
        except Exception as err:
            self.state.last_error = str(err)
            await self.async_save()
            await create_issue(self.hass, issue_id, issue_id, {"error": str(err)})
            return False
        self.state.active_version = target_version
        self.state.active_asset = target_asset
        self.state.previous_version = current_version
        self.state.previous_asset = current_asset
        self.state.last_error = None
        await self.async_save()
        await delete_issue(self.hass, issue_id)
        return True

    async def _rollback_after_failed_update(
        self,
        tunnel: "TunnelManager",
        entry_data: Mapping[str, object],
        *,
        old_version: str,
        old_asset: dict[str, str] | None,
        failed_version: str,
        error: str,
    ) -> None:
        self.state.failed_versions[failed_version] = error
        self.state.active_version = old_version
        self.state.active_asset = old_asset
        self.state.last_error = error
        await self.async_save()
        await create_issue(
            self.hass,
            "tunnel_client_update_failed",
            "tunnel_client_update_failed",
            {"error": error},
        )
        try:
            await tunnel.start(entry_data)
            if not await tunnel.wait_until_healthy(READINESS_TIMEOUT):
                raise TunnelClientError(
                    f"rollback tunnel-client {old_version} did not become ready"
                )
        except Exception as rollback_err:
            await create_issue(
                self.hass,
                "tunnel_client_rollback_failed",
                "tunnel_client_rollback_failed",
                {"error": str(rollback_err)},
            )

    async def _asset_for_release(self, release) -> TunnelClientAsset:
        try:
            return select_release_asset(release)
        except ChecksumError:
            sums_asset = checksum_asset(release)
            if sums_asset is None:
                raise
            sums_text = await download_text(sums_asset.browser_download_url)
            return select_release_asset(release, sha256_sums_text=sums_text)

    async def _fallback_to_bundled(self, error: str) -> None:
        self.state.previous_version = self.state.active_version
        self.state.previous_asset = self.state.active_asset
        self.state.active_version = TUNNEL_CLIENT_VERSION
        self.state.active_asset = None
        self.state.last_error = error
        await self.async_save()
        await create_issue(
            self.hass,
            "tunnel_client_active_binary_fallback",
            "tunnel_client_active_binary_fallback",
            {"error": error},
        )

    async def _record_error(self, issue_id: str, error: str) -> None:
        self.state.last_check = _iso(_utc_now())
        self.state.last_error = error
        await self.async_save()
        await create_issue(self.hass, issue_id, issue_id, {"error": error})


def _utc_now(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc_now(value).isoformat()


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_dict(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    return {str(key): str(item) for key, item in value.items() if item is not None}
