#!/bin/bash
# 이 폴더를 GitHub 저장소에 올린다. 맥 터미널에서:  bash upload_to_github.sh
# - data/(개발용 키·DB), .env, _archive/ 는 .gitignore 로 제외된다.
# - 저장소에 이미 커밋(README 등)이 있으면 그 위에 이어서 커밋한다.
set -euo pipefail
cd "$(dirname "$0")"
REPO="https://github.com/woojihoo/hanshin_CapStoneProject_AegisTeam.git"
BRANCH="main"

if ! git config user.name >/dev/null || ! git config user.email >/dev/null; then
  echo "git 사용자 정보가 없습니다. 먼저 아래 두 줄을 실행하세요:"
  echo '  git config --global user.name "GitHub 사용자명"'
  echo '  git config --global user.email "GitHub에 등록된 이메일"'
  exit 1
fi

[ -d .git ] || git init -q -b "$BRANCH"
git remote get-url origin >/dev/null 2>&1 || git remote add origin "$REPO"

# 원격에 이미 브랜치가 있으면 그 이력 위에서 시작 (작업 파일은 그대로 둔다)
if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
  git fetch -q origin "$BRANCH"
  if ! git rev-parse -q --verify HEAD >/dev/null; then
    git reset -q "origin/$BRANCH"
  fi
fi

git add -A
if git diff --cached --quiet; then
  echo "올릴 변경 사항이 없습니다."
else
  git commit -q -m "가명 토큰 기반 문서 포렌식 마킹 시스템 1학기 MVP (논문 사양)

- wm_core: DWT LL 8x8 DCT 계수 쌍 대소 삽입, 24비트 토큰 BCH(63,24), 반복 삽입·다수결, 원근 보정
- backend: FastAPI /issue /trace /verify, AES-GCM 발급 원장(해시 체인), 그림 2 판정 절차
- frontend: 관제 UI (문서 등록·배포, 역추적, 감사 로그)
- eval·tests·samples: 모의 공격 평가, 단위 테스트, PDF 유형 3종 샘플"
  echo "커밋 완료: $(git log -1 --oneline)"
fi

echo "GitHub 로 올리는 중... (비밀번호를 물으면 GitHub 비밀번호 대신 Personal Access Token 을 입력)"
git push -u origin "$BRANCH"
echo "완료: https://github.com/woojihoo/hanshin_CapStoneProject_AegisTeam"
