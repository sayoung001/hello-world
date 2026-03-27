"""
bot_watchdog.py — 자동매매 봇 감시 + 오류 자동 분석 + 노션 보고 시스템
═══════════════════════════════════════════════════════════════
기능:
  1. 봇 프로세스 생존 감시 → 죽으면 재시작 + 텔레그램 알림
  2. 로그 오류 감지 → 분석 보고서 생성
  3. 노션에 일일 보고서 자동 작성
  4. 텔레그램 명령 수신 → 파일 수정/재시작

사용법:
  # .env에 추가:
  # NOTION_TOKEN=ntn_xxxxx
  # NOTION_PAGE_ID=abc123def456
  # TELEGRAM_TOKEN_WATCHDOG=xxx  (별도 봇 또는 기존 봇 공유)
  # TELEGRAM_CHAT_ID=xxx

  python bot_watchdog.py                    # 기본 실행
  python bot_watchdog.py --check-only       # 1회 점검만
  python bot_watchdog.py --no-notion        # 노션 없이 실행
"""

import os, sys, json, time, subprocess, requests, traceback, re, math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

try:
    import ccxt
except ImportError:
    print("[WARNING] ccxt 미설치 — pip install ccxt (바이낸스 연동 비활성)")
    ccxt = None

KST = timezone(timedelta(hours=9))

# ═══════════════════════════════════════════════════════════
#  설정
# ═══════════════════════════════════════════════════════════

CONFIG = {
    # 텔레그램 (기존 봇과 같은 토큰 사용 가능)
    "TELEGRAM_TOKEN": os.environ.get("TELEGRAM_TOKEN_WATCHDOG",
                      os.environ.get("TELEGRAM_TOKEN_TRADER",
                      os.environ.get("TELEGRAM_TOKEN_MONITOR", ""))),
    "TELEGRAM_CHAT_ID": os.environ.get("TELEGRAM_CHAT_ID", ""),

    # 노션
    "NOTION_TOKEN": os.environ.get("NOTION_TOKEN", ""),
    "NOTION_PAGE_ID": os.environ.get("NOTION_PAGE_ID", "32c6b195d47f8037b433c171b134b9f1"),
    "NOTION_VERSION": "2022-06-28",

    # 감시 대상 봇 (2계정 운영: v9=메인계정, semi=서브계정)
    # .env 키 매핑:
    #   v9:   BINANCE_API_KEY / BINANCE_SECRET / TELEGRAM_TOKEN_TRADER / TELEGRAM_CHAT_ID
    #   semi: BINANCE_API_KEY_SEMI / BINANCE_SECRET_SEMI / TELEGRAM_TOKEN_SEMI / TELEGRAM_CHAT_ID_SEMI
    "BOTS": {
        "v9": {
            "name": "V9 자동매매 (메인계정)",
            "cmd": "python3 auto_trader_v9.py --mode E --live",
            "account": "main",
            "positions_files": [
                "trade_logs/positions_v9.json",
            ],
            "history_files": [
                "trade_logs/trade_history_v9.jsonl",
            ],
            "tmux_session": "bots",
        },
        "semi": {
            "name": "Semi-Auto V8.2 (서브계정)",
            "cmd": "python3 semi_auto_trader.py --live",
            "account": "semi",
            "positions_files": [
                "trade_logs_semi/positions_semi.json",
            ],
            "history_files": [
                "trade_logs_semi/trade_history_semi.jsonl",
            ],
            "tmux_session": "bots_2",
        },
    },

    # 바이낸스 계정 (ccxt 연동)
    "ACCOUNTS": {
        "main": {
            "name": "메인계정",
            "api_key": os.environ.get("BINANCE_API_KEY", ""),
            "secret": os.environ.get("BINANCE_SECRET", ""),
        },
        "semi": {
            "name": "서브계정",
            "api_key": os.environ.get("BINANCE_API_KEY_SEMI", ""),
            "secret": os.environ.get("BINANCE_SECRET_SEMI", ""),
        },
    },

    # 감시 주기
    "CHECK_INTERVAL_SEC": 300,     # 5분마다 점검
    "DAILY_REPORT_HOUR": 8,       # 매일 오전 8시 일일 보고
    "ERROR_PATTERNS": [
        r"Traceback \(most recent call last\)",
        r"Error|ERROR|Exception",
        r"🔴 오류",
        r"SL 설정 완전 실패",
        r"긴급 청산",
        r"수동 확인 필요",
        r"ConnectionError|TimeoutError|APIError",
    ],

    # 작업 디렉토리
    "WORK_DIR": os.path.dirname(os.path.abspath(__file__)),
    "REPORT_DIR": "trade_logs/reports",
}


# ═══════════════════════════════════════════════════════════
#  텔레그램
# ═══════════════════════════════════════════════════════════

