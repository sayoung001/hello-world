# PC 쪽 설치 — 세이브티커 뉴스 수집

VM 은 saveticker.com 에 직접 접근할 수 없습니다(사이트가 클라우드플레어로 서버 접근을
막습니다). 그래서 **PC 의 브라우저가 수집**하고, 작은 스크립트가 그 결과를 git 저장소에
올리며, VM 은 배치 직전에 받아 갑니다. PC 를 여러 대 등록해 두면 **그중 하나만 켜져
있어도** 수집됩니다.

```
PC(Edge 확장) ─ CSV ─▶ saveticker_push.py ─ git push ─▶ saveticker-data 저장소 ─ pull ─▶ VM 배치
```

## 0. 한 번만: 데이터 저장소 만들기 (GitHub)

1. <https://github.com/new> → Repository name **`saveticker-data`** → **Private** →
   "Add a README file" 체크 → Create.
2. VM 이 읽을 토큰: <https://github.com/settings/personal-access-tokens/new>
   - Token name: `vm-saveticker-read` · Expiration: 1년
   - Repository access: **Only select repositories** → `saveticker-data`
   - Permissions → Repository permissions → **Contents: Read-only**
   - Generate 후 값을 복사(한 번만 보여줍니다).
3. VM 의 `.env` 에 추가하고 데몬 재시작:
   ```
   SAVETICKER_DATA_REPO=sayoung001/saveticker-data
   SAVETICKER_DATA_TOKEN=<복사한 값>
   ```
   ```bash
   sudo systemctl restart stock-auto
   ```

## 1. PC 마다: 준비물

- **Python 3.10+** (<https://www.python.org/downloads/> — 설치 때 "Add python.exe to PATH" 체크)
- **Git** (<https://git-scm.com/download/win> — 기본값으로 설치)
- Edge 또는 Chrome

## 2. PC 마다: 저장소 받기

PowerShell 을 열고:

```powershell
cd $env:USERPROFILE
git clone https://github.com/sayoung001/saveticker-data
```

처음 push 할 때 브라우저 로그인 창이 뜹니다(Git Credential Manager). 한 번 로그인하면
그 PC 에 저장됩니다.

## 3. PC 마다: 브라우저 확장 켜기

1. 이 저장소의 `pc/extension` 폴더를 PC 에 둡니다(zip 으로 받아 풀어도 됩니다).
2. Edge: 주소창에 `edge://extensions` (Chrome: `chrome://extensions`)
   → 오른쪽 위 **개발자 모드** 켜기 → **압축 풀린 확장 프로그램 로드** → `pc/extension` 폴더 선택.
3. <https://www.saveticker.com/news> 탭을 열어 둡니다. **이 탭이 열려 있을 때만 수집됩니다.**
   확장 아이콘을 눌러 **실시간 감시**와 **자동 저장**이 켜져 있는지 확인합니다.
4. 1~2분 뒤 `다운로드\saveticker\saveticker_news_YYYY-MM-DD.csv` 가 생기면 됩니다.

> 확장은 공개 프로젝트(MIT)의 것을 그대로 가져왔습니다 — `pc/extension/NOTICE.md`.
> 페이지가 받는 응답을 기록할 뿐, 사이트에 추가 요청을 거의 하지 않습니다(1분에 2페이지).

## 4. PC 마다: 푸시 스크립트 돌리기

이 저장소의 `pc\saveticker_push.py` 와 `pc\saveticker_push.bat` 를 PC 에 둡니다.

**시험 1회:**
```powershell
python pc\saveticker_push.py --once
```
`✅ N줄 기록 → data/2026-10-21.<PC이름>.jsonl` 이 찍히고, GitHub 의 saveticker-data 저장소에
파일이 보이면 성공입니다.

**계속 돌리기:** `pc\saveticker_push.bat` 를 더블클릭(10분마다 반복, 창을 닫으면 멈춤).

**창 없이, 로그인하면 자동으로:** 작업 스케줄러
1. `Win + R` → `taskschd.msc` → 오른쪽 **기본 작업 만들기**
2. 이름 `saveticker push` → 트리거 **로그온할 때** → 동작 **프로그램 시작**
3. 프로그램: `pythonw.exe` 의 전체 경로 (PowerShell 에서 `(Get-Command pythonw).Source` 로 확인)
4. 인수: `"C:\<경로>\pc\saveticker_push.py"` · 시작 위치: `C:\<경로>\pc`
5. 마침 → 작업 속성에서 **"사용자가 로그온할 때만 실행"**, 조건 탭의 "AC 전원일 때만" 체크 해제.

로그: `%USERPROFILE%\saveticker-data\.push_state\push.log`

## 5. 여러 PC

2~4 를 각 PC 에서 반복하면 끝입니다. 각 PC 는 **자기 이름이 붙은 파일만** 씁니다
(`data/날짜.PC이름.jsonl`) — 그래서 동시에 올려도 충돌이 없고, 같은 뉴스가 겹쳐 들어온 것은
VM 이 id 로 합칩니다. PC 이름은 컴퓨터 이름에서 가져오며 `--host 이름` 으로 바꿀 수 있습니다.

## 6. 확인

- PC: `.push_state\push.log` 에 `✅` 줄
- GitHub: `saveticker-data/data/` 에 오늘 날짜 파일
- VM 배치 로그(09:30 KST 이후):
  ```bash
  grep -E "세이브티커|news_collector" ~/auto_trade/logs/daemon.log | tail -5
  ```
  `✅ 세이브티커 pull` 과 `세이브티커 N건 · PICK n · 종목태그 m · 수집PC [...]` 이 보이면 됩니다.

## 자주 생기는 문제

| 증상 | 원인 | 조치 |
|---|---|---|
`CSV 폴더 없음` | 확장이 아직 저장 안 함 / 다른 다운로드 폴더 | 3-4 확인, `--csv-dir` 로 경로 지정 |
`git 저장소 아님` | clone 안 됨 | 2 단계 |
`push 실패 … Authentication` | 로그인 안 됨 | PowerShell 에서 `git -C %USERPROFILE%\saveticker-data push` 한 번 실행해 로그인 |
VM `세이브티커 clone` 실패 | 토큰 없음/만료 | 0-2, 0-3 |
새 뉴스가 안 들어옴 | saveticker 탭이 닫힘 / 실시간 감시 꺼짐 | 3-3 |
