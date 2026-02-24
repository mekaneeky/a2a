from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from enum import StrEnum


class SkuType(StrEnum):
    DATASET_CSV = "dataset_csv"
    JSON_EXTRACTION = "json_extraction"
    CODE_PATCH_TESTS = "code_patch_tests"
    API_CALL = "api_call"
    COMPUTE_MINUTES = "compute_minutes"


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    reason: str


def _parse_json(payload: bytes) -> object:
    return json.loads(payload.decode("utf-8"))


def verify_payload(sku: SkuType, payload: bytes) -> VerificationResult:
    if sku == SkuType.DATASET_CSV:
        text = payload.decode("utf-8")
        rows = list(csv.reader(io.StringIO(text)))
        if len(rows) < 2:
            return VerificationResult(ok=False, reason="CSV needs header and at least one data row")
        if not rows[0]:
            return VerificationResult(ok=False, reason="CSV header is empty")
        return VerificationResult(ok=True, reason="ok")

    if sku == SkuType.JSON_EXTRACTION:
        parsed = _parse_json(payload)
        if isinstance(parsed, (dict, list)):
            return VerificationResult(ok=True, reason="ok")
        return VerificationResult(ok=False, reason="JSON must be object or array")

    if sku == SkuType.CODE_PATCH_TESTS:
        parsed = _parse_json(payload)
        if not isinstance(parsed, dict):
            return VerificationResult(ok=False, reason="Payload must be a JSON object")
        patch = parsed.get("patch")
        tests_passed = parsed.get("tests_passed")
        if isinstance(patch, str) and patch.strip() and tests_passed is True:
            return VerificationResult(ok=True, reason="ok")
        return VerificationResult(ok=False, reason="Patch text required and tests_passed must be true")

    if sku == SkuType.API_CALL:
        parsed = _parse_json(payload)
        if not isinstance(parsed, dict):
            return VerificationResult(ok=False, reason="Payload must be a JSON object")
        status_code = parsed.get("status_code")
        if isinstance(status_code, int) and 200 <= status_code <= 299:
            return VerificationResult(ok=True, reason="ok")
        return VerificationResult(ok=False, reason="status_code must be 2xx")

    if sku == SkuType.COMPUTE_MINUTES:
        parsed = _parse_json(payload)
        if not isinstance(parsed, dict):
            return VerificationResult(ok=False, reason="Payload must be a JSON object")
        minutes_used = parsed.get("minutes_used")
        if isinstance(minutes_used, (int, float)) and minutes_used > 0:
            return VerificationResult(ok=True, reason="ok")
        return VerificationResult(ok=False, reason="minutes_used must be positive")

    raise ValueError(f"Unsupported sku: {sku}")
