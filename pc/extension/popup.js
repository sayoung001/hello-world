const $ = (id) => document.getElementById(id);

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab && /^https:\/\/(www\.)?saveticker\.com\//.test(tab.url || "") ? tab : null;
}

async function send(msg) {
  const tab = await activeTab();
  if (!tab) {
    $("status").innerHTML = '<span class="warn">saveticker.com 뉴스 탭에서 열어 주세요.</span>';
    return null;
  }
  try {
    return await chrome.tabs.sendMessage(tab.id, msg);
  } catch {
    $("status").innerHTML = '<span class="warn">탭을 새로고침한 뒤 다시 시도하세요.</span>';
    return null;
  }
}

async function rows() {
  const { news = {} } = await chrome.storage.local.get("news");
  return Object.values(news).sort((a, b) => b.created_at.localeCompare(a.created_at));
}

const kst = (iso) =>
  iso ? new Date(iso).toLocaleString("ko-KR", { timeZone: "Asia/Seoul", hour12: false }) : "";

const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

// news_alert.py 가 판별한 뉴스 {id: 점수}. 판별기가 꺼져 있으면 빈 목록.
const JUDGED_URL = "http://127.0.0.1:18765/judged.json";
let judged = {};
async function loadJudged() {
  try {
    const r = await fetch(JUDGED_URL, { signal: AbortSignal.timeout(1500) });
    judged = r.ok ? await r.json() : {};
  } catch {
    judged = {};
  }
}

async function render() {
  const r = await rows();
  const { status = "", saveStatus = "" } = await chrome.storage.local.get(["status", "saveStatus"]);
  $("saveStatus").textContent = saveStatus;
  $("n").textContent = r.length;
  if (!$("status").querySelector(".warn")) $("status").textContent = status;
  $("list").innerHTML = r.slice(0, 50)
    .map((x) => `<li${x.id in judged ? ` class="judged" title="판별 ${judged[x.id]}점"` : ""}>${esc(x.title)} <small>${kst(x.created_at)}${x.tickers ? " · " + esc(x.tickers) : ""}</small></li>`)
    .join("");
}

$("collect").onclick = () => {
  const pages = Math.min(50, Math.max(1, +$("pages").value || 1));
  send({ cmd: "collect", pages, delayMs: 1500 });
};
$("stop").onclick = () => send({ cmd: "stop" });

$("watch").onchange = async () => {
  const on = $("watch").checked, sec = Math.max(30, +$("sec").value || 60);
  await chrome.storage.local.set({ watchOn: on, watchSec: sec });
  send({ cmd: "watch", on, sec });
};

async function save(ext) {
  $("saveStatus").textContent = "저장 중…";
  const r = await chrome.runtime.sendMessage({ cmd: "save", ext });
  if (!r?.ok) $("saveStatus").textContent = `저장 오류: ${r?.error || "응답 없음"}`;
}
$("csv").onclick = () => save("csv");
$("json").onclick = () => save("json");
$("auto").onchange = () => chrome.storage.local.set({ autoSave: $("auto").checked });

$("clear").onclick = async () => {
  if (confirm("수집한 뉴스를 모두 지울까요?")) {
    await chrome.storage.local.remove(["news", "status"]);
    render();
  }
};

chrome.storage.local.get(["watchOn", "watchSec", "autoSave"]).then(({ watchOn, watchSec, autoSave = true }) => {
  $("watch").checked = watchOn !== false;   // 기본은 켜짐
  $("auto").checked = autoSave;
  if (watchSec) $("sec").value = watchSec;
});
chrome.storage.onChanged.addListener(render);
loadJudged().then(render);
setInterval(() => loadJudged().then(render), 5000);
