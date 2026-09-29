from db import supabase_client


def test_supabase_client_is_created_once_and_reused(monkeypatch):
    created = []

    def fake_create_client(url, key):
        created.append((url, key))
        return object()

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key")
    monkeypatch.setattr(supabase_client, "create_client", fake_create_client)
    supabase_client.get_supabase_client.cache_clear()
    try:
        first = supabase_client.get_supabase_client()
        second = supabase_client.get_supabase_client()
        assert first is second
        assert created == [("https://example.supabase.co", "test-key")]
    finally:
        supabase_client.get_supabase_client.cache_clear()
