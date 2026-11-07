"""화면 한 장 — 외부 CDN 없음, 다크모드, 60초 자동 갱신. 데이터는 /api/snapshot 에서."""

PAGE_HTML = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>주식 추천 운영 화면</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--tx:#1b1f24;--mut:#6b7280;--line:#e5e7eb;--up:#1a9c7b;--dn:#d9534f;--acc:#3b6fd6;--warn:#b7791f}
@media(prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#171b21;--tx:#e6e8eb;--mut:#9aa3ad;--line:#2a3039;--up:#3ccf9e;--dn:#f0716c;--acc:#7aa2ff;--warn:#e0b45c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.5 -apple-system,"Segoe UI","Noto Sans KR",sans-serif}
header{position:sticky;top:0;background:var(--card);border-bottom:1px solid var(--line);padding:10px 16px;display:flex;gap:14px;align-items:center;flex-wrap:wrap;z-index:2}
header h1{font-size:16px;margin:0 8px 0 0}
.tabs button{background:none;border:1px solid var(--line);color:var(--tx);padding:5px 12px;border-radius:999px;cursor:pointer;margin-right:4px}
.tabs button.on{background:var(--acc);border-color:var(--acc);color:#fff}
.right{margin-left:auto;color:var(--mut);font-size:12px;display:flex;gap:10px;align-items:center}
select,input[type=number]{background:var(--card);color:var(--tx);border:1px solid var(--line);border-radius:6px;padding:3px 6px}
main{padding:14px 16px;max-width:1400px;margin:0 auto}
section{display:none}section.on{display:block}
h2{font-size:14px;color:var(--mut);margin:18px 0 8px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.tile .k{font-size:11px;color:var(--mut)}.tile .v{font-size:20px;font-weight:600}.tile .s{font-size:11px;color:var(--mut)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px;overflow-x:auto;margin-bottom:12px}
table{border-collapse:collapse;width:100%;font-size:12.5px}th,td{padding:5px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--mut);font-weight:500;white-space:nowrap}td.num,th.num{text-align:right;white-space:nowrap}
.up{color:var(--up)}.dn{color:var(--dn)}.mut{color:var(--mut)}.warn{color:var(--warn)}
.pill{display:inline-block;padding:0 7px;border-radius:999px;font-size:11px;border:1px solid var(--line);white-space:nowrap}
.pill.buy{background:var(--up);color:#fff;border-color:var(--up)}.pill.watch{background:var(--warn);color:#fff;border-color:var(--warn)}
.pill.avoid,.pill.gated{color:var(--mut)}.pill.fired{background:var(--acc);color:#fff;border-color:var(--acc)}.pill.cancelled{background:var(--dn);color:#fff;border-color:var(--dn)}
.rule{font-size:12px;color:var(--mut);max-width:520px;white-space:normal}
.etf{font-size:11.5px;color:var(--acc)}
pre{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px;font:12px/1.45 ui-monospace,Menlo,Consolas,monospace;white-space:pre-wrap;word-break:break-all;max-height:70vh;overflow:auto}
.btn{border:1px solid var(--line);background:var(--card);color:var(--tx);border-radius:6px;padding:2px 8px;cursor:pointer;font-size:11.5px}
.btn:hover{border-color:var(--acc)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media(max-width:900px){.grid2{grid-template-columns:1fr}}
.empty{color:var(--mut);padding:14px}
</style></head><body>
<header>
  <h1>📈 추천 운영 화면</h1>
  <div class="tabs">
    <button data-tab="reco" class="on">추천</button><button data-tab="trig">진입 트리거</button>
    <button data-tab="perf">성과</button><button data-tab="status">상태·로그</button>
  </div>
  <div class="right">
    시장 <select id="mkt"><option value="">전체</option><option value="US">US</option><option value="KR">KR</option></select>
    기간 <select id="days"><option value="3">3일</option><option value="10" selected>10일</option><option value="30">30일</option><option value="90">90일</option></select>
    <span id="stamp">불러오는 중…</span><button class="btn" id="reload">새로고침</button>
  </div>
</header>
<main>
<section id="reco" class="on">
  <div class="tiles" id="recoTiles"></div>
  <h2>추천 (BUY·WATCH) — 다이제스트와 같은 내용. 실행 여부는 여기서 바로 입력</h2>
  <div class="card"><table><thead><tr><th>일자</th><th>시장</th><th>종목</th><th>추천</th><th>기간</th><th class="num">확신</th><th class="num">Eff</th><th class="num">종가</th><th class="num">손절</th><th class="num">목표</th><th>진입 조건 · 관련 ETF</th><th>결과</th><th>실행</th></tr></thead><tbody id="recoBody"></tbody></table></div>
  <h2>게이트 차단·AVOID (기록만)</h2>
  <div class="card"><table><thead><tr><th>일자</th><th>시장</th><th>종목</th><th>사유</th><th class="num">Eff</th><th>결과</th></tr></thead><tbody id="gatedBody"></tbody></table></div>
  <h2>텔레그램 다이제스트 원문</h2>
  <div class="card"><select id="digestSel"></select> <pre id="digestText" style="margin-top:8px">(선택)</pre></div>
</section>
<section id="trig">
  <div class="tiles" id="trigTiles"></div>
  <h2>진입 트리거 — 조건가에 닿으면 장중 발화(🎯), 손절선 아래면 무효(⛔)</h2>
  <div class="card"><table><thead><tr><th>상태</th><th>일자</th><th>시장</th><th>종목</th><th>조건</th><th class="num">기준가</th><th class="num">도달가(목표)</th><th class="num">손절</th><th>발화</th><th>원문</th></tr></thead><tbody id="trigBody"></tbody></table></div>
</section>
<section id="perf">
  <div class="tiles" id="perfTiles"></div>
  <div class="grid2">
    <div class="card"><h2 style="margin-top:0">보유일별 평균 수익(배리어 무관)</h2><table><thead><tr><th>보유일</th><th class="num">표본</th><th class="num">평균</th><th class="num">중앙값</th><th class="num">승률</th></tr></thead><tbody id="curveBody"></tbody></table></div>
    <div class="card"><h2 style="margin-top:0">주간 요약 원문</h2><pre id="perfText"></pre></div>
  </div>
  <h2>수익 vs 손실 추천 공통점 <span class="s">(청산 완료분 · 전체 표는 Notion '[성과] 추천 요인 분석' 또는 logs/ui/attribution.md)</span></h2>
  <div class="card"><pre id="attrText"></pre></div>
  <h2>LLM 사용량·비용</h2>
  <div class="card"><table><thead><tr><th>일자</th><th>시장</th><th>모델</th><th class="num">입력 tok</th><th class="num">출력 tok</th><th class="num">추천</th><th class="num">비용 $</th></tr></thead><tbody id="usageBody"></tbody></table></div>
</section>
<section id="status">
  <div class="tiles" id="statusTiles"></div>
  <div class="grid2">
    <div class="card"><h2 style="margin-top:0">시간표(.env 기준)</h2><pre id="sched"></pre><h2>마지막 배치</h2><pre id="lastBatch"></pre></div>
    <div class="card"><h2 style="margin-top:0">배치 실패 기록</h2><pre id="fails"></pre></div>
  </div>
  <h2>데몬 로그 <select id="logName"><option>daemon.log</option><option>autopull.log</option><option>ui.log</option></select> 끝 <input type="number" id="logN" value="200" min="20" max="2000" style="width:70px"> 줄</h2>
  <pre id="logText"></pre>
</section>
</main>
<script>
const $=s=>document.querySelector(s);let SNAP=null;
const fmt=(v,d=2)=>v==null?'-':Number(v).toLocaleString('en-US',{maximumFractionDigits:d,minimumFractionDigits:d});
const px=(v,m)=>v==null?'-':(m==='KR'?Number(v).toLocaleString('ko-KR',{maximumFractionDigits:0}):fmt(v,2));
const pct=(v,s=true)=>v==null?'-':((s&&v>0?'+':'')+Number(v).toFixed(2)+'%');
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pill=(t,c)=>`<span class="pill ${c||''}">${esc(t)}</span>`;
const tile=(k,v,s)=>`<div class="tile"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s||''}</div></div>`;
const mktSel=()=>$('#mkt').value;
function tab(id){document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('on',b.dataset.tab===id));document.querySelectorAll('section').forEach(s=>s.classList.toggle('on',s.id===id));if(id==='status')loadLog();}
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>tab(b.dataset.tab));
async function api(p){const r=await fetch(p+(p.includes('?')?'&':'?')+'t='+Date.now());if(r.status===401){$('#stamp').textContent='토큰 필요(?token=…)';throw new Error('401');}return r;}
async function load(){try{const r=await api('/api/snapshot?days='+$('#days').value);SNAP=await r.json();render();$('#stamp').textContent='갱신 '+SNAP.generated_at;}catch(e){$('#stamp').textContent='오류: '+e.message;}}
function render(){if(!SNAP)return;const m=mktSel();
 const rec=SNAP.recommendations.filter(r=>!m||r.market===m);const recos=rec.filter(r=>r.is_reco),gated=rec.filter(r=>!r.is_reco);
 const today=recos.length?recos[0].date:'-';const td=recos.filter(r=>r.date===today);
 $('#recoTiles').innerHTML=tile('최근 배치 기준일',today,(SNAP.last_batches[m||'US']||{}).finished_at||'')+tile('그날 추천',td.length+'건','BUY '+td.filter(r=>r.recommend==='BUY').length+' · WATCH '+td.filter(r=>r.recommend==='WATCH').length)
  +tile('기간 내 추천',recos.length+'건','게이트 차단·AVOID '+gated.length)+tile('실행 미입력',recos.filter(r=>!r.executed).length+'건','표에서 바로 입력');
 $('#recoBody').innerHTML=recos.map(r=>{const res=r.exit_type?pill(r.exit_type.toUpperCase()+(r.ret_pct!=null?' '+pct(r.ret_pct):''),r.exit_type==='tp'?'buy':r.exit_type==='sl'?'cancelled':''):pill('대기');
  const etf=r.etfs&&r.etfs.length?`<div class="etf">관련 ETF: ${r.etfs.map(e=>(e.kind==='섹터'?'':e.kind+' ')+esc(e.name)+'('+e.code+')').join(' · ')}</div>`:'';
  const ex=r.executed?pill(r.executed==='yes'?'샀다':r.executed==='no'?'안 샀다':r.executed,r.executed==='yes'?'buy':''):`<button class="btn" onclick="triage('${r.signal_id}','yes')">샀다</button> <button class="btn" onclick="triage('${r.signal_id}','no')">안 샀다</button>`;
  return `<tr><td>${r.date}</td><td>${r.market}</td><td><b>${esc(r.symbol)}</b>${r.name?' '+esc(r.name):''}</td><td>${pill(r.recommend,r.recommend.toLowerCase())}</td><td>${esc(r.horizon)}</td><td class="num">${fmt(r.conviction,2)}</td><td class="num">${fmt(r.effective,2)}</td><td class="num">${px(r.close,r.market)}</td><td class="num">${px(r.stop,r.market)}</td><td class="num">${px(r.target,r.market)}</td><td><div class="rule">${esc(r.entry_rule)}</div>${etf}</td><td>${res}</td><td>${ex}</td></tr>`;}).join('')||'<tr><td colspan="13" class="empty">기간 내 추천 없음</td></tr>';
 $('#gatedBody').innerHTML=gated.map(r=>`<tr><td>${r.date}</td><td>${r.market}</td><td>${esc(r.symbol)}${r.name?' '+esc(r.name):''}</td><td>${pill(r.gated_by||r.recommend||'미분석','gated')}</td><td class="num">${fmt(r.effective,2)}</td><td>${r.exit_type?esc(r.exit_type)+' '+pct(r.ret_pct):'-'}</td></tr>`).join('')||'<tr><td colspan="6" class="empty">없음</td></tr>';
 const dsel=$('#digestSel');const cur=dsel.value;dsel.innerHTML='<option value="">다이제스트 선택…</option>'+SNAP.digests.filter(d=>!m||d.market===m).map(d=>`<option value="${d.name}">${d.market} ${d.date}</option>`).join('');if(cur)dsel.value=cur;
 const tr=SNAP.triggers.filter(t=>!m||t.market===m);const cnt=s=>tr.filter(t=>t.status===s).length;
 $('#trigTiles').innerHTML=tile('대기',cnt('pending')+'건','조건가 도달 감시 중')+tile('발화',cnt('fired')+'건','🎯 조건 도달')+tile('무효',cnt('cancelled')+'건','⛔ 손절선 아래 도달')+tile('만료',cnt('expired')+'건','보유기간 내 미도달');
 $('#trigBody').innerHTML=tr.map(t=>{const cond=`${t.basis==='open'?'시가':t.basis==='prev_close'?'전일종가':'종가'} 대비 ${t.value_pct}% ${t.kind==='pullback'?'눌림':t.kind==='breakout'?'돌파':'즉시'}${(t.split+'').toLowerCase()==='yes'?' · 분할':''}`;
  const fired=t.fired_at?`${esc(t.fired_at).replace('T',' ').slice(0,16)} @ ${px(t.fired_price,t.market)}${t.notion_url?` <a href="${esc(t.notion_url)}" target="_blank">노션</a>`:''}`:'-';
  return `<tr><td>${pill(t.status,t.status)}</td><td>${t.date}</td><td>${t.market}</td><td><b>${esc(t.symbol)}</b></td><td>${cond}</td><td class="num">${px(t.basis_price,t.market)}</td><td class="num">${px(t.target_price,t.market)}</td><td class="num">${px(t.stop,t.market)}</td><td>${fired}</td><td><div class="rule">${esc(t.rule_text)}</div></td></tr>`;}).join('')||'<tr><td colspan="10" class="empty">트리거 없음</td></tr>';
 const P=SNAP.performance||{},s=P.summary||{};
 $('#perfTiles').innerHTML=tile('추천 누적',(P.total??'-')+'건',`청산 ${s.n??0} · 대기 ${P.waiting??0} · 미체결 ${P.skipped??0}`)+tile('가상계좌 손익',s.n?('$'+fmt(s.total_pnl,2)):'-',s.n?`$${fmt(s.position,0)}/종목 · MDD $${fmt(s.max_dd,0)}`:'청산 대기')+tile('승률 / PF',s.n?`${fmt(s.win_rate,0)}% / ${s.pf===Infinity||s.pf==='inf'?'∞':fmt(s.pf,2)}`:'-',s.n?`TP ${s.tp} / SL ${s.sl} / TO ${s.to}`:'')+tile('BUY vs WATCH',P.by_grade?`${P.by_grade.BUY?.n??0} / ${P.by_grade.WATCH?.n??0}건`:'-',P.by_grade?`BUY $${fmt(P.by_grade.BUY?.total_pnl,0)} · WATCH $${fmt(P.by_grade.WATCH?.total_pnl,0)}`:'')+tile('최적 보유일',P.best?`D+${P.best.day}`:'-',P.best?`${pct(P.best.avg)} (${P.best.n}건)`:'표본 부족');
 $('#curveBody').innerHTML=(P.curve||[]).filter(c=>c.n).map(c=>`<tr><td>D+${c.day}</td><td class="num">${c.n}</td><td class="num ${c.avg>=0?'up':'dn'}">${pct(c.avg)}</td><td class="num">${pct(c.median)}</td><td class="num">${fmt(c.win_rate,0)}%</td></tr>`).join('')||'<tr><td colspan="5" class="empty">아직 없음</td></tr>';
 $('#perfText').textContent=P.telegram_text||'';$('#attrText').textContent=P.attribution_text||'';
 $('#usageBody').innerHTML=(SNAP.usage.rows||[]).map(u=>`<tr><td>${esc(u.date)}</td><td>${esc(u.market)}</td><td>${esc(u.model)}</td><td class="num">${fmt(u.input_tokens,0)}</td><td class="num">${fmt(u.output_tokens,0)}</td><td class="num">${esc(u.n_reco)}</td><td class="num">${u.cost_usd!=null&&u.cost_usd!==''?fmt(u.cost_usd,4):'-'}</td></tr>`).join('')||'<tr><td colspan="7" class="empty">기록 없음</td></tr>';
 const ap=SNAP.autopull||{},lb=SNAP.last_batches||{};
 $('#statusTiles').innerHTML=tile('코드',esc(SNAP.git||'-'),'VM 체크아웃')+tile('autopull',ap.heartbeat_age_min==null?'기록 없음':(ap.stuck?'⚠ ':'')+ap.heartbeat_age_min+'분 전',ap.stuck?'30분 넘게 안 돌았음':'정상')+Object.values(lb).map(b=>tile(`${b.market} 마지막 배치`,b.date,`${b.finished_at||''} · 후보 ${b.n_candidates} → 추천 ${b.n_reco}${b.n_fail?' · LLM 실패 '+b.n_fail:''}`)).join('')+tile('배치 실패',(SNAP.failures||[]).length+'건','최근 기록');
 $('#sched').textContent=(SNAP.schedule||[]).join('\n');$('#lastBatch').textContent=Object.values(lb).map(b=>`[${b.market}] ${b.date} (기준봉 ${b.as_of}) 매크로 L${b.macro_level} ${b.macro_label} · ${b.macro_detail}\n   채점 ${b.n_scored} · 후보 ${b.n_candidates} · 추천 ${b.n_reco} · 게이트 ${b.n_gated} · 매도신호 ${b.n_exits} · 차트 ${b.n_charts}\n`+(b.sector_lines||[]).map(l=>'   • '+l).join('\n')).join('\n\n')||'(배치 기록 없음 — 배치가 한 번 돌면 생긴다)';
 $('#fails').textContent=(SNAP.failures||[]).map(f=>`${f.at} [${f.market}] ${f.error}`).join('\n')||'없음';}
async function loadLog(){try{const r=await api(`/api/log?n=${$('#logN').value}&name=${$('#logName').value}`);$('#logText').textContent=await r.text();$('#logText').scrollTop=1e9;}catch(e){$('#logText').textContent='오류: '+e.message;}}
async function triage(id,v){if(!confirm((v==='yes'?'샀다':'안 샀다')+' 로 기록할까요?'))return;const r=await fetch('/api/triage'+(location.search||''),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({signal_id:id,executed:v,note:'UI 입력'})});if(!r.ok){alert('실패: '+(await r.text()));return;}load();}
$('#digestSel').onchange=async e=>{if(!e.target.value){$('#digestText').textContent='(선택)';return;}const r=await api('/api/digest?name='+encodeURIComponent(e.target.value));$('#digestText').textContent=await r.text();};
$('#mkt').onchange=render;$('#days').onchange=load;$('#reload').onclick=()=>{load();loadLog();};$('#logName').onchange=loadLog;$('#logN').onchange=loadLog;
load();setInterval(load,60000);
</script></body></html>
"""
