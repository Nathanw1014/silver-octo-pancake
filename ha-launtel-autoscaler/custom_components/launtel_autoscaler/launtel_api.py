"""Launtel API client for Home Assistant integration.

Interfaces with Launtel's residential portal to query service status
and change NBN speed tiers programmatically.

Based on community-documented portal endpoints (launtsched project).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

import aiohttp

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://residential.launtel.net.au"
LOGIN_URL = f"{BASE_URL}/login"
SERVICES_URL = f"{BASE_URL}/api/services"
CHANGE_SPEED_URL = f"{BASE_URL}/api/service/speed"
USAGE_URL = f"{BASE_URL}/api/service/usage"


class LauntelTier(Enum):
    """Known Launtel NBN speed tiers with approximate daily prices.

    Actual prices vary by POI/address. These are reference values.
    The `psid` (plan speed ID) is what Launtel's backend expects.
    """

    STANDBY = ("standby", "Standby 0/0", 0, 0)
    NBN_25_5 = ("25_5", "Basic 25/5", 25, 5)
    NBN_50_20 = ("50_20", "Standard 50/20", 50, 20)
    NBN_100_20 = ("100_20", "Home Fast 100/20", 100, 20)
    NBN_100_40 = ("100_40", "Home Fast 100/40", 100, 40)
    NBN_250_25 = ("250_25", "Home Superfast 250/25", 250, 25)
    NBN_250_100 = ("250_100", "Home Superfast 250/100", 250, 100)
    NBN_400_50 = ("400_50", "FastAF 400/50", 400, 50)
    NBN_500_200 = ("500_200", "500/200", 500, 200)
    NBN_1000_50 = ("1000_50", "Ultrafast 1000/50", 1000, 50)
    NBN_1000_400 = ("1000_400", "Ultrafast 1000/400", 1000, 400)

    def __init__(self, tier_id: str, label: str, down: int, up: int):
        self.tier_id = tier_id
        self.label = label
        self.download_mbps = down
        self.upload_mbps = up

    @classmethod
    def from_download(cls, mbps: int) -> "LauntelTier":
        """Find the closest tier at or above a given download speed."""
        candidates = sorted(
            [t for t in cls if t.download_mbps >= mbps],
            key=lambda t: t.download_mbps,
        )
        return candidates[0] if candidates else cls.NBN_1000_400


@dataclass
class LauntelService:
    """Represents a single Launtel service/connection."""

    service_id: str
    address: str
    status: str  # "active", "paused", etc.
    current_tier: str  # tier label from portal
    current_psid: int | None  # plan speed ID
    download_mbps: int = 0
    upload_mbps: int = 0
    daily_cost: float = 0.0
    available_tiers: list[dict] = field(default_factory=list)


@dataclass
class LauntelUsage:
    """Usage data for a Launtel service."""

    download_gb: float = 0.0
    upload_gb: float = 0.0
    period_start: datetime | None = None
    period_end: datetime | None = None


class LauntelApiError(Exception):
    """Base exception for Launtel API errors."""


class LauntelAuthError(LauntelApiError):
    """Authentication failed."""


class LauntelSpeedChangeError(LauntelApiError):
    """Speed change request failed."""


class LauntelApiClient:
    """Async client for Launtel's residential portal."""

    def __init__(
        self,
        username: str,
        password: str,
        session: aiohttp.ClientSession | None = None,
    ):
        self._username = username
        self._password = password
        self._session = session
        self._owns_session = session is None
        self._cookies: dict[str, str] = {}
        self._auth_expiry: datetime | None = None
        self._lock = asyncio.Lock()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def close(self):
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    # ── Authentication ──────────────────────────────────────────────

    async def authenticate(self) -> bool:
        """Login to Launtel portal and store session cookies."""
        async with self._lock:
            session = await self._ensure_session()
            try:
                async with session.post(
                    LOGIN_URL,
                    json={
                        "username": self._username,
                        "password": self._password,
                    },
                    allow_redirects=False,
                ) as resp:
                    if resp.status in (200, 302):
                        self._cookies = {
                            c.key: c.value for c in resp.cookies.values()
                        }
                        # Also check for JSON token response
                        if resp.content_type == "application/json":
                            data = await resp.json()
                            if "token" in data:
                                self._cookies["token"] = data["token"]

                        self._auth_expiry = datetime.now() + timedelta(hours=4)
                        _LOGGER.debug("Launtel authentication successful")
                        return True

                    body = await resp.text()
                    _LOGGER.error(
                        "Launtel auth failed: status=%s body=%s",
                        resp.status,
                        body[:200],
                    )
                    raise LauntelAuthError(
                        f"Authentication failed (HTTP {resp.status})"
                    )

            except aiohttp.ClientError as err:
                raise LauntelAuthError(f"Connection error: {err}") from err

    async def _ensure_auth(self):
        """Re-authenticate if session has expired."""
        if (
            not self._cookies
            or self._auth_expiry is None
            or datetime.now() >= self._auth_expiry
        ):
            await self.authenticate()

    def _auth_headers(self) -> dict[str, str]:
        """Build headers with auth cookies/token."""
        headers = {"Content-Type": "application/json"}
        if "token" in self._cookies:
            headers["Authorization"] = f"Bearer {self._cookies['token']}"
        return headers

    # ── Service Queries ─────────────────────────────────────────────

    async def get_services(self) -> list[LauntelService]:
        """Fetch all services on the account."""
        await self._ensure_auth()
        session = await self._ensure_session()

        try:
            async with session.get(
                SERVICES_URL,
                headers=self._auth_headers(),
                cookies=self._cookies,
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise LauntelApiError(
                        f"Failed to fetch services: {resp.status} - {text[:200]}"
                    )

                data = await resp.json()
                services = []

                items = data if isinstance(data, list) else data.get("services", [])
                for svc in items:
                    service = LauntelService(
                        service_id=str(svc.get("id", svc.get("service_id", ""))),
                        address=svc.get("address", svc.get("location", "Unknown")),
                        status=svc.get("status", "unknown"),
                        current_tier=svc.get(
                            "speed_name",
                            svc.get("plan_name", "Unknown"),
                        ),
                        current_psid=svc.get("psid", svc.get("plan_speed_id")),
                        download_mbps=int(svc.get("download_speed", 0)),
                        upload_mbps=int(svc.get("upload_speed", 0)),
                        daily_cost=float(svc.get("daily_cost", 0)),
                    )

                    # Parse available tiers if present
                    for tier in svc.get("available_speeds", []):
                        service.available_tiers.append(
                            {
                                "psid": tier.get("id", tier.get("psid")),
                                "name": tier.get("name", ""),
                                "download": int(tier.get("download", 0)),
                                "upload": int(tier.get("upload", 0)),
                                "daily_cost": float(tier.get("daily_cost", 0)),
                            }
                        )

                    services.append(service)

                return services

        except aiohttp.ClientError as err:
            raise LauntelApiError(f"Connection error: {err}") from err

    async def get_service(self, service_id: str) -> LauntelService | None:
        """Fetch a specific service by ID."""
        services = await self.get_services()
        return next((s for s in services if s.service_id == service_id), None)

    async def get_usage(self, service_id: str) -> LauntelUsage:
        """Fetch usage data for a service."""
        await self._ensure_auth()
        session = await self._ensure_session()

        try:
            async with session.get(
                f"{USAGE_URL}/{service_id}",
                headers=self._auth_headers(),
                cookies=self._cookies,
            ) as resp:
                if resp.status != 200:
                    return LauntelUsage()

                data = await resp.json()
                return LauntelUsage(
                    download_gb=float(data.get("download_gb", 0)),
                    upload_gb=float(data.get("upload_gb", 0)),
                )

        except (aiohttp.ClientError, ValueError):
            return LauntelUsage()

    # ── Speed Changes ───────────────────────────────────────────────

    async def get_available_tiers(self, service_id: str) -> list[dict]:
        """Get available speed tiers and their PSIDs for a service."""
        service = await self.get_service(service_id)
        if service and service.available_tiers:
            return service.available_tiers
        return []

    async def change_speed(self, service_id: str, psid: int) -> bool:
        """Change the speed tier of a service.

        Args:
            service_id: The Launtel service ID.
            psid: The plan speed ID for the desired tier.

        Returns:
            True if the change was submitted successfully.
        """
        await self._ensure_auth()
        session = await self._ensure_session()

        _LOGGER.info(
            "Requesting speed change: service=%s psid=%s",
            service_id,
            psid,
        )

        try:
            async with session.post(
                CHANGE_SPEED_URL,
                json={
                    "service_id": service_id,
                    "psid": psid,
                },
                headers=self._auth_headers(),
                cookies=self._cookies,
            ) as resp:
                body = await resp.text()

                if resp.status == 200:
                    _LOGGER.info(
                        "Speed change submitted: service=%s psid=%s",
                        service_id,
                        psid,
                    )
                    return True

                _LOGGER.error(
                    "Speed change failed: status=%s body=%s",
                    resp.status,
                    body[:300],
                )
                raise LauntelSpeedChangeError(
                    f"Speed change failed (HTTP {resp.status}): {body[:200]}"
                )

        except aiohttp.ClientError as err:
            raise LauntelSpeedChangeError(f"Connection error: {err}") from err

    async def pause_service(self, service_id: str) -> bool:
        """Pause a service (stop billing)."""
        await self._ensure_auth()
        session = await self._ensure_session()

        try:
            async with session.post(
                f"{BASE_URL}/api/service/pause",
                json={"service_id": service_id},
                headers=self._auth_headers(),
                cookies=self._cookies,
            ) as resp:
                return resp.status == 200

        except aiohttp.ClientError as err:
            raise LauntelApiError(f"Pause failed: {err}") from err

    async def unpause_service(self, service_id: str, psid: int) -> bool:
        """Unpause/resume a service at a given speed tier."""
        await self._ensure_auth()
        session = await self._ensure_session()

        try:
            async with session.post(
                f"{BASE_URL}/api/service/unpause",
                json={"service_id": service_id, "psid": psid},
                headers=self._auth_headers(),
                cookies=self._cookies,
            ) as resp:
                return resp.status == 200

        except aiohttp.ClientError as err:
            raise LauntelApiError(f"Unpause failed: {err}") from err
