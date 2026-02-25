from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from typing import Any, Literal

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.models import Agent, AgentCard, AgentHeartbeat, Artifact, Contract, LedgerEntry, Listing, ListingKind
from app.sdk import AgentClient, AgentIdentity, create_local_agent


class UiAgentCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: Literal["buyer", "seller"]
    skus: list[str] = Field(default_factory=list, max_length=64)
    capabilities: list[str] = Field(default_factory=list, max_length=64)
    tags: list[str] = Field(default_factory=list, max_length=64)
    description: str | None = Field(default=None, max_length=500)


class UiFaucetRequest(BaseModel):
    amount: int = Field(gt=0, le=1_000_000)


class UiListingCreateRequest(BaseModel):
    agent_id: str
    kind: ListingKind
    sku: str = Field(min_length=1, max_length=64)
    price_credits: int = Field(gt=0, le=1_000_000)
    description: str = Field(min_length=1, max_length=500)
    rfq_enabled: bool = False


class UiSearchRequest(BaseModel):
    buyer_id: str
    sku: str = Field(min_length=1, max_length=64)
    required_capabilities: list[str] = Field(default_factory=list, max_length=64)
    required_tags: list[str] = Field(default_factory=list, max_length=64)
    min_reputation: int = 0
    max_price_credits: int | None = Field(default=None, gt=0, le=1_000_000)
    require_online: bool = False
    online_within_seconds: int = Field(default=120, ge=1, le=86_400)
    include_non_matching: bool = True
    limit: int = Field(default=20, ge=1, le=200)


class UiHandshakeActivateRequest(BaseModel):
    buyer_id: str
    demand_listing_id: str
    offer_listing_id: str
    terms: str = Field(min_length=1, max_length=1_000)
    price_credits: int | None = Field(default=None, gt=0, le=1_000_000)


class UiDeliverRequest(BaseModel):
    seller_id: str
    payload_text: str = Field(min_length=1, max_length=100_000)


class UiArtifactRequest(BaseModel):
    buyer_id: str


class UiDecisionRequest(BaseModel):
    buyer_id: str
    accept: bool


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dashboard_static_dir() -> Path:
    return Path(__file__).resolve().parent / "static" / "dashboard"


def _dashboard_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _dashboard_examples_dir() -> Path:
    return _dashboard_project_root() / "examples"


def _dashboard_run_logs_dir() -> Path:
    run_logs_dir = _dashboard_project_root() / ".dashboard-runs"
    run_logs_dir.mkdir(parents=True, exist_ok=True)
    return run_logs_dir


def _dashboard_agents_store_path() -> Path:
    configured = os.getenv("A2A_DASHBOARD_AGENTS_FILE")
    if configured:
        path = Path(configured)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    return _dashboard_run_logs_dir() / "agents.json"


def _serialize_identity(identity: AgentIdentity) -> dict[str, Any]:
    return {
        "name": identity.name,
        "sign_private": identity.sign_private,
        "sign_public": identity.sign_public,
        "encrypt_private": identity.encrypt_private,
        "encrypt_public": identity.encrypt_public,
        "agent_id": identity.agent_id,
    }


def _deserialize_identity(payload: dict[str, Any]) -> AgentIdentity | None:
    required = {"name", "sign_private", "sign_public", "encrypt_private", "encrypt_public", "agent_id"}
    if not required.issubset(payload.keys()):
        return None
    return AgentIdentity(
        name=str(payload["name"]),
        sign_private=str(payload["sign_private"]),
        sign_public=str(payload["sign_public"]),
        encrypt_private=str(payload["encrypt_private"]),
        encrypt_public=str(payload["encrypt_public"]),
        agent_id=str(payload["agent_id"]) if payload.get("agent_id") else None,
    )


