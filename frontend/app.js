"use strict";
const $ = (s) => document.querySelector(s);

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v; else if (k.startsWith("on")) el.addEventListener(k.slice(2), v); else el.setAttribute(k, v);
  }
  for (const kid of kids) el.append(kid instanceof Node ? kid : document.createTextNode(kid ?? ""));
  return el;
}

let toastTimer;
function toast(msg, isErr) {
  const t = $("#toast");
  t.textContent = msg; t.className = isErr ? "err" : "";
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add("hidden"), 4500);
}

const keyInput = $("#apiKey");
keyInput.value = sessionStorage.getItem("apiKey") || "";
keyInput.addEventListener("change", () => { sessionStorage.setItem("apiKey", keyInput.value); refresh(); });

async function api(path, opts = {}) {
  const res = await fetch(path, { ...opts, headers: { "X-API-Key": keyInput.value, ...(opts.headers || {}) } });
  if (!res.ok) {
    let msg = res.status + " 오류";
    try { const j = await res.json(); if (j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (e) { /* 본문 없음 */ }
    throw new Error(msg);
  }
  return res;
}
const getJSON = (p) => api(p).then((r) => r.json());
const postJSON = (p, body) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

async function busy(form, fn) {
  const btn = form.querySelector("button[type=submit]");
  btn.disabled = true;
  try { await fn(); } catch (e) { toast(e.message, true); } finally { btn.disabled = false; }
}
const fmtTime = (s) => (s || "").replace("T", " ").replace("+00:00", "");
function fillTable(sel, rows) { const tb = $(sel + " tbody"); tb.replaceChildren(...rows); }

/* ---------- 탭 ---------- */
document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll("nav button").forEach((x) => x.classList.toggle("active", x === b));
  for (const t of ["docs", "trace", "audit"]) $("#tab-" + t).classList.toggle("hidden", t !== b.dataset.tab);
  refresh();
}));

/* ---------- 문서 등록 · 발급 ---------- */
let issueDocId = null;
async function loadDocs() {
  const docs = await getJSON("/api/documents");
  $("#docEmpty").classList.toggle("hidden", docs.length > 0);
  fillTable("#docTable", docs.map((d) => h("tr", null,
    h("td", null, d.title, h("div", { class: "muted" }, d.filename)),
    h("td", null, d.scenario === "c2c" ? "중고거래 사진" : "기업 문서", h("div", { class: "muted" }, d.doc_type_label || "")),
    h("td", null, String(d.pages)), h("td", null, String(d.issued_count)), h("td", null, fmtTime(d.created_at)),
    h("td", null, h("button", { class: "small", onclick: () => openIssue(d) }, "배포본 발급")))));
}
function openIssue(d) {
  issueDocId = d.id; $("#issueTitle").textContent = d.title;
  $("#issueCard").classList.remove("hidden"); $("#issueForm").subject_id.focus();
}
$("#issueCancel").addEventListener("click", () => $("#issueCard").classList.add("hidden"));
$("#uploadForm").addEventListener("submit", (ev) => {
  ev.preventDefault();
  busy(ev.target, async () => {
    await api("/api/documents", { method: "POST", body: new FormData(ev.target) });
    ev.target.reset(); toast("원본을 등록했습니다."); await loadDocs();
  });
});
$("#issueForm").addEventListener("submit", (ev) => {
  ev.preventDefault();
  busy(ev.target, async () => {
    const res = await postJSON("/api/issue", { document_id: issueDocId, ...Object.fromEntries(new FormData(ev.target)) });
    const cd = res.headers.get("Content-Disposition") || "";
    const m = /filename\*=UTF-8''([^;]+)/.exec(cd);
    const a = h("a", { href: URL.createObjectURL(await res.blob()), download: m ? decodeURIComponent(m[1]) : "watermarked" });
    document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    toast("발급 완료 · 토큰 참조 " + res.headers.get("X-Token-Ref"));
    ev.target.reset(); await loadDocs();
  });
});

