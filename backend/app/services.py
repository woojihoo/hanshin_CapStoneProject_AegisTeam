"""가명 토큰 발급, 워터마크 배포본 생성, 역추적·검증, 감사 로그 (프레임워크 비의존 서비스 계층).

논문 그림 2 의 판정 절차:
  촬영본 -> 원근 보정 -> 비트 추출·다수결 -> BCH 복호 성공?   아니오 -> 판정 불가 (재촬영)
                                           -> 발급 토큰인가?   아니오 -> 미발급 토큰 (오탐 처리)
                                           -> 해시 체인 무결?  아니오 -> 기록 위·변조 경고
                                           -> 배포 대상 식별 리포트
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

import cv2
import numpy as np
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from wm_core import TOKEN_BYTES, WatermarkEngine

from . import pdf_utils
from .config import Settings
from .db import APP_SCHEMA, VAULT_SCHEMA, connect, init_db

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
GENESIS = "0" * 64

STATUS_LABELS = {
    "identified": "배포 대상 식별",
    "undecidable": "판정 불가 (재촬영)",
    "unissued": "미발급 토큰 (오탐 처리)",
    "tampered": "기록 위·변조 경고",
}


class ServiceError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _ledger_hash(prev: str, row_id: int, token_index: str, enc_subject: bytes, document_id: str, issued_at: str) -> str:
    return _sha256(prev, str(row_id), token_index, enc_subject.hex(), document_id, issued_at)


def _aad(token_index: str, document_id: str, issued_at: str) -> bytes:
    return f"{token_index}|{document_id}|{issued_at}".encode("utf-8")


class ForensicService:
    def __init__(self, settings: Settings):
        self.s = settings
        self.doc_dir = settings.data_dir / "documents"
        self.app_db = settings.data_dir / "app.db"
        self.vault_db = settings.data_dir / "vault.db"
        self.doc_dir.mkdir(parents=True, exist_ok=True)
        init_db(self.app_db, APP_SCHEMA)
        init_db(self.vault_db, VAULT_SCHEMA)
        self.engine = WatermarkEngine(settings.wm_key)
        self._aes = AESGCM(settings.enc_key)
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 내부 도구
    def token_index(self, token: bytes) -> str:
        """토큰의 HMAC 색인. 원장은 토큰 원문 대신 이 값으로만 조회한다."""
        return hmac.new(self.s.hmac_key, token, hashlib.sha256).hexdigest()

    def _audit(self, actor: str, action: str, detail: dict) -> None:
        payload = json.dumps(detail, ensure_ascii=False, sort_keys=True)
        with self._lock, connect(self.app_db) as con:
            row = con.execute("SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
            prev = row["entry_hash"] if row else GENESIS
            ts = _now()
            con.execute(
                "INSERT INTO audit_log(ts, actor, action, detail, prev_hash, entry_hash) VALUES (?,?,?,?,?,?)",
                (ts, actor, action, payload, prev, _sha256(prev, ts, actor, action, payload)),
            )

    def _get_document(self, document_id: str):
        with connect(self.app_db) as con:
            row = con.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if row is None:
            raise ServiceError(404, "문서를 찾을 수 없습니다.")
        return row

    def _doc_path(self, row) -> Path:
        return self.doc_dir / f"{row['id']}{Path(row['filename']).suffix.lower()}"

    def _load_pages(self, kind: str, content: bytes):
        if kind == "pdf":
            try:
                pages = pdf_utils.render_pdf(content, self.s.dpi)
            except RuntimeError as e:
                raise ServiceError(501, str(e))
            except Exception:
                raise ServiceError(400, "PDF를 읽을 수 없습니다.")
        else:
            img = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
            pages = [] if img is None else [img]
        if not pages:
            raise ServiceError(400, "파일에서 페이지/이미지를 읽을 수 없습니다.")
        tile = self.engine.tile
        for p in pages:
            if min(p.shape[:2]) < 2 * tile:
                raise ServiceError(400, f"이미지가 너무 작습니다. 최소 {2 * tile}px 이상이어야 합니다.")
        return pages

    # ------------------------------------------------------------ 문서
    def register_document(self, filename: str, content: bytes, title: str = "", scenario: str = "document",
                          actor: str = "admin") -> dict:
        if scenario not in ("document", "c2c"):
            raise ServiceError(400, "scenario 는 document 또는 c2c 여야 합니다.")
        ext = Path(filename or "").suffix.lower()
        if ext == ".pdf":
            kind = "pdf"
        elif ext in IMAGE_EXTS:
            kind = "image"
        else:
            raise ServiceError(400, "PDF 또는 이미지(PNG/JPG/BMP/WEBP) 파일만 등록할 수 있습니다.")
        pages = self._load_pages(kind, content)
        doc_type = pdf_utils.classify_pdf(content)["type"] if kind == "pdf" else "image"
        doc_id = uuid.uuid4().hex[:12]
        sizes = [[int(p.shape[1]), int(p.shape[0])] for p in pages]
        row = {
            "id": doc_id,
            "title": title.strip() or Path(filename).stem,
            "scenario": scenario,
            "filename": Path(filename).name,
            "kind": kind,
            "doc_type": doc_type,
            "sha256": hashlib.sha256(content).hexdigest(),
            "page_sizes": json.dumps(sizes),
            "created_at": _now(),
        }
        (self.doc_dir / f"{doc_id}{ext}").write_bytes(content)
        with connect(self.app_db) as con:
            con.execute(
                "INSERT INTO documents(id,title,scenario,filename,kind,doc_type,sha256,page_sizes,created_at) "
                "VALUES (:id,:title,:scenario,:filename,:kind,:doc_type,:sha256,:page_sizes,:created_at)", row)
        self._audit(actor, "document.register", {"document_id": doc_id, "sha256": row["sha256"], "pages": len(pages),
                                                   "doc_type": doc_type})
        return self._doc_public(row)

    @staticmethod
    def _doc_public(row) -> dict:
        d = dict(row)
        d["page_sizes"] = json.loads(d["page_sizes"])
        d["pages"] = len(d["page_sizes"])
        d["doc_type_label"] = pdf_utils.PDF_TYPES.get(d.get("doc_type"), {}).get("label", "이미지")
        return d

    def list_documents(self) -> list:
        with connect(self.app_db) as con:
            rows = con.execute("SELECT * FROM documents ORDER BY created_at DESC").fetchall()
        with connect(self.vault_db) as con:
            counts = dict(con.execute("SELECT document_id, COUNT(*) FROM ledger GROUP BY document_id").fetchall())
        out = []
        for r in rows:
            d = self._doc_public(r)
            d["issued_count"] = counts.get(r["id"], 0)
            out.append(d)
        return out

    # ------------------------------------------------------------ /issue
    def issue(self, document_id: str, subject: dict, actor: str = "admin"):
        """CSPRNG 24비트 가명 토큰을 발급해 원장에 기록하고 워터마크 배포본을 만든다.

        반환: (파일명, 바이트, MIME, 토큰 참조값=HMAC 색인 앞 16자)
        """
        if not str(subject.get("subject_id", "")).strip():
            raise ServiceError(400, "subject_id(사번 또는 판매글 ID)는 필수입니다.")
        row = self._get_document(document_id)
        pages = self._load_pages(row["kind"], self._doc_path(row).read_bytes())
        plain = json.dumps(subject, ensure_ascii=False).encode("utf-8")

        with self._lock:
            for _ in range(16):  # 24비트 공간: 충돌하면 새로 뽑는다
                token = secrets.token_bytes(TOKEN_BYTES)
                index = self.token_index(token)
                issued_at = _now()
                nonce = secrets.token_bytes(12)
                enc = nonce + self._aes.encrypt(nonce, plain, _aad(index, document_id, issued_at))
                try:
                    with connect(self.vault_db) as con:
                        last = con.execute("SELECT id, row_hash FROM ledger ORDER BY id DESC LIMIT 1").fetchone()
                        prev = last["row_hash"] if last else GENESIS
                        row_id = (last["id"] + 1) if last else 1
                        con.execute(
                            "INSERT INTO ledger(id, token_index, enc_subject, document_id, issued_at, prev_hash, row_hash)"
                            " VALUES (?,?,?,?,?,?,?)",
                            (row_id, index, enc, document_id, issued_at, prev,
                             _ledger_hash(prev, row_id, index, enc, document_id, issued_at)))
                    break
                except sqlite3.IntegrityError:
                    continue
            else:
                raise ServiceError(503, "토큰 발급에 실패했습니다 (색인 충돌 반복).")

        scale = pdf_utils.PDF_TYPES.get(row["doc_type"], {}).get("strength_scale", 1.0)
        marked = [self.engine.embed(p, token, strength=self.s.strength * scale) for p in pages]
        stem = Path(row["filename"]).stem
        if row["kind"] == "pdf":
            data = pdf_utils.images_to_pdf(marked, self.s.dpi, self.s.jpeg_quality)
            name, mime = f"{stem}_{index[:8]}.pdf", "application/pdf"
        else:
            ok, buf = cv2.imencode(".png", marked[0])
            if not ok:
                raise ServiceError(500, "이미지 인코딩 실패")
            data, name, mime = buf.tobytes(), f"{stem}_{index[:8]}.png", "image/png"
        self._audit(actor, "token.issue", {"document_id": document_id, "token_ref": index[:16],
                                           "output_sha256": hashlib.sha256(data).hexdigest()})
        return name, data, mime, index[:16]

    def list_tokens(self) -> list:
        """발급 이력 (신원 정보 제외)."""
        with connect(self.vault_db) as con:
            rows = con.execute("SELECT id, token_index, document_id, issued_at FROM ledger ORDER BY id DESC").fetchall()
        return [{"id": r["id"], "token_ref": r["token_index"][:16], "document_id": r["document_id"],
                 "issued_at": r["issued_at"]} for r in rows]

    # ------------------------------------------------------------ /verify
    def verify_ledger(self) -> dict:
        """발급 원장 해시 체인 검증: 행을 고치거나 지우거나 끼워 넣으면 실패한다."""
        prev = GENESIS
        with connect(self.vault_db) as con:
            rows = con.execute("SELECT * FROM ledger ORDER BY id").fetchall()
        for expected_id, r in enumerate(rows, 1):
            if (r["id"] != expected_id or r["prev_hash"] != prev or r["row_hash"] != _ledger_hash(
                    prev, r["id"], r["token_index"], bytes(r["enc_subject"]), r["document_id"], r["issued_at"])):
                return {"ok": False, "entries": len(rows), "broken_at": r["id"]}
            prev = r["row_hash"]
        return {"ok": True, "entries": len(rows), "broken_at": None, "head": prev}

    def verify_audit(self) -> dict:
        prev = GENESIS
        with connect(self.app_db) as con:
            rows = con.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
        for r in rows:
            if r["prev_hash"] != prev or r["entry_hash"] != _sha256(prev, r["ts"], r["actor"], r["action"], r["detail"]):
                return {"ok": False, "entries": len(rows), "broken_at": r["id"]}
            prev = r["entry_hash"]
        return {"ok": True, "entries": len(rows), "broken_at": None, "head": prev}

    def verify(self) -> dict:
        ledger, audit = self.verify_ledger(), self.verify_audit()
        return {"ok": ledger["ok"] and audit["ok"], "ledger": ledger, "audit": audit}

    # ------------------------------------------------------------ /trace
    def trace(self, image_bytes: bytes, corners: Optional[Sequence[Sequence[float]]] = None, deep: bool = False,
              actor: str = "admin") -> dict:
        img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ServiceError(400, "이미지를 해석할 수 없습니다.")
        if corners is not None and np.asarray(corners, dtype=np.float32).shape != (4, 2):
            raise ServiceError(400, "corners 는 [[x,y] x 4] (좌상,우상,우하,좌하) 형식이어야 합니다.")
        with connect(self.app_db) as con:
            size_rows = con.execute("SELECT page_sizes FROM documents").fetchall()
        sizes = sorted({tuple(sz) for r in size_rows for sz in json.loads(r["page_sizes"])})

        res = self.engine.extract(img, corners=corners, page_sizes=sizes, deep=deep)
        steps = {"bch_decoded": res.ok, "issued": None, "chain_intact": None}
        index, vrow = None, None
        if not res.ok:
            status = "undecidable"
        else:
            index = self.token_index(res.token)
            with connect(self.vault_db) as con:
                vrow = con.execute("SELECT * FROM ledger WHERE token_index=?", (index,)).fetchone()
            steps["issued"] = vrow is not None
            if vrow is None:
                status = "unissued"
            else:
                chain = self.verify_ledger()
                steps["chain_intact"] = chain["ok"]
                status = "identified" if chain["ok"] else "tampered"

        trace_id = uuid.uuid4().hex[:12]
        detail = {
            "steps": steps,
            "sync_score": round(res.sync_score, 2),
            "regions_locked": res.regions_locked,
            "repetitions": res.repetitions,
            "bch_corrected_bits": res.corrected,
            "ber": round(res.ber, 4),
            "view": res.view,
            "image_size": [int(img.shape[1]), int(img.shape[0])],
        }
        out = {
            "trace_id": trace_id,
            "status": status,
            "status_label": STATUS_LABELS[status],
            "token_ref": index[:16] if index and vrow is not None else None,
            "document": None,
            "issued_at": vrow["issued_at"] if vrow is not None else None,
            "detail": detail,
        }
        if vrow is not None:
            try:
                d = self._get_document(vrow["document_id"])
                out["document"] = {"id": d["id"], "title": d["title"], "scenario": d["scenario"]}
            except ServiceError:
                out["document"] = {"id": vrow["document_id"], "title": "(삭제됨)", "scenario": ""}
        with connect(self.app_db) as con:
            con.execute(
                "INSERT INTO traces(id, created_at, status, token_index, document_id, detail, image_sha256)"
                " VALUES (?,?,?,?,?,?,?)",
                (trace_id, _now(), status, vrow["token_index"] if vrow is not None else None,
                 vrow["document_id"] if vrow is not None else None, json.dumps(detail),
                 hashlib.sha256(image_bytes).hexdigest()))
        self._audit(actor, "trace.run", {"trace_id": trace_id, "status": status, "token_ref": out["token_ref"]})
        return out

    def reveal(self, trace_id: str, reason: str, actor: str = "admin") -> dict:
        """식별된 역추적 결과의 신원 열람. 사유를 남기고 감사 로그에 기록한다."""
        if len((reason or "").strip()) < 5:
            raise ServiceError(400, "열람 사유를 5자 이상 입력해야 합니다.")
        with connect(self.app_db) as con:
            t = con.execute("SELECT * FROM traces WHERE id=?", (trace_id,)).fetchone()
        if t is None:
            raise ServiceError(404, "역추적 기록을 찾을 수 없습니다.")
        if t["status"] != "identified" or not t["token_index"]:
            raise ServiceError(409, "배포 대상이 식별된 기록만 열람할 수 있습니다.")
        if not self.verify_ledger()["ok"]:
            raise ServiceError(409, "발급 원장의 해시 체인이 깨져 있어 열람할 수 없습니다.")
        with connect(self.vault_db) as con:
            v = con.execute("SELECT * FROM ledger WHERE token_index=?", (t["token_index"],)).fetchone()
        if v is None:
            raise ServiceError(404, "원장 기록을 찾을 수 없습니다.")
        enc = bytes(v["enc_subject"])
        try:
            plain = self._aes.decrypt(enc[:12], enc[12:], _aad(v["token_index"], v["document_id"], v["issued_at"]))
        except InvalidTag:
            raise ServiceError(409, "암호문 인증에 실패했습니다 (기록 위·변조 의심).")
        self._audit(actor, "identity.reveal", {"trace_id": trace_id, "token_ref": t["token_index"][:16],
                                               "reason": reason.strip()})
        return {"trace_id": trace_id, "token_ref": t["token_index"][:16], "document_id": v["document_id"],
                "issued_at": v["issued_at"], "subject": json.loads(plain.decode("utf-8"))}

    def list_traces(self, limit: int = 100) -> list:
        with connect(self.app_db) as con:
            rows = con.execute("SELECT * FROM traces ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = json.loads(d["detail"])
            d["token_ref"] = d.pop("token_index")[:16] if d["token_index"] else None
            d["status_label"] = STATUS_LABELS.get(d["status"], d["status"])
            out.append(d)
        return out

    def list_audit(self, limit: int = 200) -> list:
        with connect(self.app_db) as con:
            rows = con.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]
