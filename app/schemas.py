from __future__ import annotations

from pydantic import BaseModel, Field

from app.models import ListingKind


class AgentCardRequest(BaseModel):
    skus: list[str] = Field(default_factory=list, max_length=64)
    capabilities: list[str] = Field(default_factory=list, max_length=64)
    tags: list[str] = Field(default_factory=list, max_length=64)
    description: str | None = Field(default=None, max_length=500)


class AgentRegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    public_sign_key: str
    public_encrypt_key: str
    agent_card: AgentCardRequest | None = None


class FaucetRequest(BaseModel):
    amount: int = Field(gt=0, le=1_000_000)


class ListingCreateRequest(BaseModel):
    kind: ListingKind
    sku: str = Field(min_length=1, max_length=64)
    price_credits: int = Field(gt=0, le=1_000_000)
    description: str = Field(min_length=1, max_length=500)
    rfq_enabled: bool = False


class MatchRequest(BaseModel):
    demand_listing_id: str


class HandshakeRequest(BaseModel):
    demand_listing_id: str
    offer_listing_id: str
    terms: str = Field(min_length=1, max_length=1_000)
    price_credits: int | None = Field(default=None, gt=0, le=1_000_000)


class DeliverRequest(BaseModel):
    payload_b64: str


class DecisionRequest(BaseModel):
    accept: bool


class SellerSearchRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    required_capabilities: list[str] = Field(default_factory=list, max_length=64)
    required_tags: list[str] = Field(default_factory=list, max_length=64)
    min_reputation: int = 0
    max_price_credits: int | None = Field(default=None, gt=0, le=1_000_000)
    require_online: bool = False
    online_within_seconds: int = Field(default=120, ge=1, le=86_400)
    include_non_matching: bool = False
    limit: int = Field(default=20, ge=1, le=200)
