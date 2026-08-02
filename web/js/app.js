/**
 * OpenGateway Live Ops console
 * - Room sidebar + SSE live feed + composer (agent-chat shell pattern)
 * - Talks to same-origin OpenGateway REST/SSE
 */

const API = (window.OPENGATEWAY_API || "").replace(/\/$/, "");

const state = {
  rooms: [],
  roomId: null,
  room: null,
  participantId: null,
  participantName: localStorage.getItem("og_name") || "human",
  harnessMap: {}, // name -> harness
  es: null,
  messages: [],
};

// ── DOM ──
const $ = (id) => document.getElementById(id);
const els = {
  statusDot: $("status-dot"),
  statusText: $("status-text"),
  statusMeta: $("status-meta"),
  roomList: $("room-list"),
  roomTitle: $("room-title"),
  roomGoal: $("room-goal"),
  liveBadge: $("live-badge"),
  msgCount: $("msg-count"),
  partCount: $("part-count"),
  chatEmpty: $("chat-empty"),
  messages: $("messages"),
  chatScroll: $("chat-scroll"),
  participants: $("participants"),
  tasks: $("tasks"),
  artifacts: $("artifacts"),
  eventLog: $("event-log"),
  composer: $("composer"),
  composerInput: $("composer-input"),
  composerHint: $("composer-hint"),
  btnSend: $("btn-send"),
  humanName: $("human-name"),
  humanPid: $("human-pid"),
  autoScroll: $("auto-scroll"),
  modal: $("modal-room"),
  formRoom: $("form-new-room"),
};

els.humanName.value = state.participantName;

// ── API helpers ──
async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || JSON.stringify(j);
    } catch {}
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

function logEvent(line) {
  const div = document.createElement("div");
  const t = new Date().toLocaleTimeString();
  div.textContent = `${t}  ${line}`;
  els.eventLog.prepend(div);
  while (els.eventLog.children.length > 80) els.eventLog.lastChild.remove();
}

function setGatewayStatus(ok, meta) {
  els.statusDot.className = "dot " + (ok ? "ok" : "bad");
  els.statusText.textContent = ok ? "gateway online" : "gateway offline";
  els.statusMeta.textContent = meta || "—";
}

function setLive(on) {
  els.liveBadge.classList.toggle("live", on);
  const label = els.liveBadge.querySelector(".blabel");
  if (label) label.textContent = on ? "Live" : "Offline";
}

function initials(name) {
  return (name || "?").slice(0, 2).toUpperCase();
}

function harnessClass(h) {
  const key = (h || "other").replace(/[^a-z0-9-]/gi, "");
  return "h-" + key;
}

function fmtTime(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString();
  } catch {
    return iso.slice(11, 19);
  }
}

// ── Rooms ──
async function loadRooms() {
  const data = await api("/v1/rooms");
  state.rooms = data.rooms || [];
  renderRooms();
}

function renderRooms() {
  els.roomList.innerHTML = "";
  if (!state.rooms.length) {
    els.roomList.innerHTML = `<div class="empty-hint">No rooms yet</div>`;
    return;
  }
  for (const r of state.rooms) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "room-item" + (r.id === state.roomId ? " active" : "");
    const n = (r.participant_ids || []).length;
    btn.innerHTML = `
      <span class="name">${escapeHtml(r.name)}</span>
      <span class="goal">${escapeHtml(r.goal || "No goal set")}</span>
      <span class="meta">
        <span class="chip ${r.status === "active" ? "ok" : ""}">${escapeHtml(r.status || "open")}</span>
        <span class="chip">${n} agent${n === 1 ? "" : "s"}</span>
      </span>
    `;
    btn.onclick = () => selectRoom(r.id);
    els.roomList.appendChild(btn);
  }
}

async function selectRoom(roomId) {
  if (state.roomId === roomId) return;
  disconnectSSE();
  state.roomId = roomId;
  state.messages = [];
  // Keep stable identity per room from localStorage (do NOT invent a new human)
  state.participantId = loadStoredPid(roomId);
  els.humanPid.textContent = state.participantId
    ? `id ${state.participantId}`
    : "not joined";
  renderRooms();
  await refreshSnapshot();
  await ensureJoined();
  connectSSE();
}

