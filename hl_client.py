"""Thin wrapper client for Hyperliquid SDK."""

import logging
from typing import Any, Dict, List, Optional, Tuple

import eth_account
from eth_account.signers.local import LocalAccount

from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils.constants import MAINNET_API_URL, TESTNET_API_URL

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class HLClient:
    """Hyperliquid trading client wrapper."""

    def __init__(self, wallet_address: str, private_key: str, testnet: bool = True):
        """Initialize Hyperliquid client.

        Args:
            wallet_address: Ethereum wallet address (0x...)
            private_key: Private key for signing (0x...)
            testnet: Use testnet if True, mainnet if False
        """
        self.wallet_address = wallet_address
        self.account: LocalAccount = eth_account.Account.from_key(private_key)
        base_url = TESTNET_API_URL if testnet else MAINNET_API_URL

        logger.info(
            f"Initializing HLClient for {wallet_address} on {'testnet' if testnet else 'mainnet'}"
        )

        self.info = Info(base_url, skip_ws=True)
        self.exchange = Exchange(self.account, base_url)
        self._asset_meta_cache: Dict[str, Dict[str, Any]] = {}

        logger.info("HLClient initialized successfully")

    def get_asset_meta(self, asset: str) -> Dict[str, Any]:
        """Get metadata for an asset including szDecimals and tick size.

        Args:
            asset: Asset ticker (e.g., "BTC", "ETH")

        Returns:
            Dictionary with:
                - name: Asset name
                - szDecimals: Number of decimals for size
                - priceDecimals: Number of decimals for price (6 - szDecimals for perps)
                - tickSize: Minimum price increment

        Raises:
            ValueError: If asset not found in metadata
        """
        if asset in self._asset_meta_cache:
            return self._asset_meta_cache[asset]

        logger.debug(f"Fetching metadata for {asset}")
        try:
            meta = self.info.meta()
            for asset_info in meta["universe"]:
                if asset_info["name"] == asset:
                    sz_decimals = asset_info["szDecimals"]
                    price_decimals = 6 - sz_decimals  # For perps
                    tick_size = 10 ** (-price_decimals)

                    result = {
                        "name": asset,
                        "szDecimals": sz_decimals,
                        "priceDecimals": price_decimals,
                        "tickSize": tick_size,
                    }

                    self._asset_meta_cache[asset] = result
                    logger.info(
                        f"Metadata for {asset}: szDecimals={sz_decimals}, tickSize={tick_size}"
                    )
                    return result

            raise ValueError(f"Asset {asset} not found in metadata")
        except Exception as e:
            logger.error(f"Failed to get metadata for {asset}: {e}")
            raise

    def _round_price(self, price: float, tick_size: float) -> float:
        """Round price to nearest valid tick.

        Follows SDK's rounding logic:
        1. Round to 5 significant figures
        2. Round to tick size

        Args:
            price: Price to round
            tick_size: Minimum price increment (e.g., 0.1 for BTC)

        Returns:
            Price rounded to tick size
        """
        # Round to 5 significant figures first (like SDK does)
        price_rounded = float(f"{price:.5g}")
        # Then round to tick size
        rounded = round(price_rounded / tick_size) * tick_size
        logger.debug(
            f"Rounded price from {price} to {rounded} (tick size: {tick_size})"
        )
        return rounded

    def _validate_order_response(
        self, response: Any
    ) -> Tuple[bool, Optional[int], Optional[str]]:
        """Validate order response and extract status.

        Args:
            response: Raw response from exchange

        Returns:
            Tuple of (success: bool, order_id: Optional[int], error_message: Optional[str])
        """
        if not isinstance(response, dict):
            return False, None, "Invalid response format"

        if response.get("status") != "ok":
            return False, None, f"Request failed: {response}"

        response_data = response.get("response", {})
        data = response_data.get("data", {})
        statuses = data.get("statuses", [])

        if not statuses or len(statuses) == 0:
            return False, None, "No status in response"

        status = statuses[0]

        if "error" in status:
            return False, None, status["error"]

        if "resting" in status:
            order_id = status["resting"].get("oid")
            return True, order_id, None

        if "filled" in status:
            # Order was filled immediately
            return True, None, None

        return False, None, f"Unknown status: {status}"

    def get_mid_price(self, asset: str) -> float:
        """Get current mid price for an asset.

        Args:
            asset: Asset ticker (e.g., "BTC", "ETH")

        Returns:
            Mid price as float
        """
        logger.debug(f"Fetching mid price for {asset}")
        try:
            all_mids = self.info.all_mids()
            price = float(all_mids[asset])
            logger.info(f"Mid price for {asset}: {price}")
            return price
        except Exception as e:
            logger.error(f"Failed to get mid price for {asset}: {e}")
            raise

    def get_all_mids(self) -> Dict[str, Any]:
        """Get all mid prices.

        Returns:
            Dictionary of asset -> mid price
        """
        logger.debug("Fetching all mid prices")
        try:
            mids = self.info.all_mids()
            logger.info(f"Fetched mid prices for {len(mids)} assets")
            return mids
        except Exception as e:
            logger.error(f"Failed to get all mids: {e}")
            raise

    def market_open(self, asset: str, is_buy: bool, size: float) -> Any:
        """Open a market position.

        Args:
            asset: Asset ticker (e.g., "BTC", "ETH")
            is_buy: True for long, False for short
            size: Position size

        Returns:
            Exchange response

        Raises:
            Exception: If order is rejected
        """
        side = "BUY" if is_buy else "SELL"
        logger.info(f"Opening market {side} position for {asset}, size={size}")
        try:
            result = self.exchange.market_open(asset, is_buy, size)

            # Validate response
            success, order_id, error = self._validate_order_response(result)
            if not success:
                logger.error(f"Market order rejected: {error}")
                raise Exception(f"Market order rejected: {error}")

            if order_id:
                logger.info(f"Market order placed successfully with ID: {order_id}")
            else:
                logger.info(f"Market order filled immediately")

            return result
        except Exception as e:
            logger.error(f"Failed to open market position for {asset}: {e}")
            raise

    def market_close(self, asset: str) -> Any:
        """Close a market position.

        Args:
            asset: Asset ticker (e.g., "BTC", "ETH")

        Returns:
            Exchange response
        """
        logger.info(f"Closing market position for {asset}")
        try:
            result = self.exchange.market_close(asset)
            logger.info(f"Market close order placed successfully: {result}")
            return result
        except Exception as e:
            logger.error(f"Failed to close market position for {asset}: {e}")
            raise

    def limit_order(
        self,
        asset: str,
        is_buy: bool,
        size: float,
        price: float,
        reduce_only: bool = False,
    ) -> Any:
        """Place a limit order.

        Args:
            asset: Asset ticker (e.g., "BTC", "ETH")
            is_buy: True for buy, False for sell
            size: Order size
            price: Limit price (will be rounded to tick size)
            reduce_only: If True, order can only reduce position

        Returns:
            Exchange response

        Raises:
            Exception: If order is rejected
        """
        side = "BUY" if is_buy else "SELL"
        logger.info(
            f"Placing limit {side} order for {asset}, size={size}, price={price}, reduce_only={reduce_only}"
        )
        try:
            # Get tick size and round price
            meta = self.get_asset_meta(asset)
            rounded_price = self._round_price(price, meta["tickSize"])

            logger.info(
                f"Rounded price from {price} to {rounded_price} (tick size: {meta['tickSize']})"
            )

            order_type = {"limit": {"tif": "Gtc"}}
            result = self.exchange.order(
                asset, is_buy, size, rounded_price, order_type, reduce_only
            )

            # Validate response
            success, order_id, error = self._validate_order_response(result)
            if not success:
                logger.error(f"Limit order rejected: {error}")
                raise Exception(f"Limit order rejected: {error}")

            if order_id:
                logger.info(f"Limit order placed successfully with ID: {order_id}")
            else:
                logger.info(f"Limit order filled immediately")

            return result
        except Exception as e:
            logger.error(f"Failed to place limit order for {asset}: {e}")
            raise

    def cancel_order(self, asset: str, order_id: int) -> Any:
        """Cancel a specific order.

        Args:
            asset: Asset ticker (e.g., "BTC", "ETH")
            order_id: Order ID to cancel

        Returns:
            Exchange response
        """
        logger.info(f"Cancelling order {order_id} for {asset}")
        try:
            result = self.exchange.cancel(asset, order_id)
            logger.info(f"Order cancelled successfully: {result}")
            return result
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id} for {asset}: {e}")
            raise

    def cancel_all(self, asset: str) -> List[Any]:
        """Cancel all open orders for an asset.

        Args:
            asset: Asset ticker (e.g., "BTC", "ETH")

        Returns:
            List of exchange responses
        """
        logger.info(f"Cancelling all orders for {asset}")
        try:
            open_orders = self.get_open_orders()
            asset_orders = [order for order in open_orders if order["coin"] == asset]

            if not asset_orders:
                logger.info(f"No open orders for {asset}")
                return []

            logger.info(f"Found {len(asset_orders)} orders to cancel for {asset}")
            results = []
            for order in asset_orders:
                order_id = order["oid"]
                result = self.cancel_order(asset, order_id)
                results.append(result)

            logger.info(f"Cancelled {len(results)} orders for {asset}")
            return results
        except Exception as e:
            logger.error(f"Failed to cancel all orders for {asset}: {e}")
            raise

    def get_positions(self) -> List[Any]:
        """Get all open positions.

        Returns:
            List of position objects
        """
        logger.debug("Fetching open positions")
        try:
            user_state = self.info.user_state(self.wallet_address)
            positions = user_state.get("assetPositions", [])
            logger.info(f"Fetched {len(positions)} positions")
            return positions
        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            raise

    def get_open_orders(self) -> List[Any]:
        """Get all open orders.

        Returns:
            List of order objects
        """
        logger.debug("Fetching open orders")
        try:
            orders = self.info.open_orders(self.wallet_address)
            logger.info(f"Fetched {len(orders)} open orders")
            return orders
        except Exception as e:
            logger.error(f"Failed to get open orders: {e}")
            raise
