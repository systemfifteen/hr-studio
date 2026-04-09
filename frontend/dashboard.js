const WS_URL = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`;
const RECONNECT_MS = 3000;

const HISTORY_MINUTES    = 10;
const HISTORY_BUCKET_SEC = 5;
const HISTORY_BUCKETS    = (HISTORY_MINUTES * 60) / HISTORY_BUCKET_SEC; // 120

const ZONE_COLORS = {
  0: "#2a2a2a",   // tmavosivá — pod 50%
  1: "#555555",   // sivá      — 50-59%
  2: "#1a5fa8",   // modrá     — 60-69%
  3: "#1a8c3e",   // zelená    — 70-79%
  4: "#b8820a",   // žltá      — 80-89%
  5: "#a01820",   // červená   — ≥90%
};

// Jasnejšie farby pre pásik histórie — odlíšené od pozadia karty
const ZONE_HISTORY_COLORS = {
  0: "#444444",
  1: "#888888",
  2: "#2e8fe8",
  3: "#28c45a",
  4: "#f0a800",
  5: "#e02030",
};

let riders = {};   // position → {name, hr, pct, zone, calories, connected, hidden}
let ws = null;

const hideTimers = {};       // position → setTimeout handle
const HIDE_DELAY_MS = 45000; // 45s po signal_lost → skry kartu

// ── Persistencia stavu (localStorage) ────────────────────────────────────────
const STORAGE_KEY = 'hr-studio-riders-v1';

function saveRidersState() {
  if (!Object.keys(riders).length) return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ savedAt: Date.now(), data: riders }));
  } catch (e) {}
}

async function tryRestoreRidersState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const { savedAt, data } = JSON.parse(raw);
    if (Date.now() - savedAt > 30 * 60 * 1000) {          // staršie ako 30 min → zahodiť
      localStorage.removeItem(STORAGE_KEY);
      return;
    }
    const res = await fetch('/api/session');
    if (!res.ok) return;
    const session = await res.json();
    if (!session.active) return;                            // žiadna aktívna session
    const sessionStart = new Date(session.started_at.replace(' ', 'T') + 'Z').getTime();
    if (savedAt < sessionStart - 5000) return;             // uložené pred touto session
    Object.assign(riders, data);
  } catch (e) {}
}

// Ukladaj každých 15 sekúnd + pri zatvorení stránky
setInterval(saveRidersState, 15000);
window.addEventListener('beforeunload', saveRidersState);

// ── Interval timer ────────────────────────────────────────────────────────────
let timerState = null;   // posledný stav z WS
let timerTick  = null;   // setInterval handle

function timerActive(state) {
  return state && state.configured && state.state !== "idle";
}

function applyTimerState(state) {
  const wasActive = timerActive(timerState);
  timerState = state;
  const isActive = timerActive(state);

  if (wasActive !== isActive) {
    renderGrid();   // Pridá alebo odoberie timer bunku z gridu
  } else if (isActive) {
    renderTimerSVG();
  }

  if (isActive) {
    if (!timerTick) timerTick = setInterval(renderTimerSVG, 500);
  } else {
    if (timerTick) { clearInterval(timerTick); timerTick = null; }
  }
}

function timerElapsed() {
  if (!timerState) return 0;
  let e = timerState.elapsed_at_pause || 0;
  if (timerState.is_running && timerState.start_epoch) {
    e += Date.now() / 1000 - timerState.start_epoch;
  }
  return Math.min(e, timerState.total_sec);
}

function timerArcPath(r, startDeg, endDeg) {
  const toRad = d => (d - 90) * Math.PI / 180;
  const x1 = r * Math.cos(toRad(startDeg));
  const y1 = r * Math.sin(toRad(startDeg));
  const x2 = r * Math.cos(toRad(endDeg));
  const y2 = r * Math.sin(toRad(endDeg));
  const sweep = ((endDeg - startDeg) % 360 + 360) % 360;
  const large = sweep > 180 ? 1 : 0;
  return `M ${x1.toFixed(3)} ${y1.toFixed(3)} A ${r} ${r} 0 ${large} 1 ${x2.toFixed(3)} ${y2.toFixed(3)}`;
}

function renderTimerSVG() {
  const svg = document.getElementById("timer-svg");
  if (!svg || !timerState || !timerState.configured) return;

  const { intervals, total_sec, is_paused } = timerState;
  const elapsed = timerElapsed();

  // Zisti aktuálny interval
  let cumSec = 0, currentIdx = intervals.length - 1, timeIntoInterval = elapsed;
  for (let i = 0; i < intervals.length; i++) {
    if (elapsed < cumSec + intervals[i].duration) {
      currentIdx = i;
      timeIntoInterval = elapsed - cumSec;
      break;
    }
    cumSec += intervals[i].duration;
  }
  const done = elapsed >= total_sec;
  const cur  = intervals[currentIdx];
  const remaining = done ? 0 : Math.max(0, cur.duration - timeIntoInterval);
  const remainFrac = done ? 0 : remaining / cur.duration;

  const WORK_COL = "#e03060";
  const REST_COL = "#1a8c3e";
  const col = t => t === "work" ? WORK_COL : REST_COL;

  // ── Outer ring (program segments) ──────────────────────────────────────────
  const OR = 48, OW = 6, GAP = 1.5;
  let outerHTML = `<circle r="${OR}" fill="none" stroke="#1c1c1c" stroke-width="${OW}"/>`;
  let angleDeg = 0;
  for (let i = 0; i < intervals.length; i++) {
    const iv = intervals[i];
    const sweepDeg = (iv.duration / total_sec) * 360;
    const segStart = angleDeg + GAP;
    const segEnd   = angleDeg + sweepDeg - GAP;
    if (segEnd > segStart) {
      const c = col(iv.type);
      const CONSUMED = "#333";
      if (done || i < currentIdx) {
        // Prebehnutý interval — celý sivý
        outerHTML += `<path d="${timerArcPath(OR, segStart, segEnd)}" fill="none" stroke="${CONSUMED}" stroke-width="${OW}" stroke-linecap="butt"/>`;
      } else if (i === currentIdx) {
        // Aktuálny interval — sivá prebehnutá časť + farebný zostatok
        const splitDeg = angleDeg + (timeIntoInterval / iv.duration) * sweepDeg;
        const split = Math.min(Math.max(splitDeg, segStart), segEnd);
        if (split > segStart) {
          outerHTML += `<path d="${timerArcPath(OR, segStart, split)}" fill="none" stroke="${CONSUMED}" stroke-width="${OW}" stroke-linecap="butt"/>`;
        }
        if (segEnd > split) {
          outerHTML += `<path d="${timerArcPath(OR, split, segEnd)}" fill="none" stroke="${c}" stroke-width="${OW}" stroke-linecap="butt"/>`;
        }
      } else {
        // Budúci interval — farebný, mierne stmavený
        outerHTML += `<path d="${timerArcPath(OR, segStart, segEnd)}" fill="none" stroke="${c}" stroke-width="${OW}" opacity="0.45" stroke-linecap="butt"/>`;
      }
    }
    angleDeg += sweepDeg;
  }

  // ── Inner countdown ring ───────────────────────────────────────────────────
  const IR = 36, IW = 12;
  const IC = 2 * Math.PI * IR;
  const dash = remainFrac * IC;
  const innerCol = done ? "#3a3a3a" : col(cur.type);
  const innerHTML = `
    <circle r="${IR}" fill="none" stroke="#111" stroke-width="${IW}"/>
    <circle r="${IR}" fill="none" stroke="${innerCol}" stroke-width="${IW}"
      stroke-dasharray="${dash.toFixed(2)} ${IC.toFixed(2)}"
      transform="rotate(-90)"
      stroke-linecap="butt"/>`;

  // ── Center bg ──────────────────────────────────────────────────────────────
  const centerBg = `<circle r="${IR - IW / 2 - 1}" fill="#0f0f0f"/>`;

  // ── Center text ────────────────────────────────────────────────────────────
  let centerHTML;
  if (done) {
    centerHTML = `<text y="5" text-anchor="middle" fill="#e07020" font-size="12" font-weight="700" font-family="sans-serif">Koniec!</text>`;
  } else {
    const mins = Math.floor(remaining / 60);
    const secs = Math.floor(remaining % 60);
    const timeStr = `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
    const label = is_paused ? `⏸ ${cur.label}` : cur.label;
    const progress = `${currentIdx + 1} / ${intervals.length}`;
    centerHTML = `
      <text y="-3" text-anchor="middle" fill="#fff" font-size="19" font-weight="700" font-family="sans-serif">${timeStr}</text>
      <text y="11" text-anchor="middle" fill="rgba(255,255,255,.65)" font-size="8.5" font-family="sans-serif">${label}</text>
      <text y="21" text-anchor="middle" fill="rgba(255,255,255,.35)" font-size="7.5" font-family="sans-serif">${progress}</text>`;
  }

  svg.innerHTML = outerHTML + innerHTML + centerBg + centerHTML;
}

