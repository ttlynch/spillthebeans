"""Async wrapper for Synth API."""

import os
import asyncio
import logging
from typing import Dict, Any, Optional

import httpx

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class SynthAPIError(Exception):
    """Base exception for Synth API errors."""

    pass


class UnauthorizedError(SynthAPIError):
    """Invalid API key."""

    pass


class NotFoundError(SynthAPIError):
    """Data not found."""

    pass


class ServerError(SynthAPIError):
    """Server error."""

    pass


class RateLimitError(SynthAPIError):
    """Rate limit exceeded."""

    pass


class SynthClient:
    """Async client for Synth API."""

    BASE_URL = "https://api.synthdata.co"
    MAX_RETRIES = 3
    INITIAL_RETRY_DELAY = 1.0

    def __init__(self, api_key: Optional[str] = None):
        """Initialize Synth client.

        Args:
            api_key: Synth API key (defaults to SYNTH_API_KEY env var)
        """
        self.api_key = api_key or os.getenv("SYNTH_API_KEY")
        if not self.api_key:
            raise ValueError("SYNTH_API_KEY required")
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        """Create async HTTP client."""
        self._client = httpx.AsyncClient(
            timeout=30.0, headers={"Authorization": f"Apikey {self.api_key}"}
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Close async HTTP client."""
        if self._client:
            await self._client.aclose()

    async def _request(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Make API request with retry logic for 429 errors.

        Args:
            endpoint: API endpoint path
            params: Query parameters

        Returns:
            Parsed JSON response

        Raises:
            UnauthorizedError: Invalid API key
            NotFoundError: Data not found
            ServerError: Server error
            RateLimitError: Rate limit exceeded after retries
            SynthAPIError: Other API errors
        """
        url = f"{self.BASE_URL}{endpoint}"

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await self._client.get(url, params=params)

                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 401:
                    raise UnauthorizedError("Invalid API key")
                elif response.status_code == 404:
                    raise NotFoundError(f"Data not found for {params}")
                elif response.status_code == 429:
                    if attempt < self.MAX_RETRIES - 1:
                        delay = self.INITIAL_RETRY_DELAY * (2**attempt)
                        logger.warning(
                            f"Rate limited (429), retrying in {delay}s "
                            f"(attempt {attempt + 1}/{self.MAX_RETRIES})"
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise RateLimitError("Max retries exceeded for rate limit")
                elif response.status_code == 500:
                    raise ServerError("Server error")
                else:
                    response.raise_for_status()

            except httpx.HTTPError as e:
                logger.error(f"HTTP error: {e}")
                raise SynthAPIError(f"Request failed: {e}")

        raise SynthAPIError("Unexpected error in request")

    async def get_prediction_percentiles(
        self, asset: str, horizon: str = "1h"
    ) -> Dict[str, Any]:
        """Fetch prediction percentiles for asset.

        Args:
            asset: Asset symbol (e.g., "BTC", "ETH", "SOL")
            horizon: Forecast horizon ("1h" or "24h")

        Returns:
            Prediction percentiles data
        """
        logger.info(f"Fetching prediction percentiles for {asset} (horizon={horizon})")
        return await self._request(
            "/insights/prediction-percentiles", {"asset": asset, "horizon": horizon}
        )

    async def get_volatility(self, asset: str, horizon: str = "1h") -> Dict[str, Any]:
        """Fetch volatility metrics for asset.

        Args:
            asset: Asset symbol (e.g., "BTC", "ETH", "SOL")
            horizon: Forecast horizon ("1h" or "24h")

        Returns:
            Volatility data
        """
        logger.info(f"Fetching volatility for {asset} (horizon={horizon})")
        return await self._request(
            "/insights/volatility", {"asset": asset, "horizon": horizon}
        )
