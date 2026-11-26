# artgen: 코드로 만드는 샘플 도안

## 민화 모란도 + 단청 테두리 (`minhwa_peony.py`)
괴석 위에 핀 모란(붉은 꽃, 흰 꽃, 봉오리)과 나비, 단청 구름, 연화 머리초와 연주문 테두리로 구성했다.
팔레트는 오방색과 단청 색 이름(뇌록, 삼청, 군청, 주홍, 하엽 등)을 바탕으로 한 15색이다.

```bash
pip install -r tools/artgen/requirements.txt
python3 tools/artgen/minhwa_peony.py                               # 도안 데이터 생성
node tools/artgen/render_previews.cjs assets/artworks/minhwa_peony_001   # PNG 미리보기 (Playwright 필요)
```

### 처리 과정
1. 꽃잎, 잎, 구름 같은 모티프를 도형(shapely)으로 그리는 순서대로 쌓는다
2. 위에 그린 도형을 빼서 실제로 보이는 부분만 남기면 칠할 영역이 된다
3. 너무 작은 영역(내접원 반지름 2.4px 미만)은 가장 많이 맞닿은 이웃 영역에 합친다
4. 각 영역 안에서 가장 넓은 지점(polylabel)에 번호를 두고, 넓이에 맞춰 글자 크기를 정한다
5. SVG(컬러/선화/번호/진행 예시), 영역 ID 맵(`regions.png`), `regions.json`, `meta.json`을 출력한다

### 앱에서 쓰는 방법
- 탭한 좌표 × `meta.idMap.scale`에 해당하는 `regions.png` 픽셀의 RGB를 `(R<<16)|(G<<8)|B`로 읽으면 영역 ID가 된다
- `regions.json`의 `color`(팔레트 번호)와 선택한 색이 같으면 그 영역을 칠한다
- 번호는 `label.size × 줌 배율`이 일정 크기 이상일 때만 화면에 그린다
