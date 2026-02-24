from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from threading import Event, Lock, Thread
from typing import Any, Callable

import httpx

from app.sdk import AgentClient


@dataclass(frozen=True)
class OfferSpec:
    sku: str
    price_credits: int
    description: str
    capabilities: tuple[str, ...] = ("verifiable_output",)
    tags: tuple[str, ...] = ("trusted",)


@dataclass(frozen=True)
class SearchRequest:
    sku: str
    max_price_credits: int
    required_capabilities: tuple[str, ...] = ("verifiable_output",)
    required_tags: tuple[str, ...] = ("trusted",)
    min_reputation: int = 0
    require_online: bool = True
    online_within_seconds: int = 120
    include_non_matching: bool = True
    limit: int = 20
    task_query: str = ""
    task_input: Any | None = None
    acceptance_criteria: str | None = None


@dataclass(frozen=True)
class SellerTask:
    contract_id: str
    sku: str
    buyer_id: str
    query: str
    task_input: Any | None
    terms: dict[str, Any]
    raw_terms: str


SearchEvaluator = Callable[[dict[str, Any]], dict[str, Any] | None]
TaskHandler = Callable[[SellerTask], Any]
ContractTermsBuilder = Callable[[SearchRequest, dict[str, Any]], str]


def _payload_for_sku(sku: str) -> bytes:
    if sku == "json_extraction":
        return b'{"records":[{"id":1,"source":"seller"}]}'
    if sku == "dataset_csv":
        return b"id,name\n1,alice\n"
    if sku == "code_patch_tests":
        return json.dumps({"patch": "diff --git a/a.py b/a.py", "tests_passed": True}).encode("utf-8")
    if sku == "api_call":
        return json.dumps({"status_code": 200, "response": {"ok": True}}).encode("utf-8")
    if sku == "compute_minutes":
        return json.dumps({"minutes_used": 1.0}).encode("utf-8")
    # Unknown SKU intentionally fails verification.
    return b"{}"


def _verify_payload_for_sku(sku: str, payload: bytes) -> bool:
    if sku == "json_extraction":
        parsed = json.loads(payload.decode("utf-8"))
        return isinstance(parsed, (dict, list))
    if sku == "dataset_csv":
        rows = payload.decode("utf-8").strip().splitlines()
        return len(rows) >= 2
    if sku == "code_patch_tests":
        parsed = json.loads(payload.decode("utf-8"))
        return bool(parsed.get("patch")) and parsed.get("tests_passed") is True
    if sku == "api_call":
        parsed = json.loads(payload.decode("utf-8"))
        code = parsed.get("status_code")
        return isinstance(code, int) and 200 <= code <= 299
    if sku == "compute_minutes":
        parsed = json.loads(payload.decode("utf-8"))
        minutes = parsed.get("minutes_used")
        return isinstance(minutes, (int, float)) and minutes > 0
    return False


