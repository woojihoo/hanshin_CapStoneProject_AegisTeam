"""환경 설정과 키 관리.

운영 환경에서는 아래 환경변수로 키를 주입한다. 값이 없으면 개발 편의를 위해
data/dev_keys.json 에 무작위 키를 만들어 재사용한다(운영 사용 금지).
  WM_MASTER_KEY      워터마크 블록 배치·동기 템플릿 유도 키 (hex, 32바이트 권장)
  WM_TOKEN_HMAC_KEY  토큰 HMAC 색인 키 (hex)
  WM_DB_ENC_KEY      사용자 ID 암호화용 AES-256-GCM 키 (hex 32바이트)
  WM_ADMIN_API_KEY   관리자 API 키
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    data_dir: Path
    wm_key: bytes
    hmac_key: bytes
    enc_key: bytes            # AES-256-GCM 키 (32바이트)
    admin_key: str
    dpi: int = 150
    strength: float = 1.0     # 삽입 강도 배율 (1.0 = 문서 PSNR 38dB 이상 기준값)
    jpeg_quality: int = 92


def parse_aes_key(value: str) -> bytes:
    """hex(64자) 또는 urlsafe base64(이전 버전 Fernet 키 포함) -> 32바이트."""
    value = value.strip()
    try:
        key = bytes.fromhex(value)
    except ValueError:
        try:
            key = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        except (binascii.Error, ValueError):
            key = b""
    if len(key) != 32:
        raise ValueError("WM_DB_ENC_KEY 는 32바이트(hex 64자) 키여야 합니다.")
    return key


def load_settings(data_dir: Optional[Path] = None) -> Settings:
    data_dir = Path(data_dir or os.environ.get("WM_DATA_DIR", ROOT / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    env = {
        "wm_key": os.environ.get("WM_MASTER_KEY"),
        "hmac_key": os.environ.get("WM_TOKEN_HMAC_KEY"),
        "enc_key": os.environ.get("WM_DB_ENC_KEY"),
        "admin_key": os.environ.get("WM_ADMIN_API_KEY"),
    }
    if not all(env.values()):
        key_file = data_dir / "dev_keys.json"
        if key_file.exists():
            dev = json.loads(key_file.read_text(encoding="utf-8"))
        else:
            dev = {
                "wm_key": secrets.token_hex(32),
                "hmac_key": secrets.token_hex(32),
                "enc_key": secrets.token_hex(32),
                "admin_key": secrets.token_urlsafe(18),
            }
            key_file.write_text(json.dumps(dev, indent=2), encoding="utf-8")
            try:
                os.chmod(key_file, 0o600)
            except OSError:
                pass
        print(f"[경고] 환경변수 키가 없어 개발용 키({key_file})를 사용합니다. 운영 환경에서는 환경변수로 주입하세요.",
              file=sys.stderr)
        env = {k: v or dev[k] for k, v in env.items()}
    return Settings(
        data_dir=data_dir,
        wm_key=bytes.fromhex(env["wm_key"]),
        hmac_key=bytes.fromhex(env["hmac_key"]),
        enc_key=parse_aes_key(env["enc_key"]),
        admin_key=env["admin_key"],
        dpi=int(os.environ.get("WM_DPI", "150")),
        strength=float(os.environ.get("WM_STRENGTH", "1.0")),
        jpeg_quality=int(os.environ.get("WM_JPEG_QUALITY", "92")),
    )
