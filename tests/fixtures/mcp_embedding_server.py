"""Subprocess fixture: real MCP server/Chroma, deterministic offline Embedding only."""
from types import SimpleNamespace
from unittest.mock import patch

from logpilot.integrations.mcp.server import main


def offline_client(api_key, base_url):
    assert api_key == "offline-key"
    assert base_url == "https://embedding.invalid/v1"

    def create(model, input):
        assert model == "offline-embedding"
        return SimpleNamespace(data=[SimpleNamespace(embedding=[1.0, 0.0, 0.0]) for _ in input])

    return SimpleNamespace(embeddings=SimpleNamespace(create=create))


if __name__ == "__main__":
    with patch("logpilot.rag.knowledge_base.create_openai_client", side_effect=offline_client):
        raise SystemExit(main())
