"""Quick test to verify OpenAI API key works"""
import os
from dotenv import load_dotenv
from pathlib import Path

# Load environment
load_dotenv(Path(__file__).parent / ".env")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

if not OPENAI_API_KEY:
    print("❌ OPENAI_API_KEY not found in .env")
    exit(1)

print(f"✅ API key loaded: {OPENAI_API_KEY[:10]}...{OPENAI_API_KEY[-5:]}")

# Test OpenAI connection
try:
    from openai import OpenAI
    print("✅ OpenAI package installed")
    
    client = OpenAI(api_key=OPENAI_API_KEY)
    print("✅ OpenAI client initialized")
    
    # Test API call
    print("\n🔄 Testing API call...")
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say 'API test successful' in exactly 3 words."}
        ],
        max_tokens=10,
        temperature=0
    )
    
    result = response.choices[0].message.content.strip()
    print(f"✅ API Response: {result}")
    print("\n🎉 OpenAI API is working correctly!")
    
except ImportError:
    print("❌ OpenAI package not installed. Run: pip install openai")
except Exception as e:
    print(f"❌ API Error: {e}")
    if "invalid_api_key" in str(e).lower():
        print("   → Your API key is invalid or expired")
    elif "quota" in str(e).lower():
        print("   → You've exceeded your API quota")
    else:
        print("   → Check your internet connection and API key")
