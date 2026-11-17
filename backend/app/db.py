"""SQLite 스키마.

app.db   : 문서, 역추적 기록, 운영 감사 로그(해시 체인)
vault.db : 발급 원장 — 토큰 HMAC 색인 + AES-GCM 으로 암호화한 사용자 ID. 각 행은 직전 행의
           SHA-256 해시와 연결돼 있어 한 행이라도 고치면 검증이 실패한다. (업무 DB 와 파일 분리)
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    scenario TEXT NOT NULL,          -- 'document'(기업 문서) | 'c2c'(중고거래 사진)
    filename TEXT NOT NULL,
    kind TEXT NOT NULL,              -- 'pdf' | 'image'
    doc_type TEXT NOT NULL DEFAULT 'image',  -- PDF 유형: text | scan | slide (이미지는 image)
    sha256 TEXT NOT NULL,
    page_sizes TEXT NOT NULL,        -- JSON [[W,H], ...] (워터마크 삽입 해상도)
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS traces (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,            -- identified | undecidable | unissued | tampered
    token_index TEXT,
    document_id TEXT,
    detail TEXT NOT NULL,            -- JSON
    image_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL,            -- JSON
    prev_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL
);
"""

VAULT_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_index TEXT NOT NULL UNIQUE, -- HMAC-SHA256(토큰): 토큰 원문은 저장하지 않는다
    enc_subject BLOB NOT NULL,        -- AES-256-GCM(nonce 12B || 암호문+태그), AAD = 색인|문서|시각
    document_id TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    row_hash TEXT NOT NULL            -- SHA-256(prev_hash, 이 행의 모든 값)
);
"""


def init_db(path: Path, schema: str) -> None:
    with connect(path) as con:
        con.executescript(schema)


@contextmanager
def connect(path: Path):
    con = sqlite3.connect(str(path), timeout=30)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()
