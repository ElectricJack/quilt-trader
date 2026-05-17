import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sdk.cli.client import CoordinatorClient, CLIError


@pytest.mark.asyncio
async def test_get_success_returns_json_body():
    client = CoordinatorClient(base_url="http://test:8000")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.content = b'{"ok": true, "value": 42}'
    mock_resp.json.return_value = {"ok": True, "value": 42}
    with patch.object(client._http, "get",
                       new=AsyncMock(return_value=mock_resp)):
        result = await client.get("/api/things")
    assert result == {"ok": True, "value": 42}
    await client.aclose()


@pytest.mark.asyncio
async def test_connection_refused_raises_unreachable():
    client = CoordinatorClient(base_url="http://test:8000")
    with patch.object(client._http, "get",
                       new=AsyncMock(side_effect=httpx.ConnectError("refused"))):
        with pytest.raises(CLIError) as exc_info:
            await client.get("/api/anything")
    assert exc_info.value.code == 3
    assert "unreachable" in str(exc_info.value).lower()
    await client.aclose()


@pytest.mark.asyncio
async def test_404_raises_user_error():
    client = CoordinatorClient(base_url="http://test:8000")
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.content = b'{"detail": "Not found"}'
    mock_resp.json.return_value = {"detail": "Not found"}
    with patch.object(client._http, "get",
                       new=AsyncMock(return_value=mock_resp)):
        with pytest.raises(CLIError) as exc_info:
            await client.get("/api/things/123")
    assert exc_info.value.code == 2
    assert "Not found" in str(exc_info.value)
    await client.aclose()


@pytest.mark.asyncio
async def test_500_raises_operation_failed():
    client = CoordinatorClient(base_url="http://test:8000")
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.content = b'{"detail": "boom"}'
    mock_resp.json.return_value = {"detail": "boom"}
    with patch.object(client._http, "get",
                       new=AsyncMock(return_value=mock_resp)):
        with pytest.raises(CLIError) as exc_info:
            await client.get("/api/things")
    assert exc_info.value.code == 4
    await client.aclose()


@pytest.mark.asyncio
async def test_post_sends_json_body():
    client = CoordinatorClient(base_url="http://test:8000")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.content = b'{"ok": true}'
    mock_resp.json.return_value = {"ok": True}
    post_mock = AsyncMock(return_value=mock_resp)
    with patch.object(client._http, "post", new=post_mock):
        await client.post("/api/things", json={"name": "x"})
    post_mock.assert_awaited_once()
    _, kwargs = post_mock.call_args
    assert kwargs["json"] == {"name": "x"}
    await client.aclose()


@pytest.mark.asyncio
async def test_delete_returns_empty_dict_on_204():
    client = CoordinatorClient(base_url="http://test:8000")
    mock_resp = MagicMock()
    mock_resp.status_code = 204
    mock_resp.headers = {}
    mock_resp.content = b""
    with patch.object(client._http, "delete",
                       new=AsyncMock(return_value=mock_resp)):
        result = await client.delete("/api/things/1")
    assert result == {}
    await client.aclose()