function connect() {
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    console.log("WS pripojený");
    document.getElementById("conn-status").textContent = "●";
    document.getElementById("conn-status").style.color = "#1a8c3e";
  };

  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    handleMessage(msg);
  };

  ws.onclose = () => {
    document.getElementById("conn-status").textContent = "○";
    document.getElementById("conn-status").style.color = "#a01820";
    setTimeout(connect, RECONNECT_MS);
  };

  ws.onerror = () => ws.close();
}

function handleMessage(msg) {
  switch (msg.type) {
    case "initial_state":
      msg.riders.forEach((r) => {
        const saved = riders[r.position] || {};
        riders[r.position] = {
          ...r,
          bike_label:  r.bike_label,
          pct:         saved.pct      ?? 0,
          zone:        saved.zone     ?? 0,
          calories:    saved.calories ?? 0,
          meps:        saved.meps     ?? 0,
          zoneHistory: saved.zoneHistory || [],
          hidden:      r.hidden ?? saved.hidden ?? false,
          manualHide:  r.hidden ?? saved.manualHide ?? false,
        };
        // Ak je odpojený a ešte nie je skrytý ani nemá bežiaci timer → spusti
        if (!r.connected && !riders[r.position].hidden && !hideTimers[r.position]) {
          hideTimers[r.position] = setTimeout(() => {
            if (riders[r.position]) {
              riders[r.position].hidden = true;
              renderGrid();
            }
            delete hideTimers[r.position];
          }, HIDE_DELAY_MS);
        }
      });
      renderGrid();
      if (msg.timer) applyTimerState(msg.timer);
      restoreZoneHistory();   // dotiahni históriu zón z DB (spoľahlivejšie ako localStorage)
      break;

    case "timer_update":
      applyTimerState(msg);
      break;

    case "hr_update": {
      if (!riders[msg.position]) riders[msg.position] = {};
      // Zruš prípadný auto-hide timer
      if (hideTimers[msg.position]) {
        clearTimeout(hideTimers[msg.position]);
        delete hideTimers[msg.position];
      }
      const wasHidden = riders[msg.position].hidden;
      const isManuallyHidden = riders[msg.position].manualHide === true;
      Object.assign(riders[msg.position], {
        name:      msg.name,
        hr:        msg.hr,
        pct:       msg.pct,
        zone:      msg.zone,
        calories:  msg.calories,
        meps:      msg.meps,
        watts:     msg.watts,
        battery:   msg.battery,
        connected: true,
        hidden:    isManuallyHidden,   // zachovaj manuálne skrytie
      });
      pushZoneHistory(msg.position, msg.zone);
      if (wasHidden && !isManuallyHidden) renderGrid(); else if (!isManuallyHidden) updateCard(msg.position);
      break;
    }

    case "disconnected":
    case "signal_lost":
      if (riders[msg.position]) {
        riders[msg.position].connected = false;
        updateCard(msg.position);
        // Po 60s skry kartu (session dáta zostanú zachované)
        if (hideTimers[msg.position]) clearTimeout(hideTimers[msg.position]);
        hideTimers[msg.position] = setTimeout(() => {
          if (riders[msg.position]) {
            riders[msg.position].hidden = true;
            renderGrid();
          }
          delete hideTimers[msg.position];
        }, HIDE_DELAY_MS);
      }
      break;

    case "riders_updated":
      showToast("Riders aktualizovaní");
      fetchAndMergeRiders();
      break;

    case "rider_visibility":
      if (riders[msg.position]) {
        riders[msg.position].hidden = msg.hidden;
        riders[msg.position].manualHide = msg.hidden;
        if (hideTimers[msg.position]) { clearTimeout(hideTimers[msg.position]); delete hideTimers[msg.position]; }
        renderGrid();
      }
      break;

    case "session_stopped":
      // Zruš všetky hide timery
      for (const pos of Object.keys(hideTimers)) {
        clearTimeout(hideTimers[pos]);
        delete hideTimers[pos];
      }
      localStorage.removeItem(STORAGE_KEY);
      if (msg.summary?.length) showSummary(msg.summary);
      break;
  }
}

