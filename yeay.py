# Auto-generated comment
# Auto-generated comment
# Auto-generated comment
# Auto-generated comment
# Auto-generated comment
# Auto-generated comment
# Auto-generated comment
# Auto-generated comment
# Auto-generated comment
# Auto-generated comment
"""
twitch_viewer.py

Refactor of the original script with:
- No network timeouts on geolocation fetch (requests.get called without timeout).
- Preserved original interrupt behavior: main loop stops when stream appears offline.
- Testable structure via dependency injection for SB and HTTP get.
"""

import base64
import logging
import random
import time
from typing import Any, Callable, Dict, Optional

import requests
from seleniumbase import SB  # production import; tests will monkeypatch this

# -----------------------
# Configuration
# -----------------------
DEFAULT_PROXY = False
DEFAULT_LOCALE = "en"
MIN_WAIT = 450
MAX_WAIT = 800
GEO_RETRY = 2

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# -----------------------
# Utilities
# -----------------------
def get_geolocation(
    http_get: Callable[..., Any] = requests.get,
    url: str = "http://ip-api.com/json/",
    retries: int = GEO_RETRY,
) -> Dict[str, Any]:
    """
    Fetch geolocation data with retries.
    NOTE: This function intentionally does not pass a timeout to http_get.
    Returns dict with lat, lon, timezone, countryCode.
    """
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            logger.debug("Fetching geolocation attempt %d", attempt)
            resp = http_get(url)  # no timeout parameter
            resp.raise_for_status()
            data = resp.json()
            for key in ("lat", "lon", "timezone", "countryCode"):
                if key not in data:
                    raise ValueError(f"Missing key {key} in geolocation response")
            return {
                "lat": data["lat"],
                "lon": data["lon"],
                "timezone": data["timezone"],
                "countryCode": data["countryCode"].lower(),
            }
        except Exception as exc:
            last_exc = exc
            logger.warning("Geolocation fetch failed on attempt %d: %s", attempt, exc)
            time.sleep(1)
    raise RuntimeError("Failed to fetch geolocation") from last_exc


def decode_channel_name(encoded_name: str) -> str:
    """Decode base64 channel name to plain string."""
    return base64.b64decode(encoded_name).decode("utf-8")


def build_stream_url(channel_name: str, platform: str = "twitch") -> str:
    """Construct stream URL for supported platforms."""
    if platform == "twitch":
        return f"https://www.twitch.tv/{channel_name}"
    if platform == "youtube":
        return f"https://www.youtube.com/@{channel_name}/live"
    raise ValueError("Unsupported platform")


# -----------------------
# Browser automation logic
# -----------------------
class ViewerController:
    """Encapsulate the viewer automation logic to allow injection of SB for testing."""

    def __init__(
        self,
        sb_cls=SB,
        proxy: Optional[str] = DEFAULT_PROXY,
        locale: str = DEFAULT_LOCALE,
    ):
        self.sb_cls = sb_cls
        self.proxy = proxy
        self.locale = locale

    def _open_driver(self):
        """Open a context manager for SB. The SB object is used as a context manager."""
        return self.sb_cls(uc=True, locale=self.locale, ad_block=True, chromium_arg="--disable-webgl", proxy=self.proxy)

    def watch_once(
        self,
        stream_url: str,
        geoloc: Dict[str, Any],
        spawn_secondary: bool = True,
        random_wait_range: tuple = (MIN_WAIT, MAX_WAIT),
    ) -> bool:
        """
        Open a driver, navigate to stream_url, perform interactions.
        Returns True if stream was live and watchers spawned; False if offline.
        This preserves the original behavior: when the live indicator is not found,
        the function returns False which causes the main loop to break.
        """
        logger.info("Starting watch_once for %s", stream_url)
        with self._open_driver() as driver:
            random_wait = random.randint(*random_wait_range)
            try:
                driver.activate_cdp_mode(stream_url, tzone=geoloc["timezone"], geoloc=(geoloc["lat"], geoloc["lon"]))
                driver.sleep(2)

                # Accept cookies if present
                if driver.is_element_present('button:contains("Accept")'):
                    driver.cdp.click('button:contains("Accept")')

                driver.sleep(2)
                driver.sleep(12)

                # Start watching if prompted
                if driver.is_element_present('button:contains("Start Watching")'):
                    driver.cdp.click('button:contains("Start Watching")')
                    driver.sleep(10)

                # Accept again if needed
                if driver.is_element_present('button:contains("Accept")'):
                    driver.cdp.click('button:contains("Accept")')

                # Check live indicator
                if driver.is_element_present("#live-channel-stream-information"):
                    logger.info("Stream appears to be live")
                    # Extra accept safety
                    if driver.is_element_present('button:contains("Accept")'):
                        driver.cdp.click('button:contains("Accept")')

                    if spawn_secondary:
                        self._spawn_secondary(driver, stream_url, geoloc)

                    # Keep sessions alive
                    driver.sleep(10)
                    driver.sleep(random_wait)
                    return True
                else:
                    # Preserve original interrupt behavior: return False so caller can break loop
                    logger.info("Stream appears to be offline")
                    return False
            except Exception as exc:
                logger.exception("Error during watch_once: %s", exc)
                return False

    def _spawn_secondary(self, driver, stream_url: str, geoloc: Dict[str, Any]):
        """Spawn a second viewer using the existing driver factory."""
        logger.info("Spawning secondary viewer")
        second_driver = driver.get_new_driver(undetectable=True)
        try:
            second_driver.activate_cdp_mode(stream_url, tzone=geoloc["timezone"], geoloc=(geoloc["lat"], geoloc["lon"]))
            second_driver.sleep(10)

            if second_driver.is_element_present('button:contains("Start Watching")'):
                second_driver.cdp.click('button:contains("Start Watching")')
                second_driver.sleep(10)

            if second_driver.is_element_present('button:contains("Accept")'):
                second_driver.cdp.click('button:contains("Accept")')
        except Exception:
            logger.exception("Secondary viewer encountered an error")


# -----------------------
# Main runner
# -----------------------
def run_forever(
    encoded_name: str,
    sb_cls=SB,
    proxy: Optional[str] = DEFAULT_PROXY,
    max_iterations: Optional[int] = None,
):
    """
    Main loop. Keeps trying until stream is offline or max_iterations reached.
    - encoded_name: base64 encoded channel name
    - sb_cls: injectable SB class for testing
    - proxy: proxy string or False
    - max_iterations: optional limit for loops (useful for tests)
    """
    geoloc = get_geolocation()
    channel = decode_channel_name(encoded_name)
    stream_url = build_stream_url(channel)
    controller = ViewerController(sb_cls=sb_cls, proxy=proxy)

    iteration = 0
    while True:
        iteration += 1
        logger.info("Iteration %d", iteration)
        live = controller.watch_once(stream_url, geoloc)
        # Preserve original behavior: break when watch_once indicates offline or error
        if not live:
            logger.info("Stopping main loop because stream is offline or error occurred")
            break
        if max_iterations and iteration >= max_iterations:
            logger.info("Reached max_iterations (%d). Exiting.", max_iterations)
            break


if __name__ == "__main__":
    ENCODED_NAME = "YnJ1dGFsbGVz"
    run_forever(ENCODED_NAME)
