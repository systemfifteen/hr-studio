const WS_URL = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`;
const RECONNECT_MS = 3000;

const ZONE_COLORS = {
  0: "#555555",
  1: "#1a5fa8",
  2: "#1a8c3e",
  3: "#b8820a",
  4: "#a01820",
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
      // Refresh po webhook zmene rezervácie
      setTimeout(() => location.reload(), 2000);
      showToast("Riders aktualizovaní");
      break;
  }
}

function renderGrid() {
  const positions = Object.keys(riders).map(Number).sort((a, b) => a - b);
  const count = positions.length;
  const cols = count === 1 ? 1 : count <= 4 ? 2 : count <= 9 ? 3 : count <= 16 ? 4 : 5;

  const grid = document.getElementById("grid");
  grid.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
  document.documentElement.style.setProperty("--grid-cols", cols);
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
        ${r.battery != null ? `<span class="card-bat-wrap">${batteryIcon(r.battery)}</span>` : ""}
      </div>
      <div class="zone-bar"><div class="zone-fill" style="width:${pct}%"></div></div>
      <div class="card-pct">${pct}%</div>
      <div class="card-meps">${meps} <span class="meps-label">MEPs</span></div>
      <div class="card-footer">
        <span class="card-bpm">${hr} bpm</span>
        <span class="card-cal">🔥 ${cal} kcal</span>
        <span class="card-pos">#${pos}</span>
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

function showToast(msg) {
  const t = document.getElementById("toast");
  if (!t) return;
  t.textContent = msg;
  t.style.opacity = "1";
  setTimeout(() => (t.style.opacity = "0"), 3000);
}

// Hodiny
function updateClock() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleTimeString("sk-SK");
}
setInterval(updateClock, 1000);
updateClock();

connect();
