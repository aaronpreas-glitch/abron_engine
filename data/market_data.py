import logging

from data.birdeye import fetch_birdeye_market_data
from data.dexscreener import fetch_market_data as fetch_dexscreener_market_data


def fetch_market_data():
    """
    Prefer BirdEye (higher quality market fields) and fall back to DexScreener.
    """
    birdeye_tokens = fetch_birdeye_market_data()
    if birdeye_tokens:
        logging.info("Using BirdEye feed (%d tokens)", len(birdeye_tokens))
        return birdeye_tokens

    logging.warning("BirdEye unavailable or empty. Falling back to DexScreener.")
    return fetch_dexscreener_market_data()
