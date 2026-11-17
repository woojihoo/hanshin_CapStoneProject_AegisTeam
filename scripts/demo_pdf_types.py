"""PDF 유형 3가지 샘플에 대해: 유형 분류 -> 가명 토큰 발급 -> 비가시성 워터마크 삽입 -> 역추적 검증.

    python scripts/make_sample_pdfs.py
    python scripts/demo_pdf_types.py [--data-dir data] [--src samples/original] [--out samples/watermarked]

토큰과 수령자 매핑은 서버와 같은 데이터 폴더(data/)에 저장되므로, 이후 서버를 띄우면
관제 UI 에서 같은 배포본을 그대로 역추적할 수 있다. 수령자 정보는 모두 가상이다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app import pdf_utils  # noqa: E402
from backend.app.config import load_settings  # noqa: E402
from backend.app.services import ForensicService  # noqa: E402
from wm_core import attacks as A  # noqa: E402
from wm_core import metrics as M  # noqa: E402

RECIPIENTS = {  # 유형별 가상 수령자
    "text": {"subject_id": "E2026-0101", "subject_name": "김하늘(가상)", "department": "전략기획팀"},
    "scan": {"subject_id": "E2026-0202", "subject_name": "이도윤(가상)", "department": "인사팀"},
    "slide": {"subject_id": "E2026-0303", "subject_name": "박서연(가상)", "department": "경영지원실"},
}


def _jpg(img) -> bytes:
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, default=None)
    ap.add_argument("--src", type=Path, default=ROOT / "samples" / "original")
    ap.add_argument("--out", type=Path, default=ROOT / "samples" / "watermarked")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    svc = ForensicService(load_settings(args.data_dir))
    dpi = svc.s.dpi

    md = ["# PDF 유형별 가명 토큰 발급 · 워터마크 삽입 결과", "",
          f"- PDF 백엔드: {pdf_utils.BACKEND}, 삽입 해상도 {dpi} DPI, 강도 배율 {svc.s.strength} (margin {svc.engine.cfg.margin})",
          "- 토큰 24비트 → BCH(63,24) → 160px 타일 반복 삽입, 역추적은 다수결 후 BCH 복호 (논문 사양)",
          "- 수령자 정보는 가상이며 AES-GCM 으로 암호화되어 `data/vault.db` 발급 원장(해시 체인)에만 저장됨. '토큰 참조'는 토큰 HMAC 색인 앞 16자리",
          "- '촬영 시뮬'은 합성 왜곡이며 실제 스마트폰 촬영 결과가 아님", ""]
    summary = ["| 파일 | 분류 | 쪽수 | 삽입 강도 | 토큰 참조 | PSNR(dB) | SSIM | 배포본 |", "|---|---|---|---|---|---|---|---|"]
    checks = ["| 파일 | 쪽 | 배포본 재렌더링 | 110DPI 화면 캡처 | 촬영 시뮬(자동) | 촬영 시뮬(꼭짓점 지정) |", "|---|---|---|---|---|---|"]
    all_ok = True
    for src in sorted(args.src.glob("*.pdf")):
        content = src.read_bytes()
        info = pdf_utils.classify_pdf(content)
        doc = svc.register_document(src.name, content, src.stem, "document", actor="demo")
        name, data, _, ref = svc.issue(doc["id"], RECIPIENTS[info["type"]], actor="demo")
        (args.out / name).write_bytes(data)

        orig = pdf_utils.render_pdf(content, dpi)
        marked = pdf_utils.render_pdf(data, dpi)
        zoomed = pdf_utils.render_pdf(data, 110)
        # 삽입 왜곡: 같은 강도로 임의 토큰을 넣은 래스터와 원본 래스터 비교 (PDF 재압축 영향 제외)
        scale = pdf_utils.PDF_TYPES[info["type"]]["strength_scale"]
        ref_marked = [svc.engine.embed(p, bytes(3), strength=svc.s.strength * scale) for p in orig]
        psnr = sum(M.psnr(a, b) for a, b in zip(orig, ref_marked)) / len(orig)
        ssim = sum(M.ssim(a, b) for a, b in zip(orig, ref_marked)) / len(orig)
        summary.append(f"| {src.name} | {info['label']} | {info['pages']} | {svc.s.strength * scale:.2f} | `{ref}` | "
                       f"{psnr:.1f} | {ssim:.3f} | {name} |")
        print(f"{src.name}: {info['label']}  token_ref={ref}  PSNR={psnr:.1f}  -> {name}")

        for i, (m, z) in enumerate(zip(marked, zoomed), 1):
            shot, quad = A.screen_shoot(m, seed=40 + i)
            trials = [svc.trace(_jpg(m), actor="demo"), svc.trace(_jpg(z), actor="demo"),
                      svc.trace(_jpg(shot), actor="demo"), svc.trace(_jpg(shot), corners=quad.tolist(), actor="demo")]
            cells = []
            for t in trials:
                good = t["status"] == "identified" and t["token_ref"] == ref
                d = t["detail"]
                cells.append(f"식별 (정정 {d['bch_corrected_bits']}비트, BER {d['ber'] * 100:.1f}%)" if good
                             else f"실패: {t['status_label']}")
            all_ok &= all(c.startswith("식별") for c in cells[:2] + cells[3:])
            checks.append(f"| {src.name} | {i} | " + " | ".join(cells) + " |")
            print(f"   p{i}: " + " / ".join(cells))

    md += ["## 발급 결과", ""] + summary + ["", "## 역추적 검증", ""] + checks + [""]
    (args.out.parent / "RESULT.md").write_text("\n".join(md), encoding="utf-8")
    print("해시 체인 검증 (/verify):", svc.verify())
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
