"""PDF 래스터화 / 재구성 / 유형 분류.

기본 백엔드는 PyMuPDF(fitz)이고, 설치돼 있지 않으면 pypdfium2(렌더링·분석) + img2pdf(재구성)로 대체한다.
"""
from __future__ import annotations

import io
from typing import List

import cv2
import numpy as np

try:
    import fitz  # PyMuPDF

    HAVE_PYMUPDF = True
except Exception:  # pragma: no cover
    fitz = None
    HAVE_PYMUPDF = False

try:
    import pypdfium2 as pdfium

    HAVE_PDFIUM = True
except Exception:  # pragma: no cover
    pdfium = None
    HAVE_PDFIUM = False

try:
    import img2pdf

    HAVE_IMG2PDF = True
except Exception:  # pragma: no cover
    img2pdf = None
    HAVE_IMG2PDF = False

HAVE_PDF = HAVE_PYMUPDF or (HAVE_PDFIUM and HAVE_IMG2PDF)
BACKEND = "pymupdf" if HAVE_PYMUPDF else ("pypdfium2+img2pdf" if HAVE_PDF else None)
_MISSING = "PDF 처리 라이브러리가 없습니다. pymupdf 또는 (pypdfium2 + img2pdf)를 설치하세요."

# 자주 쓰이는 PDF 유형 3가지와 유형별 삽입 프로파일 (strength_scale: 기본 강도에 곱하는 배율).
# 논문 조건(PSNR 38dB 이상)에서 정했다. 스캔형은 1.2배로 올리면 PSNR 이 36.8dB 로 떨어져 1.0 으로 둔다.
PDF_TYPES = {
    "text": {"label": "텍스트 보고서형", "strength_scale": 1.0,
             "desc": "워드·한글 등에서 내보낸 세로 문서. 텍스트 레이어가 있고 흰 배경 비중이 크다."},
    "scan": {"label": "스캔·이미지형", "strength_scale": 1.0,
             "desc": "스캐너·복합기로 만든 문서. 페이지 전체가 이미지이고 종이 질감·잡음이 있다."},
    "slide": {"label": "프레젠테이션형", "strength_scale": 1.0,
              "desc": "발표 자료를 내보낸 가로 문서. 색 배경·도형·차트 비중이 크다."},
}


def _page_stats(content: bytes) -> list:
    """페이지별 (가로pt, 세로pt, 텍스트 글자 수, 가장 큰 이미지의 페이지 면적 대비 비율)."""
    stats = []
    if HAVE_PYMUPDF:
        with fitz.open(stream=content, filetype="pdf") as doc:
            for page in doc:
                w, h = page.rect.width, page.rect.height
                cover = 0.0
                for info in page.get_image_info():
                    x0, y0, x1, y1 = info["bbox"]
                    cover = max(cover, abs((x1 - x0) * (y1 - y0)) / max(w * h, 1e-6))
                stats.append((w, h, len(page.get_text().strip()), min(cover, 1.0)))
    elif HAVE_PDFIUM:
        doc = pdfium.PdfDocument(content)
        try:
            for page in doc:
                w, h = page.get_size()
                cover = 0.0
                for obj in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]):
                    bounds = obj.get_bounds() if hasattr(obj, "get_bounds") else obj.get_pos()
                    l, b, r, t = bounds
                    cover = max(cover, abs((r - l) * (t - b)) / max(w * h, 1e-6))
                stats.append((w, h, page.get_textpage().count_chars(), min(cover, 1.0)))
        finally:
            doc.close()
    else:
        raise RuntimeError(_MISSING)
    return stats


def classify_pdf(content: bytes) -> dict:
    """PDF를 text / scan / slide 중 하나로 분류한다.

    규칙(문서 전체 평균 기준):
      1) 큰 이미지가 페이지의 85% 이상을 덮거나, 70% 이상을 덮으면서 텍스트가 거의 없으면 -> scan
      2) 가로가 세로보다 긴 페이지가 과반이면 -> slide
      3) 그 외 -> text
    """
    stats = _page_stats(content)
    if not stats:
        raise ValueError("페이지가 없는 PDF입니다.")
    n = len(stats)
    chars = sum(s[2] for s in stats) / n
    cover = sum(s[3] for s in stats) / n
    landscape = sum(1 for s in stats if s[0] > s[1] * 1.05) / n
    if cover >= 0.85 or (cover >= 0.70 and chars < 50):
        kind = "scan"
    elif landscape > 0.5:
        kind = "slide"
    else:
        kind = "text"
    return {"type": kind, "label": PDF_TYPES[kind]["label"], "pages": n, "avg_text_chars": round(chars, 1),
            "avg_image_coverage": round(cover, 3), "landscape_ratio": round(landscape, 2)}


def render_pdf(content: bytes, dpi: int) -> List[np.ndarray]:
    """PDF 각 페이지를 BGR uint8 이미지로 렌더링."""
    pages = []
    if HAVE_PYMUPDF:
        with fitz.open(stream=content, filetype="pdf") as doc:
            mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
            for page in doc:
                pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, alpha=False)
                arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)
                arr = arr[:, : pix.width * pix.n].reshape(pix.height, pix.width, pix.n)
                pages.append(cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
    elif HAVE_PDFIUM:
        doc = pdfium.PdfDocument(content)
        try:
            for page in doc:
                rgb = np.asarray(page.render(scale=dpi / 72.0).to_pil().convert("RGB"))
                pages.append(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        finally:
            doc.close()
    else:
        raise RuntimeError(_MISSING)
    return pages


def images_to_pdf(images: List[np.ndarray], dpi: int, jpeg_quality: int = 92) -> bytes:
    """워터마크가 삽입된 페이지 이미지들을 원래 물리 크기의 PDF로 재구성."""
    jpegs = []
    for img in images:
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(jpeg_quality)])
        if not ok:
            raise RuntimeError("JPEG 인코딩 실패")
        jpegs.append(buf.tobytes())
    if HAVE_PYMUPDF:
        doc = fitz.open()
        try:
            for img, jpg in zip(images, jpegs):
                h, w = img.shape[:2]
                page = doc.new_page(width=w * 72.0 / dpi, height=h * 72.0 / dpi)
                page.insert_image(page.rect, stream=jpg)
            return doc.tobytes(deflate=True)
        finally:
            doc.close()
    if HAVE_IMG2PDF:
        out = io.BytesIO()
        img2pdf.convert(jpegs, outputstream=out, layout_fun=img2pdf.get_fixed_dpi_layout_fun((dpi, dpi)))
        return out.getvalue()
    raise RuntimeError(_MISSING)