class TG:
    def __init__(self, token, chat_id):
        self.token = token; self.chat_id = chat_id
        self.ok = bool(token and chat_id)

    def send(self, msg):
        if not self.ok:
            print(f"[TG] {msg[:200]}"); return
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                data={"chat_id": self.chat_id, "text": msg[:4000]},
                timeout=10)
            if r.status_code != 200:
                print(f"[TG] 전송 실패: {r.status_code}")
        except Exception as e:
            print(f"[TG] 오류: {e}")

    def get_updates(self, offset=0):
        """텔레그램 메시지 수신 (명령어 처리용)"""
        if not self.ok: return []
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{self.token}/getUpdates",
                params={"offset": offset, "timeout": 5, "limit": 10},
                timeout=15)
            if r.status_code == 200:
                return r.json().get("result", [])
        except: pass
        return []


# ═══════════════════════════════════════════════════════════
#  노션 보고서 작성
# ═══════════════════════════════════════════════════════════

class NotionReporter:
    def __init__(self, token, page_id, version="2022-06-28"):
        self.token = token
        self.page_id = page_id
        self.version = version
        self.ok = bool(token and page_id)
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": version,
            "Content-Type": "application/json",
        }

    def append_report(self, title, content_blocks):
        """노션 페이지에 보고서 블록 추가"""
        if not self.ok:
            print("[Notion] 토큰 또는 페이지 ID 미설정")
            return False

        blocks = []

        # 제목 (heading_2)
        blocks.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": title}}]
            }
        })

        # 내용 블록들
        for block in content_blocks:
            if isinstance(block, str):
                # 텍스트 블록
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": block[:2000]}}]
                    }
                })
            elif isinstance(block, dict) and block.get("type") == "code":
                # 코드 블록
                blocks.append({
                    "object": "block",
                    "type": "code",
                    "code": {
                        "rich_text": [{"type": "text", "text": {"content": block["content"][:2000]}}],
                        "language": block.get("language", "plain text")
                    }
                })
            elif isinstance(block, dict) and block.get("type") == "divider":
                blocks.append({"object": "block", "type": "divider", "divider": {}})

        try:
            r = requests.patch(
                f"https://api.notion.com/v1/blocks/{self.page_id}/children",
                headers=self.headers,
                json={"children": blocks},
                timeout=15)
            if r.status_code == 200:
                print(f"[Notion] 보고서 작성 완료: {title}")
                return True
            else:
                print(f"[Notion] 작성 실패: {r.status_code} {r.text[:200]}")
                return False
        except Exception as e:
            print(f"[Notion] 오류: {e}")
            return False


# ═══════════════════════════════════════════════════════════
#  봇 프로세스 감시
# ═══════════════════════════════════════════════════════════

class ProcessMonitor:
    """tmux 세션 기반 봇 생존 감시"""

    @staticmethod
    def is_alive(tmux_session):
        """tmux 세션이 살아있는지 확인"""
        try:
            result = subprocess.run(
                ["tmux", "has-session", "-t", tmux_session],
                capture_output=True, timeout=5)
            return result.returncode == 0
        except:
            return False

    @staticmethod
    def get_recent_output(tmux_session, lines=50):
        """tmux 세션의 최근 출력 가져오기"""
        try:
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", tmux_session, "-p", "-S", f"-{lines}"],
                capture_output=True, text=True, timeout=5)
            return result.stdout if result.returncode == 0 else ""
        except:
            return ""

    @staticmethod
    def restart_bot(tmux_session, cmd, work_dir):
        """tmux 세션에서 봇 재시작"""
        try:
            # 기존 세션 종료
            subprocess.run(["tmux", "kill-session", "-t", tmux_session],
                          capture_output=True, timeout=5)
            time.sleep(1)
            # 새 세션에서 봇 시작
            subprocess.run(
                ["tmux", "new-session", "-d", "-s", tmux_session, "-c", work_dir, cmd],
                capture_output=True, timeout=5)
            return True
        except:
            return False


# ═══════════════════════════════════════════════════════════
#  로그 분석
# ═══════════════════════════════════════════════════════════

class LogAnalyzer:
    def __init__(self, error_patterns):
        self.patterns = [re.compile(p, re.IGNORECASE) for p in error_patterns]
        self._last_positions = {}  # {filepath: last_read_position}

    def check_new_errors(self, filepath):
        """마지막 체크 이후 새로 추가된 오류 찾기"""
        if not os.path.exists(filepath):
            return []

        last_pos = self._last_positions.get(filepath, 0)
        try:
            with open(filepath, 'r', errors='replace') as f:
                f.seek(0, 2)  # 파일 끝으로
                file_size = f.tell()

                if file_size < last_pos:
                    # 파일이 작아짐 (로테이션) → 처음부터
                    last_pos = 0

                f.seek(last_pos)
                new_content = f.read()
                self._last_positions[filepath] = f.tell()

            if not new_content:
                return []

            errors = []
            lines = new_content.split('\n')
            for i, line in enumerate(lines):
                for pattern in self.patterns:
                    if pattern.search(line):
                        # 오류 전후 5줄 컨텍스트
                        start = max(0, i - 2)
                        end = min(len(lines), i + 5)
                        context = '\n'.join(lines[start:end])
                        errors.append({
                            'line': line.strip(),
                            'context': context,
                            'pattern': pattern.pattern,
                            'time': datetime.now(KST).isoformat(),
                        })
                        break  # 같은 줄에서 중복 매칭 방지
            return errors
        except Exception as e:
            print(f"[Log] 읽기 실패 {filepath}: {e}")
            return []

    def get_trade_summary(self, history_file):
        """거래 히스토리에서 오늘 요약 추출"""
        if not os.path.exists(history_file):
            return None
        try:
            today = datetime.now(KST).strftime('%Y-%m-%d')
            trades = []
            with open(history_file) as f:
                for line in f:
                    try:
                        t = json.loads(line.strip())
                        if today in t.get('time', ''):
                            trades.append(t)
                    except: continue

            if not trades:
                return None

            total_pnl = sum(t.get('pnl', 0) for t in trades)
            wins = sum(1 for t in trades if t.get('pnl', 0) > 0)
            losses = len(trades) - wins

            return {
                'date': today,
                'trades': len(trades),
                'wins': wins,
                'losses': losses,
                'pnl': round(total_pnl, 2),
                'details': trades,
            }
        except:
            return None


