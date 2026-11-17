"""FastAPI 엔드포인트 통합 테스트 (fastapi, httpx 설치 시에만 실행)."""
import os
import tempfile

import cv2
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from wm_core import synth  # noqa: E402


@pytest.fixture()
def client():
    with tempfile.TemporaryDirectory() as d:
        os.environ["WM_DATA_DIR"] = d
        from backend.app.config import Settings
        from backend.app.main import create_app
        from pathlib import Path
        settings = Settings(data_dir=Path(d), wm_key=os.urandom(32), hmac_key=os.urandom(32),
                            enc_key=os.urandom(32), admin_key="k")
        yield TestClient(create_app(settings))


H = {"X-API-Key": "k"}


def test_auth_required(client):
    assert client.get("/api/documents").status_code == 401
    assert client.get("/api/health").status_code == 200


def test_full_flow(client):
    page = cv2.imencode(".png", synth.document_page(1))[1].tobytes()
    r = client.post("/api/documents", headers=H, files={"file": ("doc.png", page, "image/png")},
                    data={"title": "테스트", "scenario": "document"})
    assert r.status_code == 200, r.text
    doc_id = r.json()["id"]
    r = client.post("/api/issue", headers=H, json={"document_id": doc_id, "subject_id": "E1", "subject_name": "kim"})
    assert r.status_code == 200 and r.headers["X-Token-Ref"]
    r2 = client.post("/api/trace", headers=H, files={"file": ("leak.png", r.content, "image/png")})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["status"] == "identified" and body["token_ref"] == r.headers["X-Token-Ref"]
    r3 = client.post(f"/api/traces/{body['trace_id']}/reveal", headers=H, json={"reason": "사고 조사 목적"})
    assert r3.status_code == 200 and r3.json()["subject"]["subject_id"] == "E1"
    v = client.get("/api/verify", headers=H).json()
    assert v["ok"] and v["ledger"]["entries"] == 1
