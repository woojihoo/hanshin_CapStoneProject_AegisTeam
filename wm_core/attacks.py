"""모의 공격(왜곡) 모음: 재압축, 크롭, 리사이즈, 블러, 잡음, 화면 촬영 시뮬레이션."""
from __future__ import annotations

import cv2
import numpy as np


def jpeg(img: np.ndarray, quality: int) -> np.ndarray:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def crop(img: np.ndarray, frac: float, seed: int = 0) -> np.ndarray:
    """면적 비율 frac 만 남기는 임의 위치 크롭."""
    rng = np.random.default_rng(seed)
    h, w = img.shape[:2]
    ch, cw = int(h * np.sqrt(frac)), int(w * np.sqrt(frac))
    y0 = int(rng.integers(0, h - ch + 1))
    x0 = int(rng.integers(0, w - cw + 1))
    return img[y0:y0 + ch, x0:x0 + cw].copy()


def resize(img: np.ndarray, scale: float) -> np.ndarray:
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    return cv2.resize(img, None, fx=scale, fy=scale, interpolation=interp)


def blur(img: np.ndarray, sigma: float) -> np.ndarray:
    return cv2.GaussianBlur(img, (0, 0), sigma)


def noise(img: np.ndarray, sigma: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = img.astype(np.float32) + rng.normal(0, sigma, img.shape).astype(np.float32)
    return np.clip(out, 0, 255).astype(np.uint8)


def brightness_contrast(img: np.ndarray, gain: float = 1.0, bias: float = 0.0) -> np.ndarray:
    return np.clip(img.astype(np.float32) * gain + bias, 0, 255).astype(np.uint8)


def screen_shoot(img: np.ndarray, seed: int = 0, level: float = 1.0, out_long: int = 2400):
    """모니터에 띄운 페이지를 스마트폰으로 찍는 상황의 근사 시뮬레이션.

    어두운 배경 + 원근 왜곡 + 모아레 + 조명 불균일 + 감마 + 렌즈 블러 + 센서 잡음 + JPEG.
    (실제 촬영을 대체하지는 못한다. 실촬영 검증은 별도로 수행해야 한다.)
    반환: (촬영 이미지, 촬영 이미지 안의 실제 페이지 꼭짓점[좌상,우상,우하,좌하])
    """
    rng = np.random.default_rng(seed)
    h, w = img.shape[:2]
    my, mx = int(h * 0.12), int(w * 0.12)
    canvas = np.full((h + 2 * my, w + 2 * mx, 3), int(rng.integers(25, 70)), np.uint8)
    canvas[my:my + h, mx:mx + w] = img
    H, W = canvas.shape[:2]
    k = out_long / max(H, W)
    oh, ow = int(H * k), int(W * k)

    src = np.array([[0, 0], [W, 0], [W, H], [0, H]], np.float32)
    jit = rng.uniform(-1, 1, (4, 2)) * np.array([ow, oh]) * 0.045 * level
    inset = np.array([[1, 1], [-1, 1], [-1, -1], [1, -1]]) * np.array([ow, oh]) * 0.03 * level
    dst = (src * k + jit + inset).astype(np.float32)
    m = cv2.getPerspectiveTransform(src, dst)
    shot = cv2.warpPerspective(canvas, m, (ow, oh), flags=cv2.INTER_AREA, borderMode=cv2.BORDER_CONSTANT,
                               borderValue=(20, 20, 20)).astype(np.float32)
    page = np.array([[mx, my], [mx + w, my], [mx + w, my + h], [mx, my + h]], np.float32)
    quad = cv2.perspectiveTransform(page[None], m)[0]

    yy, xx = np.mgrid[0:oh, 0:ow].astype(np.float32)
    for c in range(3):  # 모아레: 채널별 위상이 다른 줄무늬
        period = rng.uniform(3.0, 7.0)
        ang = rng.uniform(0, np.pi)
        phase = rng.uniform(0, 2 * np.pi)
        wave = np.sin(2 * np.pi * (np.cos(ang) * xx + np.sin(ang) * yy) / period + phase)
        shot[..., c] *= 1 + 0.04 * level * wave
    gx, gy = rng.uniform(-0.12, 0.12, 2) * level  # 조명 불균일
    shot *= (1 + gx * (xx / ow - 0.5) + gy * (yy / oh - 0.5))[..., None]
    gamma = rng.uniform(0.85, 1.15)
    shot = 255.0 * np.power(np.clip(shot, 0, 255) / 255.0, gamma) * rng.uniform(0.85, 1.0)
    shot = cv2.GaussianBlur(shot, (0, 0), 0.8 + 0.6 * level)
    shot += rng.normal(0, 2.5 * level, shot.shape).astype(np.float32)
    shot = np.clip(shot, 0, 255).astype(np.uint8)
    return jpeg(shot, 85), quad
