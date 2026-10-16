# VM 접근 방법 — 자동 반영과 SSH 직결

작성 2026-10-16 · 대상 VM `crypto-bot-base-20260228` (`asia-northeast3-c`)

> 문제: "매번 zip 다운받고 깃허브에 올려놓는 게 귀찮다. 직접 연결해서 수정하는 게 좋다."

결론부터. **SSH 직결은 조건부로 가능하지만 설정 두 개를 바꿔야 한다.**
그런데 귀찮음의 원인은 SSH가 없는 게 아니라 **반영이 수동이라는 것**이고,
그건 설정 변경 없이 오늘 바로 없앨 수 있다.

---

## 0. 지금 환경에서 무엇이 되고 무엇이 안 되는가 (실측)

| 항목 | 결과 |
|---|---|
| `gcloud` CLI 설치 | ✅ Google Cloud SDK 570.0.0 |
| `gcloud` 인증 | ❌ `No credentialed accounts` |
| `oauth2.googleapis.com` | ✅ 도달 (HTTP 404 = 연결됨) |
| `compute.googleapis.com` | ✅ 도달 |
| `oslogin.googleapis.com` | ✅ 도달 |
| **`tunnel.cloudproxy.app`** | ❌ **네트워크 정책이 차단** |
| **22번 포트 아웃바운드** | ❌ **차단** (443만 열림) |
| `ssh.cloud.google.com` 웹 콘솔 | ❌ 차단 + 브라우저 OAuth 전용 |

읽는 법:

- **구글 API는 전부 닿는다.** 즉 인증만 되면 `gcloud compute instances list` 같은
  관리 작업은 바로 된다.
- **SSH 자체가 막혀 있다.** 22번 포트가 닫혔고, 그 대안인 IAP 터널
  (`tunnel.cloudproxy.app`, 443번을 쓴다)은 네트워크 정책에서 거부된다.
- 즉 **막는 요인은 두 개**다: 자격증명 없음 + IAP 호스트 차단. 둘 다 사용자가 풀 수 있다.

---

## 1. 먼저 할 것 — 자동 반영 (설정 변경 없음, 오늘 바로)

귀찮음의 실체는 `zip 만들기 → 업로드 → 압축 풀기 → 재시작`이다.
**VM이 깃허브를 스스로 당겨오게** 하면 이 네 단계가 전부 사라진다.

```
지금:  내가 커밋 → 당신이 zip 받기 → 업로드 → 압축 → 재시작
이후:  내가 커밋 → (5분 내 VM이 알아서 반영)
```

### 설치 (VM에서 1회)

최초 1회만 git 클론으로 바꿔 둔다. 이미 zip으로 받은 상태라면 `.env`와 `data/`를
살려서 옮긴다.

```bash
cd ~
git clone -b claude/trusting-heisenberg-387xeq \
    https://github.com/sayoung001/hello-world app_git
cp app/.env app_git/.env            # 비밀키 이전
cp -r app/data app_git/data 2>/dev/null || true   # 쌓인 데이터 이전
cp -r app/.venv app_git/.venv 2>/dev/null || true # 가상환경 재사용(없으면 setup_vm.sh)
mv app app_zip_backup && mv app_git app
```

그 다음 타이머를 등록한다.

```bash
cd ~/app
bash deploy/install_autopull.sh        # 기본 5분 간격
bash deploy/install_autopull.sh 15     # 15분 간격으로 하려면
```

### 동작

`deploy/autopull.sh`가 하는 일이다.

| 상황 | 동작 |
|---|---|
| 원격에 변경 없음 | 아무것도 안 함. 로그도 남기지 않음 |
| 문서(`*.md`)만 변경 | pull만. **데몬 재시작 안 함** |
| 코드 변경 | pull → 데몬 재시작 |
| `requirements.txt` 변경 | pull → `pip install` → 재시작 |
| **로컬에 직접 고친 게 있음** | **중단.** 덮어쓰지 않고 `git status`를 보여줌 |
| 이력이 갈라짐 | 중단. `--ff-only`라 강제 병합하지 않음 |

`.env`와 `data/`는 git 추적 대상이 아니라 pull이 건드리지 않는다.
`logs/`도 `.gitignore`에 넣었다.

검증한 네 경로:

```
① 변경 없음        → 종료코드 0, 로그 비어 있음
② 문서만 추가      → "문서만 변경 → 데몬 유지"
③ 코드 변경        → pull 후 app.py 가 v2 로 갱신
④ 로컬 수정 있음   → "로컬 변경이 있어 중단", 내가 고친 파일 보존됨
```

### 확인

```bash
systemctl list-timers stock-autopull.timer    # 다음 실행 시각
sudo systemctl start stock-autopull.service   # 즉시 1회
tail -f ~/app/logs/autopull.log
```

> ⚠️ 재시작에 `sudo`가 필요하다. 비밀번호를 묻지 않게 한 줄 넣어야 한다.
> `sudo visudo` 후 맨 아래에:
> ```
> 사용자이름 ALL=(ALL) NOPASSWD: /bin/systemctl restart stock-auto
> ```

---

## 2. SSH 직결 — 하고 싶다면 설정 두 개

자동 반영으로도 **내가 VM의 현재 상태를 볼 수는 없다.** 로그를 읽거나
`check_pipeline`을 돌려 보려면 SSH가 필요하다. 그걸 하려면 아래 둘을 바꿔야 한다.

### 2-1. 네트워크 정책에 IAP 터널 호스트 허용

