"""
Step 1 of Phase 2: the simplest possible Gemini call.
This just proves your Google Cloud auth + Gemini access actually works
before we add Grafana, tools, or any real agent logic.

Run with: python3 agent.py
"""

import os
from google import genai

PROJECT_ID = "broadcast-ops-copilot"   # your project ID
LOCATION = "us-central1"                # a region that supports Gemini

client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location=LOCATION,
)

def ask_gemini(prompt: str) -> str:
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    return response.text

if __name__ == "__main__":
    test_prompt = "You are an ops assistant for a streaming platform. Reply with one short sentence confirming you're online."
    print("Sending test prompt to Gemini...")
    result = ask_gemini(test_prompt)
    print("\nGemini responded:")
    print(result)