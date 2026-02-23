import os
from dotenv import load_dotenv

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

def test_openai_connection():
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    
    if not api_key:
        print("❌ ERROR: OPENAI_API_KEY is not set in your .env file.")
        print("Please add it like this: OPENAI_API_KEY=sk-your-key-here")
        return

    if not OpenAI:
        print("❌ ERROR: The 'openai' python package is not installed.")
        print("Run: conda run -n tradebot pip install openai")
        return

    print(f"✅ API Key found: {api_key[:8]}...{api_key[-4:]}")
    print("Testing connection to OpenAI...")

    try:
        client = OpenAI(api_key=api_key)
        # We use a cheap/fast model just to test the connection
        response = client.chat.completions.create(
            model="gpt-4o-mini", # Fallback to a standard model for the test
            messages=[{"role": "user", "content": "Say 'Connection Successful' if you receive this."}],
            max_completion_tokens=10
        )
        print(f"✅ Connection Successful! Response: {response.choices[0].message.content}")
        
        # Now let's check if you have access to the specific model configured
        model_name = os.getenv("LLM_MODEL", "gpt-5.2")
        print(f"\nChecking access to configured model: {model_name}...")
        
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": "Test"}],
                max_completion_tokens=5
            )
            print(f"✅ Successfully connected to {model_name}!")
        except Exception as e:
            print(f"⚠️ Warning: Could not connect to {model_name}. Error: {e}")
            print("Make sure you have access to this specific model name in your OpenAI account.")

    except Exception as e:
        print(f"❌ ERROR connecting to OpenAI: {e}")

if __name__ == "__main__":
    test_openai_connection()