세션 제목줄의 클라우드 환경 메뉴 → **Edit** → **Network access**에서
**Allowed domains**에 아래를 추가한다 (Allow package managers 체크는 그대로 둔다).

```
tunnel.cloudproxy.app
```

22번 포트는 어차피 못 쓰므로 **IAP 터널이 유일한 경로**다. IAP는 443을 쓰기 때문에
이 호스트만 열리면 동작한다. 설정 설명은
https://code.claude.com/docs/en/cloud-environments#network-access 에 있다.

### 2-2. 서비스 계정 키를 환경 시크릿으로

`gcloud auth login`은 브라우저 OAuth라 이 세션에서 쓸 수 없다.
**서비스 계정 키**가 유일한 방법이다.

콘솔에서(또는 로컬 gcloud로) 만든다.

```bash
PROJECT=project-1013017d-dbec-4884-afa     # 실제 프로젝트 ID로
SA=claude-ssh

gcloud iam service-accounts create $SA --project $PROJECT \
    --display-name "Claude SSH"

# 필요한 권한만 — VM 전체 관리 권한은 주지 않는다
gcloud projects add-iam-policy-binding $PROJECT \
    --member "serviceAccount:${SA}@${PROJECT}.iam.gserviceaccount.com" \
    --role roles/iap.tunnelResourceAccessor
gcloud projects add-iam-policy-binding $PROJECT \
    --member "serviceAccount:${SA}@${PROJECT}.iam.gserviceaccount.com" \
    --role roles/compute.osLogin

gcloud iam service-accounts keys create ~/claude-ssh.json \
    --iam-account "${SA}@${PROJECT}.iam.gserviceaccount.com"
```

IAP가 VM에 닿으려면 방화벽도 한 번 열어야 한다 (IAP 전용 대역이다).

```bash
gcloud compute firewall-rules create allow-iap-ssh --project $PROJECT \
    --direction INGRESS --action allow --rules tcp:22 \
    --source-ranges 35.235.240.0/20
```

그 다음 `~/claude-ssh.json`의 **내용**을 환경 설정의 **Network secrets**
(앱에 따라 API credentials로 표시) 또는 환경변수로 넣는다.
변수명은 `GCP_SA_KEY_JSON`으로 하면 내가 그 이름을 읽는다.

> **채팅에 키를 붙여넣지 마세요.** 환경 설정에만 넣으면 됩니다.
> 새 세션에서 적용됩니다.

### 2-3. 그러면 내가 하는 일

```bash
printf '%s' "$GCP_SA_KEY_JSON" > /tmp/sa.json
gcloud auth activate-service-account --key-file=/tmp/sa.json
gcloud config set project project-1013017d-dbec-4884-afa

gcloud compute ssh crypto-bot-base-20260228 \
    --zone asia-northeast3-c --tunnel-through-iap \
    --command "cd ~/app && git log --oneline -3 && tail -20 logs/daemon.log"
```

---

## 3. 두 방법 비교 — 무엇을 고를까

| | 자동 반영 (1장) | SSH 직결 (2장) |
|---|---|---|
| 설정 변경 | **없음** | 네트워크 정책 + 서비스 계정 키 |
| 오늘 가능 | ✅ | ❌ (새 세션 필요) |
| zip 업로드 제거 | ✅ | ✅ |
| 내가 코드 수정 | 깃허브 커밋으로 | 직접 수정 가능 |
| **내가 로그·상태 확인** | ❌ | ✅ |
| **내가 배치 실행·디버깅** | ❌ | ✅ |
| 자격증명 노출 | 없음 | 장기 키가 환경에 상주 |
| 되돌리기 | `git log`로 추적 | 직접 수정은 이력이 안 남음 |

**권하는 순서**

1. **먼저 1장(자동 반영)을 켠다.** 귀찮음의 90%가 사라지고 위험이 없다.
2. 그래도 "로그를 봐 달라 / 돌려 봐 달라"가 반복되면 2장을 추가한다.

두 개는 배타적이지 않다. 둘 다 켜 두면 평소 반영은 자동으로 되고,
문제가 생겼을 때만 내가 SSH로 들어가 본다. 그게 가장 편한 조합이다.

---

## 4. 보안 메모

SSH 직결을 켜는 것은 **이 세션이 당신의 VM에 들어갈 수 있게 하는 것**이다.
그래서 위 2-2에서 권한을 두 개로만 좁혔다.

- `roles/iap.tunnelResourceAccessor` — 터널 통과만
- `roles/compute.osLogin` — 로그인만. **인스턴스 생성·삭제 권한 없음**
- `roles/owner`나 `roles/editor`는 주지 말 것

그리고 키는 장기 자격증명이다. 더 쓰지 않게 되면 지우는 게 맞다.

```bash
gcloud iam service-accounts keys list \
    --iam-account "claude-ssh@${PROJECT}.iam.gserviceaccount.com"
gcloud iam service-accounts keys delete KEY_ID \
    --iam-account "claude-ssh@${PROJECT}.iam.gserviceaccount.com"
```

`.env`의 거래 API 키(KIS·Anthropic·Telegram)는 이와 **무관하게 VM 안에만** 있다.
SSH를 켜도 그 키들이 이 세션으로 복사되지는 않는다. 다만 내가 VM 안에서
`cat .env`를 할 수 있게 되는 것은 사실이니, 그게 불편하면 1장만 쓰는 것이 맞다.

---

*본 문서는 배포 환경 설정 안내다. 클라우드 콘솔 UI와 IAM 역할 이름은 바뀔 수 있으므로
실행 직전 최신 문서로 확인할 것.*
