from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request, status
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


def _dashboard_static_dir() -> Path:
    return Path(__file__).resolve().parent / "static" / "dashboard"


def _ui_store(app: FastAPI) -> dict[str, dict[str, Any]]:
    store = getattr(app.state, "dashboard_agents", None)
    if store is None:
        store = {}
        app.state.dashboard_agents = store
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

        agent_rows = []
        for agent in agents:
            card = card_map.get(agent.id)
            store_row = store.get(agent.id)
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
                    "role": store_row["role"] if store_row is not None else None,
                }
            )

    return {
        "ledger_backend": request.app.state.ledger_backend_name,
        "agents": agent_rows,
        "listings": [row.model_dump(mode="json") for row in listings],
        "contracts": [row.model_dump(mode="json") for row in contracts],
        "artifacts": [row.model_dump(mode="json") for row in artifacts],
        "ledger_entries": [row.model_dump(mode="json") for row in ledger_entries],
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
