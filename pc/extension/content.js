// Isolated-world bridge: stores headlines in chrome.storage.local and runs
// the page-fetch / watch jobs requested from the popup.
const MARK = "__saveticker_collector__";
const API = "/api/news/list";
const TOP_API = "/api/news/top-stories";   // 페이지의 "오늘 주요뉴스" (세이브티커 편집자가 고른 SAVE PICK)
const PICK = "[SAVE PICK] ";
const MAX_ITEMS = 5000;

function toRow(it) {
  const en = it.translations?.translated?.en_US || {};
  return {
    id: it.id,
    created_at: it.created_at || "",
    // 주요뉴스는 제목 앞에 표시를 붙인다. 판별기는 이 표시를 보고 9점을 준다.
    // 올라온 뒤에 뽑히기도 해서, 뽑히면 제목이 바뀐 것으로 보고 CSV 를 다시 쓴다.
    title: (it.is_top_story ? PICK : "") + (it.title || "").trim(),
    title_en: (en.title || "").trim(),
    source: it.source || it.author_name || "",
    tickers: (it.tickers || []).map((t) => t.symbol).join(" "),
    labels: (it.content_labels || []).map((l) => l.name).join(" "),
    url: `${location.origin}/news/${it.id}`,
  };
}

// Serialize writes so concurrent batches don't clobber each other.
let queue = Promise.resolve();
function store(items) {
  queue = queue.then(async () => {
    const { news = {} } = await chrome.storage.local.get("news");
    let added = 0;
    for (const it of items) {
      if (!it?.id || it.is_deleted) continue;
      const row = toRow(it);
      const old = news[it.id];
      // 사이트는 원문 제목을 먼저 올리고 몇 초~1분 뒤 한글 번역으로 바꾼다. 바뀐 제목은 덮어쓴다.
      if (old && old.title === row.title && old.title_en === row.title_en) continue;
      if (!old) added++;
      news[it.id] = row;
    }
    let rows = Object.values(news);
    if (rows.length > MAX_ITEMS) {
      rows.sort((a, b) => b.created_at.localeCompare(a.created_at));
      rows = rows.slice(0, MAX_ITEMS);
    }
    await chrome.storage.local.set({
      news: Object.fromEntries(rows.map((r) => [r.id, r])),
      lastUpdate: new Date().toISOString(),
    });
    return added;
  });
  return queue;
}

// Headlines the page loads on its own (initial load, scrolling, refresh button).
window.addEventListener("message", (e) => {
  if (e.source === window && e.data?.[MARK]) store(e.data.items);
});

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function fetchPage(page, size = 20) {
  const q = new URLSearchParams({ page, page_size: size, sort: "created_at_desc" });
  const r = await fetch(`${API}?${q}`, { credentials: "include" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return (await r.json()).news_list || [];
}

let job = { running: false, stop: false };

async function collectPages(pages, delayMs) {
  if (job.running) return;
  job = { running: true, stop: false };
  let status = "";
  try {
    for (let p = 1; p <= pages && !job.stop; p++) {
      await setStatus(`${p}/${pages} 페이지 수집 중…`);
      const items = await fetchPage(p);
      if (!items.length) { status = `${p - 1}페이지에서 끝`; break; }
      await store(items);
      if (p < pages) await sleep(delayMs);
    }
    status = status || (job.stop ? "중지됨" : "완료");
  } catch (err) {
    status = `오류: ${err.message} (페이지를 새로고침한 뒤 다시 시도)`;
  } finally {
    job.running = false;
    await setStatus(status);
  }
}

let watchTimer = null;
let ticking = false;
const CATCHUP_PAGES = 30;   // 한 페이지 20건. PC 가 하룻밤 잠들어도 메울 만큼
async function watchTick() {
  if (ticking) return;
  ticking = true;
  try {
    // 2페이지까지는 늘 본다: 번역이 늦은 뉴스가 1페이지 밖으로 밀려나도 한글 제목을 받도록.
    // 그 뒤로는 한 페이지가 통째로 처음 보는 뉴스일 때만 더 거슬러 간다.
    // PC 가 잠들었거나 탭이 재워졌던 사이의 뉴스를 깨어나서 메운다.
    let added = 0;
    // 주요뉴스는 올라온 지 몇 시간 뒤에 뽑히기도 하니 목록과 따로 받는다
    try {
      const tr = await fetch(TOP_API, { credentials: "include" });
      if (tr.ok) await store((await tr.json()).news_list || []);
    } catch (_) {}   // 주요뉴스를 못 받아도 목록 감시는 계속한다
    for (let p = 1; p <= CATCHUP_PAGES; p++) {
      if (p > 2) await sleep(500);
      const items = await fetchPage(p);
      const n = await store(items);
      added += n;
      const valid = items.filter((it) => it?.id && !it.is_deleted).length;
      if (!items.length || (p >= 2 && n < valid)) break;
    }
    await setStatus(`감시 중 · 마지막 확인 ${new Date().toLocaleTimeString()} (+${added})`);
    await chrome.storage.local.set({ lastTick: Date.now() });   // background.js 감시견이 본다
  } catch (err) {
    await setStatus(`감시 오류: ${err.message}`);
  } finally {
    ticking = false;
  }
}
function setWatch(on, sec) {
  clearInterval(watchTimer);
  watchTimer = null;
  if (on) {
    watchTick();
    watchTimer = setInterval(watchTick, Math.max(30, sec) * 1000);
  }
}

const setStatus = (status) => chrome.storage.local.set({ status });

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.cmd === "collect") collectPages(msg.pages, msg.delayMs);
  else if (msg.cmd === "stop") job.stop = true;
  else if (msg.cmd === "watch") setWatch(msg.on, msg.sec);
  reply({ ok: true, running: job.running, watching: !!watchTimer });
});

// 실시간 감시는 기본으로 켜진다. 팝업에서 끈 경우(watchOn === false)만 쉰다.
// 저장 값이 비면(다시 설치 등) 감시가 조용히 꺼진 채로 남아 새 뉴스가 안 들어왔다 (09-27).
chrome.storage.local.get(["watchOn", "watchSec"]).then(({ watchOn, watchSec }) => {
  if (watchOn !== false) {
    const start = () => setWatch(true, watchSec || 60);
    document.readyState === "loading" ? addEventListener("DOMContentLoaded", start) : start();
  }
});