def _load_ui_store_from_disk() -> dict[str, dict[str, Any]]:
    path = _dashboard_agents_store_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    store: dict[str, dict[str, Any]] = {}
    for agent_id, row in payload.items():
        if not isinstance(agent_id, str) or not isinstance(row, dict):
            continue
        identity_payload = row.get("identity")
        if not isinstance(identity_payload, dict):
            continue
        identity = _deserialize_identity(identity_payload)
        if identity is None:
            continue
        role = row.get("role")
        normalized_role = str(role) if role in {"buyer", "seller"} else None
        store[agent_id] = {"identity": identity, "role": normalized_role}
    return store


def _persist_ui_store(app: FastAPI) -> None:
    path = _dashboard_agents_store_path()
    raw_store = getattr(app.state, "dashboard_agents", None) or {}
    payload = {
        agent_id: {
            "identity": _serialize_identity(row["identity"]),
            "role": row.get("role"),
        }
        for agent_id, row in raw_store.items()
        if isinstance(agent_id, str) and isinstance(row, dict) and isinstance(row.get("identity"), AgentIdentity)
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _ui_store(app: FastAPI) -> dict[str, dict[str, Any]]:
    store = getattr(app.state, "dashboard_agents", None)
    if store is None:
        store = _load_ui_store_from_disk()
        app.state.dashboard_agents = store
    return store


def _local_run_store(app: FastAPI) -> dict[str, dict[str, Any]]:
    store = getattr(app.state, "dashboard_local_runs", None)
    if store is None:
        store = {}
        app.state.dashboard_local_runs = store
    return store


def _decode_json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _plaintext_text(payload: bytes) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        return "base64:" + base64.b64encode(payload).decode("ascii")


def _internal_api_error(exc: httpx.HTTPStatusError) -> HTTPException:
    detail: Any = "Relay request failed"
    try:
        body = exc.response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        detail = body.get("detail", detail)
    elif exc.response.text:
        detail = exc.response.text
    return HTTPException(status_code=exc.response.status_code, detail=detail)


def _managed_agent_entry(app: FastAPI, agent_id: str) -> dict[str, Any]:
    entry = _ui_store(app).get(agent_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown dashboard agent")
    return entry


def _run_as_agent(app: FastAPI, identity: AgentIdentity, func):
    try:
        with TestClient(app) as internal:
            client = AgentClient(internal, identity)
            return func(client)
    except httpx.HTTPStatusError as exc:
        raise _internal_api_error(exc) from exc


def _classify_example_role(path: Path) -> Literal["buyer", "seller"] | None:
    stem = path.stem.lower()
    if "buyer" in stem:
        return "buyer"
    if "seller" in stem:
        return "seller"
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if "BuyerApp" in source or "BuyerTask" in source:
        return "buyer"
    if "SellerApp" in source or "SellerTask" in source:
        return "seller"
    return None


def _discover_local_examples() -> list[dict[str, str]]:
    examples_dir = _dashboard_examples_dir()
    if not examples_dir.exists():
        return []
    examples: list[dict[str, str]] = []
    for path in sorted(examples_dir.glob("*.py")):
        if path.stem in {"__init__", "agent_apps"} or path.name.startswith("_"):
            continue
        role = _classify_example_role(path)
        if role is None:
            continue
        module = f"examples.{path.stem}"
        examples.append(
            {
                "id": path.stem,
                "role": role,
                "module": module,
                "command": f"{Path(sys.executable).name} -m {module}",
                "path": str(path.relative_to(_dashboard_project_root())),
            }
        )
    return examples


def _example_by_id(example_id: str) -> dict[str, str]:
    for example in _discover_local_examples():
        if example["id"] == example_id:
            return example
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown local example")


def _local_run_payload(run_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    process = entry.get("process")
    returncode = process.poll() if process is not None else entry.get("returncode")
    if returncode is not None and entry.get("ended_at") is None:
        entry["ended_at"] = _utc_now_iso()
        entry["returncode"] = returncode
    status_value = "running" if returncode is None else ("exited" if returncode == 0 else "failed")
    return {
        "run_id": run_id,
        "example_id": entry["example_id"],
        "role": entry["role"],
        "module": entry["module"],
        "pid": process.pid if process is not None else entry.get("pid"),
        "status": status_value,
        "returncode": returncode,
        "started_at": entry.get("started_at"),
        "ended_at": entry.get("ended_at"),
        "log_path": entry.get("log_path"),
    }


def _local_runs_payload(app: FastAPI) -> list[dict[str, Any]]:
    rows = [
        _local_run_payload(run_id, entry)
        for run_id, entry in _local_run_store(app).items()
    ]
    rows.sort(key=lambda row: str(row.get("started_at", "")), reverse=True)
    return rows


def _stop_local_run(app: FastAPI, run_id: str) -> dict[str, Any]:
    entry = _local_run_store(app).get(run_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown local run")
    process = entry.get("process")
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    entry["ended_at"] = _utc_now_iso()
    return _local_run_payload(run_id, entry)


def _tail_file(path: Path, tail: int) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[-tail:])


def _infer_agent_roles(
    *,
    agent_id: str,
    agent_name: str,
    ui_role: str | None,
    demand_listing_agents: set[str],
    offer_listing_agents: set[str],
    buyer_contract_agents: set[str],
    seller_contract_agents: set[str],
) -> tuple[list[str], str | None, str | None]:
    roles: set[str] = set()
    explicit_role = ui_role if ui_role in {"buyer", "seller"} else None
    if explicit_role is not None:
        roles.add(explicit_role)
    if agent_id in demand_listing_agents or agent_id in buyer_contract_agents:
        roles.add("buyer")
    if agent_id in offer_listing_agents or agent_id in seller_contract_agents:
        roles.add("seller")
    if not roles:
        lowered = agent_name.lower()
        if "buyer" in lowered:
            roles.add("buyer")
        if "seller" in lowered:
            roles.add("seller")
    ordered = [role for role in ("buyer", "seller") if role in roles]
    if explicit_role is not None:
        return ordered, explicit_role, "ui_managed"
    if len(ordered) == 1:
        return ordered, ordered[0], "inferred"
    if len(ordered) == 2:
        return ordered, "buyer,seller", "inferred"
    return [], None, None


def _state_payload(request: Request) -> dict[str, Any]:
    store = _ui_store(request.app)
    ledger = request.app.state.ledger_backend
    with Session(request.app.state.engine) as session:
        agents = session.exec(select(Agent).order_by(Agent.created_at.desc())).all()
        cards = session.exec(select(AgentCard)).all()
        heartbeats = session.exec(select(AgentHeartbeat)).all()
        listings = session.exec(select(Listing).order_by(Listing.created_at.desc())).all()
        contracts = session.exec(select(Contract).order_by(Contract.created_at.desc())).all()
        artifacts = session.exec(select(Artifact).order_by(Artifact.created_at.desc())).all()
        ledger_entries = session.exec(select(LedgerEntry).order_by(LedgerEntry.created_at.desc())).all()

        card_map = {card.agent_id: card for card in cards}
        heartbeat_map = {item.agent_id: item.last_seen_at for item in heartbeats}
        demand_listing_agents = {item.agent_id for item in listings if item.kind == ListingKind.DEMAND}
        offer_listing_agents = {item.agent_id for item in listings if item.kind == ListingKind.OFFER}
        buyer_contract_agents = {item.buyer_id for item in contracts}
        seller_contract_agents = {item.seller_id for item in contracts}

        agent_rows = []
        for agent in agents:
            card = card_map.get(agent.id)
            store_row = store.get(agent.id)
            ui_role = store_row["role"] if store_row is not None else None
            roles, role_value, role_source = _infer_agent_roles(
                agent_id=agent.id,
                agent_name=agent.name,
                ui_role=ui_role,
                demand_listing_agents=demand_listing_agents,
                offer_listing_agents=offer_listing_agents,
                buyer_contract_agents=buyer_contract_agents,
                seller_contract_agents=seller_contract_agents,
            )
            agent_rows.append(
                {
                    **agent.model_dump(mode="json"),
                    "balance": ledger.get_balance(session, ledger.agent_account(agent.id)),
                    "agent_card": {
                        "skus": _decode_json_list(card.skus_json) if card is not None else [],
                        "capabilities": _decode_json_list(card.capabilities_json) if card is not None else [],
                        "tags": _decode_json_list(card.tags_json) if card is not None else [],
                        "description": card.description if card is not None else None,
                    },
                    "last_seen_at": heartbeat_map.get(agent.id).isoformat()
                    if heartbeat_map.get(agent.id) is not None
                    else None,
                    "ui_managed": store_row is not None,
                    "role": role_value,
                    "roles": roles,
                    "role_source": role_source,
                }
            )

    return {
        "ledger_backend": request.app.state.ledger_backend_name,
        "agents": agent_rows,
        "listings": [row.model_dump(mode="json") for row in listings],
        "contracts": [row.model_dump(mode="json") for row in contracts],
        "artifacts": [row.model_dump(mode="json") for row in artifacts],
        "ledger_entries": [row.model_dump(mode="json") for row in ledger_entries],
        "local_examples": _discover_local_examples(),
        "local_runs": _local_runs_payload(request.app),
    }


def install_dashboard(app: FastAPI) -> None:
    static_dir = _dashboard_static_dir()
    app.mount("/dashboard/assets", StaticFiles(directory=static_dir), name="dashboard-assets")

    router = APIRouter()

    @router.get("/dashboard", include_in_schema=False)
    def dashboard_page():
        return FileResponse(static_dir / "index.html")

    @router.get("/ui/api/state")
    def dashboard_state(request: Request):
        return _state_payload(request)

    @router.get("/ui/api/local/examples")
    def dashboard_local_examples():
        return {"examples": _discover_local_examples()}

    @router.post("/ui/api/local/examples/{example_id}/start", status_code=status.HTTP_201_CREATED)
    def dashboard_start_local_example(example_id: str, request: Request):
        example = _example_by_id(example_id)
        run_id = str(uuid4())
        log_path = _dashboard_run_logs_dir() / f"{example_id}-{run_id}.log"
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        with log_path.open("ab") as log_file:
            process = subprocess.Popen(
                [sys.executable, "-m", example["module"]],
                cwd=str(_dashboard_project_root()),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=env,
            )
        _local_run_store(request.app)[run_id] = {
            "example_id": example["id"],
            "role": example["role"],
            "module": example["module"],
            "process": process,
            "pid": process.pid,
            "started_at": _utc_now_iso(),
            "ended_at": None,
            "returncode": None,
            "log_path": str(log_path),
        }
        return _local_run_payload(run_id, _local_run_store(request.app)[run_id])

    @router.post("/ui/api/local/runs/{run_id}/stop")
    def dashboard_stop_local_run(run_id: str, request: Request):
        return _stop_local_run(request.app, run_id)

    @router.get("/ui/api/local/runs/{run_id}/log")
    def dashboard_local_run_log(run_id: str, request: Request, tail: int = Query(default=200, ge=1, le=5000)):
        entry = _local_run_store(request.app).get(run_id)
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown local run")
        log_path_value = entry.get("log_path")
        log_path = Path(str(log_path_value)) if log_path_value else None
        return {
            "run_id": run_id,
            "tail": tail,
            "log": _tail_file(log_path, tail) if log_path is not None else "",
        }

    @router.post("/ui/api/agents", status_code=status.HTTP_201_CREATED)
    def dashboard_create_agent(payload: UiAgentCreateRequest, request: Request):
        identity = create_local_agent(payload.name)
        card = {
            "skus": payload.skus,
            "capabilities": payload.capabilities,
            "tags": payload.tags,
            "description": payload.description,
        }
        should_send_card = any(
            [payload.skus, payload.capabilities, payload.tags, payload.description is not None]
        )
        agent = _run_as_agent(
            request.app,
            identity,
            lambda client: client.register(agent_card=card if should_send_card else None),
        )
        _ui_store(request.app)[agent["id"]] = {"identity": identity, "role": payload.role}
        _persist_ui_store(request.app)
        return next(row for row in _state_payload(request)["agents"] if row["id"] == agent["id"])

    @router.post("/ui/api/agents/{agent_id}/faucet")
    def dashboard_faucet(agent_id: str, payload: UiFaucetRequest, request: Request):
        entry = _managed_agent_entry(request.app, agent_id)
        return _run_as_agent(
            request.app,
            entry["identity"],
            lambda client: client.faucet(payload.amount),
        )

    @router.post("/ui/api/listings", status_code=status.HTTP_201_CREATED)
    def dashboard_create_listing(payload: UiListingCreateRequest, request: Request):
        entry = _managed_agent_entry(request.app, payload.agent_id)
        return _run_as_agent(
            request.app,
            entry["identity"],
            lambda client: client.create_listing(
                kind=payload.kind.value,
                sku=payload.sku,
                price_credits=payload.price_credits,
                description=payload.description,
                rfq_enabled=payload.rfq_enabled,
            ),
        )

    @router.post("/ui/api/search")
    def dashboard_search(payload: UiSearchRequest, request: Request):
        entry = _managed_agent_entry(request.app, payload.buyer_id)
        return _run_as_agent(
            request.app,
            entry["identity"],
            lambda client: client.search_sellers(
                sku=payload.sku,
                required_capabilities=payload.required_capabilities,
                required_tags=payload.required_tags,
                min_reputation=payload.min_reputation,
                max_price_credits=payload.max_price_credits,
                require_online=payload.require_online,
                online_within_seconds=payload.online_within_seconds,
                include_non_matching=payload.include_non_matching,
                limit=payload.limit,
            ),
        )

    @router.post("/ui/api/contracts/handshake-activate")
    def dashboard_handshake_activate(payload: UiHandshakeActivateRequest, request: Request):
        entry = _managed_agent_entry(request.app, payload.buyer_id)

        def _run(client: AgentClient):
            contract = client.handshake(
                demand_listing_id=payload.demand_listing_id,
                offer_listing_id=payload.offer_listing_id,
                terms=payload.terms,
                price_credits=payload.price_credits,
            )
            return client.activate_contract(contract["id"])

        return _run_as_agent(request.app, entry["identity"], _run)

    @router.post("/ui/api/contracts/{contract_id}/deliver")
    def dashboard_deliver(contract_id: str, payload: UiDeliverRequest, request: Request):
        entry = _managed_agent_entry(request.app, payload.seller_id)
        return _run_as_agent(
            request.app,
            entry["identity"],
            lambda client: client.deliver(contract_id, payload.payload_text.encode("utf-8")),
        )

    @router.post("/ui/api/contracts/{contract_id}/artifact")
    def dashboard_artifact(contract_id: str, payload: UiArtifactRequest, request: Request):
        entry = _managed_agent_entry(request.app, payload.buyer_id)

        def _run(client: AgentClient):
            artifact = client.get_artifact(contract_id)
            plaintext = client.decrypt_artifact(artifact)
            return {
                "contract_id": contract_id,
                "sha256": artifact["sha256"],
                "plaintext_sha256": artifact["plaintext_sha256"],
                "plaintext": _plaintext_text(plaintext),
            }

        return _run_as_agent(request.app, entry["identity"], _run)

    @router.post("/ui/api/contracts/{contract_id}/decision")
    def dashboard_decision(contract_id: str, payload: UiDecisionRequest, request: Request):
        entry = _managed_agent_entry(request.app, payload.buyer_id)
        return _run_as_agent(
            request.app,
            entry["identity"],
            lambda client: client.decide(contract_id, accept=payload.accept),
        )

    app.include_router(router)
