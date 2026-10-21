// Writes one CSV per KST date into <Downloads>/saveticker/ (a junction to
// C:\_c\saveticker\data). Auto-saves the dates that got new headlines, and
// handles the popup's CSV/JSON buttons.
const SAVE_DIR = "saveticker";
const COLS = ["created_at", "title", "title_en", "source", "tickers", "labels", "url", "id"];
const DEBOUNCE_MS = 5000;

const kst = (iso) =>
  iso ? new Date(iso).toLocaleString("ko-KR", { timeZone: "Asia/Seoul", hour12: false }) : "";
const kstDate = (iso) =>
  iso ? new Date(iso).toLocaleDateString("sv-SE", { timeZone: "Asia/Seoul" }) : "unknown";
const csvCell = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;

const FORMATS = {
  csv: {
    type: "text/csv",
    render: (r) => "\ufeff" + [["time_kst", ...COLS].join(",")]
      .concat(r.map((x) => [kst(x.created_at), ...COLS.map((c) => x[c])].map(csvCell).join(",")))
      .join("\r\n"),
  },
  json: { type: "application/json", render: (r) => JSON.stringify(r, null, 2) },
};

// Keep auto-saves out of Edge's download flyout and history.
chrome.downloads.setUiOptions?.({ enabled: false }).catch(() => {});
const ours = new Set();
chrome.downloads.onChanged.addListener((d) => {
  if (!ours.has(d.id) || !d.state) return;
  if (d.state.current === "complete") chrome.downloads.erase({ id: d.id });
  if (d.state.current !== "in_progress") ours.delete(d.id);
  if (d.state.current === "interrupted") setSave(`저장 실패: ${d.error?.current || "interrupted"}`);
});

const setSave = (saveStatus) => chrome.storage.local.set({ saveStatus });

async function saveDates(dates, ext = "csv") {
  const { news = {} } = await chrome.storage.local.get("news");
  const groups = new Map();
  for (const r of Object.values(news)) {
    const d = kstDate(r.created_at);
    if (dates && !dates.has(d)) continue;
    if (!groups.has(d)) groups.set(d, []);
    groups.get(d).push(r);
  }
  const fmt = FORMATS[ext];
  for (const [d, rows] of groups) {
    rows.sort((a, b) => b.created_at.localeCompare(a.created_at));
    const url = `data:${fmt.type};charset=utf-8,` + encodeURIComponent(fmt.render(rows));
    const id = await chrome.downloads.download({
      url, filename: `${SAVE_DIR}/saveticker_news_${d}.${ext}`,
      conflictAction: "overwrite", saveAs: false,
    });
    ours.add(id);
  }
  const t = new Date().toLocaleTimeString("ko-KR", { hour12: false });
  await setSave(`${t} 저장: ${[...groups.keys()].sort().join(", ")} (.${ext})`);
  return [...groups.keys()];
}

// Auto-save: collect the dates touched by new headlines, then write once.
let pending = new Set();
let timer = null;
chrome.storage.onChanged.addListener(async (changes) => {
  if (!changes.news) return;
  const { autoSave = true } = await chrome.storage.local.get("autoSave");
  if (!autoSave) return;
  const before = changes.news.oldValue || {};
  for (const [id, r] of Object.entries(changes.news.newValue || {})) {
    // 새 뉴스이거나, 원문 제목이 한글 번역으로 바뀐 뉴스
    if (!before[id] || before[id].title !== r.title) pending.add(kstDate(r.created_at));
  }
  if (!pending.size) return;
  clearTimeout(timer);
  timer = setTimeout(() => {
    const dates = pending;
    pending = new Set();
    saveDates(dates).catch((e) => setSave(`저장 오류: ${e.message}`));
  }, DEBOUNCE_MS);
});

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.cmd !== "save") return;
  saveDates(null, msg.ext)
    .then((dates) => reply({ ok: true, dates }))
    .catch((e) => { setSave(`저장 오류: ${e.message}`); reply({ ok: false, error: e.message }); });
  return true;
});

// On install/reload, write out everything already collected.
chrome.runtime.onInstalled.addListener(() => {
  saveDates(null).catch((e) => setSave(`저장 오류: ${e.message}`));
});


// ---- 감시견: Edge 가 saveticker 탭을 재우면 실시간 감시가 멈춘다 ----
// 1분마다 탭이 재워지지 않게 표시하고, 감시가 한동안 소식이 없으면 탭을 새로고침해 깨운다.
const SAVETICKER_URLS = ["https://saveticker.com/*", "https://www.saveticker.com/*"];
chrome.alarms.create("watchdog", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== "watchdog") return;
  const { watchOn, watchSec = 60, lastTick = 0, lastRevive = 0 } =
    await chrome.storage.local.get(["watchOn", "watchSec", "lastTick", "lastRevive"]);
  const tabs = await chrome.tabs.query({ url: SAVETICKER_URLS });
  // 판별기에 살아 있다고 알린다. 판별기는 이 소식이 끊기거나 감시가 멈추면 토스트로 알린다.
  const on = watchOn !== false;   // 기본은 켜짐 (content.js 와 같게)
  const q = new URLSearchParams({ watch: on ? 1 : 0, tick: lastTick, tabs: tabs.length });
  fetch(`http://127.0.0.1:18765/ping?${q}`, { cache: "no-store" }).catch(() => {});
  if (!on || !tabs.length) return;
  for (const t of tabs) {
    if (t.autoDiscardable) chrome.tabs.update(t.id, { autoDiscardable: false }).catch(() => {});
  }
  const now = Date.now();
  const stale = now - lastTick > Math.max(3 * watchSec * 1000, 180000);
  if (stale && now - lastRevive > 180000) {
    await chrome.storage.local.set({
      lastRevive: now,
      status: `감시가 멈춰 탭을 새로고침했습니다 (${new Date().toLocaleTimeString("ko-KR", { hour12: false })})`,
    });
    chrome.tabs.reload(tabs[0].id);
  }
});
