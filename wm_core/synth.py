"""평가/테스트용 합성 이미지 생성 (문서 페이지, 상품 사진 유사 이미지)."""
from __future__ import annotations

import cv2
import numpy as np

A4_150DPI = (1240, 1754)
_WORDS = ("confidential internal report quarterly revenue forecast customer pipeline security "
          "review design architecture roadmap budget approval strategy analysis draft only").split()


def document_page(seed: int = 0, size=A4_150DPI) -> np.ndarray:
    """흰 배경에 제목/본문/표/그림 상자가 있는 문서 페이지."""
    rng = np.random.default_rng(seed)
    w, h = size
    img = np.full((h, w, 3), 255, np.uint8)
    cv2.putText(img, f"Internal Document #{seed:04d}", (90, 150), cv2.FONT_HERSHEY_DUPLEX, 1.6, (20, 20, 20), 3, cv2.LINE_AA)
    cv2.line(img, (90, 180), (w - 90, 180), (60, 60, 60), 2)
    y = 250
    while y < h - 620:
        x = 90
        while True:
            word = _WORDS[int(rng.integers(len(_WORDS)))]
            (tw, _), _ = cv2.getTextSize(word, cv2.FONT_HERSHEY_SIMPLEX, 0.75, 2)
            if x + tw > w - 90:
                break
            cv2.putText(img, word, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (30, 30, 30), 2, cv2.LINE_AA)
            x += tw + 16
        y += 38 if rng.random() > 0.12 else 80
    ty = h - 560  # 표
    for i in range(6):
        cv2.line(img, (90, ty + i * 50), (w - 480, ty + i * 50), (80, 80, 80), 2)
    for j in range(5):
        xj = 90 + j * (w - 570) // 4
        cv2.line(img, (xj, ty), (xj, ty + 250), (80, 80, 80), 2)
        for i in range(5):
            if j < 4:
                cv2.putText(img, str(int(rng.integers(10, 9999))), (xj + 20, ty + i * 50 + 35),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (40, 40, 40), 2, cv2.LINE_AA)
    img[ty:ty + 250, w - 420:w - 90] = product_photo(seed + 1, (330, 250))  # 삽화
    cv2.putText(img, "- 1 -", (w // 2 - 30, h - 80), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (60, 60, 60), 2, cv2.LINE_AA)
    return img


def product_photo(seed: int = 0, size=(1080, 1080)) -> np.ndarray:
    """부드러운 배경 + 도형 + 질감 잡음으로 이뤄진 자연 영상 유사 이미지."""
    rng = np.random.default_rng(seed)
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.zeros((h, w, 3), np.float32)
    for c in range(3):
        a, b = rng.uniform(60, 200, 2)
        img[..., c] = a + (b - a) * (xx / w * rng.uniform(0.2, 1) + yy / h * rng.uniform(0.2, 1)) / 1.5
    img = np.clip(img, 0, 255).astype(np.uint8)
    for _ in range(8):
        color = tuple(int(v) for v in rng.integers(20, 235, 3))
        center = (int(rng.integers(0, w)), int(rng.integers(0, h)))
        if rng.random() < 0.5:
            cv2.circle(img, center, int(rng.integers(w // 16, w // 5)), color, -1, cv2.LINE_AA)
        else:
            d = rng.integers(w // 12, w // 4, 2)
            cv2.rectangle(img, center, (center[0] + int(d[0]), center[1] + int(d[1])), color, -1)
    tex = cv2.GaussianBlur(rng.normal(0, 14, (h, w, 1)).astype(np.float32), (0, 0), 1.2)
    if tex.ndim == 2:
        tex = tex[..., None]
    return np.clip(cv2.GaussianBlur(img, (0, 0), 1.5).astype(np.float32) + tex * 3, 0, 255).astype(np.uint8)
