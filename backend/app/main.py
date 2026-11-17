"""FastAPI REST API + 관제 UI 정적 파일 제공.

논문의 세 핵심 API:
  POST /api/issue   가명 토큰 발급 + 워터마크 배포본 생성
  POST /api/trace   유출 의심 촬영본 역추적 (그림 2 판정 절차)
  GET  /api/verify  발급 원장·감사 로그 해시 체인 무결성 검증
그 밖에 원본 등록, 이력 조회, 신원 열람(사유 기록) 같은 관리용 API 가 있다.

실행: uvicorn backend.app.main:app --reload   (프로젝트 루트에서)
"""

import hmac
import json
from typing import Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import ROOT, Settings, load_settings
from .services import ForensicService, ServiceError

MAX_UPLOAD = 50 * 1024 * 1024


class IssueRequest(BaseModel):
    document_id: str
    subject_id: str               # 사번 또는 판매글 ID
    subject_name: str = ""
    department: str = ""
    note: str = ""


class RevealRequest(BaseModel):
    reason: str


def _dump(model: BaseModel) -> dict:
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or load_settings()
    svc = ForensicService(settings)
    app = FastAPI(title="가명 토큰 기반 문서 포렌식 마킹 시스템", version="0.2.0")
    app.state.service = svc

    def admin(x_api_key: str = Header(default="")) -> str:
        if not hmac.compare_digest(x_api_key.encode(), settings.admin_key.encode()):
            raise HTTPException(status_code=401, detail="API 키가 올바르지 않습니다.")
        return "admin"

    def run(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ServiceError as e:
            raise HTTPException(status_code=e.status, detail=e.message)

    def read_upload(file: UploadFile) -> bytes:
        data = file.file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(status_code=413, detail="파일이 너무 큽니다 (최대 50MB).")
        if not data:
            raise HTTPException(status_code=400, detail="빈 파일입니다.")
        return data

    @app.get("/api/health")
    def health():
        from . import pdf_utils
        from wm_core.dwt import HAVE_PYWT
        return {"status": "ok", "pdf": pdf_utils.HAVE_PDF, "pdf_backend": pdf_utils.BACKEND, "pywavelets": HAVE_PYWT}

    # ---- 문서 등록 / 발급 ----
    @app.post("/api/documents")
    def register_document(file: UploadFile = File(...), title: str = Form(""), scenario: str = Form("document"),
                          actor: str = Depends(admin)):
        return run(svc.register_document, file.filename or "upload", read_upload(file), title, scenario, actor)

    @app.get("/api/documents")
    def list_documents(actor: str = Depends(admin)):
        return svc.list_documents()

    @app.post("/api/issue")
    def issue(body: IssueRequest, actor: str = Depends(admin)):
        subject = _dump(body)
        document_id = subject.pop("document_id")
        name, data, mime, token_ref = run(svc.issue, document_id, subject, actor)
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "X-Token-Ref": token_ref,
            "Access-Control-Expose-Headers": "X-Token-Ref, Content-Disposition",
        }
        return Response(content=data, media_type=mime, headers=headers)

    @app.get("/api/tokens")
    def list_tokens(actor: str = Depends(admin)):
        return svc.list_tokens()

    # ---- 역추적 ----
    @app.post("/api/trace")
    def trace(file: UploadFile = File(...), corners: str = Form(""), deep: bool = Form(False),
              actor: str = Depends(admin)):
        pts = None
        if corners.strip():
            try:
                pts = json.loads(corners)
            except ValueError:
                raise HTTPException(status_code=400, detail="corners 는 JSON 배열이어야 합니다.")
        return run(svc.trace, read_upload(file), pts, deep, actor)

    @app.get("/api/traces")
    def list_traces(actor: str = Depends(admin)):
        return svc.list_traces()

    @app.post("/api/traces/{trace_id}/reveal")
    def reveal(trace_id: str, body: RevealRequest, actor: str = Depends(admin)):
        return run(svc.reveal, trace_id, body.reason, actor)

    # ---- 감사 로그 ----
    @app.get("/api/audit")
    def list_audit(actor: str = Depends(admin)):
        return svc.list_audit()

    @app.get("/api/verify")
    def verify(actor: str = Depends(admin)):
        result = svc.verify()
        svc._audit(actor, "chain.verify", {"ok": result["ok"]})
        return result

    frontend = ROOT / "frontend"
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=str(frontend), html=True), name="ui")
    return app


app = create_app()
