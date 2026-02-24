import json

import pytest

from app.services.verifier import SkuType, verify_payload


def test_dataset_csv_verifier_passes_for_header_and_row() -> None:
    payload = b"id,name\n1,alice\n"
    result = verify_payload(SkuType.DATASET_CSV, payload)
    assert result.ok


def test_dataset_csv_verifier_fails_without_data_rows() -> None:
    payload = b"id,name\n"
    result = verify_payload(SkuType.DATASET_CSV, payload)
    assert not result.ok


def test_dataset_csv_verifier_fails_for_empty_header() -> None:
    payload = b"\n1,alice\n"
    result = verify_payload(SkuType.DATASET_CSV, payload)
    assert not result.ok


def test_json_extraction_verifier_accepts_json_object() -> None:
    payload = json.dumps({"items": [1, 2, 3]}).encode("utf-8")
    result = verify_payload(SkuType.JSON_EXTRACTION, payload)
    assert result.ok


def test_json_extraction_verifier_rejects_non_collection() -> None:
    payload = json.dumps(3).encode("utf-8")
    result = verify_payload(SkuType.JSON_EXTRACTION, payload)
    assert not result.ok


def test_code_patch_tests_verifier_requires_tests_passed_true() -> None:
    payload = json.dumps({"patch": "diff --git ...", "tests_passed": True}).encode("utf-8")
    result = verify_payload(SkuType.CODE_PATCH_TESTS, payload)
    assert result.ok


def test_code_patch_tests_verifier_rejects_false_tests() -> None:
    payload = json.dumps({"patch": "diff --git ...", "tests_passed": False}).encode("utf-8")
    result = verify_payload(SkuType.CODE_PATCH_TESTS, payload)
    assert not result.ok


def test_code_patch_tests_verifier_rejects_non_object_payload() -> None:
    payload = json.dumps(["not", "object"]).encode("utf-8")
    result = verify_payload(SkuType.CODE_PATCH_TESTS, payload)
    assert not result.ok


def test_api_call_verifier_requires_2xx_status() -> None:
    payload_ok = json.dumps({"status_code": 204, "response": {"ok": True}}).encode("utf-8")
    payload_bad = json.dumps({"status_code": 500, "response": {"ok": False}}).encode("utf-8")
    assert verify_payload(SkuType.API_CALL, payload_ok).ok
    assert not verify_payload(SkuType.API_CALL, payload_bad).ok


def test_api_call_verifier_rejects_non_object_payload() -> None:
    payload = json.dumps(["bad"]).encode("utf-8")
    assert not verify_payload(SkuType.API_CALL, payload).ok


def test_compute_minutes_verifier_requires_positive_minutes() -> None:
    payload_ok = json.dumps({"minutes_used": 2.5}).encode("utf-8")
    payload_bad = json.dumps({"minutes_used": 0}).encode("utf-8")
    assert verify_payload(SkuType.COMPUTE_MINUTES, payload_ok).ok
    assert not verify_payload(SkuType.COMPUTE_MINUTES, payload_bad).ok


def test_compute_minutes_verifier_rejects_non_object_payload() -> None:
    payload = json.dumps("oops").encode("utf-8")
    assert not verify_payload(SkuType.COMPUTE_MINUTES, payload).ok


def test_invalid_json_bubbles_up_error() -> None:
    with pytest.raises(json.JSONDecodeError):
        verify_payload(SkuType.JSON_EXTRACTION, b"not-json")


def test_unknown_sku_raises_value_error() -> None:
    with pytest.raises(ValueError):
        verify_payload("unknown", b"x")  # type: ignore[arg-type]