async function refreshSnapshot() {
  if (!state.roomId) return;
  const snap = await api(`/v1/rooms/${state.roomId}/snapshot`);
  state.room = snap.room;
  els.roomTitle.textContent = snap.room.name;
  els.roomGoal.textContent = snap.room.goal || "—";
  state.messages = snap.messages || [];
  renderMessages();
  renderParticipants(snap.participants || []);
  renderTasks(snap.tasks || []);
  renderArtifacts(snap.artifacts || []);
  const mc = state.messages.length;
  const pc = (snap.participants || []).length;
  els.msgCount.textContent = `${mc} message${mc === 1 ? "" : "s"}`;
  els.partCount.textContent = `${pc} agent${pc === 1 ? "" : "s"}`;
}

function renderParticipants(list) {
  state.harnessMap = {};
  els.participants.innerHTML = "";
  if (!list.length) {
    els.participants.innerHTML = `<li class="sub">none</li>`;
    return;
  }
  for (const p of list) {
    state.harnessMap[p.name] = p.harness;
    const li = document.createElement("li");
    const st = p.status === "online" ? "tag-online" : "tag-offline";
    li.innerHTML = `
      <strong>${escapeHtml(p.name)}</strong>
      <span class="sub"><span class="${st}">● ${p.status}</span> · ${escapeHtml(p.harness)} · ${escapeHtml(p.role)}</span>
    `;
    els.participants.appendChild(li);
  }
}

function renderTasks(list) {
  els.tasks.innerHTML = "";
  if (!list.length) {
    els.tasks.innerHTML = `<li><span class="sub">none</span></li>`;
    return;
  }
  for (const t of list) {
    const li = document.createElement("li");
    li.innerHTML = `
      ${escapeHtml(t.title)}
      <span class="sub">${escapeHtml(t.status)}${t.claimed_by ? " · claimed" : ""}</span>
    `;
    els.tasks.appendChild(li);
  }
}

function renderArtifacts(list) {
  els.artifacts.innerHTML = "";
  if (!list.length) {
    els.artifacts.innerHTML = `<li><span class="sub">none</span></li>`;
    return;
  }
  for (const a of list) {
    const li = document.createElement("li");
    li.innerHTML = `
      ${escapeHtml(a.name)}
      <span class="sub">${escapeHtml(a.content_type)}</span>
    `;
    els.artifacts.appendChild(li);
  }
}

// ── Messages ──
function renderMessages() {
  if (!state.messages.length) {
    els.chatEmpty.hidden = false;
    els.messages.hidden = true;
    return;
  }
  els.chatEmpty.hidden = true;
  els.messages.hidden = false;
  els.messages.innerHTML = "";
  for (const m of state.messages) {
    els.messages.appendChild(messageEl(m));
  }
  maybeScroll();
  els.msgCount.textContent = `${state.messages.length} msgs`;
}

function messageEl(m) {
  const name = m.from_name || "?";
  const harness = state.harnessMap[name] || guessHarness(name);
  const body =
    (m.message && m.message.parts && m.message.parts[0] && m.message.parts[0].content) || "";
  const isSystem = name === "system" || (m.metadata && m.metadata.system);
  const isMine =
    (state.participantId && m.from_participant_id === state.participantId) ||
    (state.participantName && name === state.participantName && harness === "human");

  const div = document.createElement("div");
  div.className =
    "msg" + (isMine ? " mine" : "") + (isSystem ? " system-msg" : "");
  div.dataset.id = m.id;

  if (isSystem) {
    div.innerHTML = `
      <div class="bubble system" style="width:100%">
        <div class="bubble-body">${escapeHtml(body)}</div>
      </div>
    `;
    return div;
  }

  div.innerHTML = `
    <div class="avatar ${harnessClass(harness)}" title="${escapeHtml(harness)}">${escapeHtml(initials(name))}</div>
    <div class="bubble">
      <div class="bubble-actions">
        <button type="button" class="btn-icon" data-copy title="Copy">⎘</button>
      </div>
      <div class="bubble-head">
        <span class="bubble-name">${escapeHtml(name)}</span>
        <span class="bubble-time">${fmtTime(m.created_at)}</span>
        <span class="bubble-harness">${escapeHtml(harness)}</span>
      </div>
      <div class="bubble-body">${escapeHtml(body)}</div>
    </div>
  `;
  const copyBtn = div.querySelector("[data-copy]");
  if (copyBtn) {
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(body);
        copyBtn.textContent = "✓";
        setTimeout(() => (copyBtn.textContent = "⎘"), 900);
      } catch {}
    });
  }
  return div;
}