function renderGrid() {
  const hasTimer = timerActive(timerState);
  const positions = Object.keys(riders).map(Number).filter(pos => !riders[pos].hidden).sort((a, b) => {
    const nameA = (riders[a].name || '').toLowerCase();
    const nameB = (riders[b].name || '').toLowerCase();
    return nameA.localeCompare(nameB);
  });
  const count = positions.length;
  // Timer zaberá 2×2 = 4 sloty; zabezpeč min. 2 stĺpce
  const effective = count + (hasTimer ? 4 : 0);

  const grid = document.getElementById("grid");
  const GAP = 8, PAD = 10;
  const availW = grid.clientWidth  - PAD * 2;
  const availH = grid.clientHeight - PAD * 2;

  // Nájdi počet stĺpcov ktorý maximalizuje veľkosť bunky pre danú obrazovku
  const minCols = hasTimer ? 2 : 1;
  let cols = minCols;
  let bestCell = 0;
  for (let c = minCols; c <= Math.max(effective, minCols); c++) {
    const r = Math.ceil(effective / c) || 1;
    const cw = (availW - GAP * (c - 1)) / c;
    const ch = (availH - GAP * (r - 1)) / r;
    const cellSize = Math.min(cw, ch);
    if (cellSize > bestCell) { bestCell = cellSize; cols = c; }
  }
  const rows   = Math.ceil(effective / cols) || 1;
  const cellW  = (availW - GAP * (cols - 1)) / cols;
  const cellH  = (availH - GAP * (rows - 1)) / rows;
  const cell   = Math.floor(Math.min(cellW, cellH));

  grid.style.gridTemplateColumns = `repeat(${cols}, ${cell}px)`;
  grid.style.gridTemplateRows    = `repeat(${rows}, ${cell}px)`;
  grid.style.alignContent        = "center";
  grid.style.justifyContent      = "center";
  document.documentElement.style.setProperty("--cell", cell + "px");
  grid.innerHTML = "";

  // Timer bunka — vložená ako prvá, span 2×2
  if (hasTimer) {
    const tc = document.createElement("div");
    tc.id = "timer-cell";
    tc.innerHTML = `<svg id="timer-svg" viewBox="-55 -55 110 110" width="100%" height="100%"></svg>`;
    grid.appendChild(tc);
    renderTimerSVG();
  }

  positions.forEach((pos) => {
    const r = riders[pos];
    const card = document.createElement("div");
    card.className = "card";
    card.id = `card-${pos}`;
    card.innerHTML = cardHTML(pos, r);
    grid.appendChild(card);
  });
}

