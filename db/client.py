from supabase import create_client, Client
from config.settings import settings

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(settings.supabase_url, settings.supabase_service_key)
    return _client


def get_db() -> Client:
    """FastAPI dependency that returns the Supabase client."""
    return get_client()
