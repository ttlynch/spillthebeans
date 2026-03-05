"""Main entry point for SpillTheBeans trading bot."""

import argparse
import asyncio
import logging
from config import (
    validate_config,
    HL_WALLET_ADDRESS,
    HL_PRIVATE_KEY,
    HL_TESTNET,
    TELEGRAM_CHAT_ID,
)
from db import init_db
from hl_client import HLClient
from synth_client import SynthClient
from telegram_bot import create_bot
from execution import synth_poller, position_monitor
from logging_config import setup_logging

logger = logging.getLogger(__name__)


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="SpillTheBeans - Synth-powered trading bot for Hyperliquid"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without placing real orders (signals sent to Telegram, orders logged but not executed)",
    )
    return parser.parse_args()


async def main():
    """Main entry point that runs all concurrent tasks."""
    args = parse_args()

    setup_logging()

    if args.dry_run:
        logger.warning(
            "🧪 DRY-RUN MODE ENABLED - Orders will be logged but not executed"
        )
        import config

        config.DRY_RUN = True

    logger.info("Starting SpillTheBeans trading bot...")

    try:
        validate_config()
        logger.info("Configuration validated")
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return

    db_conn = init_db("data/trading.db")
    logger.info("Database initialized")

    hl_client = HLClient(HL_WALLET_ADDRESS, HL_PRIVATE_KEY, HL_TESTNET)
    logger.info(
        f"Hyperliquid client initialized on {'testnet' if HL_TESTNET else 'mainnet'}"
    )

    synth_client = SynthClient()
    logger.info("Synth API client initialized")

    telegram_app = create_bot(
        db_conn=db_conn, hl_client=hl_client, synth_client=synth_client
    )
    logger.info("Telegram bot initialized")

    async def run_telegram_bot(telegram_app, allowed_updates):
        await telegram_app.initialize()
        await telegram_app.start()
        await telegram_app.updater.start_polling(allowed_updates=allowed_updates)

    async with synth_client:
        tasks = [
            asyncio.create_task(synth_poller(db_conn, synth_client, telegram_app)),
            asyncio.create_task(position_monitor(db_conn, hl_client, telegram_app)),
            asyncio.create_task(run_telegram_bot(telegram_app, allowed_updates=[])),
        ]

        logger.info("All tasks started. Running concurrently...")
        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt, shutting down...")
        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)
        finally:
            await telegram_app.updater.stop()
            await telegram_app.stop()
            await telegram_app.shutdown()
            db_conn.close()
            logger.info("Database connection closed")


if __name__ == "__main__":
    asyncio.run(main())
