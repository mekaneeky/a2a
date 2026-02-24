from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.security import (
    canonical_request_message,
    decrypt_with_private_key,
    generate_encryption_keypair,
    generate_signing_keypair,
    sign_message,
)


class SDKError(RuntimeError):
    """Raised for SDK usage errors."""


@dataclass
class AgentIdentity:
    name: str
    sign_private: str
    sign_public: str
    encrypt_private: str
    encrypt_public: str
    agent_id: str | None = None


def create_local_agent(name: str) -> AgentIdentity:
    sign_private, sign_public = generate_signing_keypair()
    encrypt_private, encrypt_public = generate_encryption_keypair()
    return AgentIdentity(
        name=name,
        sign_private=sign_private,
        sign_public=sign_public,
        encrypt_private=encrypt_private,
        encrypt_public=encrypt_public,
    )


class AgentClient:
    def __init__(self, http_client: Any, identity: AgentIdentity):
        self._http_client = http_client
        self.identity = identity

    @classmethod
    def create(cls, http_client: Any, name: str) -> "AgentClient":
        return cls(http_client=http_client, identity=create_local_agent(name))

    def register(self, agent_card: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.identity.name,
            "public_sign_key": self.identity.sign_public,
            "public_encrypt_key": self.identity.encrypt_public,
        }
        if agent_card is not None:
            payload["agent_card"] = agent_card
        response = self._http_client.post("/agents/register", json=payload)
        response.raise_for_status()
        response_payload = response.json()
        self.identity.agent_id = response_payload["id"]
        return response_payload

    def signed_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ):
        if not self.identity.agent_id:
            raise SDKError("Agent is not registered")

        body = b""
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

        timestamp = str(int(time.time()))
        nonce = str(uuid4())
        message = canonical_request_message(method, path, timestamp, nonce, body)
        signature = sign_message(self.identity.sign_private, message)

        headers = {
            "x-agent-id": self.identity.agent_id,
            "x-timestamp": timestamp,
            "x-nonce": nonce,
            "x-signature": signature,
        }
        if payload is None:
            return self._http_client.request(method, path, headers=headers, params=params)

        return self._http_client.request(
            method,
            path,
            headers={**headers, "content-type": "application/json"},
            content=body,
            params=params,
        )

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        response = self.signed_request(method, path, payload, params=params)
        response.raise_for_status()
        return response.json()

    def faucet(self, amount: int) -> dict[str, Any]:
        return self._request_json("POST", "/ledger/faucet", {"amount": amount})

    def heartbeat(self) -> dict[str, Any]:
        return self._request_json("POST", "/agents/heartbeat")

    def balance(self) -> dict[str, Any]:
        return self._request_json("GET", "/ledger/balance")

    def create_listing(
        self,
        kind: str,
        sku: str,
        price_credits: int,
        description: str,
        rfq_enabled: bool = False,
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/listings",
            {
                "kind": kind,
                "sku": sku,
                "price_credits": price_credits,
                "description": description,
                "rfq_enabled": rfq_enabled,
            },
        )

    def match(self, demand_listing_id: str) -> dict[str, Any]:
        return self._request_json("POST", "/match", {"demand_listing_id": demand_listing_id})

    def list_listings(
        self,
        *,
        kind: str | None = None,
        sku: str | None = None,
        active: bool | None = True,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if kind is not None:
            params["kind"] = kind
        if sku is not None:
            params["sku"] = sku
        if active is not None:
            params["active"] = str(active).lower()
        return self._request_json("GET", "/listings", params=params)

    def handshake(
        self,
        demand_listing_id: str,
        offer_listing_id: str,
        terms: str,
        price_credits: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "demand_listing_id": demand_listing_id,
            "offer_listing_id": offer_listing_id,
            "terms": terms,
        }
        if price_credits is not None:
            payload["price_credits"] = price_credits
        return self._request_json("POST", "/contracts/handshake", payload)

    def activate_contract(self, contract_id: str) -> dict[str, Any]:
        return self._request_json("POST", f"/contracts/{contract_id}/activate")

    def deliver(self, contract_id: str, payload: bytes) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/contracts/{contract_id}/deliver",
            {"payload_b64": base64.b64encode(payload).decode("ascii")},
        )

    def get_artifact(self, contract_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/contracts/{contract_id}/artifact")

    def decrypt_artifact(self, artifact_payload: dict[str, Any]) -> bytes:
        try:
            ciphertext_b64 = artifact_payload["ciphertext_b64"]
            ciphertext = base64.b64decode(ciphertext_b64.encode("ascii"), validate=True)
            return decrypt_with_private_key(self.identity.encrypt_private, ciphertext)
        except (KeyError, ValueError) as exc:
            raise SDKError("Invalid artifact payload") from exc

    def decide(self, contract_id: str, accept: bool) -> dict[str, Any]:
        return self._request_json("POST", f"/contracts/{contract_id}/decision", {"accept": accept})

    def get_contract(self, contract_id: str) -> dict[str, Any]:
        return self._request_json("GET", f"/contracts/{contract_id}")

    def list_contracts(
        self,
        *,
        role: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if role not in {None, "buyer", "seller"}:
            raise SDKError("role must be buyer, seller, or None")
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if role is not None:
            params["role"] = role
        if status is not None:
            params["status"] = status
        return self._request_json("GET", "/contracts", params=params)

    def search_sellers(
        self,
        *,
        sku: str,
        required_capabilities: list[str] | None = None,
        required_tags: list[str] | None = None,
        min_reputation: int = 0,
        max_price_credits: int | None = None,
        require_online: bool = False,
        online_within_seconds: int = 120,
        include_non_matching: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "sku": sku,
            "required_capabilities": required_capabilities or [],
            "required_tags": required_tags or [],
            "min_reputation": min_reputation,
            "max_price_credits": max_price_credits,
            "require_online": require_online,
            "online_within_seconds": online_within_seconds,
            "include_non_matching": include_non_matching,
            "limit": limit,
        }
        return self._request_json("POST", "/agents/search", payload=payload)