# ═══════════════════════════════════════════════════════════
#  바이낸스 계정 연동 (ccxt)
# ═══════════════════════════════════════════════════════════

class BinanceClient:
    """바이낸스 선물 계정 조회/주문 클라이언트"""

    def __init__(self, accounts_cfg):
        self.exchanges = {}  # {account_id: ccxt.binance}
        if not ccxt:
            return
        for acc_id, acc in accounts_cfg.items():
            if acc["api_key"] and acc["secret"]:
                try:
                    ex = ccxt.binance({
                        'apiKey': acc["api_key"],
                        'secret': acc["secret"],
                        'enableRateLimit': True,
                        'options': {'defaultType': 'future'},
                    })
                    self.exchanges[acc_id] = ex
                    print(f"[Binance] {acc['name']}({acc_id}) 연결 완료")
                except Exception as e:
                    print(f"[Binance] {acc['name']}({acc_id}) 연결 실패: {e}")

    def get_balance(self, account_id):
        """계정 잔고 조회 → {free, total, margin_used}"""
        ex = self.exchanges.get(account_id)
        if not ex:
            return None
        try:
            b = ex.fetch_balance({"type": "future"})
            usdt = b.get("USDT", {})
            return {
                "free": float(usdt.get("free", 0)),
                "total": float(usdt.get("total", 0)),
                "used": float(usdt.get("used", 0)),
            }
        except Exception as e:
            print(f"[Binance] 잔고 조회 실패 ({account_id}): {e}")
            return None

    def get_positions(self, account_id):
        """열린 포지션 조회 → [{symbol, side, qty, entry, mark, pnl, leverage, margin, roe}]"""
        ex = self.exchanges.get(account_id)
        if not ex:
            return []
        try:
            raw = ex.fetch_positions()
            positions = []
            for p in raw:
                contracts = abs(float(p.get('contracts', 0)))
                if contracts == 0:
                    continue
                entry = float(p.get('entryPrice', 0))
                mark = float(p.get('markPrice', 0))
                notional = abs(float(p.get('notional', 0)))
                pnl = float(p.get('unrealizedPnl', 0))
                leverage = int(float(p.get('leverage', 1)))
                margin = notional / leverage if leverage else notional
                roe = (pnl / margin * 100) if margin > 0 else 0

                positions.append({
                    'symbol': p.get('symbol', ''),
                    'side': p.get('side', '').upper(),
                    'qty': contracts,
                    'entry': entry,
                    'mark': mark,
                    'notional': notional,
                    'pnl': pnl,
                    'leverage': leverage,
                    'margin': margin,
                    'roe': roe,
                })
            return positions
        except Exception as e:
            print(f"[Binance] 포지션 조회 실패 ({account_id}): {e}")
            return []

    def close_position(self, account_id, symbol, side, qty=None):
        """포지션 시장가 청산. qty=None이면 전량 청산"""
        ex = self.exchanges.get(account_id)
        if not ex:
            return False, "계정 미연결"
        try:
            # 현재 포지션에서 수량 확인
            if qty is None:
                positions = ex.fetch_positions([symbol])
                for p in positions:
                    p_side = (p.get('side') or '').upper()
                    contracts = abs(float(p.get('contracts', 0)))
                    if p_side == side.upper() and contracts > 0:
                        qty = contracts
                        break
                if not qty:
                    return False, f"{symbol} {side} 포지션 없음"

            # 청산 주문
            ps = "LONG" if side.upper() == "LONG" else "SHORT"
            close_side = "sell" if side.upper() == "LONG" else "buy"
            o = ex.create_order(symbol, "market", close_side, qty,
                                params={"positionSide": ps})
            fill = float(o.get("average", 0))

            # 잔여 주문 정리
            try:
                raw_sym = symbol.replace('/', '')
                ex.fapiPrivateDeleteAllOpenOrders({"symbol": raw_sym})
            except:
                pass

            return True, f"체결 @{fill:,.4f} qty={qty}"
        except Exception as e:
            return False, str(e)

    def close_all_positions(self, account_id):
        """계정의 모든 포지션 청산"""
        positions = self.get_positions(account_id)
        if not positions:
            return True, "열린 포지션 없음"
        results = []
        for p in positions:
            ok, msg = self.close_position(account_id, p['symbol'], p['side'], p['qty'])
            results.append(f"{'✅' if ok else '❌'} {p['symbol']} {p['side']}: {msg}")
        return all('✅' in r for r in results), '\n'.join(results)


