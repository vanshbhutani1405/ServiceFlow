"""Small, server-side Supabase client factory."""

from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()


class ConfigurationError(RuntimeError):
    """Raised when the database client cannot be configured safely."""


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    missing = [name for name, value in (("SUPABASE_URL", url), ("SUPABASE_SERVICE_ROLE_KEY", key)) if not value]
    if missing:
        raise ConfigurationError(f"Missing required Supabase configuration: {', '.join(missing)}")
    return create_client(url, key)

