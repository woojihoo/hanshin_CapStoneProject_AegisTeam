"""서비스 계층 테스트: 그림 2 판정 절차, AES-GCM 저장, 해시 체인 원장, 감사 로그."""
import os
import sqlite3
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from backend.app.config import Settings
from backend.app.services import ForensicService, ServiceError
from wm_core import attacks as A
from wm_core import synth


def make_service(tmp: Path) -> ForensicService:
    return ForensicService(Settings(data_dir=tmp, wm_key=os.urandom(32), hmac_key=os.urandom(32),
                                    enc_key=os.urandom(32), admin_key="test-key"))


def png(img) -> bytes:
    return cv2.imencode(".png", img)[1].tobytes()


def decode(data: bytes):
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


@pytest.fixture()
def svc():
    with tempfile.TemporaryDirectory() as d:
        yield make_service(Path(d))


def test_issue_trace_reveal_flow(svc):
    doc = svc.register_document("기밀보고서.png", png(synth.document_page(1)), "기밀 보고서", "document")
    subjects = [{"subject_id": f"E{i:04d}", "subject_name": f"직원{i}", "department": "보안팀"} for i in range(3)]
    outs = [svc.issue(doc["id"], s) for s in subjects]
    assert len({o[3] for o in outs}) == 3  # 서로 다른 토큰

    shot, _ = A.screen_shoot(decode(outs[1][1]), seed=5)
    result = svc.trace(cv2.imencode(".jpg", shot)[1].tobytes())
    assert result["status"] == "identified" and result["token_ref"] == outs[1][3]
    assert result["detail"]["steps"] == {"bch_decoded": True, "issued": True, "chain_intact": True}
    assert "subject" not in result  # 신원은 별도 열람 절차로만

    with pytest.raises(ServiceError):
        svc.reveal(result["trace_id"], "")
    revealed = svc.reveal(result["trace_id"], "유출 사고 조사 #2026-01")
    assert revealed["subject"]["subject_id"] == "E0001"
    actions = [a["action"] for a in svc.list_audit()]
    assert {"identity.reveal", "token.issue", "trace.run"} <= set(actions)
    assert svc.verify()["ok"]


def test_unmarked_image_is_undecidable(svc):
    svc.register_document("a.png", png(synth.document_page(2)))
    result = svc.trace(png(synth.document_page(9)))
    assert result["status"] == "undecidable" and result["token_ref"] is None
    assert result["detail"]["steps"]["bch_decoded"] is False
    with pytest.raises(ServiceError):
        svc.reveal(result["trace_id"], "사유를 적습니다")


def test_token_from_other_system_is_unissued(svc):
    """BCH 복호는 되지만 발급 원장에 없는 토큰 -> 미발급 토큰(오탐 처리)."""
    doc = svc.register_document("a.png", png(synth.document_page(2)))
    svc.issue(doc["id"], {"subject_id": "E1"})
    foreign = svc.engine.embed(synth.document_page(2), b"\x12\x34\x56")  # 같은 키, 발급되지 않은 토큰
    result = svc.trace(png(foreign))
    assert result["status"] == "unissued"
    assert result["detail"]["steps"] == {"bch_decoded": True, "issued": False, "chain_intact": None}


def test_tampered_ledger_is_flagged(svc):
    doc = svc.register_document("a.png", png(synth.document_page(2)))
    _, data, _, ref = svc.issue(doc["id"], {"subject_id": "E1"})
    svc.issue(doc["id"], {"subject_id": "E2"})
    con = sqlite3.connect(str(svc.vault_db))
    con.execute("UPDATE ledger SET document_id='forged' WHERE id=2")  # 다른 행을 몰래 수정
    con.commit()
    con.close()
    assert not svc.verify()["ledger"]["ok"]
    result = svc.trace(data)
    assert result["status"] == "tampered" and result["detail"]["steps"]["chain_intact"] is False
    with pytest.raises(ServiceError):
        svc.reveal(result["trace_id"], "조사 목적 열람")


def test_identity_is_encrypted_and_token_not_stored(svc):
    doc = svc.register_document("a.png", png(synth.document_page(2)))
    svc.issue(doc["id"], {"subject_id": "E7777", "subject_name": "홍길동"})
    raw = svc.vault_db.read_bytes() + svc.app_db.read_bytes()
    assert b"E7777" not in raw and "홍길동".encode() not in raw
    con = sqlite3.connect(str(svc.vault_db))
    cols = [r[1] for r in con.execute("PRAGMA table_info(ledger)")]
    con.close()
    assert "token" not in cols and "token_index" in cols  # 토큰 원문은 저장하지 않는다


def test_ciphertext_tamper_breaks_chain_and_gcm(svc):
    doc = svc.register_document("a.png", png(synth.document_page(2)))
    svc.issue(doc["id"], {"subject_id": "E1"})
    con = sqlite3.connect(str(svc.vault_db))
    enc = bytearray(con.execute("SELECT enc_subject FROM ledger WHERE id=1").fetchone()[0])
    enc[-1] ^= 1
    con.execute("UPDATE ledger SET enc_subject=? WHERE id=1", (bytes(enc),))
    con.commit()
    con.close()
    assert svc.verify_ledger() == {"ok": False, "entries": 1, "broken_at": 1}


def test_audit_chain_detects_tampering(svc):
    doc = svc.register_document("a.png", png(synth.document_page(2)))
    svc.issue(doc["id"], {"subject_id": "E1"})
    assert svc.verify_audit()["ok"]
    con = sqlite3.connect(str(svc.app_db))
    con.execute("UPDATE audit_log SET actor='attacker' WHERE id=1")
    con.commit()
    con.close()
    assert not svc.verify_audit()["ok"]


def test_c2c_photo_scenario(svc):
    doc = svc.register_document("item.png", png(synth.product_photo(3)), "중고 카메라", "c2c")
    _, data, _, ref = svc.issue(doc["id"], {"subject_id": "listing-991", "note": "판매글"})
    stolen = cv2.imencode(".jpg", A.crop(decode(data), 0.6, 2), [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()
    result = svc.trace(stolen)
    assert result["status"] == "identified" and result["token_ref"] == ref


def test_validation_errors(svc):
    with pytest.raises(ServiceError):
        svc.register_document("a.txt", b"hello")
    with pytest.raises(ServiceError):
        svc.register_document("a.png", b"not an image")
    doc = svc.register_document("a.png", png(synth.document_page(2)))
    with pytest.raises(ServiceError):
        svc.issue(doc["id"], {"subject_id": " "})
    with pytest.raises(ServiceError):
        svc.issue("nope", {"subject_id": "E1"})
