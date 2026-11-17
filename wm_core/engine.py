"""비가시성 워터마크 삽입/추출 엔진 (논문 사양).

토큰   : 배포본별 무작위 24비트 가명 토큰 -> BCH(63,24) 부호화 (7비트 정정)
삽입   : 휘도 Y -> 1단계 Haar DWT -> LL 대역 -> 8x8 블록 DCT.
         블록마다 중주파 계수 쌍 (A, B) 의 대소 관계로 1비트를 표현한다.
         비트 1 이면 A - B >= margin, 비트 0 이면 B - A >= margin 이 되도록 두 계수를 반씩 옮긴다.
         촬영 때 소실되는 고주파 계수는 쓰지 않고, 흰 배경 블록은 강도를 낮춘다.
반복   : (tile x tile) 블록 = 부호어 63비트 + 동기(템플릿) 비트. 이 타일을 페이지 전체에 반복 삽입한다.
역추적 : 원근 보정(페이지 경계 검출 / 꼭짓점 지정) -> 동기 템플릿 상관으로 블록 위치 동기
         -> 반복된 타일마다 계수 쌍 대소로 비트 판정 -> 비트별 다수결 -> BCH 복호.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

import cv2
import numpy as np

from . import bch
from .dwt import dwt2, idwt2
from .rectify import detect_page_quads, quad_aspect, warp_quad

BLOCK = 8  # LL 대역 DCT 블록 크기 (원본 16x16 픽셀)
TOKEN_BYTES = bch.K // 8  # 3바이트 = 24비트

# 중주파 계수 쌍 (u, v): 대각선 대칭인 두 계수를 묶어 밝기·대비 변화에 같이 움직이게 한다.
PAIRS = (((1, 2), (2, 1)), ((1, 3), (3, 1)), ((2, 3), (3, 2)))


@dataclass(frozen=True)
class WMConfig:
    tile_blocks: int = 10        # 타일 한 변의 블록 수 (10 -> 160px 주기, 블록 100개 = 부호 63 + 동기 37)
    margin: float = 36.0         # 계수 쌍 차이 목표값 (LL-DCT 단위). 문서 PSNR 38dB 이상이 되도록 정함
    max_step: float = 2.0        # 계수 쌍 이동량 상한 = max_step x margin (글자 위 과도한 변형 방지)
    white_scale: float = 0.6     # 흰 배경 블록 강도 배율
    headroom: float = 1.5        # 흰색(255) 포화 방지용 계조 압축 (밝기 단계)
    sync_gate: float = 5.5       # 동기 상관 피크 z-score 임계값


@dataclass
class ExtractResult:
    ok: bool                       # BCH 복호 성공 여부
    token: Optional[bytes] = None
    corrected: int = 0             # BCH 가 정정한 비트 수
    sync_score: float = 0.0        # 동기 템플릿 상관 피크 z-score (영역 결합)
    regions_locked: int = 0
    repetitions: int = 0           # 다수결에 쓰인 타일 반복 수
    ber: float = 0.0               # 복호된 부호어 대비 개별 판정(표) 비트 오류율
    view: str = ""                 # 성공한 기하 가설


def _keystream(key: bytes, label: bytes, n: int) -> np.ndarray:
    out, c = bytearray(), 0
    while len(out) < n:
        out += hmac.new(key, label + c.to_bytes(4, "big"), hashlib.sha256).digest()
        c += 1
    return np.frombuffer(bytes(out[:n]), dtype=np.uint8)


def _dct_matrix(n: int = BLOCK) -> np.ndarray:
    k = np.arange(n)[:, None]
    i = np.arange(n)[None, :]
    d = np.cos(np.pi * (2 * i + 1) * k / (2 * n)) * np.sqrt(2.0 / n)
    d[0] *= np.sqrt(0.5)
    return d.astype(np.float32)


def _to_bgr(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    return image


class WatermarkEngine:
    def __init__(self, key: bytes, config: WMConfig = WMConfig()):
        if len(key) < 16:
            raise ValueError("watermark key must be at least 16 bytes")
        self.cfg = cfg = config
        self.tb = tb = cfg.tile_blocks
        self.n_blocks = tb * tb
        if self.n_blocks <= bch.N + 16:
            raise ValueError("tile too small: need room for 63 code bits and sync bits")
        self.tile = tb * BLOCK * 2  # 픽셀 단위 타일 주기
        self._D = _dct_matrix()

        order = np.argsort(_keystream(key, b"perm", 4 * self.n_blocks).view(">u4"), kind="stable")
        self.data_pos = order[: bch.N]       # 부호어 비트 i 가 놓이는 블록
        self.sync_pos = order[bch.N:]        # 동기 템플릿 블록
        self.sync_bits = (_keystream(key, b"sync", len(self.sync_pos)) & 1).astype(np.int8)
        self._ua = np.array([p[0][0] for p in PAIRS])
        self._va = np.array([p[0][1] for p in PAIRS])
        self._ub = np.array([p[1][0] for p in PAIRS])
        self._vb = np.array([p[1][1] for p in PAIRS])

        # 동기 템플릿 (픽셀 영역): 동기 블록의 계수 쌍을 단위량만큼 벌린 패턴
        sym = np.zeros(self.n_blocks, np.float32)
        sym[self.sync_pos] = self.sync_bits * 2.0 - 1.0
        coef = np.zeros((self.n_blocks, BLOCK, BLOCK), np.float32)
        coef[:, self._ua, self._va] = sym[:, None]
        coef[:, self._ub, self._vb] = -sym[:, None]
        blocks = np.einsum("ji,bjk,kl->bil", self._D, coef, self._D)
        ll = blocks.reshape(tb, tb, BLOCK, BLOCK).transpose(0, 2, 1, 3).reshape(tb * BLOCK, tb * BLOCK)
        z = np.zeros_like(ll)
        self._sync_fft_conj = np.conj(np.fft.rfft2(idwt2((ll, (z, z, z)))))

    # ------------------------------------------------------------------ 공통
    def _block_dct(self, ll_blocks: np.ndarray) -> np.ndarray:
        return np.einsum("ij,bjk,lk->bil", self._D, ll_blocks, self._D)

    def _block_idct(self, coef: np.ndarray) -> np.ndarray:
        return np.einsum("ji,bjk,kl->bil", self._D, coef, self._D)

    def codeword(self, token: bytes) -> np.ndarray:
        """24비트 토큰 -> BCH(63,24) 부호어 (0/1, 길이 63)."""
        return bch.encode(bch.token_to_bits(token))

    # ------------------------------------------------------------------ 삽입
    def embed(self, image: np.ndarray, token: bytes, strength: float = 1.0) -> np.ndarray:
        """BGR(또는 Gray) uint8 이미지에 토큰을 삽입해 BGR uint8 로 반환. strength 는 margin 배율."""
        if len(token) != TOKEN_BYTES:
            raise ValueError(f"token must be {TOKEN_BYTES} bytes (24 bits)")
        img = _to_bgr(image)
        cfg = self.cfg
        tb = self.tb
        sym = np.zeros(self.n_blocks, np.int8)
        sym[self.data_pos] = self.codeword(token)
        sym[self.sync_pos] = self.sync_bits
        sign_tile = (sym.astype(np.float32) * 2 - 1).reshape(tb, tb)

        f = img.astype(np.float32)
        h = cfg.headroom
        if h > 0:
            f = h + f * ((255.0 - 2 * h) / 255.0)
        y = 0.114 * f[..., 0] + 0.587 * f[..., 1] + 0.299 * f[..., 2]
        H, W = y.shape
        S = 2 * BLOCK
        ph, pw = (-H) % S, (-W) % S
        yp = np.pad(y, ((0, ph), (0, pw)), mode="edge")
        ll, highs = dwt2(yp)
        by, bx = ll.shape[0] // BLOCK, ll.shape[1] // BLOCK
        blocks = ll.reshape(by, BLOCK, bx, BLOCK).transpose(0, 2, 1, 3).reshape(-1, BLOCK, BLOCK)
        coef = self._block_dct(blocks)

        signs = np.tile(sign_tile, (-(-by // tb), -(-bx // tb)))[:by, :bx].reshape(-1)
        # 흰 배경 블록: 밝고 평탄하면 강도를 낮춘다 (LL 값 = 2 x 픽셀 평균, DC = 8 x LL 평균)
        mean_px = coef[:, 0, 0] / (2.0 * BLOCK)
        flat = np.abs(coef).sum(axis=(1, 2)) - np.abs(coef[:, 0, 0])
        white = (mean_px > 235) & (flat < 40)
        scale = np.where(white, cfg.white_scale, 1.0).astype(np.float32) * float(strength)
        target = cfg.margin * scale

        for k in range(len(PAIRS)):
            a = coef[:, self._ua[k], self._va[k]]
            b = coef[:, self._ub[k], self._vb[k]]
            d = (a - b) * signs                      # 원하는 방향으로의 현재 차이
            need = np.clip(target - d, 0.25 * target, cfg.max_step * target)  # 최소량은 항상 넣어 템플릿을 남김
            coef[:, self._ua[k], self._va[k]] = a + signs * need / 2
            coef[:, self._ub[k], self._vb[k]] = b - signs * need / 2

        blocks = self._block_idct(coef)
        ll = blocks.reshape(by, bx, BLOCK, BLOCK).transpose(0, 2, 1, 3).reshape(by * BLOCK, bx * BLOCK)
        y2 = idwt2((ll, highs))[:H, :W]
        out = f + (y2 - y)[..., None]  # Y 만 바꾸고 색차는 유지
        return np.clip(np.rint(out), 0, 255).astype(np.uint8)

    # ------------------------------------------------------------------ 추출
    @staticmethod
    def _prep(y: np.ndarray) -> np.ndarray:
        """조명·배경 성분 제거 (글자 화소는 배경 추정에서 제외)."""
        y8 = np.clip(y, 0, 255).astype(np.uint8)
        dev = np.abs(y - cv2.medianBlur(y8, 21).astype(np.float32))
        thr = float(np.clip(4.5 * 1.4826 * float(np.median(dev[::4, ::4])) + 2.0, 8.0, 48.0))
        w = 1.0 - cv2.dilate((dev > thr).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(np.float32)
        bg = cv2.GaussianBlur(y * w, (0, 0), 10) / np.maximum(cv2.GaussianBlur(w, (0, 0), 10), 1e-3)
        return y - bg

    def _region_votes(self, r: np.ndarray, ty, tx):
        """타일 인덱스 범위의 영역: 동기 위치를 찾고, 반복된 타일마다 비트 표(+1/-1)를 낸다."""
        T = self.tile
        rr = r[ty[0] * T:(ty[-1] + 1) * T, tx[0] * T:(tx[-1] + 1) * T]
        hh, ww = rr.shape
        if hh < T or ww < T:
            return None
        ph, pw = (-hh) % T, (-ww) % T
        valid = np.pad(np.ones_like(rr), ((0, ph), (0, pw)))
        rp = np.pad(rr, ((0, ph), (0, pw)))
        ny, nx = rp.shape[0] // T, rp.shape[1] // T
        acc = rp.reshape(ny, T, nx, T).sum(axis=(0, 2))
        cnt = valid.reshape(ny, T, nx, T).sum(axis=(0, 2))
        folded = np.where(cnt > 0, acc / np.maximum(cnt, 1), 0).astype(np.float32)

        # 템플릿 상관으로 블록 동기 (타일 주기 안의 위상 찾기)
        corr = np.fft.irfft2(np.fft.rfft2(folded) * self._sync_fft_conj, s=folded.shape)
        sd = float(corr.std())
        if sd <= 1e-9:
            return None
        oy, ox = np.unravel_index(int(np.argmax(corr)), corr.shape)
        z = float((corr[oy, ox] - corr.mean()) / sd)

        # 위상에 맞춰 잘라낸, 완전히 들어온 타일들
        tiles = []
        for y0 in range(oy, hh - T + 1, T):
            for x0 in range(ox, ww - T + 1, T):
                tiles.append(rr[y0:y0 + T, x0:x0 + T])
        if not tiles:  # 영역이 타일 하나 크기뿐이면 접은 결과를 한 번의 반복으로 쓴다
            tiles = [np.roll(folded, (-oy, -ox), axis=(0, 1))]
        tb = self.tb
        stack = np.stack(tiles).astype(np.float32)
        lls = np.stack([dwt2(t)[0] for t in stack])  # (R, tb*8, tb*8)
        R = lls.shape[0]
        blocks = lls.reshape(R, tb, BLOCK, tb, BLOCK).transpose(0, 1, 3, 2, 4).reshape(R * self.n_blocks, BLOCK, BLOCK)
        coef = self._block_dct(blocks).reshape(R, self.n_blocks, BLOCK, BLOCK)
        diff = coef[:, :, self._ua, self._va] - coef[:, :, self._ub, self._vb]  # (R, blocks, pairs)
        votes = np.sign(diff)                                                     # 계수 쌍 대소 판정
        sync_votes = votes[:, self.sync_pos, :] * (self.sync_bits * 2 - 1)[None, :, None]
        sync_agree = float((sync_votes > 0).mean())
        return z, sync_agree, votes[:, self.data_pos, :], diff[:, self.data_pos, :]

    def _decode_view(self, y: np.ndarray) -> ExtractResult:
        T = self.tile
        H, W = y.shape
        best = ExtractResult(ok=False)
        if H < T or W < T:
            return best
        r = self._prep(y)
        ny, nx = -(-H // T), -(-W // T)
        grids = [1] + [g for g in (2, 3) if min(ny, nx) >= 2 * g]
        for g in grids:
            vote_sum = np.zeros(bch.N, np.float32)
            diff_sum = np.zeros(bch.N, np.float32)
            n_votes, z2, locked, reps = 0, 0.0, 0, 0
            for ty in np.array_split(np.arange(ny), g):
                for tx in np.array_split(np.arange(nx), g):
                    res = self._region_votes(r, ty, tx)
                    if res is None:
                        continue
                    z, sync_agree, votes, diff = res
                    if z < self.cfg.sync_gate or sync_agree <= 0.5:
                        continue
                    vote_sum += votes.sum(axis=(0, 2))
                    diff_sum += diff.sum(axis=(0, 2))
                    n_votes += votes.shape[0] * votes.shape[2]
                    z2 += z * z
                    locked += 1
                    reps += votes.shape[0]
            if not locked:
                continue
            # 다수결 (동수면 차이 합의 부호)
            word = np.where(vote_sum != 0, vote_sum > 0, diff_sum > 0).astype(np.uint8)
            cand = ExtractResult(ok=False, sync_score=float(np.sqrt(z2)), regions_locked=locked, repetitions=reps)
            try:
                msg, nerr = bch.decode(word)
            except bch.BCHDecodeError:
                if cand.sync_score > best.sync_score:
                    best = cand
                continue
            cw = bch.encode(msg).astype(np.float32) * 2 - 1
            agree_votes = (vote_sum * cw + n_votes) / 2  # 부호어와 같은 방향의 표 수
            cand.ok, cand.token, cand.corrected = True, bch.bits_to_token(msg), nerr
            cand.ber = float(1 - agree_votes.sum() / max(n_votes * bch.N, 1))
            return cand
        return best

    def extract(
        self,
        image: np.ndarray,
        corners: Optional[Sequence[Sequence[float]]] = None,
        page_sizes: Optional[Iterable[Sequence[int]]] = None,
        deep: bool = False,
    ) -> ExtractResult:
        """유출 의심 이미지에서 토큰 추출.

        corners    : 사용자가 지정한 페이지 네 꼭짓점 (좌상,우상,우하,좌하). 없으면 자동 검출.
        page_sizes : 삽입 당시 페이지 픽셀 크기 (W,H) 후보. 촬영본/리사이즈본 복원에 사용.
        deep       : 배율을 모르는 사본에 대해 넓은 배율 탐색까지 수행.
        """
        img = _to_bgr(image)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        sizes = [(int(a), int(b)) for a, b in (page_sizes or [])]
        best = ExtractResult(ok=False)

        def attempt(view: np.ndarray, label: str) -> Optional[ExtractResult]:
            nonlocal best
            res = self._decode_view(view.astype(np.float32))
            res.view = label
            if res.ok:
                return res
            if res.sync_score > best.sync_score:
                best = res
            return None

        def scaled(view: np.ndarray, s: float) -> np.ndarray:
            if s == 1.0:
                return view
            return cv2.resize(view, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)

        hit = attempt(gray, "direct")  # 디지털 사본·크롭·재압축
        if hit:
            return hit
        H, W = gray.shape
        for (pw, ph) in sizes:  # 등록된 원본 크기로 되돌리기
            if (pw, ph) != (W, H) and abs((W / H) / (pw / ph) - 1) < 0.02:
                interp = cv2.INTER_AREA if pw < W else cv2.INTER_LINEAR
                hit = attempt(cv2.resize(gray, (pw, ph), interpolation=interp), f"resize:{pw}x{ph}")
                if hit:
                    return hit

        quads = []  # 촬영본: 페이지 경계 -> 원근 보정
        if corners is not None:
            quads.append(("manual", np.asarray(corners, np.float32).reshape(4, 2), (0,)))
        for k, auto in enumerate(detect_page_quads(img)):
            quads.append((f"auto{k}", auto, (0, 2, 1, 3)))
        for name, quad, rots in quads:
            for rot in rots:
                q = np.roll(quad, -rot, axis=0)
                asp = quad_aspect(q)
                for (pw, ph) in sizes:
                    if not 0.75 < asp / (pw / ph) < 1.33:
                        continue
                    base = warp_quad(gray, q, (pw, ph))
                    for s in (1.0, 0.99, 1.01, 0.98, 1.02):
                        hit = attempt(scaled(base, s), f"{name}:rot{rot * 90}:{pw}x{ph}:s{s}")
                        if hit:
                            return hit

        if deep:
            for s in np.exp(np.arange(np.log(0.5), np.log(2.0), 0.01)):
                if abs(s - 1.0) < 0.004:
                    continue
                hit = attempt(scaled(gray, float(s)), f"direct:s{s:.3f}")
                if hit:
                    return hit
        return best