function pushZoneHistory(pos, zone) {
  const r = riders[pos];
  if (!r.zoneHistory) r.zoneHistory = [];
  r.zoneHistory.push({ zone, ts: Date.now() });
  const cutoff = Date.now() - (HISTORY_MINUTES * 60 + 30) * 1000;
  while (r.zoneHistory.length > 0 && r.zoneHistory[0].ts < cutoff) {
    r.zoneHistory.shift();
  }
}

function zoneHistoryHTML(r) {
  const now     = Date.now();
  const history = r.zoneHistory || [];
  const segs    = [];
  for (let i = 0; i < HISTORY_BUCKETS; i++) {
    const bucketEnd   = now - (HISTORY_BUCKETS - 1 - i) * HISTORY_BUCKET_SEC * 1000;
    const bucketStart = bucketEnd - HISTORY_BUCKET_SEC * 1000;
    let zone = null;
    for (let j = history.length - 1; j >= 0; j--) {
      if (history[j].ts >= bucketStart && history[j].ts < bucketEnd) {
        zone = history[j].zone;
        break;
      }
    }
    const color = zone !== null ? ZONE_HISTORY_COLORS[zone] : "rgba(255,255,255,0.06)";
    segs.push(`<div class="zh-seg" style="background:${color}"></div>`);
  }
  return `<div class="zone-history">${segs.join("")}</div>`;
}

