from typing import Optional
import os
from dotenv import load_dotenv

load_dotenv(override=True)

def get_api_url() -> str:
    load_dotenv(override=True)
    return os.getenv("EVOLUTION_API_URL", "http://localhost:8080").rstrip("/")

def get_api_key() -> str:
    load_dotenv(override=True)
    return os.getenv("EVOLUTION_API_KEY", "MySuperSecretKey123!")

def get_instance_name() -> str:
    load_dotenv(override=True)
    return os.getenv("EVOLUTION_INSTANCE_NAME", "callinggen_default")

def resolve_instance_name(name: Optional[str] = None) -> str:
    if not name or name in ("callinggen", "default", "undefined", "null"):
        return get_instance_name()
    return name

# Backward compatibility properties
EVOLUTION_API_URL = get_api_url()
EVOLUTION_API_KEY = get_api_key()
EVOLUTION_INSTANCE_NAME = get_instance_name()
