"""Configuration module for Hyperliquid trading client."""

import os
from typing import List

from dotenv import load_dotenv

load_dotenv()

SYNTH_API_KEY: str = os.getenv("SYNTH_API_KEY", "")
HL_WALLET_ADDRESS: str = os.getenv("HL_WALLET_ADDRESS", "")
HL_PRIVATE_KEY: str = os.getenv("HL_PRIVATE_KEY", "")
HL_TESTNET: bool = os.getenv("HL_TESTNET", "true").lower() in ("true", "1", "yes")
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

ASSETS: List[str] = ["BTC", "ETH", "SOL"]
DEFAULT_POSITION_SIZE_USD: float = 100.0


def validate_config() -> None:
    """Validate that required configuration is present."""
    if not HL_WALLET_ADDRESS:
        raise ValueError("HL_WALLET_ADDRESS not set in environment")
    if not HL_PRIVATE_KEY:
        raise ValueError("HL_PRIVATE_KEY not set in environment")
    if not HL_WALLET_ADDRESS.startswith("0x") or len(HL_WALLET_ADDRESS) != 42:
        raise ValueError("HL_WALLET_ADDRESS must be a valid Ethereum address (0x...)")
    if not HL_PRIVATE_KEY.startswith("0x") or len(HL_PRIVATE_KEY) != 66:
        raise ValueError("HL_PRIVATE_KEY must be a valid hex private key (0x...)")
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN not set in environment")
    if not TELEGRAM_CHAT_ID:
        raise ValueError("TELEGRAM_CHAT_ID not set in environment")
