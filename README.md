# 미국 52주 신고가 브리프 (자동 갱신 웹사이트)

Finviz 스크리너(52주 신고가 · 미국 · 시총 $2B 이상)를 미국 장마감 후 자동 수집해
GitHub Pages 대시보드로 게시합니다. 서버 비용 없이 GitHub 무료 기능만 사용합니다.

## 설치 (최초 1회, 약 10분)

1. **GitHub 저장소 생성**
   - github.com 로그인 → New repository → 이름 예: `us-new-highs` → Public → Create

2. **이 폴더의 파일 전체 업로드**
   - 저장소 페이지에서 `Add file → Upload files` → 아래 구조 그대로 드래그
   ```
   pipeline.py
   requirements.txt
   README.md
   docs/index.html
   docs/data.json          (샘플 — 첫 자동 실행 시 실제 데이터로 교체됨)
   .github/workflows/daily.yml
   ```
   - 웹 업로드 시 `.github` 폴더가 안 올라가면: `Add file → Create new file` →
     파일명에 `.github/workflows/daily.yml` 입력 후 내용 붙여넣기

3. **GitHub Pages 켜기**
   - 저장소 Settings → Pages → Source: `Deploy from a branch` →
     Branch: `main`, 폴더: `/docs` → Save
   - 1~2분 후 `https://<아이디>.github.io/us-new-highs/` 접속 가능

4. **Actions 권한 확인**
   - Settings → Actions → General → Workflow permissions →
     `Read and write permissions` 선택 → Save

5. **(선택) Claude 정성분석 켜기**
   - Settings → Secrets and variables → Actions → New repository secret
   - Name: `ANTHROPIC_API_KEY`, Value: `sk-ant-...`
   - 미설정 시 룰베이스 정량 시그널로 자동 대체됩니다.

## 자동 실행 스케줄

- 매일 **21:15 UTC** (한국시간 오전 6:15) = 미국 장마감 이후
- 미국 월~금 장마감분이 한국 화~토 아침에 반영됩니다
- 즉시 실행: 저장소 Actions 탭 → `daily-new-highs` → `Run workflow`

## 로컬 실행 (선택)

```bash
pip install -r requirements.txt
python pipeline.py              # docs/data.json 갱신
python -m http.server -d docs   # http://localhost:8000 에서 확인
```

## 참고

- Finviz 무료 페이지를 요청 간 1초 대기로 하루 1회만 조회합니다.
  고빈도/상업적 사용 시 Finviz Elite의 공식 CSV export를 사용하세요.
- `docs/history.csv`에 일별 섹터 breadth가 누적되어 시계열 분석에 쓸 수 있습니다.
