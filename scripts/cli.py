"""서버 없이 워터마크 삽입/추출을 해보는 명령행 도구 (실촬영 실험용).

  python scripts/cli.py embed  원본.png 배포본.png --token a1b2c3
  python scripts/cli.py extract 촬영본.jpg --size 1240x1754 [--corners "x1,y1 x2,y2 x3,y3 x4,y4"] [--deep]

키는 환경변수 WM_MASTER_KEY(hex)를 쓰고, 없으면 실험용 고정 키를 쓴다.
--size 는 삽입 당시 이미지의 픽셀 크기(가로x세로)이며 촬영본/리사이즈본 복원에 필요하다.
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wm_core import TOKEN_BYTES, WatermarkEngine, WMConfig  # noqa: E402


def _engine() -> WatermarkEngine:
    key = bytes.fromhex(os.environ["WM_MASTER_KEY"]) if os.environ.get("WM_MASTER_KEY") else b"cli-experiment-key-0123456789abc"
    return WatermarkEngine(key, WMConfig())


def _read(path: str) -> np.ndarray:
    img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)  # 한글 경로 대응
    if img is None:
        sys.exit(f"이미지를 읽을 수 없습니다: {path}")
    return img


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("embed")
    e.add_argument("src")
    e.add_argument("dst")
    e.add_argument("--token", help="24비트 토큰, 6자리 hex (생략 시 무작위)")
    e.add_argument("--strength", type=float, default=1.0, help="삽입 강도 배율 (1.0 = 문서 PSNR 38dB 이상 기준)")
    x = sub.add_parser("extract")
    x.add_argument("src")
    x.add_argument("--size", action="append", default=[], help="삽입 당시 크기 WxH (여러 번 지정 가능)")
    x.add_argument("--corners", help='페이지 꼭짓점 "x,y x,y x,y x,y" (좌상 우상 우하 좌하)')
    x.add_argument("--deep", action="store_true")
    args = ap.parse_args()

    if args.cmd == "embed":
        token = bytes.fromhex(args.token) if args.token else secrets.token_bytes(TOKEN_BYTES)
        img = _read(args.src)
        out = _engine().embed(img, token, strength=args.strength)
        ext = Path(args.dst).suffix or ".png"
        ok, buf = cv2.imencode(ext, out)
        if not ok:
            sys.exit("저장 실패")
        buf.tofile(args.dst)
        print(f"token={token.hex()} size={img.shape[1]}x{img.shape[0]} -> {args.dst}")
    else:
        sizes = [tuple(int(v) for v in s.lower().split("x")) for s in args.size]
        corners = [[float(v) for v in p.split(",")] for p in args.corners.split()] if args.corners else None
        res = _engine().extract(_read(args.src), corners=corners, page_sizes=sizes, deep=args.deep)
        if res.ok:
            print(f"token={res.token.hex()} bch_corrected_bits={res.corrected} repetitions={res.repetitions} "
                  f"ber={res.ber:.3f} sync={res.sync_score:.1f} view={res.view}")
        else:
            print(f"판정 불가: BCH 복호 실패 (최고 동기 점수 {res.sync_score:.1f}) — 재촬영 필요")
            sys.exit(1)


if __name__ == "__main__":
    main()
