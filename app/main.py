from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from sqlalchemy import delete, or_
from sqlmodel import Session, select

from app.dashboard import install_dashboard
from app.db import init_db, make_engine
from app.models import (
    Agent,
    AgentCard,
    AgentHeartbeat,
    Artifact,
    Contract,
    ContractStatus,
    Listing,
    ListingKind,
    Match,
    Nonce,
    SettlementOutcome,
)
from app.schemas import (
    AgentRegisterRequest,
    DecisionRequest,
    DeliverRequest,
    FaucetRequest,
    HandshakeRequest,
    ListingCreateRequest,
    MatchRequest,
    SellerSearchRequest,
)
from app.security import canonical_request_message, encrypt_for_recipient, is_fresh_timestamp, verify_signature
from app.services.ledger_backend import build_ledger_backend
from app.services.verifier import SkuType, verify_payload


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_terms(values: list[str]) -> list[str]:
    normalized = [value.strip().lower() for value in values if value.strip()]
    # preserve deterministic output while removing duplicates
    return sorted(dict.fromkeys(normalized))


def _card_payload(card: AgentCard | None) -> dict[str, object]:
    if card is None:
        return {"skus": [], "capabilities": [], "tags": [], "description": None}
    return {
        "skus": json.loads(card.skus_json),
        "capabilities": json.loads(card.capabilities_json),
        "tags": json.loads(card.tags_json),
        "description": card.description,
    }


