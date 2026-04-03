import google.generativeai as genai
import os

api_key = os.environ.get("GEMINI_API_KEY")

if not api_key:
    print("Error: GEMINI_API_KEY environment variable is not set.")
    exit(1)

genai.configure(api_key=api_key)

print("Available models that support text generation (Flash tier):")
for m in genai.list_models():
    if 'generateContent' in m.supported_generation_methods:
        if 'flash' in m.name.lower():
            print(f" -> {m.name}")
