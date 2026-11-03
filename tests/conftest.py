import os

# The agents build their OpenAI model at import time. Tests never call it.
os.environ.setdefault("OPENAI_API_KEY", "test-key")
