from data.dexscreener import fetch_market_data

def test_fetch_market_data():
    # Fetch tokens from DexScreener
    tokens = fetch_market_data()
    
    if tokens:
        print(f"Fetched {len(tokens)} tokens:")
        for token in tokens:
            print(token)
    else:
        print("No tokens found or an error occurred.")

if __name__ == "__main__":
    test_fetch_market_data()
