"""민화 모란도 + 단청 테두리 샘플 도안 생성기.

그림을 코드(도형)로 그린 뒤, 숫자 색칠 앱이 쓰는 데이터까지 한 번에 만든다.

출력 (assets/artworks/minhwa_peony_001/):
  color.svg      완성 컬러본
  lineart.svg    선화 (외곽선만)
  numbered.svg   색칠 시작 화면 (선화 + 번호)
  progress.svg   색칠 진행 중 예시 (일부만 칠한 상태)
  regions.png    영역 ID 맵 (2배 해상도, RGB 값 = 영역 ID)
  regions.json   영역 목록 (id, 색 번호, bbox, 번호 위치/크기, 면적)
  meta.json      그림 정보와 팔레트

실행: python3 tools/artgen/minhwa_peony.py
필요 패키지: shapely, numpy, scipy, pillow
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
from shapely.affinity import rotate, scale, translate
from shapely.geometry import MultiPolygon, Point, Polygon, box
from shapely.ops import polylabel, unary_union

SIZE = 1000
ID_SCALE = 2
OUT = Path(__file__).resolve().parents[2] / "assets" / "artworks" / "minhwa_peony_001"

# ---------------------------------------------------------------------------
# 팔레트: 오방색과 단청 색 이름을 바탕으로 한 15색
# ---------------------------------------------------------------------------
PALETTE = [
    ("한지", "#F2E6CC"),
    ("백", "#FBF7EE"),
    ("황", "#E8B94A"),
    ("주홍", "#C9442F"),
    ("연지", "#D9677A"),
    ("분홍", "#EE9FA6"),
    ("연분홍", "#F7D0CC"),
    ("연두", "#B7C98A"),
    ("하엽", "#6F9558"),
    ("뇌록", "#2F6E5E"),
    ("삼청", "#7FA3CC"),
    ("군청", "#2D4A80"),
    ("석", "#BDB2A0"),
    ("석록", "#8D7F6E"),
    ("먹갈", "#6B5444"),
]
C = {name: i + 1 for i, (name, _) in enumerate(PALETTE)}  # 이름 -> 색 번호(1부터)

LINE_COLOR = "#3A2E28"

# 그리는 순서대로 쌓이는 도형 목록: (shapely 도형, 색 번호)
shapes: list[tuple[Polygon, int]] = []


def add(geom, color: int):
    geom = geom.buffer(0)
    if not geom.is_empty:
        shapes.append((geom, color))


# ---------------------------------------------------------------------------
# 기본 도형 도우미
# ---------------------------------------------------------------------------
def circle(x, y, r):
    return Point(x, y).buffer(r, quad_segs=max(8, int(r)))


def ellipse(x, y, rx, ry, deg=0.0):
    e = scale(Point(0, 0).buffer(1, quad_segs=24), rx, ry, origin=(0, 0))
    return translate(rotate(e, deg, origin=(0, 0)), x, y)


def qbez(p0, p1, p2, n=30):
    pts = []
    for i in range(n + 1):
        t = i / n
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]
        pts.append((x, y))
    return pts


def ribbon(pts, w0, w1):
    """중심선을 따라 굵기가 w0 → w1 로 변하는 띠 (줄기, 풀잎)."""
    left, right = [], []
    n = len(pts)
    for i, (x, y) in enumerate(pts):
        ax, ay = pts[max(i - 1, 0)]
        bx, by = pts[min(i + 1, n - 1)]
        tx, ty = bx - ax, by - ay
        ln = math.hypot(tx, ty) or 1
        nx, ny = -ty / ln, tx / ln
        w = w0 + (w1 - w0) * i / (n - 1)
        left.append((x + nx * w, y + ny * w))
        right.append((x - nx * w, y - ny * w))
    return Polygon(left + right[::-1])


def chaikin(pts, it=3):
    for _ in range(it):
        out = []
        for i in range(len(pts)):
            p, q = pts[i], pts[(i + 1) % len(pts)]
            out.append((0.75 * p[0] + 0.25 * q[0], 0.75 * p[1] + 0.25 * q[1]))
            out.append((0.25 * p[0] + 0.75 * q[0], 0.25 * p[1] + 0.75 * q[1]))
        pts = out
    return pts


def polar(cx, cy, r, a):
    return (cx + r * math.cos(a), cy + r * math.sin(a))


# ---------------------------------------------------------------------------
# 모티프
# ---------------------------------------------------------------------------
def petal(cx, cy, ang, r_in, r_out, hw, lobes=3, depth=0.08):
    """끝이 물결 모양(꽃잎 갈래)인 부채꼴 꽃잎."""
    pts = []
    side = 12
    for i in range(side + 1):
        t = i / side
        r = r_in + (r_out * (1 - depth) - r_in) * t
        pts.append(polar(cx, cy, r, ang - hw * (0.35 + 0.65 * math.sin(t * math.pi / 2))))
    n = 48
    for i in range(1, n):
        u = i / n
        r = r_out * (1 - depth * (1 - abs(math.sin(math.pi * lobes * u))))
        pts.append(polar(cx, cy, r, ang - hw + 2 * hw * u))
    for i in range(side, -1, -1):
        t = i / side
        r = r_in + (r_out * (1 - depth) - r_in) * t
        pts.append(polar(cx, cy, r, ang + hw * (0.35 + 0.65 * math.sin(t * math.pi / 2))))
    return Polygon(pts)


def peony(cx, cy, R, colors, squash=1.0, deg=0.0, start=-math.pi / 2):
    """겹꽃 모란. colors = (바깥잎, 바깥잎 안쪽, 중간잎, 중간잎 안쪽, 속잎, 꽃술 바탕, 꽃술)."""
    out_c, out_in_c, mid_c, mid_in_c, in_c, center_c, dot_c = colors
    parts = []
    n1 = 8
    for k in range(n1):
        a = start + 2 * math.pi * k / n1
        hw = math.pi / n1 * 1.25
        parts.append((petal(cx, cy, a, 0.12 * R, R, hw, 3), out_c))
        parts.append((petal(cx, cy, a, 0.12 * R, 0.6 * R, hw * 0.72, 2, 0.1), out_in_c))
    n2 = 6
    for k in range(n2):
        a = start + math.pi / n2 + 2 * math.pi * k / n2
        hw = math.pi / n2 * 1.1
        parts.append((petal(cx, cy, a, 0.08 * R, 0.7 * R, hw, 3), mid_c))
        parts.append((petal(cx, cy, a, 0.08 * R, 0.42 * R, hw * 0.7, 2, 0.1), mid_in_c))
    n3 = 5
    for k in range(n3):
        a = start + 2 * math.pi * k / n3
        parts.append((petal(cx, cy, a, 0.05 * R, 0.45 * R, math.pi / n3 * 1.1, 2), in_c))
    parts.append((circle(cx, cy, 0.17 * R), center_c))
    for k in range(7):
        x, y = polar(cx, cy, 0.1 * R, 2 * math.pi * k / 7)
        parts.append((circle(x, y, 0.035 * R), dot_c))
    for g, c in parts:
        g = scale(g, 1, squash, origin=(cx, cy))
        g = rotate(g, deg, origin=(cx, cy))
        add(g, c)


def leaf(base, deg, L, W, colors=("뇌록", "하엽"), bend=0.12):
    """갈래가 진 모란 잎. 잎맥을 따라 두 쪽(진한 쪽/연한 쪽)으로 나뉜다."""
    a = math.radians(deg)
    d = (math.cos(a), math.sin(a))
    nrm = (-d[1], d[0])
    left, right, mid = [], [], []
    N = 40
    for i in range(N + 1):
        t = i / N
        c = bend * L * math.sin(math.pi * t)
        m = (base[0] + d[0] * t * L + nrm[0] * c, base[1] + d[1] * t * L + nrm[1] * c)
        hw = W * (math.sin(math.pi * t) ** 0.8)
        hw *= 1 - 0.35 * math.exp(-(((t - 0.45) / 0.05) ** 2))
        hw *= 1 - 0.3 * math.exp(-(((t - 0.72) / 0.04) ** 2))
        left.append((m[0] + nrm[0] * hw, m[1] + nrm[1] * hw))
        right.append((m[0] - nrm[0] * hw, m[1] - nrm[1] * hw))
        mid.append(m)
    add(Polygon(left + mid[::-1]), C[colors[0]])
    add(Polygon(right + mid[::-1]), C[colors[1]])


def bud(cx, cy, s):
    """꽃봉오리: 양옆 꽃잎 + 가운데 꽃잎 + 꽃받침."""

    def drop(w, h):
        pts = []
        for i in range(60):
            t = 2 * math.pi * i / 60
            pts.append((w * math.sin(t) * math.sin(t / 2), -h * math.cos(t)))
        return Polygon(pts)

    bottom = (cx, cy + s)
    for sgn in (-1, 1):
        g = translate(drop(0.62 * s, s), cx, cy)
        add(rotate(g, sgn * 22, origin=bottom), C["분홍"])
    add(translate(drop(0.55 * s, 0.95 * s), cx, cy + 0.05 * s), C["연지"])
    for deg, col in ((-145, ("뇌록", "하엽")), (-35, ("하엽", "연두"))):
        leaf((cx, cy + 1.02 * s), deg, 0.62 * s, 0.2 * s, col, bend=0.05)


def butterfly(cx, cy, s, deg):
    parts = []
    for sgn in (-1, 1):
        parts.append((ellipse(cx + sgn * 0.28 * s, cy + 0.22 * s, 0.3 * s, 0.22 * s, sgn * 30), C["분홍"]))
    for sgn in (-1, 1):
        parts.append((ellipse(cx + sgn * 0.4 * s, cy - 0.16 * s, 0.46 * s, 0.3 * s, -sgn * 25), C["황"]))
        parts.append((circle(cx + sgn * 0.5 * s, cy - 0.2 * s, 0.11 * s), C["주홍"]))
        parts.append((circle(cx + sgn * 0.3 * s, cy + 0.26 * s, 0.07 * s), C["군청"]))
    parts.append((ellipse(cx, cy, 0.08 * s, 0.46 * s), C["먹갈"]))
    for sgn in (-1, 1):
        ant = qbez((cx, cy - 0.4 * s), (cx + sgn * 0.1 * s, cy - 0.75 * s), (cx + sgn * 0.3 * s, cy - 0.85 * s), 12)
        parts.append((ribbon(ant, 1.6, 1.2), C["먹갈"]))
    for g, c in parts:
        add(rotate(g, deg, origin=(cx, cy)), c)


def dancheong_cloud(cx, cy, s):
    """단청 구름: 바깥에서 안으로 군청 → 삼청 → 백 띠(바림)."""
    blobs = [(-1.0, 0.1, 0.42), (-0.5, -0.3, 0.5), (0.1, -0.42, 0.55), (0.65, -0.2, 0.45), (1.05, 0.12, 0.35)]
    g = unary_union([circle(cx + x * s, cy + y * s, r * s) for x, y, r in blobs]
                    + [box(cx - 1.0 * s, cy, cx + 1.05 * s, cy + 0.35 * s)])
    add(g, C["군청"])
    add(g.buffer(-0.13 * s), C["삼청"])
    add(g.buffer(-0.26 * s), C["백"])


def lotus_rosette(cx, cy, r):
    """단청 머리초의 연화(연꽃) 문양을 단순화한 원형 장식."""
    add(circle(cx, cy, r), C["삼청"])
    for k in range(8):
        a = math.pi * 2 * k / 8
        x, y = polar(cx, cy, r * 0.5, a)
        add(ellipse(x, y, r * 0.45, r * 0.24, math.degrees(a)), C["연분홍"])
    add(circle(cx, cy, r * 0.33), C["황"])
    add(circle(cx, cy, r * 0.15), C["주홍"])


# ---------------------------------------------------------------------------
# 구도
# ---------------------------------------------------------------------------
def build():
    # 1) 단청 테두리
    add(box(0, 0, SIZE, SIZE), C["군청"])
    add(box(22, 22, SIZE - 22, SIZE - 22), C["주홍"])
    add(box(32, 32, SIZE - 32, SIZE - 32), C["뇌록"])
    band = 60  # 띠 중심선
    skip = [(0, 150), (420, 580), (850, 1000)]  # 모서리와 가운데 장식 자리
    k = 0
    for i in range(41):
        t = 100 + i * 20.0
        if any(a <= t <= b for a, b in skip):
            continue
        col = C["황"] if k % 2 == 0 else C["백"]
        k += 1
        for x, y in ((t, band), (t, SIZE - band), (band, t), (SIZE - band, t)):
            add(circle(x, y, 7.5), col)
    add(box(88, 88, SIZE - 88, SIZE - 88), C["백"])
    add(box(94, 94, SIZE - 94, SIZE - 94), C["한지"])
    for x, y in ((band, band), (SIZE - band, band), (band, SIZE - band), (SIZE - band, SIZE - band)):
        lotus_rosette(x, y, 46)
    for x, y in ((SIZE / 2, band), (SIZE / 2, SIZE - band), (band, SIZE / 2), (SIZE - band, SIZE / 2)):
        lotus_rosette(x, y, 30)

    # 2) 구름
    dancheong_cloud(215, 200, 68)
    dancheong_cloud(815, 805, 46)
    dancheong_cloud(790, 165, 50)

    # 3) 줄기 (괴석 뒤에서 올라옴)
    stem = C["먹갈"]
    add(ribbon(qbez((470, 680), (530, 520), (500, 400)), 6.5, 4.5), stem)
    add(ribbon(qbez((510, 690), (630, 650), (680, 560)), 6, 4), stem)
    add(ribbon(qbez((440, 680), (350, 500), (335, 360)), 5.5, 3.5), stem)

    # 4) 괴석
    rock = Polygon(chaikin([(300, 908), (282, 840), (312, 790), (288, 740), (330, 700), (318, 650),
                            (372, 612), (420, 632), (452, 598), (502, 616), (522, 662), (566, 690),
                            (546, 742), (578, 792), (560, 852), (592, 908)]))
    add(rock, C["석"])
    add(rock.intersection(Polygon([(270, 700), (372, 690), (352, 780), (402, 862), (300, 930), (240, 930)])), C["석록"])
    add(rock.intersection(Polygon([(482, 598), (590, 640), (606, 770), (522, 722)])), C["석록"])
    for x, y, rx, ry, d in ((392, 700, 20, 30, 10), (482, 765, 17, 26, -15), (424, 832, 26, 17, 5), (512, 668, 11, 15, 0)):
        add(ellipse(x, y, rx, ry, d), C["먹갈"])

    # 5) 땅과 풀
    top = [(x, 878 + 7 * math.sin(x / 38)) for x in range(94, 907, 6)]
    add(Polygon(top + [(906, 906), (94, 906)]), C["연두"])
    rnd = random.Random(7)
    for x in list(range(130, 290, 26)) + list(range(610, 880, 26)):
        h = rnd.uniform(40, 75)
        lean = rnd.uniform(-25, 25)
        add(ribbon(qbez((x, 890), (x + lean * 0.3, 890 - h * 0.6), (x + lean, 890 - h), 14), 4.5, 0.8), C["하엽"])

    # 6) 잎
    leaf((508, 535), -25, 115, 34)
    leaf((508, 548), 197, 118, 35, ("하엽", "연두"))
    leaf((498, 592), -6, 118, 35, ("하엽", "연두"))
    leaf((486, 615), 186, 112, 34)
    leaf((600, 640), -65, 100, 30, ("하엽", "연두"))
    leaf((645, 600), 18, 105, 32)
    leaf((372, 480), 205, 105, 32)
    leaf((385, 430), -40, 90, 28, ("하엽", "연두"))
    leaf((350, 390), 155, 85, 26)
    leaf((420, 625), 140, 100, 30, ("하엽", "연두"))

    # 7) 모란 (붉은 큰 꽃, 흰 꽃, 봉오리)
    peony(500, 350, 140, (C["연분홍"], C["분홍"], C["분홍"], C["연지"], C["주홍"], C["황"], C["주홍"]))
    peony(690, 535, 105, (C["백"], C["연분홍"], C["연분홍"], C["분홍"], C["연지"], C["황"], C["주홍"]),
          squash=0.72, deg=-15)
    bud(334, 296, 52)

    # 8) 나비 (모란과 나비: 부귀와 기쁨)
    butterfly(785, 300, 62, 18)
    butterfly(195, 540, 46, -22)


# ---------------------------------------------------------------------------
# 도형 → 숫자 색칠 영역
# ---------------------------------------------------------------------------
def visible_regions():
    """위에 그린 도형을 빼서, 실제로 보이는 부분만 영역으로 남긴다."""
    covered = Polygon()
    regions = []
    for geom, color in reversed(shapes):
        vis = geom.difference(covered).buffer(0)
        covered = unary_union([covered, geom])
        parts = list(vis.geoms) if isinstance(vis, MultiPolygon) else [vis]
        for p in parts:
            if isinstance(p, Polygon) and p.area > 0.5:
                regions.append({"geom": p, "color": color})
    regions.reverse()
    return regions


def inscribed(geom):
    poly = max(geom.geoms, key=lambda g: g.area) if isinstance(geom, MultiPolygon) else geom
    pt = polylabel(poly, tolerance=0.3)
    return pt, poly.exterior.distance(pt) if not poly.interiors else poly.boundary.distance(pt)


def merge_tiny(regions, min_r=2.4, min_area=30):
    """너무 작아서 칠하기 어려운 영역은 가장 많이 맞닿은 이웃에 합친다."""
    changed = True
    while changed:
        changed = False
        for i, r in enumerate(regions):
            _, rad = inscribed(r["geom"])
            if rad >= min_r and r["geom"].area >= min_area:
                continue
            grown = r["geom"].buffer(0.8)
            best, best_len = None, 0.0
            for j, o in enumerate(regions):
                if j != i and grown.intersects(o["geom"]):
                    shared = grown.intersection(o["geom"]).area
                    if shared > best_len:
                        best, best_len = j, shared
            if best is None:
                continue
            o = regions[best]
            o["geom"] = unary_union([o["geom"], r["geom"].buffer(0.05)]).buffer(0)
            regions.pop(i)
            changed = True
            break
    return regions


def svg_path(geom):
    polys = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
    d = []
    for p in polys:
        for ring in [p.exterior, *p.interiors]:
            pts = list(ring.coords)[:-1]
            d.append("M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in pts) + "Z")
    return "".join(d)


def write_svg(path, regions, mode, colored_ids=frozenset()):
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}" width="{SIZE}" height="{SIZE}">']
    out.append(f'<rect width="{SIZE}" height="{SIZE}" fill="#ffffff"/>')
    for r in regions:
        if mode == "color" or r["id"] in colored_ids:
            fill = PALETTE[r["color"] - 1][1]
        elif mode == "lineart":
            fill = "#ffffff"
        else:
            fill = "#F7F5F1"
        out.append(f'<path data-id="{r["id"]}" data-color="{r["color"]}" d="{r["d"]}" fill="{fill}" '
                   f'fill-rule="evenodd" stroke="{LINE_COLOR}" stroke-width="1.3" stroke-linejoin="round"/>')
    if mode in ("numbered", "progress"):
        out.append('<g font-family="Noto Sans, Arial, sans-serif" fill="#8A7F74" text-anchor="middle" '
                   'dominant-baseline="central">')
        for r in regions:
            if r["id"] in colored_ids:
                continue
            lb = r["label"]
            out.append(f'<text x="{lb["x"]}" y="{lb["y"]}" font-size="{lb["size"]}">{r["color"]}</text>')
        out.append("</g>")
    out.append("</svg>")
    path.write_text("\n".join(out), encoding="utf-8")


def write_id_map(path, regions):
    W = SIZE * ID_SCALE
    img = Image.new("RGB", (W, W), (0, 0, 0))
    dr = ImageDraw.Draw(img)

    def outer_area(g):
        polys = list(g.geoms) if isinstance(g, MultiPolygon) else [g]
        return max(Polygon(p.exterior).area for p in polys)

    # 바깥 윤곽이 큰 영역부터 칠하면, 구멍 안쪽 영역이 나중에 덮어써서 정확해진다
    for r in sorted(regions, key=lambda r: -outer_area(r["geom"])):
        rid = r["id"]
        col = (rid >> 16 & 255, rid >> 8 & 255, rid & 255)
        polys = list(r["geom"].geoms) if isinstance(r["geom"], MultiPolygon) else [r["geom"]]
        for p in polys:
            dr.polygon([(x * ID_SCALE, y * ID_SCALE) for x, y in p.exterior.coords], fill=col)
    arr = np.array(img).astype(np.int32)
    ids = arr[..., 0] << 16 | arr[..., 1] << 8 | arr[..., 2]
    # 경계에서 빈 픽셀은 가장 가까운 영역으로 채운다
    empty = ids == 0
    if empty.any():
        _, (iy, ix) = ndimage.distance_transform_edt(empty, return_indices=True)
        ids = ids[iy, ix]
    rgb = np.stack([(ids >> 16) & 255, (ids >> 8) & 255, ids & 255], axis=-1).astype(np.uint8)
    Image.fromarray(rgb).save(path, optimize=True)
    return ids


def main():
    build()
    regions = merge_tiny(visible_regions())
    for i, r in enumerate(regions, start=1):
        r["id"] = i
        pt, rad = inscribed(r["geom"])
        digits = len(str(r["color"]))
        size = max(4.0, min(24.0, 1.4 * rad, 2.4 * rad / digits))
        r["label"] = {"x": round(pt.x, 1), "y": round(pt.y, 1), "size": round(size, 1)}
        r["d"] = svg_path(r["geom"])

    OUT.mkdir(parents=True, exist_ok=True)
    write_svg(OUT / "color.svg", regions, "color")
    write_svg(OUT / "lineart.svg", regions, "lineart")
    write_svg(OUT / "numbered.svg", regions, "numbered")
    rnd = random.Random(3)
    done = {r["id"] for r in regions if rnd.random() < 0.55}
    write_svg(OUT / "progress.svg", regions, "progress", done)
    write_id_map(OUT / "regions.png", regions)

    data = []
    for r in regions:
        x0, y0, x1, y1 = r["geom"].bounds
        data.append({
            "id": r["id"],
            "color": r["color"],
            "bbox": [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)],
            "label": r["label"],
            "area": round(r["geom"].area, 1),
        })
    (OUT / "regions.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    counts = {}
    for r in regions:
        counts[r["color"]] = counts.get(r["color"], 0) + 1
    meta = {
        "id": "minhwa_peony_001",
        "title": "모란도 (牡丹圖)",
        "description": "괴석 위에 핀 모란과 나비, 단청 구름과 연화 문양 테두리",
        "style": ["민화", "단청"],
        "size": [SIZE, SIZE],
        "idMap": {"file": "regions.png", "scale": ID_SCALE, "encoding": "rgb24"},
        "regionCount": len(regions),
        "palette": [{"index": i + 1, "name": n, "hex": h, "regions": counts.get(i + 1, 0)}
                    for i, (n, h) in enumerate(PALETTE)],
    }
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"regions: {len(regions)}  colors: {len(PALETTE)}  -> {OUT}")


if __name__ == "__main__":
    main()
