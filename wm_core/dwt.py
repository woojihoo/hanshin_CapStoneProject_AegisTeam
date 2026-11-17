"""1단계 Haar DWT. PyWavelets 가 설치돼 있으면 그것을 쓰고, 없으면 동일 결과의 NumPy 구현을 쓴다."""
from __future__ import annotations

import numpy as np

try:  # pragma: no cover - 환경에 따라 다름
    import pywt

    HAVE_PYWT = True
except Exception:  # pragma: no cover
    pywt = None
    HAVE_PYWT = False


def dwt2(a: np.ndarray):
    """짝수 크기 2D 배열 -> (LL, (LH, HL, HH))."""
    if a.shape[0] % 2 or a.shape[1] % 2:
        raise ValueError("dwt2 requires even dimensions")
    if HAVE_PYWT:
        return pywt.dwt2(a, "haar", mode="periodization")
    a00, a01 = a[0::2, 0::2], a[0::2, 1::2]
    a10, a11 = a[1::2, 0::2], a[1::2, 1::2]
    ll = (a00 + a01 + a10 + a11) * 0.5
    lh = (a00 + a01 - a10 - a11) * 0.5
    hl = (a00 - a01 + a10 - a11) * 0.5
    hh = (a00 - a01 - a10 + a11) * 0.5
    return ll, (lh, hl, hh)


def idwt2(coeffs) -> np.ndarray:
    ll, (lh, hl, hh) = coeffs
    if HAVE_PYWT:
        return pywt.idwt2((ll, (lh, hl, hh)), "haar", mode="periodization")
    out = np.empty((ll.shape[0] * 2, ll.shape[1] * 2), dtype=ll.dtype)
    out[0::2, 0::2] = (ll + lh + hl + hh) * 0.5
    out[0::2, 1::2] = (ll + lh - hl - hh) * 0.5
    out[1::2, 0::2] = (ll - lh + hl - hh) * 0.5
    out[1::2, 1::2] = (ll - lh - hl + hh) * 0.5
    return out