/* ---------- 역추적 ---------- */
const canvas = $("#preview"), ctx = canvas.getContext("2d");
let img = null, pts = [], viewScale = 1, lastTrace = null;
function draw() {
  if (!img) return;
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  if (!pts.length) return;
  ctx.lineWidth = 2; ctx.strokeStyle = "#ff3b30"; ctx.fillStyle = "#ff3b30"; ctx.font = "bold 15px sans-serif";
  ctx.beginPath();
  pts.forEach(([x, y], i) => { const px = x * viewScale, py = y * viewScale; if (i) ctx.lineTo(px, py); else ctx.moveTo(px, py); });
  if (pts.length === 4) ctx.closePath();
  ctx.stroke();
  pts.forEach(([x, y], i) => { ctx.beginPath(); ctx.arc(x * viewScale, y * viewScale, 5, 0, 7); ctx.fill(); ctx.fillText(String(i + 1), x * viewScale + 8, y * viewScale - 8); });
}
$("#traceFile").addEventListener("change", (ev) => {
  const f = ev.target.files[0]; pts = [];
  if (!f) return;
  const im = new Image();
  im.onload = () => {
    img = im; viewScale = Math.min(1, 760 / im.naturalWidth);
    canvas.width = Math.round(im.naturalWidth * viewScale); canvas.height = Math.round(im.naturalHeight * viewScale);
    canvas.classList.remove("hidden"); draw();
  };
  im.onerror = () => toast("미리보기를 만들 수 없는 이미지입니다.", true);
  im.src = URL.createObjectURL(f);
});
canvas.addEventListener("click", (ev) => {
  if (pts.length >= 4) return;
  const r = canvas.getBoundingClientRect();
  const k = canvas.width / r.width;  // CSS 축소 보정
  pts.push([(ev.clientX - r.left) * k / viewScale, (ev.clientY - r.top) * k / viewScale]); draw();
});
$("#resetPts").addEventListener("click", () => { pts = []; draw(); });

