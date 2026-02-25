import sys
import os

# Add the current directory (where test_format.py is located) to sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# Import format_signal from the correct location
from utils.format import format_signal  # Corrected the import here

# Test the format_signal function
token_data = {
    "symbol": "BONK",
    "score": 90,
    "liquidity": 5000000,
    "volume_24h": 1000000,
    "holders": 1500,
    "trend": "Uptrend",
    "entry_type": "20–35% pullback"
}

formatted_signal = format_signal(token_data)
print(formatted_signal)
