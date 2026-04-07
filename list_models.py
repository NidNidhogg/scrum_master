import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    print("Error: GEMINI_API_KEY not set in .env")
    exit(1)

genai.configure(api_key=GEMINI_API_KEY, transport="rest")

print("--- [1] Standard Models (v1) ---")
try:
    for m in genai.list_models():
        if "generateContent" in m.supported_generation_methods:
            print(f"Model: {m.name}")
except Exception as e:
    print(f"Error list_models: {e}")
