import os
import sys
try:
    from google import genai
except ImportError:
    print("google-genai not installed. Please run `pip install google-genai`")
    sys.exit(1)

# Check for API key locally
api_key = os.environ.get("GEMINI_API_KEY")

if not api_key:
    print("Error: GEMINI_API_KEY is not set in your terminal's environment variables.")
    print("Please set it or run this on your Render cloud server.")
    sys.exit(1)

client = genai.Client(api_key=api_key)

print(f"\n[OK] Authenticated successfully.")
print("Fetching all available models for your specific free tier account...\n")

try:
    models = client.models.list()
    
    flash_models = []
    print("--- Supported Models ---")
    
    for m in models:
        # We specifically care about the low-cost 'flash' tier
        if 'flash' in m.name.lower():
            print(f" [Fast/Free] -> {m.name}")
            flash_models.append(m.name)
        else:
            print(f" [Other] -> {m.name}")
                
    if not flash_models:
        print("\n[Warning] No Flash models were returned. Your account may have region restrictions.")
    
except Exception as e:
    print(f"\n[Error] contacting Google: {e}")
