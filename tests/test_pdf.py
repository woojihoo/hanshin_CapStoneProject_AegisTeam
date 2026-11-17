"""PDF 유형 분류 및 PDF 발급·역추적 테스트 (PDF 백엔드와 reportlab 이 있을 때만 실행)."""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import cv2
import pytest

from backend.app import pdf_utils
from backend.app.config import Settings
from backend.app.services import ForensicService
from wm_core import attacks as A

pytestmark = pytest.mark.skipif(not pdf_utils.HAVE_PDF, reason="PDF 백엔드 없음")
pytest.importorskip("reportlab")
pytest.importorskip("img2pdf")

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {"01_text_report.pdf": "text", "02_scanned_form.pdf": "scan", "03_slides.pdf": "slide"}


@pytest.fixture(scope="module")
def samples():
    spec = importlib.util.spec_from_file_location("make_sample_pdfs", ROOT / "scripts" / "make_sample_pdfs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with tempfile.TemporaryDirectory() as d:
        argv, sys.argv = sys.argv, ["make_sample_pdfs.py", d]
        try:
            mod.main()
        finally:
            sys.argv = argv
        yield {p.name: p.read_bytes() for p in Path(d).glob("*.pdf")}


def test_classifies_three_types(samples):
    assert {name: pdf_utils.classify_pdf(data)["type"] for name, data in samples.items()} == EXPECTED


def test_pdf_issue_and_trace_each_type(samples):
    with tempfile.TemporaryDirectory() as d:
        svc = ForensicService(Settings(data_dir=Path(d), wm_key=os.urandom(32), hmac_key=os.urandom(32),
                                       enc_key=os.urandom(32), admin_key="k"))
        for name, data in samples.items():
            doc = svc.register_document(name, data)
            assert doc["doc_type"] == EXPECTED[name]
            out_name, out, mime, ref = svc.issue(doc["id"], {"subject_id": "E-" + name})
            assert mime == "application/pdf" and out[:5] == b"%PDF-"
            pages = pdf_utils.render_pdf(out, 120)  # 배포본을 다른 배율로 화면 캡처한 상황
            assert len(pages) == doc["pages"]
            shot, quad = A.screen_shoot(pdf_utils.render_pdf(out, 150)[-1], seed=3)
            for img, kw in ((pages[0], {}), (shot, {"corners": quad.tolist()})):
                res = svc.trace(cv2.imencode(".png", img)[1].tobytes(), **kw)
                assert res["status"] == "identified" and res["token_ref"] == ref, (name, res)
