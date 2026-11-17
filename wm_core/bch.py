"""이진 BCH(63,24) 부호 (t = 7, 최소 거리 15).

GF(2^6), 원시다항식 x^6 + x + 1. 협의(narrow-sense) BCH: 생성다항식은 alpha^1 ~ alpha^14 의
최소다항식들의 최소공배수(차수 39). 체계적 부호화(앞 24비트 = 메시지)를 쓴다.
복호: 신드롬 14개 -> Berlekamp-Massey -> Chien 탐색. 정정 능력을 넘으면 BCHDecodeError.
외부 의존성 없이 비트 배열(0/1 리스트 또는 numpy 배열)로 동작한다.
"""
from __future__ import annotations

from typing import Sequence, Tuple

import numpy as np

M, N, K, T = 6, 63, 24, 7
_PRIM = 0b1000011  # x^6 + x + 1

_EXP = [0] * (2 * N)
_LOG = [0] * (N + 1)
_x = 1
for _i in range(N):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & (1 << M):
        _x ^= _PRIM
for _i in range(N, 2 * N):
    _EXP[_i] = _EXP[_i - N]


def _mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _inv(a: int) -> int:
    return _EXP[(N - _LOG[a]) % N]


def _poly_mul_gf2(a: int, b: int) -> int:
    """GF(2)[x] 다항식 곱 (정수 비트 표현, bit i = x^i 계수)."""
    out = 0
    while b:
        if b & 1:
            out ^= a
        a <<= 1
        b >>= 1
    return out


def _minimal_poly(i: int) -> int:
    """alpha^i 의 GF(2) 최소다항식."""
    coset, j = [], i % N
    while j not in coset:
        coset.append(j)
        j = (2 * j) % N
    poly = [1]  # GF(64) 계수, 저차항부터
    for c in coset:
        root = _EXP[c]
        nxt = [0] * (len(poly) + 1)
        for k, v in enumerate(poly):
            nxt[k + 1] ^= v
            nxt[k] ^= _mul(v, root)
        poly = nxt
    assert all(v in (0, 1) for v in poly)
    return sum(v << k for k, v in enumerate(poly))


def _generator() -> int:
    g, used = 1, set()
    for i in range(1, 2 * T + 1):
        mp = _minimal_poly(i)
        if mp not in used:
            used.add(mp)
            g = _poly_mul_gf2(g, mp)
    return g


GEN = _generator()
assert GEN.bit_length() - 1 == N - K, "BCH(63,24) 생성다항식 차수 오류"


class BCHDecodeError(Exception):
    """정정 능력(7비트)을 넘는 오류."""


def _bits_to_int(bits: Sequence[int]) -> int:
    """bits[0] 이 최고차항(x^(n-1))."""
    v = 0
    for b in bits:
        v = (v << 1) | (int(b) & 1)
    return v


def _int_to_bits(v: int, n: int) -> np.ndarray:
    return np.array([(v >> (n - 1 - i)) & 1 for i in range(n)], dtype=np.uint8)


def encode(message_bits: Sequence[int]) -> np.ndarray:
    """24비트 -> 63비트 체계적 부호어 (앞 24비트 = 메시지, 뒤 39비트 = 패리티)."""
    if len(message_bits) != K:
        raise ValueError(f"message must be {K} bits")
    m = _bits_to_int(message_bits) << (N - K)
    r, glen = m, GEN.bit_length()
    while r.bit_length() >= glen:
        r ^= GEN << (r.bit_length() - glen)
    return _int_to_bits(m | r, N)


def _syndromes(bits: np.ndarray) -> list:
    """S_j = c(alpha^j), j = 1..2t. bits[i] 은 x^(n-1-i) 의 계수."""
    pos = [N - 1 - i for i in np.flatnonzero(bits)]
    out = []
    for j in range(1, 2 * T + 1):
        s = 0
        for p in pos:
            s ^= _EXP[(p * j) % N]
        out.append(s)
    return out


def decode(word: Sequence[int]) -> Tuple[np.ndarray, int]:
    """63비트 수신어 -> (24비트 메시지, 정정한 비트 수). 실패 시 BCHDecodeError."""
    r = np.asarray(word, dtype=np.uint8).copy() & 1
    if r.shape != (N,):
        raise ValueError(f"word must be {N} bits")
    S = _syndromes(r)
    if not any(S):
        return r[:K].copy(), 0

    # Berlekamp-Massey (오류 위치 다항식 sigma, 저차항부터)
    C, B, L, m, b = [1], [1], 0, 1, 1
    for n in range(2 * T):
        d = S[n]
        for i in range(1, L + 1):
            if i < len(C) and C[i]:
                d ^= _mul(C[i], S[n - i])
        if d == 0:
            m += 1
            continue
        Tm = C[:]
        coef = _mul(d, _inv(b))
        if len(C) < len(B) + m:
            C += [0] * (len(B) + m - len(C))
        for i, bv in enumerate(B):
            C[i + m] ^= _mul(coef, bv)
        if 2 * L <= n:
            L, B, b, m = n + 1 - L, Tm, d, 1
        else:
            m += 1
    if L > T:
        raise BCHDecodeError("too many errors")

    # Chien 탐색: x^p 위치 오류 <=> sigma(alpha^-p) = 0
    errs = []
    for p in range(N):
        xinv = _EXP[(N - p) % N]
        acc, pw = 0, 1
        for c in C[: L + 1]:
            if c:
                acc ^= _mul(c, pw)
            pw = _mul(pw, xinv)
        if acc == 0:
            errs.append(N - 1 - p)
    if len(errs) != L:
        raise BCHDecodeError("error locator has wrong number of roots")
    for i in errs:
        r[i] ^= 1
    if any(_syndromes(r)):
        raise BCHDecodeError("residual syndrome")
    return r[:K].copy(), L


def token_to_bits(token: bytes) -> np.ndarray:
    if len(token) * 8 != K:
        raise ValueError(f"token must be {K // 8} bytes")
    return np.unpackbits(np.frombuffer(token, dtype=np.uint8))


def bits_to_token(bits: Sequence[int]) -> bytes:
    return np.packbits(np.asarray(bits, dtype=np.uint8)).tobytes()