def create_app(
    db_url: str | None = None,
    artifact_dir: str | Path | None = None,
    ledger_backend: str | None = None,
) -> FastAPI:
    app = FastAPI(title="agents-souq", version="0.1.0")
    install_dashboard(app)

    final_db_url = db_url or os.getenv("A2A_DB_URL", "sqlite:///./app.db")
    final_artifact_dir = Path(artifact_dir or os.getenv("ARTIFACT_DIR", "artifacts"))
    final_ledger_backend = (ledger_backend or os.getenv("LEDGER_BACKEND", "db")).strip().lower()
    final_artifact_dir.mkdir(parents=True, exist_ok=True)

    engine = make_engine(final_db_url)
    init_db(engine)
    ledger = build_ledger_backend(
        final_ledger_backend,
        evm_rpc_url=os.getenv("LEDGER_EVM_RPC_URL"),
        evm_contract_address=os.getenv("LEDGER_EVM_CONTRACT_ADDRESS"),
        evm_private_key=os.getenv("LEDGER_EVM_PRIVATE_KEY"),
    )

    app.state.engine = engine
    app.state.artifact_dir = final_artifact_dir
    app.state.ledger_backend = ledger
    app.state.ledger_backend_name = final_ledger_backend

    def get_session(request: Request):
        with Session(request.app.state.engine) as session:
            yield session

    async def require_agent(
        request: Request,
        session: Session = Depends(get_session),
    ) -> Agent:
        agent_id = request.headers.get("x-agent-id")
        timestamp_header = request.headers.get("x-timestamp")
        nonce = request.headers.get("x-nonce")
        signature = request.headers.get("x-signature")

        if not agent_id or not timestamp_header or not nonce or not signature:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing signature headers")

        try:
            ts = int(timestamp_header)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid timestamp") from exc

        if not is_fresh_timestamp(int(time.time()), ts, ttl_seconds=300):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Stale timestamp")

        agent = session.get(Agent, agent_id)
        if agent is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown agent")

        existing_nonce = session.exec(
            select(Nonce).where(Nonce.agent_id == agent_id, Nonce.nonce == nonce)
        ).first()
        if existing_nonce:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Nonce already used")

        body = await request.body()
        message = canonical_request_message(
            method=request.method,
            path=request.url.path,
            timestamp=timestamp_header,
            nonce=nonce,
            body=body,
        )
        if not verify_signature(agent.public_sign_key, message, signature):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

        session.add(Nonce(agent_id=agent_id, nonce=nonce))
        session.commit()
        return agent

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/agents/register", status_code=status.HTTP_201_CREATED)
    def register_agent(payload: AgentRegisterRequest, session: Session = Depends(get_session)):
        existing = session.exec(
            select(Agent).where(Agent.public_sign_key == payload.public_sign_key)
        ).first()
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Agent already registered")

        agent = Agent(
            name=payload.name,
            public_sign_key=payload.public_sign_key,
            public_encrypt_key=payload.public_encrypt_key,
        )
        session.add(agent)
        session.commit()
        session.refresh(agent)

        card_in = payload.agent_card
        skus = _normalize_terms(card_in.skus if card_in else [])
        capabilities = _normalize_terms(card_in.capabilities if card_in else [])
        tags = _normalize_terms(card_in.tags if card_in else [])
        description = card_in.description if card_in else None

        card = AgentCard(
            agent_id=agent.id,
            skus_json=json.dumps(skus),
            capabilities_json=json.dumps(capabilities),
            tags_json=json.dumps(tags),
            description=description,
        )
        session.add(card)
        session.commit()
        session.refresh(agent)

        payload_out = agent.model_dump(mode="json")
        payload_out["agent_card"] = _card_payload(card)
        return payload_out

    @app.post("/agents/heartbeat")
    def heartbeat(
        agent: Agent = Depends(require_agent),
        session: Session = Depends(get_session),
    ):
        now = _utc_now()
        session.exec(delete(AgentHeartbeat).where(AgentHeartbeat.agent_id == agent.id))
        session.add(AgentHeartbeat(agent_id=agent.id, last_seen_at=now))
        session.commit()
        return {"agent_id": agent.id, "last_seen_at": now.isoformat()}

    @app.post("/ledger/faucet")
    def faucet_credits(
        payload: FaucetRequest,
        agent: Agent = Depends(require_agent),
        session: Session = Depends(get_session),
    ):
        tx_id = ledger.post_transfer(
            session,
            from_account="mint",
            to_account=ledger.agent_account(agent.id),
            amount=payload.amount,
            reason="faucet",
            contract_id=None,
            allow_overdraft=True,
        )
        return {"tx_id": tx_id, "balance": ledger.get_balance(session, ledger.agent_account(agent.id))}

    @app.post("/agents/search")
    def search_sellers(
        payload: SellerSearchRequest,
        _buyer: Agent = Depends(require_agent),
        session: Session = Depends(get_session),
    ):
        required_capabilities = _normalize_terms(payload.required_capabilities)
        required_tags = _normalize_terms(payload.required_tags)
        requested_sku = payload.sku.strip().lower()

        offers = session.exec(
            select(Listing)
            .where(
                Listing.kind == ListingKind.OFFER,
                Listing.active.is_(True),
                Listing.sku == payload.sku,
            )
            .order_by(Listing.price_credits.asc(), Listing.created_at.desc())
        ).all()

        # Keep one offer per seller (lowest price because of ordering).
        seller_offer_map: dict[str, Listing] = {}
        for offer in offers:
            seller_offer_map.setdefault(offer.agent_id, offer)

        seller_ids = list(seller_offer_map.keys())
        if not seller_ids:
            return {
                "requirements": {
                    "sku": requested_sku,
                    "required_capabilities": required_capabilities,
                    "required_tags": required_tags,
                    "min_reputation": payload.min_reputation,
                    "max_price_credits": payload.max_price_credits,
                    "require_online": payload.require_online,
                    "online_within_seconds": payload.online_within_seconds,
                },
                "results": [],
            }

        sellers = session.exec(select(Agent).where(Agent.id.in_(seller_ids))).all()
        seller_map = {seller.id: seller for seller in sellers}

        cards = session.exec(select(AgentCard).where(AgentCard.agent_id.in_(seller_ids))).all()
        card_map = {card.agent_id: card for card in cards}
        heartbeats = session.exec(
            select(AgentHeartbeat).where(AgentHeartbeat.agent_id.in_(seller_ids))
        ).all()
        heartbeat_map = {heartbeat.agent_id: heartbeat.last_seen_at for heartbeat in heartbeats}
        online_cutoff = _utc_now() - timedelta(seconds=payload.online_within_seconds)

        results: list[dict[str, object]] = []
        for seller_id, offer in seller_offer_map.items():
            seller = seller_map.get(seller_id)
            if seller is None:
                continue

            card = _card_payload(card_map.get(seller_id))
            card_skus = set(card["skus"])
            card_capabilities = set(card["capabilities"])
            card_tags = set(card["tags"])

            reasons: list[str] = []
            if requested_sku not in card_skus:
                reasons.append("sku_not_declared")

            missing_capabilities = [cap for cap in required_capabilities if cap not in card_capabilities]
            if missing_capabilities:
                reasons.append(f"missing_capabilities:{','.join(missing_capabilities)}")

            missing_tags = [tag for tag in required_tags if tag not in card_tags]
            if missing_tags:
                reasons.append(f"missing_tags:{','.join(missing_tags)}")

            if seller.reputation < payload.min_reputation:
                reasons.append("reputation_too_low")

            if payload.max_price_credits is not None and offer.price_credits > payload.max_price_credits:
                reasons.append("price_too_high")

            if payload.require_online:
                last_seen = heartbeat_map.get(seller_id)
                if last_seen is not None and last_seen.tzinfo is None:
                    last_seen = last_seen.replace(tzinfo=timezone.utc)
                if last_seen is None or last_seen < online_cutoff:
                    reasons.append("seller_offline")

            card_match = len(reasons) == 0
            result = {
                "seller": {
                    "id": seller.id,
                    "name": seller.name,
                    "reputation": seller.reputation,
                },
                "offer": {
                    "listing_id": offer.id,
                    "sku": offer.sku,
                    "price_credits": offer.price_credits,
                    "description": offer.description,
                },
                "agent_card": card,
                "card_match": card_match,
                "reasons": reasons,
            }
            if card_match or payload.include_non_matching:
                results.append(result)

        results.sort(
            key=lambda item: (
                not bool(item["card_match"]),
                int(item["offer"]["price_credits"]),
                -int(item["seller"]["reputation"]),
            )
        )

        return {
            "requirements": {
                "sku": requested_sku,
                "required_capabilities": required_capabilities,
                "required_tags": required_tags,
                "min_reputation": payload.min_reputation,
                "max_price_credits": payload.max_price_credits,
                "require_online": payload.require_online,
                "online_within_seconds": payload.online_within_seconds,
            },
            "results": results[: payload.limit],
        }

    @app.get("/ledger/balance")
    def ledger_balance(
        agent: Agent = Depends(require_agent),
        session: Session = Depends(get_session),
    ):
        return {"agent_id": agent.id, "balance": ledger.get_balance(session, ledger.agent_account(agent.id))}

    @app.post("/listings", status_code=status.HTTP_201_CREATED)
    def create_listing(
        payload: ListingCreateRequest,
        agent: Agent = Depends(require_agent),
        session: Session = Depends(get_session),
    ):
        listing = Listing(
            agent_id=agent.id,
            kind=payload.kind,
            sku=payload.sku,
            price_credits=payload.price_credits,
            description=payload.description,
            rfq_enabled=payload.rfq_enabled,
        )
        session.add(listing)
        session.commit()
        session.refresh(listing)
        return listing.model_dump(mode="json")

    @app.get("/listings")
    def list_listings(
        kind: ListingKind | None = None,
        sku: str | None = None,
        active: bool | None = None,
        limit: int = 50,
        offset: int = 0,
        _agent: Agent = Depends(require_agent),
        session: Session = Depends(get_session),
    ):
        safe_limit = max(1, min(limit, 200))
        safe_offset = max(0, offset)
        query = select(Listing).order_by(Listing.created_at.desc())
        if kind is not None:
            query = query.where(Listing.kind == kind)
        if sku is not None:
            query = query.where(Listing.sku == sku)
        if active is not None:
            query = query.where(Listing.active == active)
        query = query.offset(safe_offset).limit(safe_limit)
        rows = session.exec(query).all()
        return [row.model_dump(mode="json") for row in rows]

    @app.post("/match")
    def match_listing(
        payload: MatchRequest,
        agent: Agent = Depends(require_agent),
        session: Session = Depends(get_session),
    ):
        demand = session.get(Listing, payload.demand_listing_id)
        if demand is None or demand.kind != ListingKind.DEMAND:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Demand listing not found")
        if demand.agent_id != agent.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your demand listing")

        offer = session.exec(
            select(Listing)
            .where(
                Listing.kind == ListingKind.OFFER,
                Listing.active.is_(True),
                Listing.sku == demand.sku,
            )
            .order_by(Listing.price_credits.asc())
        ).first()
        if offer is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No offer found")

        matched = Match(
            demand_listing_id=demand.id,
            offer_listing_id=offer.id,
            sku=demand.sku,
            price_credits=offer.price_credits,
        )
        session.add(matched)
        session.commit()
        session.refresh(matched)
        return {
            "id": matched.id,
            "demand_listing_id": matched.demand_listing_id,
            "offer_listing_id": matched.offer_listing_id,
            "sku": matched.sku,
            "price_credits": matched.price_credits,
            "seller_id": offer.agent_id,
        }

    @app.post("/contracts/handshake", status_code=status.HTTP_201_CREATED)
    def contract_handshake(
        payload: HandshakeRequest,
        agent: Agent = Depends(require_agent),
        session: Session = Depends(get_session),
    ):
        demand = session.get(Listing, payload.demand_listing_id)
        offer = session.get(Listing, payload.offer_listing_id)
        if demand is None or offer is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found")
        if demand.kind != ListingKind.DEMAND or offer.kind != ListingKind.OFFER:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid listing kinds")
        if demand.agent_id != agent.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only buyer can handshake")
        if demand.sku != offer.sku:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SKU mismatch")

        price = payload.price_credits if payload.price_credits is not None else offer.price_credits
        contract = Contract(
            demand_listing_id=demand.id,
            offer_listing_id=offer.id,
            buyer_id=demand.agent_id,
            seller_id=offer.agent_id,
            sku=demand.sku,
            price_credits=price,
            terms=payload.terms,
            status=ContractStatus.PROPOSED,
        )
        session.add(contract)
        session.commit()
        session.refresh(contract)
        return contract.model_dump(mode="json")

    @app.post("/contracts/{contract_id}/activate")
    def activate_contract(
        contract_id: str,
        agent: Agent = Depends(require_agent),
        session: Session = Depends(get_session),
    ):
        contract = session.get(Contract, contract_id)
        if contract is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contract not found")
        if contract.buyer_id != agent.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only buyer can activate")
        if contract.status != ContractStatus.PROPOSED:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Contract must be proposed")

        ledger.post_transfer(
            session,
            from_account=ledger.agent_account(agent.id),
            to_account=ledger.escrow_account(contract.id),
            amount=contract.price_credits,
            reason="reserve",
            contract_id=contract.id,
        )
        contract.status = ContractStatus.ACTIVE
        contract.updated_at = _utc_now()
        session.add(contract)
        session.commit()
        session.refresh(contract)
        return contract.model_dump(mode="json")

    @app.post("/contracts/{contract_id}/deliver")
    def deliver_artifact(
        request: Request,
        contract_id: str,
        payload: DeliverRequest,
        agent: Agent = Depends(require_agent),
        session: Session = Depends(get_session),
    ):
        contract = session.get(Contract, contract_id)
        if contract is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contract not found")
        if contract.seller_id != agent.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only seller can deliver")
        if contract.status != ContractStatus.ACTIVE:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Contract is not active")

        try:
            plaintext = base64.b64decode(payload.payload_b64.encode("ascii"), validate=True)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="payload_b64 must be valid base64") from exc

        try:
            verification = verify_payload(SkuType(contract.sku), plaintext)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        if not verification.ok:
            tx_id = ledger.post_transfer(
                session,
                from_account=ledger.escrow_account(contract.id),
                to_account=ledger.agent_account(contract.buyer_id),
                amount=contract.price_credits,
                reason="auto_refund_verification_failed",
                contract_id=contract.id,
            )
            contract.status = ContractStatus.SETTLED
            contract.settlement_outcome = SettlementOutcome.REFUND
            contract.verification_reason = verification.reason
            contract.settlement_tx_id = tx_id
            contract.updated_at = _utc_now()
            agent.reputation -= 1
            session.add(agent)
            session.add(contract)
            session.commit()
            return {
                "id": contract.id,
                "status": contract.status.value,
                "outcome": contract.settlement_outcome.value,
                "reason": contract.verification_reason,
                "tx_id": tx_id,
            }

        buyer = session.get(Agent, contract.buyer_id)
        if buyer is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Buyer not found")

        ciphertext = encrypt_for_recipient(buyer.public_encrypt_key, plaintext)
        path = request.app.state.artifact_dir / f"{contract.id}.bin"
        path.write_bytes(ciphertext)

        artifact = session.exec(select(Artifact).where(Artifact.contract_id == contract.id)).first()
        sha256_cipher = hashlib.sha256(ciphertext).hexdigest()
        sha256_plaintext = hashlib.sha256(plaintext).hexdigest()
        if artifact is None:
            artifact = Artifact(
                contract_id=contract.id,
                storage_path=str(path),
                sha256=sha256_cipher,
                plaintext_sha256=sha256_plaintext,
            )
        else:
            artifact.storage_path = str(path)
            artifact.sha256 = sha256_cipher
            artifact.plaintext_sha256 = sha256_plaintext
        session.add(artifact)

        contract.status = ContractStatus.DELIVERED
        contract.verification_reason = "ok"
        contract.updated_at = _utc_now()
        session.add(contract)
        session.commit()
        return {
            "id": contract.id,
            "status": contract.status.value,
            "sha256": artifact.sha256,
        }

    @app.get("/contracts/{contract_id}/artifact")
    def fetch_artifact(
        contract_id: str,
        agent: Agent = Depends(require_agent),
        session: Session = Depends(get_session),
    ):
        contract = session.get(Contract, contract_id)
        if contract is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contract not found")
        if contract.buyer_id != agent.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only buyer can fetch artifact")
        if contract.status not in {ContractStatus.DELIVERED, ContractStatus.SETTLED}:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Artifact unavailable")

        artifact = session.exec(select(Artifact).where(Artifact.contract_id == contract_id)).first()
        if artifact is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")

        file_path = Path(artifact.storage_path)
        if not file_path.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact missing on disk")

        ciphertext = file_path.read_bytes()
        return {
            "contract_id": contract.id,
            "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
            "sha256": artifact.sha256,
            "plaintext_sha256": artifact.plaintext_sha256,
        }

    @app.post("/contracts/{contract_id}/decision")
    def contract_decision(
        contract_id: str,
        payload: DecisionRequest,
        agent: Agent = Depends(require_agent),
        session: Session = Depends(get_session),
    ):
        contract = session.get(Contract, contract_id)
        if contract is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contract not found")
        if contract.buyer_id != agent.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only buyer can decide")
        if contract.status != ContractStatus.DELIVERED:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Contract not delivered")

        if payload.accept:
            tx_id = ledger.post_transfer(
                session,
                from_account=ledger.escrow_account(contract.id),
                to_account=ledger.agent_account(contract.seller_id),
                amount=contract.price_credits,
                reason="payout",
                contract_id=contract.id,
            )
            outcome = SettlementOutcome.PAYOUT
            seller = session.get(Agent, contract.seller_id)
            if seller is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Seller not found")
            agent.reputation += 1
            seller.reputation += 1
            session.add(agent)
            session.add(seller)
        else:
            tx_id = ledger.post_transfer(
                session,
                from_account=ledger.escrow_account(contract.id),
                to_account=ledger.agent_account(contract.buyer_id),
                amount=contract.price_credits,
                reason="refund",
                contract_id=contract.id,
            )
            outcome = SettlementOutcome.REFUND
            seller = session.get(Agent, contract.seller_id)
            if seller is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Seller not found")
            seller.reputation -= 1
            session.add(seller)

        contract.status = ContractStatus.SETTLED
        contract.settlement_outcome = outcome
        contract.settlement_tx_id = tx_id
        contract.updated_at = _utc_now()
        session.add(contract)
        session.commit()
        session.refresh(contract)
        return {
            "id": contract.id,
            "status": contract.status.value,
            "outcome": contract.settlement_outcome.value,
            "tx_id": contract.settlement_tx_id,
        }

    @app.get("/contracts/{contract_id}")
    def get_contract(
        contract_id: str,
        agent: Agent = Depends(require_agent),
        session: Session = Depends(get_session),
    ):
        contract = session.get(Contract, contract_id)
        if contract is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contract not found")
        if agent.id not in {contract.buyer_id, contract.seller_id}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        return contract.model_dump(mode="json")

    @app.get("/contracts")
    def list_contracts(
        contract_status: ContractStatus | None = Query(default=None, alias="status"),
        role: str | None = None,
        limit: int = 50,
        offset: int = 0,
        agent: Agent = Depends(require_agent),
        session: Session = Depends(get_session),
    ):
        safe_limit = max(1, min(limit, 200))
        safe_offset = max(0, offset)

        query = (
            select(Contract)
            .where(or_(Contract.buyer_id == agent.id, Contract.seller_id == agent.id))
            .order_by(Contract.created_at.desc())
        )

        if role is not None:
            if role == "buyer":
                query = query.where(Contract.buyer_id == agent.id)
            elif role == "seller":
                query = query.where(Contract.seller_id == agent.id)
            else:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="role must be buyer or seller")

        if contract_status is not None:
            query = query.where(Contract.status == contract_status)

        rows = session.exec(query.offset(safe_offset).limit(safe_limit)).all()
        return [row.model_dump(mode="json") for row in rows]

    return app


app = create_app()
