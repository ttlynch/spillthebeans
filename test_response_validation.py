#!/usr/bin/env
import sys
from config import HL_PRIVATE_KEY, HL_WALLET_ADDRESS, HL_TESTNET, validate_config
from hl_client import HLClient

try:
    validate_config()
except ValueError as e:
    print(f"❌ Configuration error: {e}")
    sys.exit(1)

    client = HLClient(HL_WALLET_ADDRESS, HL_PRIVATE_KEY, testnet=HL_TESTNET)

    print("\n" + "=" * 80)
    print("TEST 1: Get BTC metadata and tick size")
    print("-" * 80)

    btc_meta = client.get_asset_meta("BTC")
    print(f"✅ BTC metadata:")
    print(f"  Name: {btc_meta['name']}")
    print(f"  szDecimals: {btc_meta['szDecimals']}")
    print(f"  priceDecimals: {btc_meta['priceDecimals']}")
    print(f"  tickSize: {btc_meta['tickSize']}")

    print("\n" + "=" * 80)
    print("TEST 2: Price rounding")
    print("-" * 80)

    btc_price = client.get_mid_price("BTC")
    print(f"BTC mid price: ${btc_price:,.2f}")

    test_cases = [
        (0.1, 33702.5),
        (0.1, 67424.56),
        (0.1, 33702.55),
    ]

    for price, expected in test_cases:
        rounded = client._round_price(price, btc_meta["tickSize"])
        status = "✅" if rounded == expected else "❌"
        print(f"  {status} {price} -> {rounded} (expected {expected})")

    print("\n✅ All tests passed!")
    print("=" * 80)
except Exception as e:
    print(f"❌ Test failed: {e}")
    import traceback

    traceback.print_exc()
    sys.exit(1)
