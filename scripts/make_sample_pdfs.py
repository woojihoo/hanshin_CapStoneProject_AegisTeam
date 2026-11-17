"""자주 쓰이는 PDF 유형 3가지의 샘플을 만든다 (내용은 모두 가상).

    python scripts/make_sample_pdfs.py [출력 폴더=samples/original]

  01_text_report.pdf   텍스트 보고서형  (A4 세로, 텍스트 레이어)
  02_scanned_form.pdf  스캔·이미지형    (페이지 전체가 스캔 이미지)
  03_slides.pdf        프레젠테이션형   (16:9 가로, 색 배경·차트)
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import cv2
import img2pdf
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
GOTHIC, MYEONGJO = "HYGothic-Medium", "HYSMyeongJo-Medium"
pdfmetrics.registerFont(UnicodeCIDFont(GOTHIC))
pdfmetrics.registerFont(UnicodeCIDFont(MYEONGJO))

PARAS = [
    "본 문서는 2026년 3분기 신규 서비스 사업의 추진 현황과 4분기 계획을 정리한 내부 검토용 자료이다. "
    "기재된 수치는 잠정치이며 재무팀 확정 전까지 외부 반출을 금한다.",
    "3분기 매출은 전 분기 대비 12.4% 증가하였고, 신규 고객사는 17곳이 추가되었다. 다만 서버 증설 비용이 "
    "예상보다 빠르게 늘어 영업이익률은 1.8%p 하락하였다.",
    "4분기에는 기업 고객 전용 요금제를 출시하고 보안 인증 심사를 마무리할 계획이다. 인증 일정이 지연될 "
    "경우 공공 부문 입찰 참여가 다음 해로 미뤄질 수 있어 일정 관리가 가장 큰 위험 요소이다.",
    "경쟁사 동향을 보면 상위 2개 업체가 가격을 평균 8% 인하하였다. 우리는 가격 대응보다 장기 계약 고객에 "
    "대한 기술 지원 범위를 넓히는 방향으로 대응한다.",
]


def _wrap(text: str, n: int) -> list:
    return [text[i:i + n] for i in range(0, len(text), n)]


def text_report(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    W, H = A4
    for page in range(2):
        c.setFont(GOTHIC, 9)
        c.setFillColor(colors.HexColor("#b3261e"))
        c.drawString(72, H - 50, "대외비 · 사내 한정")
        c.setFillColor(colors.black)
        y = H - 100
        if page == 0:
            c.setFont(GOTHIC, 20)
            c.drawString(72, y, "2026년 3분기 신규 서비스 사업 검토 보고")
            c.setFont(MYEONGJO, 10)
            c.drawString(72, y - 24, "전략기획팀 · 2026. 10. 15.")
            c.line(72, y - 36, W - 72, y - 36)
            y -= 70
        for k, sec in enumerate(["추진 현황", "재무 요약", "4분기 계획", "경쟁 환경"]):
            c.setFont(GOTHIC, 13)
            c.drawString(72, y, f"{page * 4 + k + 1}. {sec}")
            y -= 22
            c.setFont(MYEONGJO, 11)
            for para in (PARAS[k], PARAS[(k + 1) % 4]):
                for line in _wrap(para, 40):
                    c.drawString(72, y, line)
                    y -= 17
                y -= 6
            y -= 8
        if page == 0:  # 표
            rows = [["구분", "2분기", "3분기", "증감"], ["매출(억 원)", "41.2", "46.3", "+12.4%"],
                    ["신규 고객사", "11", "17", "+6"], ["영업이익률", "9.1%", "7.3%", "-1.8%p"]]
            c.setFont(GOTHIC, 10)
            for r, row in enumerate(rows):
                for j, cell in enumerate(row):
                    x = 72 + j * 110
                    c.rect(x, y - 20 * r - 6, 110, 20)
                    c.drawString(x + 8, y - 20 * r, cell)
        c.setFont(GOTHIC, 9)
        c.drawCentredString(W / 2, 40, f"- {page + 1} -")
        c.showPage()
    c.save()


def _pil_font(size: int, bold: bool = False):
    cands = ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
             "/System/Library/Fonts/AppleSDGothicNeo.ttc", "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
             "C:/Windows/Fonts/malgunbd.ttf" if bold else "C:/Windows/Fonts/malgun.ttf",
             "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"]
    for p in cands:
        if Path(p).exists():
            for idx in (1, 0):  # Noto CJK ttc 의 1번이 KR
                try:
                    return ImageFont.truetype(p, size, index=idx)
                except Exception:
                    continue
    raise SystemExit("한글 글꼴을 찾지 못했습니다.")


def scanned_form(path: Path, dpi: int = 200) -> None:
    W, H = int(8.27 * dpi), int(11.69 * dpi)
    im = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.text((W // 2, 190), "비밀유지 서약서", font=_pil_font(64, True), fill=(25, 25, 25), anchor="mm")
    body = _pil_font(34)
    y = 330
    clauses = ["제1조 (목적) 본 서약은 회사의 영업비밀 및 내부 정보의 보호를 목적으로 한다.",
               "제2조 (비밀 정보) 재직 중 알게 된 기술 자료, 고객 정보, 사업 계획 일체를 말한다.",
               "제3조 (의무) 서약인은 비밀 정보를 촬영·복제·전송하거나 제3자에게 누설하지 않는다.",
               "제4조 (반환) 퇴직 시 보유한 모든 자료와 사본을 지체 없이 회사에 반환한다.",
               "제5조 (책임) 본 서약을 위반한 경우 관련 법령과 사규에 따른 책임을 진다."]
    for cl in clauses:
        for line in _wrap(cl, 38):
            d.text((150, y), line, font=body, fill=(30, 30, 30))
            y += 56
        y += 34
    y += 40
    for label, val in (("소  속", "연구개발본부"), ("성  명", "홍 길 동 (가상 인물)"), ("작성일", "2026년 10월 15일")):
        d.rectangle((150, y, 430, y + 84), outline=(40, 40, 40), width=3)
        d.rectangle((430, y, W - 150, y + 84), outline=(40, 40, 40), width=3)
        d.text((180, y + 20), label, font=body, fill=(30, 30, 30))
        d.text((460, y + 20), val, font=body, fill=(30, 30, 30))
        y += 84
    d.ellipse((W - 420, y + 60, W - 220, y + 260), outline=(190, 40, 40), width=7)  # 직인
    d.text((W - 320, y + 160), "직인", font=_pil_font(48, True), fill=(190, 40, 40), anchor="mm")

    # 스캔 흔적: 종이색, 미세 기울어짐, 블러, 잡음
    a = cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR).astype(np.float32) * (243 / 255.0)
    m = cv2.getRotationMatrix2D((W / 2, H / 2), 0.5, 1.0)
    a = cv2.warpAffine(a, m, (W, H), borderValue=(243, 243, 243))
    a = cv2.GaussianBlur(a, (0, 0), 0.9)
    a += np.random.default_rng(7).normal(0, 3.0, a.shape).astype(np.float32)
    ok, buf = cv2.imencode(".jpg", np.clip(a, 0, 255).astype(np.uint8), [cv2.IMWRITE_JPEG_QUALITY, 88])
    out = io.BytesIO()
    img2pdf.convert([buf.tobytes()], outputstream=out, layout_fun=img2pdf.get_fixed_dpi_layout_fun((dpi, dpi)))
    path.write_bytes(out.getvalue())


def slides(path: Path) -> None:
    W, H = 960, 540  # 16:9
    c = canvas.Canvas(str(path), pagesize=(W, H))
    navy, accent = colors.HexColor("#172033"), colors.HexColor("#2457d6")
    # 1) 표지
    c.setFillColor(navy)
    c.rect(0, 0, W, H, stroke=0, fill=1)
    c.setFillColor(accent)
    c.rect(0, 0, 18, H, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont(GOTHIC, 40)
    c.drawString(80, 300, "2027 제품 로드맵")
    c.setFont(GOTHIC, 18)
    c.drawString(80, 255, "경영회의 보고 자료 · 대외비")
    c.showPage()
    # 2) 불릿
    c.setFillColor(accent)
    c.rect(0, H - 90, W, 90, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont(GOTHIC, 28)
    c.drawString(60, H - 58, "핵심 추진 과제")
    c.setFillColor(colors.black)
    c.setFont(GOTHIC, 20)
    for i, t in enumerate(["기업 전용 요금제 1분기 출시", "보안 인증 심사 상반기 완료", "해외 리전 2곳 추가 구축",
                           "기술 지원 조직 20명 증원", "파트너 프로그램 개편"]):
        c.setFillColor(accent)
        c.circle(80, H - 150 - i * 62 + 7, 6, stroke=0, fill=1)
        c.setFillColor(colors.black)
        c.drawString(104, H - 150 - i * 62, t)
    c.showPage()
    # 3) 차트
    c.setFillColor(accent)
    c.rect(0, H - 90, W, 90, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont(GOTHIC, 28)
    c.drawString(60, H - 58, "분기별 매출 전망 (억 원)")
    vals = [46, 52, 61, 58, 70, 83]
    for i, v in enumerate(vals):
        x = 110 + i * 130
        c.setFillColor(accent if i < 2 else colors.HexColor("#7fa2ee"))
        c.rect(x, 90, 80, v * 3.6, stroke=0, fill=1)
        c.setFillColor(colors.black)
        c.setFont(GOTHIC, 16)
        c.drawCentredString(x + 40, 98 + v * 3.6, str(v))
        c.drawCentredString(x + 40, 64, f"{'26' if i < 2 else '27'}.{[3, 4, 1, 2, 3, 4][i]}Q")
    c.line(90, 90, W - 60, 90)
    c.showPage()
    c.save()


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "samples" / "original"
    out.mkdir(parents=True, exist_ok=True)
    text_report(out / "01_text_report.pdf")
    scanned_form(out / "02_scanned_form.pdf")
    slides(out / "03_slides.pdf")
    print("생성:", ", ".join(sorted(p.name for p in out.glob("*.pdf"))))


if __name__ == "__main__":
    main()