/* 논문 그림 2 의 판정 결과 네 가지 */
const STATUS = {
  identified: ["배포 대상 식별", "ok"],
  undecidable: ["판정 불가 (재촬영)", "warn"],
  unissued: ["미발급 토큰 (오탐 처리)", "warn"],
  tampered: ["기록 위·변조 경고", "bad"],
};
const STEP = (v) => (v === null || v === undefined ? "—" : v ? "예" : "아니오");
function showResult(t) {
  lastTrace = t;
  const [label, cls] = STATUS[t.status] || [t.status, ""];
  const rows = [["판정", h("span", { class: "pill " + cls }, label)]];
  const st = t.detail.steps || {};
  rows.push(["① BCH 복호 성공", STEP(st.bch_decoded)], ["② 발급 토큰", STEP(st.issued)], ["③ 해시 체인 무결", STEP(st.chain_intact)]);
  if (t.status === "identified" || t.status === "tampered") {
    rows.push(["토큰 참조(HMAC 색인)", h("code", null, t.token_ref)], ["원본", t.document ? t.document.title : "-"], ["발급 시각", fmtTime(t.issued_at)]);
  }
  rows.push(["다수결 반복 수", String(t.detail.repetitions)], ["BCH 정정 비트", `${t.detail.bch_corrected_bits} / 7`],
    ["개별 판정 BER", t.detail.bch_decoded === false ? "-" : (t.detail.ber * 100).toFixed(1) + " %"],
    ["동기 점수", `${t.detail.sync_score} (잠긴 영역 ${t.detail.regions_locked})`], ["기하 가설", h("code", null, t.detail.view || "-")]);
  const dl = h("dl");
  rows.forEach(([k, v]) => dl.append(h("dt", null, k), h("dd", null, v)));
  $("#resultBody").replaceChildren(dl); $("#revealBody").replaceChildren();
  $("#revealForm").classList.toggle("hidden", t.status !== "identified");
  $("#resultCard").classList.remove("hidden");
}
$("#traceForm").addEventListener("submit", (ev) => {
  ev.preventDefault();
  busy(ev.target, async () => {
    if (pts.length && pts.length !== 4) throw new Error("꼭짓점은 4개를 모두 찍거나 초기화하세요.");
    const fd = new FormData();
    fd.append("file", $("#traceFile").files[0]);
    if (pts.length === 4) fd.append("corners", JSON.stringify(pts));
    fd.append("deep", $("#deep").checked ? "true" : "false");
    showResult(await (await api("/api/trace", { method: "POST", body: fd })).json());
    await loadTraces();
  });
});
$("#revealForm").addEventListener("submit", (ev) => {
  ev.preventDefault();
  busy(ev.target, async () => {
    const r = await (await postJSON(`/api/traces/${lastTrace.trace_id}/reveal`, { reason: $("#reason").value })).json();
    const dl = h("dl");
    const names = { subject_id: "사번 / 판매글 ID", subject_name: "이름", department: "부서", note: "비고" };
    for (const [k, v] of Object.entries(r.subject)) if (v) dl.append(h("dt", null, names[k] || k), h("dd", null, String(v)));
    $("#revealBody").replaceChildren(h("h2", null, "열람된 수령자 정보"), dl);
    $("#reason").value = "";
  });
});
async function loadTraces() {
  const list = await getJSON("/api/traces");
  fillTable("#traceTable", list.map((t) => { const [label, cls] = STATUS[t.status] || [t.status, ""]; return h("tr", null,
    h("td", null, fmtTime(t.created_at)), h("td", null, h("span", { class: "pill " + cls }, label)),
    h("td", null, String(t.detail.repetitions ?? "-")), h("td", null, t.status === "undecidable" ? "-" : (t.detail.ber * 100).toFixed(1) + " %"),
    h("td", null, h("code", null, t.token_ref || "-")), h("td", null, String(t.detail.sync_score))); }));
}

/* ---------- 감사 로그 ---------- */
async function loadAudit() {
  const list = await getJSON("/api/audit");
  fillTable("#auditTable", list.map((a) => h("tr", null, h("td", null, String(a.id)), h("td", null, fmtTime(a.ts)), h("td", null, a.actor),
    h("td", null, h("code", null, a.action)), h("td", { class: "mono" }, JSON.stringify(a.detail)))));
}
$("#verifyBtn").addEventListener("click", async () => {
  try {
    const v = await getJSON("/api/verify"), out = $("#verifyOut");
    out.textContent = v.ok ? `무결성 확인 (발급 원장 ${v.ledger.entries}건 · 감사 로그 ${v.audit.entries}건)`
      : `위·변조 의심: ${v.ledger.ok ? "" : "발급 원장 #" + v.ledger.broken_at + " "}${v.audit.ok ? "" : "감사 로그 #" + v.audit.broken_at}`;
    out.className = "pill " + (v.ok ? "ok" : "bad");
  } catch (e) { toast(e.message, true); }
});

/* ---------- 초기화 ---------- */
async function refresh() {
  if (!keyInput.value) { toast("오른쪽 위에 관리자 API 키를 입력하세요."); return; }
  const tab = document.querySelector("nav button.active").dataset.tab;
  try { await ({ docs: loadDocs, trace: loadTraces, audit: loadAudit })[tab](); } catch (e) { toast(e.message, true); }
}
fetch("/api/health").then((r) => r.json()).then((j) => {
  const el = $("#health");
  el.textContent = j.pdf ? "서버 정상" : "서버 정상 · PDF 불가(라이브러리 없음)"; el.className = "pill " + (j.pdf ? "ok" : "warn");
}).catch(() => { $("#health").textContent = "서버 연결 실패"; $("#health").className = "pill bad"; });
refresh();