def _encode_handler_output(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _payload_log_text(payload: bytes) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        return "base64:" + base64.b64encode(payload).decode("ascii")


def _parse_contract_terms(raw_terms: str) -> tuple[str, Any | None, dict[str, Any]]:
    query = raw_terms
    task_input: Any | None = None
    parsed_terms: dict[str, Any] = {}

    try:
        decoded = json.loads(raw_terms)
    except (TypeError, json.JSONDecodeError):
        return query, task_input, parsed_terms

    if isinstance(decoded, dict):
        parsed_terms = decoded
        query_value = decoded.get("query")
        if isinstance(query_value, str):
            query = query_value
        task_input = decoded.get("input")

    return query, task_input, parsed_terms


def _default_contract_terms(request: SearchRequest) -> str:
    return json.dumps(
        {
            "query": request.task_query,
            "input": request.task_input,
            "acceptance_criteria": request.acceptance_criteria or f"Deliver valid {request.sku}",
        },
        separators=(",", ":"),
        sort_keys=True,
    )


class SellerApp:
    def __init__(
        self,
        *,
        base_url: str,
        seller_name: str,
        offers: list[OfferSpec],
        task_handlers: dict[str, TaskHandler] | None = None,
        poll_seconds: float = 1.0,
        heartbeat_seconds: float = 15.0,
        max_retry_backoff_seconds: float = 60.0,
        log: Callable[[str], None] = print,
        stop_event: Event | None = None,
    ) -> None:
        self.base_url = base_url
        self.seller_name = seller_name
        self.offers = offers
        self.task_handlers = task_handlers or {}
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = max(1.0, heartbeat_seconds)
        self.max_retry_backoff_seconds = max(1.0, max_retry_backoff_seconds)
        self.log = log
        self.stop_event = stop_event

    def _agent_card(self) -> dict[str, Any]:
        skus = sorted({offer.sku for offer in self.offers})
        capabilities = sorted({cap for offer in self.offers for cap in offer.capabilities})
        tags = sorted({tag for offer in self.offers for tag in offer.tags})
        return {
            "skus": skus,
            "capabilities": capabilities,
            "tags": tags,
            "description": "Auto seller catalog",
        }

    def _payload_for_contract(self, contract: dict[str, Any]) -> bytes:
        handler = self.task_handlers.get(contract["sku"])
        raw_terms = contract.get("terms", "")
        query, task_input, parsed_terms = _parse_contract_terms(raw_terms)

        if handler is None:
            return _payload_for_sku(contract["sku"])

        task = SellerTask(
            contract_id=contract["id"],
            sku=contract["sku"],
            buyer_id=contract["buyer_id"],
            query=query,
            task_input=task_input,
            terms=parsed_terms,
            raw_terms=raw_terms,
        )
        return _encode_handler_output(handler(task))

    def _deliver_contract(
        self,
        *,
        identity: Any,
        contract: dict[str, Any],
        in_flight_contracts: set[str],
        delivered_contracts: set[str],
        retry_state: dict[str, tuple[int, float]],
        lock: Lock,
    ) -> None:
        contract_id = contract["id"]
        try:
            payload = self._payload_for_contract(contract)
            self.log(f"[seller] payload contract={contract_id} body={_payload_log_text(payload)}")

            with httpx.Client(base_url=self.base_url, timeout=20.0) as delivery_http:
                seller = AgentClient(delivery_http, identity)
                delivery = seller.deliver(contract_id, payload)

            self.log(f"[seller] delivered contract={contract_id} status={delivery['status']}")
            with lock:
                delivered_contracts.add(contract_id)
                retry_state.pop(contract_id, None)
        except Exception as exc:
            with lock:
                previous_attempts, _ = retry_state.get(contract_id, (0, 0.0))
                attempts = previous_attempts + 1
                base_delay = max(1.0, self.poll_seconds)
                delay = min(self.max_retry_backoff_seconds, base_delay * (2 ** (attempts - 1)))
                next_retry_at = time.monotonic() + delay
                retry_state[contract_id] = (attempts, next_retry_at)
            self.log(
                f"[seller] delivery handler error contract={contract_id}: {exc} "
                f"(retry_in_seconds={delay:.1f}, attempt={attempts})"
            )
        finally:
            with lock:
                in_flight_contracts.discard(contract_id)

    def run(self) -> None:
        offer_by_sku = {offer.sku: offer for offer in self.offers}
        processed_demands: set[str] = set()
        delivered_contracts: set[str] = set()
        in_flight_contracts: set[str] = set()
        retry_state: dict[str, tuple[int, float]] = {}
        worker_threads: list[Thread] = []
        lock = Lock()
        last_heartbeat = 0.0

        with httpx.Client(base_url=self.base_url, timeout=20.0) as http:
            seller = AgentClient.create(http, self.seller_name)
            reg = seller.register(agent_card=self._agent_card())
            self.log(f"[seller] registered id={reg['id']}")

            while True:
                if self.stop_event is not None and self.stop_event.is_set():
                    break
                try:
                    now = time.monotonic()
                    if now - last_heartbeat >= self.heartbeat_seconds:
                        seller.heartbeat()
                        last_heartbeat = now

                    demands = seller.list_listings(kind="demand", active=True, limit=100)
                    for demand in demands:
                        demand_id = demand["id"]
                        sku = demand["sku"]
                        offer_spec = offer_by_sku.get(sku)
                        if offer_spec is None or demand_id in processed_demands:
                            continue

                        processed_demands.add(demand_id)
                        offer = seller.create_listing(
                            kind="offer",
                            sku=sku,
                            price_credits=offer_spec.price_credits,
                            description=f"{offer_spec.description} (for demand {demand_id})",
                        )
                        self.log(f"[seller] offered listing={offer['id']} for demand={demand_id}")

                    active_contracts = seller.list_contracts(role="seller", status="active", limit=100)
                    for contract in active_contracts:
                        contract_id = contract["id"]
                        now = time.monotonic()
                        with lock:
                            if contract_id in delivered_contracts or contract_id in in_flight_contracts:
                                continue
                            _attempts, next_retry_at = retry_state.get(contract_id, (0, 0.0))
                            if now < next_retry_at:
                                continue
                            in_flight_contracts.add(contract_id)

                        self.log(f"[seller] processing contract={contract_id} sku={contract['sku']}")
                        worker = Thread(
                            target=self._deliver_contract,
                            kwargs={
                                "identity": seller.identity,
                                "contract": contract,
                                "in_flight_contracts": in_flight_contracts,
                                "delivered_contracts": delivered_contracts,
                                "retry_state": retry_state,
                                "lock": lock,
                            },
                            daemon=True,
                        )
                        worker.start()
                        worker_threads.append(worker)

                    worker_threads = [thread for thread in worker_threads if thread.is_alive()]
                except httpx.HTTPError as exc:
                    if self.stop_event is None or not self.stop_event.is_set():
                        self.log(f"[seller] relay error: {exc}")
                time.sleep(self.poll_seconds)

        for worker in worker_threads:
            worker.join(timeout=1)


class BuyerApp:
    def __init__(
        self,
        *,
        base_url: str,
        buyer_name: str,
        faucet_amount: int = 100,
        poll_seconds: float = 1.0,
        log: Callable[[str], None] = print,
    ) -> None:
        self.base_url = base_url
        self.buyer_name = buyer_name
        self.faucet_amount = faucet_amount
        self.poll_seconds = poll_seconds
        self.log = log

    def search(self, buyer: AgentClient, request: SearchRequest) -> dict[str, Any]:
        return buyer.search_sellers(
            sku=request.sku,
            required_capabilities=list(request.required_capabilities),
            required_tags=list(request.required_tags),
            min_reputation=request.min_reputation,
            max_price_credits=request.max_price_credits,
            require_online=request.require_online,
            online_within_seconds=request.online_within_seconds,
            include_non_matching=request.include_non_matching,
            limit=request.limit,
        )

    def _prefer_current_demand_offers(
        self,
        search_response: dict[str, Any],
        *,
        demand_id: str,
    ) -> dict[str, Any]:
        results = search_response.get("results", [])
        if not isinstance(results, list):
            return search_response

        marker = f"(for demand {demand_id})"
        preferred = [
            item
            for item in results
            if marker in str(item.get("offer", {}).get("description", ""))
        ]
        if not preferred:
            return search_response

        if len(preferred) != len(results):
            self.log(
                f"[buyer] filtered search results for current demand={demand_id} "
                f"kept={len(preferred)} dropped={len(results) - len(preferred)}"
            )
        return {
            **search_response,
            "results": preferred,
        }

    def _wait_for_contract_delivery(
        self,
        *,
        buyer: AgentClient,
        contract_id: str,
        sku: str,
        max_wait_seconds: float | None = None,
        status_log_seconds: float = 30.0,
    ) -> None:
        started = time.monotonic()
        last_wait_log = started
        wait_log_interval = max(self.poll_seconds, status_log_seconds)

        while True:
            state = buyer.get_contract(contract_id)
            if state["status"] == "delivered":
                artifact = buyer.get_artifact(contract_id)
                plaintext = buyer.decrypt_artifact(artifact)
                self.log(f"[buyer] decrypted payload contract={contract_id} body={_payload_log_text(plaintext)}")
                accepted = _verify_payload_for_sku(sku, plaintext)
                decision = buyer.decide(contract_id, accept=accepted)
                self.log(f"[buyer] decision={decision['outcome']} status={decision['status']}")
                return
            if state["status"] == "settled":
                self.log("[buyer] contract already settled before decision")
                return

            now = time.monotonic()
            elapsed = now - started
            if max_wait_seconds is not None and elapsed > max_wait_seconds:
                raise TimeoutError(
                    f"timed out waiting for contract={contract_id} after {int(elapsed)}s"
                )
            if now - last_wait_log >= wait_log_interval:
                self.log(
                    f"[buyer] waiting for delivery contract={contract_id} "
                    f"status={state['status']} elapsed_seconds={int(elapsed)}"
                )
                last_wait_log = now

            time.sleep(self.poll_seconds)

    def run_once(
        self,
        search_request: SearchRequest,
        evaluate_search_results: SearchEvaluator,
        build_contract_terms: ContractTermsBuilder | None = None,
        max_wait_seconds: float | None = None,
        status_log_seconds: float = 30.0,
    ) -> None:
        with httpx.Client(base_url=self.base_url, timeout=20.0) as http:
            buyer = AgentClient.create(http, self.buyer_name)
            reg = buyer.register()
            self.log(f"[buyer] registered id={reg['id']}")

            buyer.faucet(self.faucet_amount)
            self.log(f"[buyer] faucet amount={self.faucet_amount}")

            demand = buyer.create_listing(
                kind="demand",
                sku=search_request.sku,
                price_credits=search_request.max_price_credits,
                description=f"Need {search_request.sku}",
            )
            self.log(f"[buyer] demand listing={demand['id']} sku={search_request.sku}")

            selected: dict[str, Any] | None = None
            while selected is None:
                search = self.search(buyer, search_request)
                demand_scoped_search = self._prefer_current_demand_offers(
                    search,
                    demand_id=demand["id"],
                )
                selected = evaluate_search_results(demand_scoped_search)
                if selected is None:
                    self.log("[buyer] waiting for search results")
                    time.sleep(self.poll_seconds)

            offer_id = selected["offer"]["listing_id"]
            seller_name = selected["seller"]["name"]
            self.log(f"[buyer] selected seller={seller_name} offer={offer_id}")

            terms = (
                _default_contract_terms(search_request)
                if build_contract_terms is None
                else build_contract_terms(search_request, selected)
            )
            if not isinstance(terms, str) or not terms.strip():
                raise ValueError("Contract terms must be a non-empty string")

            contract = buyer.handshake(demand["id"], offer_id, terms)
            contract_id = contract["id"]
            buyer.activate_contract(contract_id)
            self.log(f"[buyer] contract active id={contract_id}")

            self._wait_for_contract_delivery(
                buyer=buyer,
                contract_id=contract_id,
                sku=search_request.sku,
                max_wait_seconds=max_wait_seconds,
                status_log_seconds=status_log_seconds,
            )

            self.log(f"[buyer] final balance={buyer.balance()['balance']}")
