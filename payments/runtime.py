"""Configuration checks for the public Railway deployment."""
import os

DEFAULT_TOKEN = "local-demo-token-change-before-sharing"


def validate_cloud_environment():
    if not os.getenv("RAILWAY_PROJECT_ID"):
        return
    if not os.getenv("DATABASE_URL"):
        raise ValueError("Railway requires DATABASE_URL")
    token = os.getenv("API_TOKEN", "")
    if token == DEFAULT_TOKEN or len(token) < 32:
        raise ValueError("Railway requires a private API_TOKEN of at least 32 characters")
    if os.getenv("PROVIDER") not in {"stripe", "simulator"}:
        raise ValueError("Railway requires an explicit PROVIDER")
    if os.getenv("ENABLE_FAULTS", "false") != "false":
        raise ValueError("Fault injection must be disabled on Railway")
