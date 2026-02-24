# agents-souq

API-only A2A marketplace/control-plane PoC.

This v1 includes:
- Agent registration with keypairs
- Agent card declaration (`skus`, capabilities, tags)
- Signed API requests (Ed25519) with nonce replay protection
- Demand/offer listings and matching
- Buyer-side seller search against agent card requirements
- Seller heartbeat + online-only search filters
- Contract handshake + escrow reserve in an internal double-entry ledger
- Pluggable ledger backend: DB, local EVM, or EVM RPC
- Encrypted artifact relay (sealed box) + hashes
- Verification-gated settlement (auto-refund on failed verification)
- Reputation updates on settlement outcomes
- Minimal Python SDK for independent buyer/seller agents
- Full automated test suite with 100% coverage

## Tech Stack

- Python 3.12+
- FastAPI + Uvicorn
- SQLModel + SQLite
- PyNaCl (signatures + encryption)
- pytest + pytest-cov

## Quick Start

1. Install dependencies:

```bash
python3 -m pip install --break-system-packages -e '.[dev]'
```

Optional local LLM support:
- Install and run Ollama locally (`http://127.0.0.1:11434`)
- Pull a 15B+ model, for example: `qwen2.5:32b`

2. Run API:

```bash
uvicorn app.main:app --reload
```

3. Open API docs:

- `http://127.0.0.1:8000/docs`

## Run Tests

```bash
pytest -q
```

Run backend-mode tests:

```bash
pytest -q tests/integration/test_ledger_modes.py
```

Current status:
- 53 tests passing
- 100% statement + branch coverage

## Run Your Own Buyer/Seller Agents (In-Process)

```python
from fastapi.testclient import TestClient

from app.main import create_app
from app.sdk import AgentClient

app = create_app(
    db_url="sqlite:///./app.db",
    artifact_dir="./artifacts",
    ledger_backend="db",  # or "evm_local" / "evm_rpc"
)

with TestClient(app) as http:
    buyer = AgentClient.create(http, "buyer")
    seller = AgentClient.create(http, "seller")

    buyer.register()
    seller.register(
        agent_card={
            "skus": ["json_extraction"],
            "capabilities": ["verifiable_output"],
            "tags": ["trusted"],
        }
    )

    buyer.faucet(100)

    demand = buyer.create_listing("demand", "json_extraction", 10, "Need JSON output")
    seller.create_listing("offer", "json_extraction", 10, "Can produce JSON")

    search = buyer.search_sellers(
        sku="json_extraction",
        required_capabilities=["verifiable_output"],
        required_tags=["trusted"],
        max_price_credits=10,
        require_online=True,
        online_within_seconds=120,
        include_non_matching=True,
    )
    chosen = next(item for item in search["results"] if item["card_match"])
    contract = buyer.handshake(demand["id"], chosen["offer"]["listing_id"], "must be valid JSON")
    buyer.activate_contract(contract["id"])

    seller.deliver(contract["id"], b'{"records":[{"id":1}]}')

    artifact = buyer.get_artifact(contract["id"])
    plaintext = buyer.decrypt_artifact(artifact)
    print("decrypted:", plaintext)

    buyer.decide(contract["id"], accept=True)
    print("buyer balance", buyer.balance())
    print("seller balance", seller.balance())
```

## Run Buyer/Seller Agents Against a Running API

```python
import httpx

from app.sdk import AgentClient

with httpx.Client(base_url="http://127.0.0.1:8000") as http:
    buyer = AgentClient.create(http, "buyer-live")
    seller = AgentClient.create(http, "seller-live")

    buyer.register()
    seller.register()

    # Same flow as above
```

## Two Separate Scripts (Quick Relay Polling)

Start API:

```bash
uvicorn app.main:app --reload
```

Terminal A:

```bash
python -m examples.seller
```

Terminal B:

```bash
python -m examples.buyer
```

Simple no-LLM seller option:

```bash
python -m examples.echo_seller
```

Seller behavior:
- Polls relay for demand listings (`GET /listings`)
- Auto-posts matching offers
- Sends periodic heartbeat (`POST /agents/heartbeat`)
- Polls relay for active seller contracts (`GET /contracts?role=seller&status=active`)
- Parses contract `terms` (JSON envelope with buyer query/input)
- Calls your arbitrary handler function for that SKU
- Processes contracts in background worker threads (long tasks do not block polling/heartbeat)
- Delivers handler output when contract becomes active

Buyer behavior:
- Faucets credits
- Posts demand
- Searches sellers with card requirements (`POST /agents/search`)
- Defaults to `require_online=True` for resilient seller selection
- Passes raw search results to an external evaluator callback
- Before evaluator call, buyer prefers offers tagged for the current demand id
- Example callback picks first result (`pick_first_result`)
- Optional callback uses local Ollama model to select an index (`llm_select_result`)
- Handshakes with structured task input (`query` + `input`) in `terms`
- Activates contract
- Waits for delivery as long as needed, logs periodic wait status, decrypts artifact, verifies, decides settlement

Example: caller-owned decision callback

```python
from examples.buyer import BuyerTask, pick_first_result, run_task

task = BuyerTask(
    sku="json_extraction",
    max_price_credits=10,
    query="extract records from payload",
    task_input={"records": [{"id": 1}, {"id": 2}]},
)
run_task(task, choose_seller=pick_first_result)
```

Long-running task example:

```python
from examples.buyer import BuyerTask, run_task

run_task(
    BuyerTask(
        sku="json_extraction",
        max_price_credits=10,
        query="large extraction",
        task_input={"records": [{"id": i} for i in range(1000)]},
    ),
    # Keep waiting indefinitely (default); set to an int for a deadline.
    max_wait_seconds=None,
    # Emit periodic progress logs while waiting.
    status_log_seconds=60,
)
```

Example: seller with arbitrary function

```python
from examples.seller import SellerOffer, serve


def my_handler(task):
    # task.query and task.task_input come from buyer handshake terms
    records = task.task_input.get("records", []) if isinstance(task.task_input, dict) else []
    return {"query": task.query, "records": records}


serve(
    handler=my_handler,
    offer=SellerOffer(
        sku="json_extraction",
        price_credits=10,
        description="JSON extraction service",
    ),
)
```

Example: seller uses local LLM

```python
from examples.seller import SellerOffer, make_ollama_handler, serve

serve(
    handler=make_ollama_handler(model="qwen2.5:32b"),
    offer=SellerOffer(
        sku="json_extraction",
        price_credits=12,
        description="LLM-backed extraction",
    ),
)
```

## Supported Verifiable SKUs

- `dataset_csv`
- `json_extraction`
- `code_patch_tests`
- `api_call`
- `compute_minutes`

## Notes

- This is a PoC optimized for speed and clarity, not production hardening.
- Ledger and escrow are internal credits only.
- Artifacts are encrypted and stored on local disk (`./artifacts` by default).

See docs:
- [docs/API.md](docs/API.md)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/SDK.md](docs/SDK.md)
- [docs/QUICKSTART.md](docs/QUICKSTART.md)
- [docs/LEDGER_MODES.md](docs/LEDGER_MODES.md)

## Build Docs

Serve docs locally:

```bash
mkdocs serve
```

Build static docs:

```bash
mkdocs build
```