# ═══════════════════════════════════════════════════════════
#  메인 워치독
# ═══════════════════════════════════════════════════════════

class BotWatchdog:
    def __init__(self, cfg=None):
        self.cfg = cfg or CONFIG
        self.tg = TG(self.cfg["TELEGRAM_TOKEN"], self.cfg["TELEGRAM_CHAT_ID"])
        self.notion = NotionReporter(
            self.cfg["NOTION_TOKEN"],
            self.cfg["NOTION_PAGE_ID"])
        self.monitor = ProcessMonitor()
        self.analyzer = LogAnalyzer(self.cfg["ERROR_PATTERNS"])
        self.binance = BinanceClient(self.cfg.get("ACCOUNTS", {}))
        self._daily_report_done = False
        self._tg_offset = 0
        self._close_confirm = {}  # 청산 확인 대기 {chat_id: {symbol, side, account, time}}

        os.makedirs(self.cfg["REPORT_DIR"], exist_ok=True)

    # ── 1. 프로세스 생존 체크 ──

    def check_processes(self):
        """모든 봇 프로세스 생존 확인"""
        results = {}
        for bot_id, bot_cfg in self.cfg["BOTS"].items():
            alive = self.monitor.is_alive(bot_cfg["tmux_session"])
            results[bot_id] = alive

            if not alive:
                self.tg.send(
                    f"🚨 [{bot_cfg['name']}] 프로세스 죽음 감지!\n"
                    f"tmux 세션 '{bot_cfg['tmux_session']}' 없음\n"
                    f"→ 재시작 시도 중...")

                # 재시작 시도
                ok = self.monitor.restart_bot(
                    bot_cfg["tmux_session"],
                    bot_cfg["cmd"],
                    self.cfg["WORK_DIR"])

                if ok:
                    self.tg.send(f"✅ [{bot_cfg['name']}] 재시작 성공")
                    # 노션 기록
                    self.notion.append_report(
                        f"🚨 봇 재시작 — {datetime.now(KST).strftime('%m/%d %H:%M')}",
                        [f"{bot_cfg['name']} 프로세스 죽음 감지 → 자동 재시작 완료"])
                else:
                    self.tg.send(f"❌ [{bot_cfg['name']}] 재시작 실패! 수동 확인 필요")

        return results

    # ── 2. 로그 오류 감지 ──

    def check_errors(self):
        """모든 봇 로그에서 새 오류 감지"""
        all_errors = {}
        for bot_id, bot_cfg in self.cfg["BOTS"].items():
            # tmux 출력에서 오류 감지
            output = self.monitor.get_recent_output(bot_cfg["tmux_session"], 100)
            if output:
                # 임시 파일로 저장 후 분석
                tmp_log = os.path.join(self.cfg["REPORT_DIR"], f"{bot_id}_latest.log")
                with open(tmp_log, 'w') as f:
                    f.write(output)
                errors = self.analyzer.check_new_errors(tmp_log)
                if errors:
                    all_errors[bot_id] = errors

            # 로그 파일에서도 체크 (있으면)
            for lf in bot_cfg.get("log_files", []):
                if lf and os.path.exists(lf):
                    errors = self.analyzer.check_new_errors(lf)
                    if errors:
                        all_errors.setdefault(bot_id, []).extend(errors)

        # 오류 발견 시 알림
        for bot_id, errors in all_errors.items():
            bot_name = self.cfg["BOTS"][bot_id]["name"]
            for err in errors[:3]:  # 최대 3개만 알림 (스팸 방지)
                self.tg.send(
                    f"⚠️ [{bot_name}] 오류 감지\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"패턴: {err['pattern']}\n"
                    f"내용: {err['line'][:200]}\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"컨텍스트:\n{err['context'][:500]}")

            # 노션 기록
            if errors:
                self.notion.append_report(
                    f"⚠️ 오류 감지 [{bot_name}] — {datetime.now(KST).strftime('%m/%d %H:%M')}",
                    [f"감지된 오류: {len(errors)}건"] +
                    [{"type": "code", "content": e['context'][:500], "language": "plain text"}
                     for e in errors[:3]] +
                    [{"type": "divider"}])

        return all_errors

    # ── 3. 일일 보고서 ──

    def daily_report(self):
        """일일 거래 요약 + 봇 상태 보고"""
        now = datetime.now(KST)
        lines = [f"📊 일일 보고서 [{now.strftime('%Y-%m-%d %H:%M')}]"]
        notion_blocks = []

        for bot_id, bot_cfg in self.cfg["BOTS"].items():
            alive = self.monitor.is_alive(bot_cfg["tmux_session"])
            status = "🟢 작동중" if alive else "🔴 중단"
            lines.append(f"\n{bot_cfg['name']}: {status}")
            notion_blocks.append(f"{bot_cfg['name']}: {status}")

            # 포지션 현황 (여러 파일 지원)
            all_positions = []
            for pos_file in bot_cfg.get("positions_files", []):
                if pos_file and os.path.exists(pos_file):
                    try:
                        with open(pos_file) as f:
                            all_positions.extend(json.load(f))
                    except: pass

            if all_positions:
                lines.append(f"  포지션: {len(all_positions)}개")
                for p in all_positions:
                    sym = p.get('symbol', '?')
                    d = p.get('direction', '?')
                    ep = p.get('entry_price', 0)
                    lines.append(f"    {d[0].upper()} {sym} @{ep:,.4f}")
            else:
                lines.append("  포지션: 없음")

            # 거래 요약 (여러 히스토리 파일 합산)
            total_trades = 0; total_wins = 0; total_pnl = 0
            has_summary = False
            for hf in bot_cfg.get("history_files", []):
                summary = self.analyzer.get_trade_summary(hf)
                if summary:
                    has_summary = True
                    total_trades += summary['trades']
                    total_wins += summary['wins']
                    total_pnl += summary['pnl']

            if has_summary:
                total_losses = total_trades - total_wins
                lines.append(f"  오늘 거래: {total_trades}건 "
                           f"(승:{total_wins} 패:{total_losses})")
                lines.append(f"  오늘 손익: ${total_pnl:+,.2f}")
                notion_blocks.append(
                    f"거래 {total_trades}건 | "
                    f"승:{total_wins} 패:{total_losses} | "
                    f"PnL: ${total_pnl:+,.2f}")
            else:
                notion_blocks.append("오늘 거래 없음")

        msg = '\n'.join(lines)
        self.tg.send(msg)

        # 노션 일일 보고
        self.notion.append_report(
            f"📊 일일 보고 — {now.strftime('%Y-%m-%d')}",
            notion_blocks + [{"type": "divider"}])

        # 로컬 보고서 저장
        report_path = os.path.join(
            self.cfg["REPORT_DIR"],
            f"daily_{now.strftime('%Y%m%d')}.json")
        with open(report_path, 'w') as f:
            json.dump({"date": now.isoformat(), "report": lines}, f,
                      indent=2, ensure_ascii=False)

    # ── 4. 텔레그램 명령 수신 ──

    def process_commands(self):
        """텔레그램에서 수신한 명령 처리"""
        updates = self.tg.get_updates(self._tg_offset)
        for update in updates:
            self._tg_offset = update["update_id"] + 1
            msg = update.get("message", {})
            text = msg.get("text", "").strip()
            chat_id = str(msg.get("chat", {}).get("id", ""))

            # 본인 chat_id만 처리
            if chat_id != self.cfg["TELEGRAM_CHAT_ID"]:
                continue

            if text.startswith("/status"):
                self._cmd_status()
            elif text.startswith("/positions"):
                parts = text.split()
                acc = parts[1] if len(parts) > 1 else "all"
                self._cmd_positions(acc)
            elif text.startswith("/balance"):
                self._cmd_balance()
            elif text.startswith("/close"):
                self._cmd_close(text)
            elif text == "/yes":
                self._cmd_close_confirm(chat_id)
            elif text == "/no":
                self._close_confirm.pop(chat_id, None)
                self.tg.send("❌ 청산 취소")
            elif text.startswith("/closeall"):
                parts = text.split()
                acc = parts[1] if len(parts) > 1 else None
                self._cmd_closeall(acc, chat_id)
            elif text.startswith("/restart"):
                parts = text.split()
                bot_id = parts[1] if len(parts) > 1 else "all"
                self._cmd_restart(bot_id)
            elif text.startswith("/errors"):
                self._cmd_errors()
            elif text.startswith("/report"):
                self.daily_report()
            elif text.startswith("/help"):
                bot_list = ", ".join(self.cfg["BOTS"].keys())
                acc_list = ", ".join(self.cfg.get("ACCOUNTS", {}).keys())
                self.tg.send(
                    "🤖 워치독 명령어 안내\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "\n📊 상태 조회\n"
                    "\n/status\n"
                    "  봇 프로세스 생존 상태 (🟢/🔴)\n"
                    "  + 계정별 잔고, 포지션 수, 총 PnL 요약\n"
                    "  + 각 포지션: 방향, 심볼, 레버리지, PnL, ROE%\n"
                    f"\n/positions [{acc_list}|all]\n"
                    "  포지션 상세 정보 조회\n"
                    "  진입가, 현재가, 수량, 명목금액, 마진, ROE%\n"
                    "  예: /positions main → 메인계정만\n"
                    "  예: /positions → 전체 계정\n"
                    "\n/balance\n"
                    "  계정별 잔고 상세 (총자산/사용중/가용)\n")
                self.tg.send(
                    "💰 포지션 관리 (원격 청산)\n"
                    "\n/close <심볼> [계정]\n"
                    "  특정 심볼 포지션 시장가 청산\n"
                    "  확인 메시지 후 /yes로 실행, /no로 취소\n"
                    "  30초 내 미응답 시 자동 취소\n"
                    "  예: /close BTCUSDT → 양쪽 계정에서 검색\n"
                    "  예: /close ETHUSDT semi → 서브계정만\n"
                    f"\n/closeall [{acc_list}]\n"
                    "  해당 계정의 모든 포지션 일괄 청산\n"
                    "  확인 절차 동일 (/yes, /no)\n"
                    "  예: /closeall semi → 서브계정 전체 청산\n"
                    "  예: /closeall → 모든 계정 전체 청산\n"
                    "\n━━━━━━━━━━━━━━━━━━━━\n"
                    "🔧 봇 관리\n"
                    f"\n/restart [{bot_list}|all]\n"
                    "  tmux 세션 kill 후 재시작\n"
                    f"  예: /restart v9 → 메인봇 재시작\n"
                    f"  예: /restart semi → 서브봇 재시작\n"
                    "  예: /restart all → 전체 재시작\n"
                    "\n/errors\n"
                    "  최근 로그에서 오류 패턴 스캔\n"
                    "  (Traceback, 긴급청산, SL실패, API에러 등)\n"
                    "\n/report\n"
                    "  일일 보고서 즉시 생성\n"
                    "  (봇 상태 + 포지션 + 오늘 거래 승패/PnL)\n"
                    "  노션에도 자동 기록됨\n"
                    "  ※ 매일 오전 8시 자동 생성")

    def _cmd_status(self):
        """봇 프로세스 상태 + 실시간 포지션/잔고 요약"""
        lines = ["🤖 봇 상태:"]

        # 계정별 잔고/포지션을 미리 조회 (중복 API 호출 방지)
        acc_data = {}  # {account_id: {balance, positions}}
        seen_accounts = set()
        for bot_id, bot_cfg in self.cfg["BOTS"].items():
            acc_id = bot_cfg.get("account")
            if acc_id and acc_id not in seen_accounts:
                seen_accounts.add(acc_id)
                bal = self.binance.get_balance(acc_id)
                pos = self.binance.get_positions(acc_id)
                acc_data[acc_id] = {"balance": bal, "positions": pos}

        for bot_id, bot_cfg in self.cfg["BOTS"].items():
            alive = self.monitor.is_alive(bot_cfg["tmux_session"])
            status = "🟢" if alive else "🔴"
            lines.append(f"\n{status} {bot_cfg['name']}")

            acc_id = bot_cfg.get("account")
            data = acc_data.get(acc_id, {})
            bal = data.get("balance")
            positions = data.get("positions", [])

            # 잔고
            if bal:
                lines.append(f"  💰 잔고: ${bal['total']:,.2f} (가용: ${bal['free']:,.2f})")
            else:
                lines.append("  💰 잔고: 조회 불가")

            # 포지션 요약
            if positions:
                total_pnl = sum(p['pnl'] for p in positions)
                total_margin = sum(p['margin'] for p in positions)
                pnl_sign = "+" if total_pnl >= 0 else ""
                lines.append(f"  📊 포지션 {len(positions)}개 | "
                             f"마진 ${total_margin:,.1f} | "
                             f"PnL {pnl_sign}${total_pnl:,.2f}")
                for p in positions:
                    d = "L" if p['side'] == "LONG" else "S"
                    pnl_s = f"+${p['pnl']:,.2f}" if p['pnl'] >= 0 else f"-${abs(p['pnl']):,.2f}"
                    roe_s = f"+{p['roe']:.1f}%" if p['roe'] >= 0 else f"{p['roe']:.1f}%"
                    sym_short = p['symbol'].replace('/USDT:USDT', '').replace('USDT', '')
                    lines.append(f"    {d} {sym_short} x{p['leverage']} | "
                                 f"{pnl_s} ({roe_s})")
            else:
                lines.append("  📊 포지션 없음")

        self.tg.send('\n'.join(lines))

    def _cmd_positions(self, target="all"):
        """포지션 상세 조회"""
        accounts = self.cfg.get("ACCOUNTS", {})
        if target != "all":
            if target in accounts:
                accounts = {target: accounts[target]}
            else:
                self.tg.send(f"❌ 알 수 없는 계정: {target}"); return

        for acc_id, acc_cfg in accounts.items():
            positions = self.binance.get_positions(acc_id)
            bal = self.binance.get_balance(acc_id)

            lines = [f"📋 {acc_cfg['name']} ({acc_id}) 포지션 상세"]
            if bal:
                usage = (bal['used'] / bal['total'] * 100) if bal['total'] > 0 else 0
                lines.append(f"잔고: ${bal['total']:,.2f} | "
                             f"사용: ${bal['used']:,.2f} ({usage:.1f}%) | "
                             f"가용: ${bal['free']:,.2f}")

            if not positions:
                lines.append("\n포지션 없음")
                self.tg.send('\n'.join(lines))
                continue

            lines.append(f"\n{'─' * 28}")
            total_pnl = 0
            total_margin = 0

            for p in positions:
                d = "LONG 🟢" if p['side'] == "LONG" else "SHORT 🔴"
                sym = p['symbol'].replace('/USDT:USDT', '').replace('USDT', '')
                pnl_s = f"+${p['pnl']:,.2f}" if p['pnl'] >= 0 else f"-${abs(p['pnl']):,.2f}"
                roe_s = f"+{p['roe']:.1f}%" if p['roe'] >= 0 else f"{p['roe']:.1f}%"
                lines.append(f"\n{sym} {d} x{p['leverage']}")
                lines.append(f"  수량: {p['qty']} | 명목: ${p['notional']:,.1f}")
                lines.append(f"  진입: {p['entry']:,.4f} → 현재: {p['mark']:,.4f}")
                lines.append(f"  마진: ${p['margin']:,.2f} | PnL: {pnl_s} ({roe_s})")

                total_pnl += p['pnl']
                total_margin += p['margin']

            lines.append(f"\n{'─' * 28}")
            pnl_total_s = f"+${total_pnl:,.2f}" if total_pnl >= 0 else f"-${abs(total_pnl):,.2f}"
            lines.append(f"합계: {len(positions)}개 | "
                         f"마진 ${total_margin:,.1f} | PnL {pnl_total_s}")
            self.tg.send('\n'.join(lines))

    def _cmd_balance(self):
        """계정별 잔고 조회"""
        lines = ["💰 계정별 잔고:"]
        for acc_id, acc_cfg in self.cfg.get("ACCOUNTS", {}).items():
            bal = self.binance.get_balance(acc_id)
            if bal:
                usage = (bal['used'] / bal['total'] * 100) if bal['total'] > 0 else 0
                lines.append(f"\n{acc_cfg['name']} ({acc_id})")
                lines.append(f"  총자산: ${bal['total']:,.2f}")
                lines.append(f"  사용중: ${bal['used']:,.2f} ({usage:.1f}%)")
                lines.append(f"  가용:   ${bal['free']:,.2f}")
            else:
                lines.append(f"\n{acc_cfg['name']} ({acc_id}): 조회 불가")
        self.tg.send('\n'.join(lines))

    def _cmd_close(self, text):
        """포지션 청산: /close BTCUSDT [계정]"""
        parts = text.split()
        if len(parts) < 2:
            self.tg.send("사용법: /close <심볼> [계정]\n"
                         "예: /close BTCUSDT\n"
                         "예: /close ETHUSDT semi")
            return

        raw_symbol = parts[1].upper()
        target_acc = parts[2] if len(parts) > 2 else None

        # 심볼 정규화 (BTCUSDT → BTC/USDT:USDT)
        if '/' not in raw_symbol:
            if raw_symbol.endswith('USDT'):
                symbol = raw_symbol[:-4] + '/USDT:USDT'
            else:
                symbol = raw_symbol + '/USDT:USDT'
        else:
            symbol = raw_symbol

        # 해당 심볼의 포지션 찾기
        found = []
        accounts = self.cfg.get("ACCOUNTS", {})
        search_accs = {target_acc: accounts[target_acc]} if target_acc and target_acc in accounts else accounts

        for acc_id in search_accs:
            positions = self.binance.get_positions(acc_id)
            for p in positions:
                if p['symbol'] == symbol:
                    found.append((acc_id, p))

        if not found:
            self.tg.send(f"❌ {raw_symbol} 포지션 없음")
            return

        # 확인 메시지
        lines = ["⚠️ 청산 확인:"]
        for acc_id, p in found:
            acc_name = self.cfg["ACCOUNTS"][acc_id]["name"]
            d = "LONG" if p['side'] == "LONG" else "SHORT"
            pnl_s = f"+${p['pnl']:,.2f}" if p['pnl'] >= 0 else f"-${abs(p['pnl']):,.2f}"
            sym_short = p['symbol'].replace('/USDT:USDT', '')
            lines.append(f"  {acc_name}: {d} {sym_short} qty={p['qty']} PnL={pnl_s}")

        lines.append("\n/yes — 청산 실행\n/no — 취소")
        self.tg.send('\n'.join(lines))

        # 확인 대기 저장
        self._close_confirm[self.cfg["TELEGRAM_CHAT_ID"]] = {
            "targets": found,
            "symbol": symbol,
            "time": time.time(),
        }

    def _cmd_close_confirm(self, chat_id):
        """청산 확인 처리"""
        pending = self._close_confirm.pop(chat_id, None)
        if not pending:
            self.tg.send("❌ 대기 중인 청산 요청 없음"); return

        # 30초 타임아웃
        if time.time() - pending["time"] > 30:
            self.tg.send("❌ 청산 요청 만료 (30초 초과)"); return

        lines = ["🔄 청산 실행:"]
        for acc_id, p in pending["targets"]:
            acc_name = self.cfg["ACCOUNTS"][acc_id]["name"]
            sym_short = p['symbol'].replace('/USDT:USDT', '')
            ok, msg = self.binance.close_position(acc_id, p['symbol'], p['side'], p['qty'])
            if ok:
                lines.append(f"  ✅ {acc_name} {sym_short}: {msg}")
            else:
                lines.append(f"  ❌ {acc_name} {sym_short}: {msg}")
        self.tg.send('\n'.join(lines))

    def _cmd_closeall(self, target_acc, chat_id):
        """전체 포지션 청산: /closeall [계정]"""
        accounts = self.cfg.get("ACCOUNTS", {})
        if target_acc and target_acc not in accounts:
            self.tg.send(f"❌ 알 수 없는 계정: {target_acc}"); return

        search = {target_acc: accounts[target_acc]} if target_acc else accounts

        # 대상 포지션 수집
        all_found = []
        for acc_id, acc_cfg in search.items():
            positions = self.binance.get_positions(acc_id)
            for p in positions:
                all_found.append((acc_id, p))

        if not all_found:
            self.tg.send("✅ 열린 포지션 없음"); return

        lines = ["⚠️ 전체 청산 확인:"]
        for acc_id, p in all_found:
            acc_name = self.cfg["ACCOUNTS"][acc_id]["name"]
            d = "L" if p['side'] == "LONG" else "S"
            sym_short = p['symbol'].replace('/USDT:USDT', '').replace('USDT', '')
            pnl_s = f"+${p['pnl']:,.2f}" if p['pnl'] >= 0 else f"-${abs(p['pnl']):,.2f}"
            lines.append(f"  {acc_name}: {d} {sym_short} {pnl_s}")
        lines.append(f"\n총 {len(all_found)}개 포지션 청산됩니다.")
        lines.append("/yes — 청산 실행\n/no — 취소")
        self.tg.send('\n'.join(lines))

        self._close_confirm[self.cfg["TELEGRAM_CHAT_ID"]] = {
            "targets": all_found,
            "symbol": "ALL",
            "time": time.time(),
        }

    def _cmd_restart(self, bot_id):
        if bot_id == "all":
            targets = list(self.cfg["BOTS"].keys())
        elif bot_id in self.cfg["BOTS"]:
            targets = [bot_id]
        else:
            self.tg.send(f"❌ 알 수 없는 봇: {bot_id}"); return

        for bid in targets:
            bc = self.cfg["BOTS"][bid]
            self.tg.send(f"🔄 {bc['name']} 재시작 중...")
            ok = self.monitor.restart_bot(
                bc["tmux_session"], bc["cmd"], self.cfg["WORK_DIR"])
            self.tg.send(f"{'✅ 성공' if ok else '❌ 실패'}: {bc['name']}")

    def _cmd_errors(self):
        errors = self.check_errors()
        if not errors:
            self.tg.send("✅ 최근 오류 없음")

    # ── 메인 루프 ──

    def run(self):
        now = datetime.now(KST)
        self.tg.send(
            f"🤖 워치독 시작 [{now.strftime('%m/%d %H:%M')}]\n"
            f"감시 대상: {len(self.cfg['BOTS'])}개 봇\n"
            f"점검 주기: {self.cfg['CHECK_INTERVAL_SEC']}초\n"
            f"노션: {'✅' if self.notion.ok else '❌ 미설정'}\n"
            f"명령어: /help")
        print(f"[Watchdog] 시작 — {len(self.cfg['BOTS'])}개 봇 감시")

        while True:
            try:
                now = datetime.now(KST)

                # 프로세스 생존 체크
                self.check_processes()

                # 로그 오류 체크
                self.check_errors()

                # 텔레그램 명령 처리
                self.process_commands()

                # 일일 보고 (오전 8시)
                if now.hour == self.cfg["DAILY_REPORT_HOUR"] and not self._daily_report_done:
                    self.daily_report()
                    self._daily_report_done = True
                elif now.hour != self.cfg["DAILY_REPORT_HOUR"]:
                    self._daily_report_done = False

                time.sleep(self.cfg["CHECK_INTERVAL_SEC"])

            except KeyboardInterrupt:
                self.tg.send("🤖 워치독 종료"); break
            except Exception as e:
                traceback.print_exc()
                self.tg.send(f"🤖 워치독 오류: {e}")
                time.sleep(60)

    def check_once(self):
        """1회 점검만 실행"""
        print("=" * 50)
        print("  Bot Watchdog — 1회 점검")
        print("=" * 50)

        # 프로세스 체크
        results = self.check_processes()
        for bot_id, alive in results.items():
            name = self.cfg["BOTS"][bot_id]["name"]
            print(f"  {'✅' if alive else '❌'} {name}")

        # 오류 체크
        errors = self.check_errors()
        if errors:
            for bot_id, errs in errors.items():
                print(f"  ⚠️ {bot_id}: {len(errs)}건 오류")
        else:
            print("  ✅ 오류 없음")

        # 일일 보고
        self.daily_report()


# ═══════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='Bot Watchdog Agent')
    parser.add_argument('--check-only', action='store_true',
                        help='1회 점검만 실행')
    parser.add_argument('--no-notion', action='store_true',
                        help='노션 보고 비활성화')
    args = parser.parse_args()

    cfg = CONFIG.copy()
    if args.no_notion:
        cfg["NOTION_TOKEN"] = ""
        cfg["NOTION_PAGE_ID"] = ""

    wd = BotWatchdog(cfg)
    if args.check_only:
        wd.check_once()
    else:
        wd.run()