function batteryIcon(pct) {
  if (pct == null) return "";
  const color = pct <= 20 ? "#e05050" : pct <= 50 ? "#e0a020" : "#60c060";
  return `<span style="color:${color}">⬛ ${pct}%</span>`;
}

function cardHTML(pos, r) {
  const bg   = r.connected === false ? "#2a2a2a" : ZONE_COLORS[r.zone ?? 0];
  const pct  = r.pct ?? 0;
  const hr   = r.hr  ?? "--";
  const cal  = r.calories ?? 0;
  const meps  = r.meps  ?? 0;
  return `
    <div class="card-inner" style="background:${bg}">
      <div class="card-header">
        <span class="card-name">${r.name ?? `Bike ${pos}`}</span>
        <span class="card-cal">🔥 ${cal} kcal</span>
      </div>
      ${zoneHistoryHTML(r)}
      <div class="card-pct" style="text-align:center${(r.zone ?? 0) === 0 ? ';color:#e05050' : ''}">${pct}%</div>
      <div class="card-footer">
        <span class="card-bpm">❤️ ${hr}</span>
        <span class="card-meps">⛰️ ${meps}</span>
      </div>
      ${r.connected === false ? '<div class="card-offline">signal lost</div>' : ""}
    </div>
  `;
}

function updateCard(pos) {
  const card = document.getElementById(`card-${pos}`);
  if (!card) {
    renderGrid();
    return;
  }
  card.innerHTML = cardHTML(pos, riders[pos]);
}

const ZONE_BG = { 1:"#555555", 2:"#1a5fa8", 3:"#1a8c3e", 4:"#b8820a", 5:"#a01820" };

function showSummary(summary) {
  const grid = document.getElementById("sum-grid");
  const totalSec = r => Object.values(r.zones_sec).reduce((a,b) => a+b, 0) || 1;
  grid.innerHTML = summary.map(r => {
    const dur = Math.round(r.duration_sec / 60);
    const bars = [1,2,3,4,5].map(z => {
      const pct = Math.round((r.zones_sec[z] || 0) / totalSec(r) * 100);
      return pct > 0
        ? `<div class="sum-zone-bar" style="width:${pct}%;background:${ZONE_BG[z]}" title="Z${z}: ${Math.round((r.zones_sec[z]||0)/60)}min"></div>`
        : "";
    }).join("");
    return `
      <div class="sum-card">
        <div class="sum-name">${r.name}</div>
        <div class="sum-row"><span>Čas</span><span>${dur} min</span></div>
        <div class="sum-row"><span>MEPs</span><span>${r.total_meps}</span></div>
        <div class="sum-row"><span>Kalórie</span><span>${r.total_calories} kcal</span></div>
        <div class="sum-row"><span>Avg / Max HR</span><span>${r.avg_hr} / ${r.max_hr} bpm</span></div>
        <div class="sum-zones">${bars}</div>
      </div>`;
  }).join("");
  document.getElementById("summary-overlay").classList.add("open");
}

