import httpx
from pathlib import Path

import pytest
from fastapi import FastAPI

import app.dashboard as dashboard_module
from app.main import create_app
from app.dashboard import (
    _deserialize_identity,
    _discover_local_examples,
    _infer_agent_roles,
    _load_ui_store_from_disk,
    _local_run_payload,
    _persist_ui_store,
    _classify_example_role,
    _stop_local_run,
    _serialize_identity,
    _tail_file,
    _ui_store,
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


def test_identity_serialize_roundtrip_and_missing_fields() -> None:
    identity = create_local_agent("roundtrip")
    identity.agent_id = "agent-1"
    payload = _serialize_identity(identity)
    restored = _deserialize_identity(payload)
    assert restored is not None
    assert restored.agent_id == "agent-1"
    assert _deserialize_identity({"name": "bad"}) is None


def test_infer_agent_roles_ui_and_inferred_paths() -> None:
    both_roles, both_value, both_source = _infer_agent_roles(
        agent_id="agent-1",
        agent_name="neutral",
        ui_role=None,
        demand_listing_agents={"agent-1"},
        offer_listing_agents={"agent-1"},
        buyer_contract_agents=set(),
        seller_contract_agents=set(),
    )
    assert both_roles == ["buyer", "seller"]
    assert both_value == "buyer,seller"
    assert both_source == "inferred"

    ui_roles, ui_value, ui_source = _infer_agent_roles(
        agent_id="agent-2",
        agent_name="seller-name",
        ui_role="seller",
        demand_listing_agents={"agent-2"},
        offer_listing_agents=set(),
        buyer_contract_agents=set(),
        seller_contract_agents=set(),
    )
    assert ui_roles == ["buyer", "seller"]
    assert ui_value == "seller"
    assert ui_source == "ui_managed"

    none_roles, none_value, none_source = _infer_agent_roles(
        agent_id="agent-3",
        agent_name="plain",
        ui_role=None,
        demand_listing_agents=set(),
        offer_listing_agents=set(),
        buyer_contract_agents=set(),
        seller_contract_agents=set(),
    )
    assert none_roles == []
    assert none_value is None
    assert none_source is None


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


def test_load_ui_store_from_disk_handles_invalid_shapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_path = tmp_path / "agents.json"
    monkeypatch.setenv("A2A_DASHBOARD_AGENTS_FILE", str(store_path))

    store_path.write_text("{", encoding="utf-8")
    assert _load_ui_store_from_disk() == {}

    store_path.write_text("[]", encoding="utf-8")
    assert _load_ui_store_from_disk() == {}

    store_path.write_text(
        '{"a":[],"b":{"identity":"bad"},"c":{"identity":{"name":"x"},"role":"buyer"}}',
        encoding="utf-8",
    )
    assert _load_ui_store_from_disk() == {}


def test_load_ui_store_from_disk_normalizes_invalid_role(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_path = tmp_path / "agents-role.json"
    monkeypatch.setenv("A2A_DASHBOARD_AGENTS_FILE", str(store_path))
    identity = create_local_agent("agent")
    identity.agent_id = "agent-1"
    payload = {
        "agent-1": {
            "identity": _serialize_identity(identity),
            "role": "admin",
        }
    }
    store_path.write_text(__import__("json").dumps(payload), encoding="utf-8")

    loaded = _load_ui_store_from_disk()
    assert loaded["agent-1"]["role"] is None


def test_ui_store_load_and_persist_roundtrip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_path = tmp_path / "agents-roundtrip.json"
    monkeypatch.setenv("A2A_DASHBOARD_AGENTS_FILE", str(store_path))

    app = FastAPI()
    identity = create_local_agent("persisted")
    identity.agent_id = "persisted-id"
    app.state.dashboard_agents = {
        "persisted-id": {"identity": identity, "role": "buyer"},
        "bad": {"identity": "not-identity", "role": "seller"},
    }
    _persist_ui_store(app)
    assert store_path.exists()

    loaded_app = FastAPI()
    loaded_store = _ui_store(loaded_app)
    assert "persisted-id" in loaded_store
    assert loaded_store["persisted-id"]["role"] == "buyer"


def test_classify_example_role_with_source_and_errors(tmp_path: Path) -> None:
    buyer_like = tmp_path / "alpha.py"
    buyer_like.write_text("class BuyerTask: ...", encoding="utf-8")
    assert _classify_example_role(buyer_like) == "buyer"

    seller_like = tmp_path / "beta.py"
    seller_like.write_text("class SellerTask: ...", encoding="utf-8")
    assert _classify_example_role(seller_like) == "seller"

    unknown = tmp_path / "gamma.py"
    unknown.write_text("print('x')", encoding="utf-8")
    assert _classify_example_role(unknown) is None

    missing = tmp_path / "missing.py"
    assert _classify_example_role(missing) is None


def test_discover_local_examples_with_missing_dir_and_unknown_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_dir = tmp_path / "missing-examples"
    monkeypatch.setattr(dashboard_module, "_dashboard_examples_dir", lambda: missing_dir)
    assert _discover_local_examples() == []

    examples_dir = tmp_path / "examples"
    examples_dir.mkdir()
    (examples_dir / "__init__.py").write_text("", encoding="utf-8")
    (examples_dir / "agent_apps.py").write_text("", encoding="utf-8")
    (examples_dir / "_private.py").write_text("", encoding="utf-8")
    (examples_dir / "unknown.py").write_text("print('x')", encoding="utf-8")
    monkeypatch.setattr(dashboard_module, "_dashboard_examples_dir", lambda: examples_dir)
    monkeypatch.setattr(dashboard_module, "_dashboard_project_root", lambda: tmp_path)
    assert _discover_local_examples() == []


def test_local_run_payload_updates_end_time_when_process_finished() -> None:
    class _DoneProc:
        pid = 999

        @staticmethod
        def poll():
            return 0

    entry = {
        "example_id": "e1",
        "role": "seller",
        "module": "examples.echo_seller",
        "process": _DoneProc(),
        "started_at": "t0",
        "ended_at": None,
        "returncode": None,
        "log_path": "/tmp/x.log",
    }
    payload = _local_run_payload("run-1", entry)
    assert payload["status"] == "exited"
    assert entry["ended_at"] is not None
    assert entry["returncode"] == 0


def test_stop_local_run_handles_timeout_then_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    class _SlowProc:
        pid = 7

        def __init__(self):
            self.wait_calls = 0
            self.killed = False
            self.terminated = False
            self.alive = True

        def poll(self):
            return None if self.alive else 1

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise __import__("subprocess").TimeoutExpired(cmd="x", timeout=timeout)
            self.alive = False
            return 0

        def kill(self):
            self.killed = True
            self.alive = False

    app = FastAPI()
    app.state.dashboard_local_runs = {
        "run-slow": {
            "example_id": "echo_seller",
            "role": "seller",
            "module": "examples.echo_seller",
            "process": _SlowProc(),
            "pid": 7,
            "started_at": "t0",
            "ended_at": None,
            "returncode": None,
            "log_path": "/tmp/slow.log",
        }
    }

    payload = _stop_local_run(app, "run-slow")
    assert payload["status"] in {"failed", "exited"}
    proc = app.state.dashboard_local_runs["run-slow"]["process"]
    assert proc.terminated is True
    assert proc.killed is True


def test_stop_local_run_when_process_already_finished() -> None:
    class _DoneProc:
        pid = 8

        @staticmethod
        def poll():
            return 0

    app = FastAPI()
    app.state.dashboard_local_runs = {
        "run-done": {
            "example_id": "buyer",
            "role": "buyer",
            "module": "examples.buyer",
            "process": _DoneProc(),
            "pid": 8,
            "started_at": "t0",
            "ended_at": None,
            "returncode": None,
            "log_path": "/tmp/done.log",
        }
    }

    payload = _stop_local_run(app, "run-done")
    assert payload["status"] == "exited"


def test_tail_file_handles_missing_and_oserror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing.log"
    assert _tail_file(missing, 10) == ""

    existing = tmp_path / "existing.log"
    existing.write_text("line1\nline2", encoding="utf-8")
    original_read_text = Path.read_text

    def _raise_read_text(path_obj, *args, **kwargs):
        if path_obj == existing:
            raise OSError("read failed")
        return original_read_text(path_obj, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _raise_read_text)
    assert _tail_file(existing, 10) == ""


def test_infer_agent_roles_single_inferred_paths() -> None:
    buyer_roles, buyer_value, buyer_source = _infer_agent_roles(
        agent_id="agent-buyer",
        agent_name="Buyer Local",
        ui_role=None,
        demand_listing_agents=set(),
        offer_listing_agents=set(),
        buyer_contract_agents=set(),
        seller_contract_agents=set(),
    )
    assert buyer_roles == ["buyer"]
    assert buyer_value == "buyer"
    assert buyer_source == "inferred"

    seller_roles, seller_value, seller_source = _infer_agent_roles(
        agent_id="agent-seller",
        agent_name="Seller Local",
        ui_role=None,
        demand_listing_agents=set(),
        offer_listing_agents=set(),
        buyer_contract_agents=set(),
        seller_contract_agents=set(),
    )
    assert seller_roles == ["seller"]
    assert seller_value == "seller"
    assert seller_source == "inferred"
