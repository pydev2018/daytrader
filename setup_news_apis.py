"""
Quick setup script to get free API keys for news integration.
Run this once to get all API keys automatically.
"""

import webbrowser
import time

print("=" * 70)
print("  WOLF TRADING SYSTEM — News API Setup")
print("=" * 70)
print()
print("Opening registration pages for 3 free news APIs...")
print("Sign up for each (takes 30 seconds each), copy the API keys,")
print("and paste them into your .env file.")
print()

# Alpha Vantage
print("1/3 Opening Alpha Vantage...")
webbrowser.open("https://www.alphavantage.co/support/#api-key")
time.sleep(2)

# Finnhub
print("2/3 Opening Finnhub...")
webbrowser.open("https://finnhub.io/register")
time.sleep(2)

# NewsAPI
print("3/3 Opening NewsAPI...")
webbrowser.open("https://newsapi.org/register")
time.sleep(2)

print()
print("=" * 70)
print("After getting your keys, add them to .env:")
print()
print("ALPHA_VANTAGE_KEY=your_key_here")
print("FINNHUB_KEY=your_key_here")
print("NEWSAPI_KEY=your_key_here")
print()
print("Then restart the bot: conda run -n tradebot python main.py")
print("=" * 70)