function showToast(msg) {
  const t = document.getElementById("toast");
  if (!t) return;
  t.textContent = msg;
  t.style.opacity = "1";
  setTimeout(() => (t.style.opacity = "0"), 3000);
}

// Sieťový stav
async function updateNetInfo() {
  const el = document.getElementById("netinfo");
  if (!el) return;
  try {
    const res = await fetch("/api/network");
    const ifaces = await res.json();
    if (!ifaces.length) {
      el.textContent = "🔴 offline";
      el.style.color = "#e05050";
    } else {
      const { iface, type } = ifaces[0];
      el.textContent = (type === "wifi" ? "🟡" : "🟢") + " " + iface;
      el.style.color = "#aaa";
    }
  } catch {
    el.textContent = "🔴 offline";
    el.style.color = "#e05050";
  }
}
updateNetInfo();
setInterval(updateNetInfo, 10000);

// Hodiny
function updateClock() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleTimeString("sk-SK");
}
setInterval(updateClock, 1000);
updateClock();

// Batéria notebooku
function updateBattery(bat) {
  const el = document.getElementById("battery");
  if (!el) return;
  const pct   = Math.round(bat.level * 100);
  const color = pct <= 15 ? "#e05050" : pct <= 30 ? "#e0a020" : "#60c060";
  const icon  = bat.charging ? "⚡" : "🔋";
  el.textContent  = `${icon} ${pct}%`;
  el.style.color  = color;
}

if (navigator.getBattery) {
  navigator.getBattery().then(bat => {
    updateBattery(bat);
    bat.addEventListener("levelchange",    () => updateBattery(bat));
    bat.addEventListener("chargingchange", () => updateBattery(bat));
  });
}

async function fetchAndMergeRiders() {
  try {
    const res  = await fetch('/api/riders');
    const list = await res.json();
    const newPositions = new Set(list.map(r => r.bike_position));

    // Odstráň pozície ktoré už nie sú v konfigurácii
    for (const pos of Object.keys(riders).map(Number)) {
      if (!newPositions.has(pos)) {
        if (hideTimers[pos]) { clearTimeout(hideTimers[pos]); delete hideTimers[pos]; }
        delete riders[pos];
      }
    }

    // Doplň nových, aktualizuj metadata existujúcich (zachovaj live dáta)
    list.forEach(r => {
      if (riders[r.bike_position]) {
        riders[r.bike_position].name   = r.name;
        riders[r.bike_position].max_hr = r.max_hr;
      } else {
        riders[r.bike_position] = {
          ...r, position: r.bike_position,
          pct: 0, zone: 0, calories: 0, meps: 0, connected: false,
        };
      }
    });

    renderGrid();
  } catch (e) {
    console.warn("fetchAndMergeRiders zlyhalo, reloading:", e);
    location.reload();
  }
}

async function restoreZoneHistory() {
  try {
    const res = await fetch('/api/zone-history');
    if (!res.ok) return;
    const { history } = await res.json();
    let changed = false;
    for (const [pos, entries] of Object.entries(history)) {
      if (riders[pos] && entries.length) {
        riders[pos].zoneHistory = entries;
        changed = true;
      }
    }
    if (changed) renderGrid();
  } catch (e) {}
}

async function init() {
  await tryRestoreRidersState();
  connect();
}
init();
