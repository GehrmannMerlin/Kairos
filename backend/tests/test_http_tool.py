import pytest
from app.agent.tools import fetch_url


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fetch_url_uses_real_example_com_and_returns_bounded_typed_result() -> None:
    result = await fetch_url("https://example.com")

    assert result.status_code == 200
    assert result.content_type.startswith("text/html")
    assert result.title == "Example Domain"
    assert "Example Domain" in result.text_preview
    assert result.bytes_read > 0
    assert len(result.text_preview) <= 4000
