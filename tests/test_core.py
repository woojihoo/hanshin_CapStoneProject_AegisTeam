"""워터마킹 코어 단위 테스트 (논문 사양: 24비트 토큰, BCH(63,24), 계수 쌍 대소 + 다수결).

실행: python -m pytest -q
"""
import os

import numpy as np

from wm_core import TOKEN_BYTES, WatermarkEngine
from wm_core import attacks as A
from wm_core import bch
from wm_core import metrics as M
from wm_core import synth
from wm_core.dwt import dwt2, idwt2

KEY = b"unit-test-key-0123456789abcdef!!"
ENG = WatermarkEngine(KEY)
A4 = synth.A4_150DPI


def test_bch_parameters():
    assert (bch.N, bch.K, bch.T) == (63, 24, 7)
    assert TOKEN_BYTES == 3


def test_bch_corrects_up_to_7_errors():
    rng = np.random.default_rng(1)
    for _ in range(400):
        msg = rng.integers(0, 2, 24).astype(np.uint8)
        cw = bch.encode(msg)
        assert (cw[:24] == msg).all()  # 체계적 부호
        e = int(rng.integers(0, 8))
        r = cw.copy()
        r[rng.choice(63, e, replace=False)] ^= 1
        out, n = bch.decode(r)
        assert (out == msg).all() and n == e


def test_bch_rejects_most_heavy_errors():
    rng = np.random.default_rng(2)
    accepted = 0
    for _ in range(400):
        r = bch.encode(rng.integers(0, 2, 24).astype(np.uint8))
        r[rng.choice(63, 12, replace=False)] ^= 1
        try:
            bch.decode(r)
            accepted += 1
        except bch.BCHDecodeError:
            pass
    assert accepted <= 4  # 무작위 수신어가 복호될 확률은 약 0.15%


def test_dwt_roundtrip():
    a = np.random.default_rng(0).normal(size=(64, 96)).astype(np.float32)
    assert np.allclose(idwt2(dwt2(a)), a, atol=1e-4)


def test_embed_meets_psnr_38db_and_extracts():
    page, token = synth.document_page(3), os.urandom(3)
    wm = ENG.embed(page, token)
    assert wm.shape == page.shape and wm.dtype == np.uint8
    assert M.psnr(page, wm) >= 38.0  # 논문의 비가시성 조건
    res = ENG.extract(wm)
    assert res.ok and res.token == token and res.corrected == 0
    assert res.repetitions >= 50  # 페이지 전체에 반복 삽입


def test_unmarked_and_wrong_key_give_nothing():
    page, token = synth.document_page(4), os.urandom(3)
    assert not ENG.extract(page, page_sizes=[A4]).ok
    other = WatermarkEngine(b"another-key-another-key-another!")
    assert not other.extract(ENG.embed(page, token)).ok


def test_survives_jpeg_crop_noise_blur_brightness():
    page, token = synth.document_page(5), os.urandom(3)
    wm = ENG.embed(page, token)
    for attacked in (A.jpeg(wm, 50), A.crop(wm, 0.3, 1), A.noise(wm, 8), A.blur(wm, 1.2),
                     A.brightness_contrast(wm, 0.8, 10)):
        res = ENG.extract(attacked)
        assert res.ok and res.token == token


def test_resized_copy_recovered_with_registered_size():
    photo, token = synth.product_photo(6), os.urandom(3)
    wm = ENG.embed(photo, token)
    res = ENG.extract(A.jpeg(A.resize(wm, 0.6), 80), page_sizes=[(1080, 1080)])
    assert res.ok and res.token == token


def test_simulated_screen_shot_auto_and_manual():
    page, token = synth.document_page(7), os.urandom(3)
    wm = ENG.embed(page, token)
    shot, quad = A.screen_shoot(wm, seed=11)
    auto = ENG.extract(shot, page_sizes=[A4])
    manual = ENG.extract(shot, corners=quad, page_sizes=[A4])
    assert auto.ok and auto.token == token
    assert manual.ok and manual.token == token
