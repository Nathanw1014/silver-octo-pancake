"""Launtel API client for Home Assistant integration.

Interfaces with Launtel's residential portal by scraping the web UI.
Based on the launtsched project (github.com/lachlanmacphee/launtsched).

Launtel does NOT have a JSON API — the portal is a traditional
server-rendered web app, so we use session cookies + HTML parsing.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import aiohttp
from bs4 import BeautifulSoup

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://residential.launtel.net.au"


@dataclass
class LauntelService:
    """Represents a single Launtel service/connection."""

    service_id: int
    name: str
    avc_id: str
    user_id: str
    status: str = "active"
    current_tier: str = ""
    current_psid: int | None = None
    download_mbps: int = 0
    upload_mbps: int = 0
    daily_cost: float = 0.0
    loc_id: str = ""
    available_tiers: list[dict] = field(default_factory=list)


class LauntelApiError(Exception):
    """Base exception for Launtel API errors."""


class LauntelAuthError(LauntelApiError):
    """Authentication failed."""


class LauntelSpeedChangeError(LauntelApiError):
    """Speed change request failed."""


class LauntelApiClient:
    """Async client for Launtel's residential portal.

    This works by maintaining a session with cookies,
    posting form data for login, and scraping HTML for service info.
    """

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
        self._authenticated = False
        self._lock = asyncio.Lock()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            jar = aiohttp.CookieJar()
            self._session = aiohttp.ClientSession(
                cookie_jar=jar,
                headers={"Accept-Encoding": "gzip"},
            )
            self._owns_session = True
            self._authenticated = False
        return self._session

    async def close(self):
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    # ── Authentication ──────────────────────────────────────────────

    async def authenticate(self) -> bool:
        """Login to Launtel portal using form POST.

        The portal expects form-encoded username/password and returns
        a 302 redirect on success. We follow the redirect to fully
        establish the session.
        """
        async with self._lock:
            session = await self._ensure_session()
            self._authenticated = False

            try:
                # Follow the redirect chain to fully establish the session
                async with session.post(
                    f"{BASE_URL}/login",
                    data={
                        "username": self._username,
                        "password": self._password,
                    },
                    allow_redirects=True,
                ) as resp:
                    final_url = str(resp.url)
                    html = await resp.text()

                    # Check if we ended up on the login page (auth failed)
                    # or on the dashboard/services page (auth succeeded)
                    if "/login" in final_url or "login" in html[:500].lower():
                        # Check if the page has a login form - means auth failed
                        soup = BeautifulSoup(html, "html.parser")
                        login_form = soup.find("form", action=re.compile(r"login", re.I))
                        if login_form:
                            _LOGGER.error("Launtel auth failed: redirected back to login page")
                            raise LauntelAuthError(
                                "Authentication failed. Check your Launtel username and password."
                            )

                    self._authenticated = True
                    _LOGGER.debug(
                        "Launtel authentication successful (landed on %s)",
                        final_url,
                    )
                    return True

            except aiohttp.ClientError as err:
                raise LauntelAuthError(f"Connection error: {err}") from err

    async def _ensure_auth(self):
        """Re-authenticate if not authenticated."""
        if not self._authenticated:
            await self.authenticate()

    def _is_login_page(self, html: str) -> bool:
        """Check if the HTML is a login page (means session expired)."""
        if len(html) < 50:
            return False
        # Quick check before parsing
        lower = html[:2000].lower()
        if "password" in lower and ("login" in lower or "sign in" in lower):
            return True
        return False

    async def _get_page(self, url: str, params: dict | None = None) -> str:
        """Fetch a page, retrying once with re-auth if session expired."""
        await self._ensure_auth()
        session = await self._ensure_session()

        async with session.get(url, params=params, allow_redirects=True) as resp:
            html = await resp.text()

            # If we got redirected to login, session expired
            if self._is_login_page(html):
                _LOGGER.debug("Session expired, re-authenticating...")
                self._authenticated = False
                await self.authenticate()

                # Retry the request
                async with session.get(url, params=params, allow_redirects=True) as retry_resp:
                    html = await retry_resp.text()
                    if self._is_login_page(html):
                        raise LauntelAuthError(
                            "Still on login page after re-authentication"
                        )

            return html

    # ── Service Queries ─────────────────────────────────────────────

    async def get_services(self) -> list[LauntelService]:
        """Fetch all services by scraping the /services page.

        Parses the HTML service cards to extract service IDs,
        names, AVC IDs, and user IDs.
        """
        try:
            html = await self._get_page(f"{BASE_URL}/services")
        except aiohttp.ClientError as err:
            raise LauntelApiError(f"Connection error: {err}") from err

        soup = BeautifulSoup(html, "html.parser")
        services = []
        service_cards = soup.find_all("div", class_="service-card")

        _LOGGER.debug(
            "Found %d service cards on /services page", len(service_cards)
        )

        if not service_cards:
            # Log a snippet of the page for debugging
            _LOGGER.warning(
                "No service-card divs found. Page title: %s, body length: %d",
                soup.title.string if soup.title else "none",
                len(html),
            )

        for card in service_cards:
            # Service name
            title_el = card.find("span", class_="service-title-txt")
            name = title_el.text.strip() if title_el else "Unknown"

            # AVC ID (the card's id attribute)
            avc_id = card.get("id", "")

            # User ID (from the stats link href)
            user_id = ""
            stats_link = card.find("i", class_="fa-bar-chart")
            if stats_link and stats_link.parent:
                href = stats_link.parent.get("href", "")
                parts = href.split("=")
                if len(parts) >= 3:
                    user_id = parts[2]

            # Service ID (from the pause/unpause button onclick)
            service_id = 0
            pause_button = card.find(
                "button",
                onclick=re.compile(r"(un)?pauseService\((\d+)"),
            )
            if pause_button:
                match = re.search(r"\d+", pause_button["onclick"])
                if match:
                    service_id = int(match.group())

            # Detect pause status
            status = "active"
            if pause_button:
                onclick_text = pause_button.get("onclick", "")
                if "unpauseService" in onclick_text:
                    status = "paused"

            if service_id:
                _LOGGER.debug(
                    "Found service: id=%d name=%s avc=%s",
                    service_id, name, avc_id,
                )
                services.append(
                    LauntelService(
                        service_id=service_id,
                        name=name,
                        avc_id=avc_id,
                        user_id=user_id,
                        status=status,
                    )
                )

        return services

    async def get_service(self, service_id: int) -> LauntelService | None:
        """Fetch a specific service by ID."""
        services = await self.get_services()
        _LOGGER.debug(
            "Looking for service %d in %d services: %s",
            service_id,
            len(services),
            [s.service_id for s in services],
        )
        return next((s for s in services if s.service_id == service_id), None)

    async def get_available_tiers(self, service: LauntelService) -> list[dict]:
        """Get available speed tiers by scraping the service detail page.

        Navigates to /service?avcid=<avc_id> and parses the speed
        choices from the HTML list group items.
        """
        try:
            html = await self._get_page(
                f"{BASE_URL}/service",
                params={"avcid": service.avc_id},
            )
        except (aiohttp.ClientError, LauntelAuthError) as err:
            _LOGGER.error("Failed to fetch tiers: %s", err)
            return []

        soup = BeautifulSoup(html, "html.parser")
        tiers = []

        # Extract loc_id — needed for speed change POST
        loc_input = soup.find("input", {"name": "locid"})
        if loc_input:
            service.loc_id = loc_input.get("value", "")

        # Parse speed choices from the list
        speed_choices = soup.find_all("span", class_="list-group-item")
        _LOGGER.debug(
            "Found %d speed choices for service %s",
            len(speed_choices), service.service_id,
        )

        for choice in speed_choices:
            psid = choice.get("data-value")
            if not psid:
                continue

            col_values = choice.find_all("div", class_="col-sm-4")
            plan_name = (
                col_values[0].text.strip() if col_values else "Unknown"
            )
            price = (
                col_values[2].text.strip()
                if len(col_values) > 2
                else "N/A"
            )

            # Parse download/upload from plan name
            # e.g. "nbn100/20(100/20)" or "Home Fast(500/50)"
            download = 0
            upload = 0
            speed_match = re.search(r"(\d+)/(\d+)", plan_name)
            if speed_match:
                download = int(speed_match.group(1))
                upload = int(speed_match.group(2))

            # Parse daily cost from price string
            daily_cost = 0.0
            cost_match = re.search(r"\$(\d+\.?\d*)", price)
            if cost_match:
                daily_cost = float(cost_match.group(1))

            tiers.append(
                {
                    "psid": int(psid),
                    "name": plan_name,
                    "price": price,
                    "download": download,
                    "upload": upload,
                    "daily_cost": daily_cost,
                }
            )

        service.available_tiers = tiers
        return tiers

    # ── Speed Changes ───────────────────────────────────────────────

    async def change_speed(self, service: LauntelService, psid: int) -> bool:
        """Change the speed tier of a service.

        Posts to /confirm_service with all required parameters as
        query string values, matching the portal's form submission.
        """
        await self._ensure_auth()
        session = await self._ensure_session()

        # Ensure we have loc_id — fetch tiers page if needed
        if not service.loc_id:
            await self.get_available_tiers(service)

        if not service.loc_id:
            raise LauntelSpeedChangeError(
                "Could not determine loc_id for service. "
                "The service detail page may have changed."
            )

        url = (
            f"{BASE_URL}/confirm_service"
            f"?userid={service.user_id}"
            f"&psid={psid}"
            f"&unpause=0"
            f"&service_id={service.service_id}"
            f"&upgrade_options="
            f"&discount_code="
            f"&avcid={service.avc_id}"
            f"&locid={service.loc_id}"
            f"&coat="
        )

        _LOGGER.info(
            "Requesting speed change: service=%s psid=%s",
            service.service_id,
            psid,
        )

        try:
            async with session.post(url, allow_redirects=True) as resp:
                if resp.status in (200, 302):
                    _LOGGER.info(
                        "Speed change submitted: service=%s psid=%s",
                        service.service_id,
                        psid,
                    )
                    return True

                body = await resp.text()
                _LOGGER.error(
                    "Speed change failed: status=%s body=%s",
                    resp.status,
                    body[:300],
                )
                raise LauntelSpeedChangeError(
                    f"Speed change failed (HTTP {resp.status})"
                )

        except aiohttp.ClientError as err:
            raise LauntelSpeedChangeError(f"Connection error: {err}") from err

    async def pause_service(self, service: LauntelService) -> bool:
        """Pause a service (stop billing)."""
        await self._ensure_auth()
        session = await self._ensure_session()

        try:
            async with session.post(
                f"{BASE_URL}/service_pause/{service.service_id}",
                allow_redirects=True,
            ) as resp:
                resp.raise_for_status()
                _LOGGER.info("Service %s paused", service.service_id)
                return True

        except aiohttp.ClientError as err:
            raise LauntelApiError(f"Pause failed: {err}") from err

    async def unpause_service(self, service: LauntelService) -> bool:
        """Unpause/resume a service."""
        await self._ensure_auth()
        session = await self._ensure_session()

        try:
            async with session.post(
                f"{BASE_URL}/service_unpause/{service.service_id}",
                allow_redirects=True,
            ) as resp:
                resp.raise_for_status()
                _LOGGER.info("Service %s unpaused", service.service_id)
                return True

        except aiohttp.ClientError as err:
            raise LauntelApiError(f"Unpause failed: {err}") from err
