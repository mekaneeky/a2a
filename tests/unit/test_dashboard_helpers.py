import httpx
from pathlib import Path

from app.main import create_app
from app.dashboard import (
    _decode_json_list,
    _internal_api_error,
    _plaintext_text,
    _run_as_agent,
)
from app.sdk import create_local_agent


def test_decode_json_list_handles_invalid_and_non_list_values() -> None:
    assert _decode_json_list("not-json") == []
    assert _decode_json_list('{"key":"value"}') == []
    assert _decode_json_list('["a",1]') == ["a", "1"]


def test_plaintext_text_handles_binary_bytes() -> None:
    rendered = _plaintext_text(b"\xff\x00")
    assert rendered.startswith("base64:")


def test_internal_api_error_uses_response_text_when_json_unavailable() -> None:
    request = httpx.Request("GET", "http://test.local/path")
    response = httpx.Response(status_code=502, text="upstream-down", request=request)
    exc = httpx.HTTPStatusError("relay failed", request=request, response=response)

    mapped = _internal_api_error(exc)

    assert mapped.status_code == 502
    assert mapped.detail == "upstream-down"


def test_internal_api_error_uses_default_detail_without_text() -> None:
    class _Response:
        status_code = 500
        text = ""

        def json(self):
            return []

    class _Exc:
        response = _Response()

    mapped = _internal_api_error(_Exc())  # type: ignore[arg-type]

    assert mapped.status_code == 500
    assert mapped.detail == "Relay request failed"


def test_run_as_agent_maps_http_status_error_to_http_exception(tmp_path: Path) -> None:
    app = create_app(
        db_url=f"sqlite:///{tmp_path / 'dashboard-helper.db'}",
        artifact_dir=tmp_path / "artifacts",
    )
    identity = create_local_agent("unregistered")
    identity.agent_id = "missing-agent"

    def _call_balance(client):
        return client.balance()

    mapped = None
    try:
        _run_as_agent(app, identity, _call_balance)
    except Exception as exc:  # noqa: PERF203 - explicit mapping assertion
        mapped = exc

    assert mapped is not None
    assert getattr(mapped, "status_code", None) == 401
    assert getattr(mapped, "detail", None) == "Unknown agent"
