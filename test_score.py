# test_score.py
from scoring.model import score_token

# Define test data
token_data = {
    "symbol": "BONK",
    "score": 90,
    "liquidity": 5000000,
    "volume_24h": 1000000,
    "holders": 1500,
    "trend_confirmed": True,
    "pullback_depth": 0.25,  # In the ideal range for pullbacks
    "rs_7d": 0.8,
    "rs_3d": 0.6,
    "liquidity_stable_72h": True,
    "volume_contracting": True,
    "volume_expanding_on_bounce": True,
    "price_data": [
        [1770498188563, 69580.38],
        [1770501764692, 69423.29],
        [1770505404271, 69375.53],
        [1770509035993, 69271.22],
        [1770512658719, 69072.05],
    ],  # Example price data for volatility calculation
    "sentiment": 8  # Placeholder for sentiment score
}

regime_score = 75  # Example regime score

# Get the score and breakdown
total_score, breakdown = score_token(token_data, regime_score)

# Print the results
print(f"Total Score: {total_score}/100")
print("Breakdown:")
for category, score in breakdown.items():
    print(f"{category}: {score}")