function guessHarness(name) {
  const n = (name || "").toLowerCase();
  if (n.includes("grok")) return "grok";
  if (n.includes("claude") || n.includes("alice")) return "claude-code";
  if (n.includes("cursor")) return "cursor";
  if (n.includes("human") || n.includes("ops")) return "human";
  return "other";
}

function appendMessage(m) {
  if (state.messages.some((x) => x.id === m.id)) return;
  state.messages.push(m);
  if (els.messages.hidden) {
    els.chatEmpty.hidden = true;
    els.messages.hidden = false;
  }
  els.messages.appendChild(messageEl(m));
  const mc = state.messages.length;
  els.msgCount.textContent = `${mc} message${mc === 1 ? "" : "s"}`;
  maybeScroll();
}

function maybeScroll() {
  if (!els.autoScroll.checked) return;
  els.chatScroll.scrollTop = els.chatScroll.scrollHeight;
}

// ── Join + send ──
function pidKey(roomId) {
  return `og_pid_${roomId}`;
}

function loadStoredPid(roomId) {
  return localStorage.getItem(pidKey(roomId)) || null;
}

function saveStoredPid(roomId, id) {
  if (roomId && id) localStorage.setItem(pidKey(roomId), id);
}

async function ensureJoined() {
  if (!state.roomId) return;
  const name = (els.humanName.value || "human").trim() || "human";
  state.participantName = name;
  localStorage.setItem("og_name", name);

  // Restore stable identity for this room (prevents human + mark duplicates)
  if (!state.participantId) {
    state.participantId = loadStoredPid(state.roomId);
  }

  const body = {
    name,
    harness: "human",
    role: "observer",
    capabilities: ["monitor", "chat"],
  };
  if (state.participantId) body.participant_id = state.participantId;

  const p = await api(`/v1/rooms/${state.roomId}/join`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  state.participantId = p.id;
  saveStoredPid(state.roomId, p.id);
  els.humanPid.textContent = `id ${p.id}`;
  els.composerInput.disabled = false;
  els.btnSend.disabled = false;
  els.composerHint.textContent = `as ${name} · Enter send · “everyone …” nudges all agents`;
  logEvent(`session ${p.id.slice(0, 8)}… as ${name}`);
  await refreshSnapshot();
}

/** Rename in place — never creates a second participant. */
async function renameSelf(newName) {
  if (!state.roomId || !state.participantId) {
    await ensureJoined();
    return;
  }
  const name = (newName || "").trim() || "human";
  state.participantName = name;
  localStorage.setItem("og_name", name);
  try {
    const p = await api(
      `/v1/rooms/${state.roomId}/participants/${state.participantId}`,
      {
        method: "PATCH",
        body: JSON.stringify({ name }),
      }
    );
    state.participantId = p.id;
    saveStoredPid(state.roomId, p.id);
    els.humanPid.textContent = `id ${p.id}`;
    els.composerHint.textContent = `as ${name} · Enter send · “everyone …” nudges all agents`;
    logEvent(`renamed → ${name} (same id)`);
    await refreshSnapshot();
  } catch (e) {
    // Fallback: re-join with same participant_id (server updates in place)
    logEvent("patch failed, rejoin: " + e.message);
    await ensureJoined();
  }
}

function isAllCall(text) {
  const t = (text || "").toLowerCase();
  return [
    "everyone",
    "everybody",
    "@all",
    "@everyone",
    "all agents",
    "hey all",
    "hey everyone",
    "all of you",
    "you all",
  ].some((n) => t.includes(n));
}

async function sendMessage(text) {
  if (!state.roomId || !state.participantId || !text.trim()) return;
  const content = text.trim();
  const nudgeBox = document.getElementById("nudge-all");
  const nudge_all = !!(nudgeBox && nudgeBox.checked) || isAllCall(content);
  const res = await api(`/v1/rooms/${state.roomId}/messages`, {
    method: "POST",
    body: JSON.stringify({
      from_participant_id: state.participantId,
      content,
      nudge_all,
    }),
  });
  if (res && res.nudge_count != null) {
    logEvent(`nudged ${res.nudge_count} agent(s)`);
  }
  // SSE will deliver; no optimistic needed
}

// ── SSE ──
function connectSSE() {
  disconnectSSE();
  if (!state.roomId) return;
  const url = `${API}/v1/rooms/${state.roomId}/events`;
  const es = new EventSource(url);
  state.es = es;

  es.onopen = () => {
    setLive(true);
    logEvent("SSE connected");
  };
  es.onerror = () => {
    setLive(false);
    logEvent("SSE reconnecting…");
  };

  const handle = (type, raw) => {
    let data;
    try {
      data = JSON.parse(raw);
    } catch {
      return;
    }
    if (type === "message") {
      const msg = data.payload?.message || data.message;
      if (msg) appendMessage(msg);
      return;
    }
    if (type === "participant" || type === "task" || type === "artifact" || type === "room") {
      logEvent(`${type}: ${data.payload?.action || ""}`);
      // light refresh side panels
      refreshSnapshot().catch(() => {});
    }
  };

  for (const t of ["message", "participant", "task", "artifact", "room", "system"]) {
    es.addEventListener(t, (ev) => handle(t, ev.data));
  }
  es.onmessage = (ev) => handle("message", ev.data);
}

function disconnectSSE() {
  if (state.es) {
    state.es.close();
    state.es = null;
  }
  setLive(false);
}

// ── Bootstrap ──
function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function ping() {
  try {
    const p = await api("/ping");
    setGatewayStatus(
      true,
      `${p.version} · rooms ${p.rooms ?? "?"} · ${p.persistent ? "sqlite" : "memory"}${
        p.db_path ? " · " + p.db_path.split("/").slice(-2).join("/") : ""
      }`
    );
  } catch (e) {
    setGatewayStatus(false, String(e.message || e));
  }
}

async function boot() {
  await ping();
  await loadRooms();
  // auto-select first room or hash
  const hash = location.hash.replace(/^#/, "");
  if (hash && state.rooms.some((r) => r.id === hash || r.name === hash)) {
    const r = state.rooms.find((x) => x.id === hash || x.name === hash);
    await selectRoom(r.id);
  } else if (state.rooms[0]) {
    await selectRoom(state.rooms[0].id);
  }
  setInterval(ping, 15000);
  setInterval(() => {
    if (state.roomId) loadRooms().catch(() => {});
  }, 20000);
}

// Events
$("btn-refresh").onclick = async () => {
  await ping();
  await loadRooms();
  if (state.roomId) await refreshSnapshot();
};

$("btn-new-room").onclick = () => els.modal.showModal();
$("btn-cancel-room").onclick = () => els.modal.close();

els.formRoom.onsubmit = async (e) => {
  e.preventDefault();
  const fd = new FormData(els.formRoom);
  const body = {
    name: fd.get("name"),
    goal: fd.get("goal") || "",
    created_by: els.humanName.value || "human",
  };
  const path = fd.get("project_path");
  if (path) body.project_path = path;
  const room = await api("/v1/rooms", { method: "POST", body: JSON.stringify(body) });
  els.modal.close();
  els.formRoom.reset();
  await loadRooms();
  await selectRoom(room.id);
  location.hash = room.id;
};

els.composer.onsubmit = async (e) => {
  e.preventDefault();
  const text = els.composerInput.value;
  els.composerInput.value = "";
  try {
    await sendMessage(text);
  } catch (err) {
    logEvent("send failed: " + err.message);
    els.composerInput.value = text;
  }
};

els.composerInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    els.composer.requestSubmit();
  }
});

els.humanName.addEventListener("change", async () => {
  if (!state.roomId) return;
  try {
    await renameSelf(els.humanName.value);
  } catch (e) {
    logEvent(String(e.message || e));
  }
});

boot().catch((e) => {
  setGatewayStatus(false, String(e.message || e));
  logEvent("boot failed: " + e);
});
