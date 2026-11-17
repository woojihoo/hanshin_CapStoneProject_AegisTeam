"""촬영본에서 문서(페이지) 사각형을 찾아 정면 시점으로 펴는 원근 보정."""
from __future__ import annotations

import cv2
import numpy as np


def order_quad(pts) -> np.ndarray:
    """4점을 좌상-우상-우하-좌하 순으로 정렬."""
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    s = pts.sum(axis=1)
    d = pts[:, 0] - pts[:, 1]
    tl, br = pts[np.argmin(s)], pts[np.argmax(s)]
    tr, bl = pts[np.argmax(d)], pts[np.argmin(d)]
    return np.array([tl, tr, br, bl], dtype=np.float32)


def _quad_from_mask(mask: np.ndarray, scale: float):
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(c)
    total = mask.shape[0] * mask.shape[1]
    if area < 0.12 * total or area > 0.985 * total:
        return None
    hull = cv2.convexHull(c)
    peri = cv2.arcLength(hull, True)
    quad = None
    for eps in (0.01, 0.02, 0.03, 0.05, 0.08):
        approx = cv2.approxPolyDP(hull, eps * peri, True)
        if len(approx) == 4:
            quad = approx.reshape(4, 2).astype(np.float32)
            break
    if quad is None:
        quad = cv2.boxPoints(cv2.minAreaRect(hull)).astype(np.float32)
    return order_quad(quad / scale)


def detect_page_quads(image: np.ndarray) -> list:
    """페이지 외곽 사각형 후보들을 돌려준다 (없으면 빈 리스트).

    1) 밝기 기준: 어두운 배경 위의 밝은 페이지 (흰 문서)
    2) 배경색 거리 기준: 사진 가장자리 색과 다른 영역 (색 배경·머리띠가 있는 슬라이드)
    """
    img = image if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    h, w = img.shape[:2]
    scale = 1000.0 / max(h, w) if max(h, w) > 1000 else 1.0
    small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale != 1.0 else img
    small = cv2.GaussianBlur(small, (5, 5), 0)
    quads = []

    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    quads.append(_quad_from_mask(th, scale))

    m = max(3, int(0.03 * min(small.shape[:2])))
    border = np.concatenate([small[:m].reshape(-1, 3), small[-m:].reshape(-1, 3),
                             small[:, :m].reshape(-1, 3), small[:, -m:].reshape(-1, 3)])
    bg = np.median(border, axis=0)
    dist = np.abs(small.astype(np.float32) - bg).max(axis=2)
    dist8 = np.clip(dist, 0, 255).astype(np.uint8)
    _, th2 = cv2.threshold(dist8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    quads.append(_quad_from_mask(th2, scale))

    out = []
    for q in quads:
        if q is not None and not any(np.abs(q - o).max() < 0.01 * max(h, w) for o in out):
            out.append(q)
    return out


def detect_page_quad(image: np.ndarray):
    """가장 유력한 페이지 사각형 하나 (실패하면 None)."""
    quads = detect_page_quads(image)
    return quads[0] if quads else None


def quad_aspect(quad: np.ndarray) -> float:
    """사각형의 (가로/세로) 비율 추정."""
    q = np.asarray(quad, dtype=np.float32)
    wlen = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
    hlen = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2
    return float(wlen / max(hlen, 1e-6))


def warp_quad(image: np.ndarray, quad, size) -> np.ndarray:
    """quad(좌상,우상,우하,좌하) 영역을 size=(W,H) 직사각형으로 변환."""
    w, h = int(size[0]), int(size[1])
    dst = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(np.asarray(quad, dtype=np.float32), dst)
    return cv2.warpPerspective(image, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
