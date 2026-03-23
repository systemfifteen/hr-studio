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

let riders = {};   // position → {name, hr, pct, zone, calories, connected}
let ws = null;

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
        riders[r.position] = { ...r, pct: 0, zone: 0, calories: 0 };
      });
      renderGrid();
      break;

    case "hr_update":
      if (!riders[msg.position]) riders[msg.position] = {};
      Object.assign(riders[msg.position], {
        name:      msg.name,
        hr:        msg.hr,
        pct:       msg.pct,
        zone:      msg.zone,
        calories:  msg.calories,
        meps:      msg.meps,
        battery:   msg.battery,
        connected: true,
      });
      pushZoneHistory(msg.position, msg.zone);
      updateCard(msg.position);
      break;

    case "disconnected":
    case "signal_lost":
      if (riders[msg.position]) {
        riders[msg.position].connected = false;
        updateCard(msg.position);
      }
      break;

    case "riders_updated":
      setTimeout(() => location.reload(), 2000);
      showToast("Riders aktualizovaní");
      break;

    case "session_stopped":
      if (msg.summary?.length) showSummary(msg.summary);
      break;
  }
}

function renderGrid() {
  const positions = Object.keys(riders).map(Number).sort((a, b) => a - b);
  const count = positions.length;
  const cols = count === 1 ? 1 : count <= 4 ? 2 : count <= 9 ? 3 : count <= 16 ? 4 : 5;
  const rows = Math.ceil(count / cols);

  const grid = document.getElementById("grid");
  const GAP = 8, PAD = 10;
  const availW = grid.clientWidth  - PAD * 2;
  const availH = grid.clientHeight - PAD * 2;
  const cellW  = (availW - GAP * (cols - 1)) / cols;
  const cellH  = (availH - GAP * (rows - 1)) / rows;
  const cell   = Math.floor(Math.min(cellW, cellH));

  grid.style.gridTemplateColumns = `repeat(${cols}, ${cell}px)`;
  grid.style.gridTemplateRows    = `repeat(${rows}, ${cell}px)`;
  grid.style.alignContent        = "center";
  grid.style.justifyContent      = "center";
  document.documentElement.style.setProperty("--cell", cell + "px");
  grid.innerHTML = "";

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
  const meps = r.meps ?? 0;
  return `
    <div class="card-inner" style="background:${bg}">
      <div class="card-header">
        <span class="card-name">${r.name ?? `Bike ${pos}`}</span>
        <span class="card-cal">🔥 ${cal} kcal</span>
      </div>
      ${zoneHistoryHTML(r)}
      <div class="zone-bar"><div class="zone-fill" style="width:${pct}%"></div></div>
      <div class="card-pct" ${(r.zone ?? 0) === 0 ? 'style="color:#e05050"' : ""}>${pct}%</div>
      <div class="card-footer">
        <span class="card-bpm">❤️ ${hr} bpm</span>
        <span class="card-pos">#${pos}</span>
        <span class="card-meps">⛰️ ${meps} <span class="meps-label">MEPs</span></span>
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

connect();
