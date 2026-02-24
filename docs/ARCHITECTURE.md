# Architecture

## Overview

Single-process FastAPI app with SQLite persistence.

Modules:
- `app/main.py`: API routes + auth enforcement
- `app/models.py`: SQLModel entities
- `app/services/ledger.py`: double-entry ledger primitives
- `app/services/verifier.py`: SKU verification rules
- `app/security.py`: Ed25519 signing + sealed-box encryption
- `app/sdk.py`: SDK client for independently running buyer/seller agents

## Data Model

Core tables:
- `Agent`
- `AgentCard`
- `Nonce`
- `Listing`
- `Match`
- `Contract`
- `Artifact`
- `LedgerEntry`

## Control-Plane Flow

- Register agents and public keys
- Register seller-declared agent cards
- Query relay for open demands/offers
- Search sellers by card requirements
- Create demand/offer listings
- Match and handshake contract
- Reserve buyer credits to escrow
- Verify and relay encrypted artifact
- Buyer decision -> settle (payout/refund)
- Reputation updates

## Security Model (PoC)

- Signed requests with nonce replay guard
- Timestamp freshness check
- Artifact encrypted to buyer public key
- Hashes retained for integrity checks

## Limitations

- SQLite and local filesystem storage only
- No distributed queue/worker system
- No external identity/KMS
- No production abuse controls beyond basic replay protection
