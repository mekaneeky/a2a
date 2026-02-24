from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional
from uuid import uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class ListingKind(StrEnum):
    DEMAND = "demand"
    OFFER = "offer"


class ContractStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    DELIVERED = "delivered"
    SETTLED = "settled"


class SettlementOutcome(StrEnum):
    PAYOUT = "payout"
    REFUND = "refund"


class Agent(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    name: str = Field(index=True)
    public_sign_key: str = Field(unique=True, index=True)
    public_encrypt_key: str
    reputation: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AgentCard(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: str = Field(unique=True, index=True)
    skus_json: str = "[]"
    capabilities_json: str = "[]"
    tags_json: str = "[]"
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AgentHeartbeat(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: str = Field(unique=True, index=True)
    last_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)


class Nonce(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("agent_id", "nonce", name="uq_agent_nonce"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    agent_id: str = Field(index=True)
    nonce: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Listing(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    agent_id: str = Field(index=True)
    kind: ListingKind = Field(index=True)
    sku: str = Field(index=True)
    price_credits: int
    description: str
    active: bool = True
    rfq_enabled: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Match(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    demand_listing_id: str = Field(index=True)
    offer_listing_id: str = Field(index=True)
    sku: str
    price_credits: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Contract(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    demand_listing_id: str = Field(index=True)
    offer_listing_id: str = Field(index=True)
    buyer_id: str = Field(index=True)
    seller_id: str = Field(index=True)
    sku: str
    price_credits: int
    terms: str
    status: ContractStatus = Field(default=ContractStatus.PROPOSED, index=True)
    verification_reason: Optional[str] = None
    settlement_outcome: Optional[SettlementOutcome] = Field(default=None, index=True)
    settlement_tx_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Artifact(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    contract_id: str = Field(unique=True, index=True)
    storage_path: str
    sha256: str
    plaintext_sha256: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LedgerEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tx_id: str = Field(index=True)
    account: str = Field(index=True)
    amount: int
    reason: str
    contract_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
