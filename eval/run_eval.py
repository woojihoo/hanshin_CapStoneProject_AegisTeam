"""모의 공격 테스트 및 복원율 평가.

사용법 (프로젝트 루트에서):
    python eval/run_eval.py --n 20 --strengths 0.8 1.0 1.2
결과: eval/results/report.md, eval/results/results.csv

주의: screen_shoot 은 실촬영을 근사한 시뮬레이션이다. 실제 스마트폰 촬영 검증은
scripts/cli.py 로 배포본을 만들어 직접 촬영한 사진을 extract 해서 별도로 기록한다.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wm_core import TOKEN_BYTES, WatermarkEngine, WMConfig  # noqa: E402
from wm_core import attacks as A  # noqa: E402
from wm_core import metrics as M  # noqa: E402
from wm_core import synth  # noqa: E402

A4 = synth.A4_150DPI

# (이름, 대상['doc'|'photo'], 공격 함수(img, i) -> (image, extract kwargs))
ATTACKS = [
    ("원본 그대로", "doc", lambda im, i: (im, {})),
    ("JPEG 품질 70", "doc", lambda im, i: (A.jpeg(im, 70), {})),
    ("JPEG 품질 30", "doc", lambda im, i: (A.jpeg(im, 30), {})),
    ("크롭 50%", "doc", lambda im, i: (A.crop(im, 0.5, i), {})),
    ("크롭 25%", "doc", lambda im, i: (A.crop(im, 0.25, i), {})),
    ("크롭 10% + JPEG 50", "doc", lambda im, i: (A.jpeg(A.crop(im, 0.10, i), 50), {})),
    ("가우시안 블러 σ=1.5", "doc", lambda im, i: (A.blur(im, 1.5), {})),
    ("잡음 σ=10", "doc", lambda im, i: (A.noise(im, 10, i), {})),
    ("밝기·대비 변경", "doc", lambda im, i: (A.brightness_contrast(im, 0.75, 20), {})),
    ("리사이즈 70% (원본 크기 등록)", "doc", lambda im, i: (A.resize(im, 0.7), {"page_sizes": [A4]})),
    ("화면 촬영 시뮬 (보통, 자동 검출)", "doc",
     lambda im, i: (A.screen_shoot(im, seed=i, level=1.0)[0], {"page_sizes": [A4]})),
    ("화면 촬영 시뮬 (강함·저해상도, 자동 검출)", "doc",
     lambda im, i: (A.screen_shoot(im, seed=1000 + i, level=1.5, out_long=1800)[0], {"page_sizes": [A4]})),
    ("화면 촬영 시뮬 (강함·저해상도, 꼭짓점 지정)", "doc",
     lambda im, i: (lambda s: (s[0], {"page_sizes": [A4], "corners": s[1]}))(
         A.screen_shoot(im, seed=1000 + i, level=1.5, out_long=1800))),
    ("[사진] JPEG 품질 60", "photo", lambda im, i: (A.jpeg(im, 60), {})),
    ("[사진] 크롭 25% + JPEG 70", "photo", lambda im, i: (A.jpeg(A.crop(im, 0.25, i), 70), {})),
    ("[사진] 리사이즈 60% (원본 크기 등록)", "photo",
     lambda im, i: (A.jpeg(A.resize(im, 0.6), 80), {"page_sizes": [(1080, 1080)]})),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20, help="조건당 시험 이미지 수")
    ap.add_argument("--strengths", type=float, nargs="+", default=[0.8, 1.0, 1.2], help="강도 배율 (1.0 = 기본)")
    ap.add_argument("--out", type=Path, default=ROOT / "eval" / "results")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    rows, invis, falsepos = [], [], []
    t0 = time.time()
    for s in args.strengths:
        eng = WatermarkEngine(b"evaluation-key-evaluation-key-00", WMConfig())
        stats = {name: [0, 0, 0.0, []] for name, _, _ in ATTACKS}  # 성공, 다른 토큰으로 복호, sync 합, BER 목록
        ps = {"doc": [], "photo": []}
        ss = {"doc": [], "photo": []}
        for i in range(args.n):
            token = os.urandom(TOKEN_BYTES)
            hosts = {"doc": synth.document_page(i), "photo": synth.product_photo(i)}
            marked = {k: eng.embed(v, token, strength=s) for k, v in hosts.items()}
            for k in hosts:
                ps[k].append(M.psnr(hosts[k], marked[k]))
                ss[k].append(M.ssim(hosts[k], marked[k]))
            for name, kind, fn in ATTACKS:
                img, kw = fn(marked[kind], i)
                res = eng.extract(img, **kw)
                st = stats[name]
                st[0] += int(res.ok and res.token == token)
                st[1] += int(res.ok and res.token != token)
                st[2] += res.sync_score
                if res.ok and res.token == token:
                    st[3].append(res.ber)
        fp = 0
        for i in range(args.n):  # 워터마크 없는 이미지에서의 오검출
            fp += int(eng.extract(synth.document_page(5000 + i), page_sizes=[A4]).ok)
            fp += int(eng.extract(A.screen_shoot(synth.document_page(6000 + i), seed=i)[0], page_sizes=[A4]).ok)
        falsepos.append((s, fp, 2 * args.n))
        invis.append((s, np.mean(ps["doc"]), np.mean(ss["doc"]), np.mean(ps["photo"]), np.mean(ss["photo"])))
        for name, _, _ in ATTACKS:
            ok, wrong, z, bers = stats[name]
            rows.append({"strength": s, "attack": name, "n": args.n, "recovered": ok, "wrong_token": wrong,
                         "recovery_rate": round(ok / args.n, 3), "mean_ber": round(float(np.mean(bers)), 4) if bers else "",
                         "mean_sync_score": round(z / args.n, 2)})
        print(f"strength {s}: done ({time.time() - t0:.0f}s)", flush=True)

    with open(args.out / "results.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    md = ["# 모의 공격 평가 결과", "",
          f"- 조건당 이미지 수: {args.n} (합성 문서 A4@150DPI {A4[0]}x{A4[1]}, 합성 사진 1080x1080)",
          "- 논문 사양: 24비트 토큰 → BCH(63,24), DWT LL 8×8 DCT 계수 쌍 대소 삽입, 반복 삽입 후 다수결, 조건마다 무작위 토큰",
          "- BER: 복원에 성공한 경우, 다수결 전 개별 판정(타일 반복 × 계수 쌍)의 비트 오류율 평균",
          "- 화면 촬영 항목은 **시뮬레이션**이며 실제 스마트폰 촬영 결과가 아님", "",
          "## 비가시성", "", "| 강도 배율 | 문서 PSNR(dB) | 문서 SSIM | 사진 PSNR(dB) | 사진 SSIM |", "|---|---|---|---|---|"]
    md += [f"| {s} | {a:.1f} | {b:.3f} | {c:.1f} | {d:.3f} |" for s, a, b, c, d in invis]
    md += ["", "## 토큰 복원율 (괄호: 평균 BER)", "", "| 공격 | " + " | ".join(f"강도 {s}" for s in args.strengths) + " |",
           "|---|" + "---|" * len(args.strengths)]
    for name, _, _ in ATTACKS:
        cells = [f"{r['recovered']}/{r['n']}" + (f" ({r['mean_ber'] * 100:.1f}%)" if r["mean_ber"] != "" else "")
                 for r in rows if r["attack"] == name]
        md.append(f"| {name} | " + " | ".join(cells) + " |")
    md += ["", "## 오검출", "",
           f"- BCH 복호는 됐지만 삽입한 것과 다른 토큰이 나온 횟수(전체 조건 합): {sum(r['wrong_token'] for r in rows)}"
           " — 시스템에서는 발급 원장 조회로 '미발급 토큰'으로 걸러진다",
           "- 워터마크 없는 이미지에서 토큰이 검출된 횟수: "
           + ", ".join(f"강도 {s}: {fp}/{n}" for s, fp, n in falsepos), ""]
    (args.out / "report.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
