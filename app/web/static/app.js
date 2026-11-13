const state = {
  status: null,
  kinds: [],
  assets: [],
  rules: [],
  livePrices: {},
  password: localStorage.getItem("panel_password") || "",
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function fmtPrice(n) {
  if (typeof n !== "number") return "—";
  if (n >= 1000) return n.toLocaleString("es-ES", { maximumFractionDigits: 0 });
  if (n >= 1) return n.toLocaleString("es-ES", { maximumFractionDigits: 3 });
  return n.toLocaleString("es-ES", { maximumFractionDigits: 6 });
}

function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString("es-ES");
}

function fmtDateTime(ts) {
  return new Date(ts * 1000).toLocaleString("es-ES");
}

function toast(message, type = "info", ms = 4000) {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.innerHTML = message;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), ms);
}

async function api(path, options = {}) {
  options.headers = { ...(options.headers || {}), "X-Panel-Password": state.password };
  const resp = await fetch(path, options);
  if (resp.status === 401) {
    showAuth();
    throw new Error("Contraseña requerida");
  }
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || resp.statusText);
  }
  return resp.json();
}

function showAuth() {
  $("#auth-overlay").classList.remove("hidden");
}

$("#auth-submit").addEventListener("click", () => {
  state.password = $("#auth-password").value;
  localStorage.setItem("panel_password", state.password);
  $("#auth-overlay").classList.add("hidden");
  refreshAll();
});

$("#auth-password").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("#auth-submit").click();
});

function tab(name) {
  $$(".tab, .tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab-page").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  if (name === "alerts") refreshAlertsTab();
  if (name === "chart") refreshChart();
  if (name === "settings") refreshSettings();
  if (name === "logs") refreshLogs();
}

$$(".tab").forEach((b) => b.addEventListener("click", () => tab(b.dataset.tab)));

async function refreshStatus() {
  try {
    state.status = await api("/api/status");
    renderStatus();
  } catch (e) { console.error(e); }
}

function renderStatus() {
  const s = state.status;
  if (!s) return;
  const mode = $("#mode-pill");
  mode.textContent = s.mock ? "● demo" : "● real";
  mode.className = `pill ${s.mock ? "paused" : "running"}`;
  const eng = $("#engine-pill");
  eng.className = `pill ${s.running ? (s.paused ? "paused" : "running") : "stopped"}`;
  eng.textContent = s.running ? (s.paused ? "⏸ pausado" : "● en marcha") : "○ parado";
  renderPriceCards();
}

function renderPriceCards() {
  const s = state.status;
  const assets = (s && s.assets) || [];
  $("#price-cards").innerHTML = assets.map((a) => {
    const ch = a.change_24h;
    const cls = ch == null ? "" : ch >= 0 ? "up" : "down";
    const sign = ch == null ? "" : `${ch >= 0 ? "+" : ""}${ch.toFixed(2)}%`;
    const sparkId = `spark-${a.symbol}`;
    return `<div class="price-card">
      <div class="sym">${escapeHtml(a.symbol)}</div>
      <div class="name">${escapeHtml(a.name || "—")}</div>
      <div class="price" id="price-${a.symbol}">${fmtPrice(a.price)}</div>
      <div class="change ${cls}" id="change-${a.symbol}">${sign} <span class="muted">24h</span></div>
      <canvas id="${sparkId}" height="40"></canvas>
    </div>`;
  }).join("") || '<div class="card muted">Añade activos en Ajustes.</div>';
  assets.forEach((a) => drawSparkline(a.symbol));
}

function drawSparkline(symbol) {
  const canvas = document.getElementById(`spark-${symbol}`);
  const series = state.livePrices[symbol];
  if (!canvas || !series || series.length < 2) return;
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || canvas.parentElement.clientWidth;
  const h = 40;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  ctx.scale(dpr, dpr);
  const min = Math.min(...series);
  const max = Math.max(...series);
  const range = max - min || 1;
  ctx.beginPath();
  series.forEach((v, i) => {
    const x = (i / (series.length - 1)) * (w - 2) + 1;
    const y = h - 4 - ((v - min) / range) * (h - 8);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  const up = series[series.length - 1] >= series[0];
  ctx.strokeStyle = up ? "#34d399" : "#f87171";
  ctx.lineWidth = 1.5;
  ctx.stroke();
}

async function refreshAlertsTab() {
  try {
    const [assetsData, rulesData, alertsData] = await Promise.all([
      api("/api/assets"),
      api("/api/rules"),
      api("/api/alerts?limit=50"),
    ]);
    state.assets = assetsData.assets;
    state.rules = rulesData.rules;
    const sel = $("#rule-symbol");
    sel.innerHTML = state.assets.map((a) => `<option value="${a.symbol}">${a.symbol}</option>`).join("");
    $("#rules-table tbody").innerHTML = state.rules.map((r) => `<tr>
      <td><b>${escapeHtml(r.symbol)}</b></td>
      <td>${escapeHtml(r.description)}</td>
      <td>${r.cooldown_min} min</td>
      <td>${r.last_triggered ? fmtDateTime(r.last_triggered) : "—"}</td>
      <td>
        <label class="check"><input type="checkbox" data-rule="${r.id}" ${r.enabled ? "checked" : ""}></label>
      </td>
      <td><button class="btn danger" data-del="${r.id}">✕</button></td>
    </tr>`).join("") || '<tr><td colspan="6" class="muted">Sin reglas. Crea la primera arriba.</td></tr>';
    $$("#rules-table [data-rule]").forEach((input) => {
      input.addEventListener("change", async () => {
        try {
          await api(`/api/rules/${input.dataset.rule}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled: input.checked }),
          });
          toast("Regla actualizada", "success");
        } catch (e) {
          toast("Error: " + e.message, "error");
          input.checked = !input.checked;
        }
      });
    });
    $$("#rules-table [data-del]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        try {
          await api(`/api/rules/${btn.dataset.del}`, { method: "DELETE" });
          toast("Regla eliminada", "info");
          refreshAlertsTab();
        } catch (e) { toast("Error: " + e.message, "error"); }
      });
    });
    $("#alerts-table tbody").innerHTML = alertsData.alerts.map((a) => `<tr>
      <td>${fmtDateTime(a.ts)}</td>
      <td><b>${escapeHtml(a.symbol)}</b></td>
      <td>${escapeHtml(a.message.replace(/<[^>]+>/g, ""))}</td>
    </tr>`).join("") || '<tr><td colspan="3" class="muted">Sin alertas disparadas todavía.</td></tr>';
    renderRecentAlerts(alertsData.alerts.slice(0, 5));
  } catch (e) { console.error(e); }
}

function renderRecentAlerts(alerts) {
  const box = $("#recent-alerts");
  box.innerHTML = alerts.length
    ? alerts.map((a) => `<div class="alert-banner">🚨 ${escapeHtml(a.message.replace(/<[^>]+>/g, ""))} <span class="muted">· ${fmtDateTime(a.ts)}</span></div>`).join("")
    : '<p class="muted">Nada aún. Cuando una regla se cumpla, aparecerá aquí y en Telegram.</p>';
}

async function refreshKinds() {
  const data = await api("/api/kinds");
  state.kinds = data.kinds;
  const sel = $("#rule-kind");
  sel.innerHTML = state.kinds.map((k) => `<option value="${k.kind}">${k.label}</option>`).join("");
  updateRuleHint();
}

function updateRuleHint() {
  const kind = $("#rule-kind").value;
  const meta = state.kinds.find((k) => k.kind === kind);
  const label = $("#rule-value-label");
  const input = $("#rule-value");
  if (meta && meta.value_label) {
    label.classList.remove("hidden");
    label.childNodes[0].textContent = meta.value_label + " ";
    input.required = true;
  } else {
    label.classList.add("hidden");
    input.required = false;
  }
  const asset = state.assets.find((a) => a.symbol === $("#rule-symbol").value);
  const isPrice = kind === "above" || kind === "below";
  $("#rule-hint").textContent = isPrice
    ? (asset && asset.price != null ? `Precio actual de ${asset.symbol}: ${fmtPrice(asset.price)}` : "")
    : "";
}

$("#rule-kind").addEventListener("change", updateRuleHint);
$("#rule-symbol").addEventListener("change", updateRuleHint);

$("#rule-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    await api("/api/rules", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol: $("#rule-symbol").value,
        kind: $("#rule-kind").value,
        value: parseFloat($("#rule-value").value) || 1,
        cooldown_min: parseInt($("#rule-cooldown").value) || 30,
      }),
    });
    toast("Alerta creada 🔔", "success");
    $("#rule-value").value = "";
    refreshAlertsTab();
  } catch (e) { toast("Error: " + e.message, "error"); }
});

async function refreshChart() {
  const symbol = $("#chart-symbol").value || (state.assets[0] && state.assets[0].symbol);
  if (!symbol) return;
  const hours = parseInt($("#chart-range").value) || 24;
  try {
    const data = await api(`/api/history?symbol=${symbol}&hours=${hours}`);
    drawChart(data.points);
  } catch (e) { console.error(e); }
}

function drawChart(points) {
  const canvas = $("#price-chart");
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || canvas.parentElement.clientWidth;
  const h = canvas.clientHeight || 380;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  if (!points || points.length < 2) {
    ctx.fillStyle = "#8b93a7";
    ctx.font = "13px sans-serif";
    ctx.fillText("Sin datos suficientes todavía", 20, h / 2);
    $("#chart-stats").innerHTML = "";
    return;
  }
  const prices = points.map((p) => p.price);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const range = max - min || 1;
  const pad = 30;
  const x = (i) => (i / (points.length - 1)) * (w - pad * 2) + pad;
  const y = (v) => h - 30 - ((v - min) / range) * (h - 60);
  ctx.strokeStyle = "#232c45";
  ctx.fillStyle = "#8b93a7";
  ctx.font = "11px sans-serif";
  for (let g = 0; g <= 4; g++) {
    const gy = 30 + (g / 4) * (h - 60);
    const val = max - (g / 4) * range;
    ctx.fillText(fmtPrice(val), 2, gy + 4);
    ctx.beginPath();
    ctx.moveTo(pad, gy);
    ctx.lineTo(w - pad, gy);
    ctx.stroke();
  }
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  const up = prices[prices.length - 1] >= prices[0];
  grad.addColorStop(0, up ? "rgba(52,211,153,0.25)" : "rgba(248,113,113,0.25)");
  grad.addColorStop(1, "rgba(0,0,0,0)");
  ctx.beginPath();
  points.forEach((p, i) => (i === 0 ? ctx.moveTo(x(i), y(p.price)) : ctx.lineTo(x(i), y(p.price))));
  ctx.strokeStyle = up ? "#34d399" : "#f87171";
  ctx.lineWidth = 2;
  ctx.stroke();
  ctx.lineTo(x(points.length - 1), h - 30);
  ctx.lineTo(x(0), h - 30);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();
  const first = prices[0];
  const last = prices[prices.length - 1];
  const chg = ((last - first) / first) * 100;
  $("#chart-stats").innerHTML = `
    <span>Mín <b>${fmtPrice(min)}</b></span>
    <span>Máx <b>${fmtPrice(max)}</b></span>
    <span>Actual <b>${fmtPrice(last)}</b></span>
    <span class="${chg >= 0 ? "change up" : "change down"}"><b>${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%</b></span>`;
}

$("#chart-symbol").addEventListener("change", refreshChart);
$("#chart-range").addEventListener("change", refreshChart);

async function refreshSettings() {
  try {
    const [cfg, assetsData] = await Promise.all([api("/api/config"), api("/api/assets")]);
    state.assets = assetsData.assets;
    $("#poll-seconds").value = cfg.poll_seconds;
    $("#digest-enabled").checked = cfg.digest ? cfg.digest.enabled !== false : true;
    $("#digest-hour").value = cfg.digest && cfg.digest.hour != null ? cfg.digest.hour : 9;
    const s = state.status || {};
    $("#engine-info").innerHTML = `
      <span class="k">Modo</span><span>${cfg.mock ? "Demo (simulado)" : "Real (Binance)"}</span>
      <span class="k">Ciclos completados</span><span>${s.ticks ?? "—"}</span>
      <span class="k">Errores</span><span>${s.errors ?? "—"}</span>
      <span class="k">Última consulta</span><span>${s.last_tick ? fmtDateTime(s.last_tick) : "—"}</span>`;
    $("#chart-symbol").innerHTML = state.assets.map((a) => `<option value="${a.symbol}">${a.symbol}</option>`).join("");
    $("#assets-manage").innerHTML = state.assets.map((a) => `<span class="chip">${a.symbol}
      <button data-rm="${a.symbol}" title="Eliminar">✕</button></span>`).join("");
    $$("#assets-manage [data-rm]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm(`¿Eliminar ${btn.dataset.rm} y sus alertas?`)) return;
        try {
          await api(`/api/assets/${btn.dataset.rm}`, { method: "DELETE" });
          toast("Activo eliminado", "info");
          refreshAll();
        } catch (e) { toast("Error: " + e.message, "error"); }
      });
    });
  } catch (e) { console.error(e); }
}

$("#btn-save-config").addEventListener("click", async () => {
  try {
    await api("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ poll_seconds: parseInt($("#poll-seconds").value) || 15 }),
    });
    toast("Configuración guardada ✓", "success");
  } catch (e) { toast("Error: " + e.message, "error"); }
});

$( "#btn-save-digest").addEventListener("click", async () => {
  try {
    await api("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        digest_enabled: $("#digest-enabled").checked,
        digest_hour: parseInt($("#digest-hour").value) || 9,
      }),
    });
    toast("Resumen diario guardado ✓", "success");
  } catch (e) { toast("Error: " + e.message, "error"); }
});

$("#btn-test-digest").addEventListener("click", async () => {
  try {
    const r = await api("/api/digest/test", { method: "POST" });
    toast(r.telegram_sent ? "Resumen enviado a Telegram ✓" : "Resumen generado (Telegram no configurado: míralo en Logs)", r.telegram_sent ? "success" : "info");
  } catch (e) { toast("Error: " + e.message, "error"); }
});

$("#asset-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const form = ev.target;
  try {
    await api("/api/assets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol: form.symbol.value, name: form.name.value }),
    });
    toast("Activo añadido ✓", "success");
    form.reset();
    refreshAll();
  } catch (e) { toast("Error: " + e.message, "error"); }
});

$("#notify-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const form = ev.target;
  try {
    await api("/api/notify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token: form.token.value,
        chat_id: form.chat_id.value,
        enabled: form.enabled.checked,
      }),
    });
    toast("Telegram configurado. Mensaje de prueba enviado.", "success");
  } catch (e) { toast("Error: " + e.message, "error"); }
});

function logLine(level, message, ts) {
  return `<div class="log-line ${level}"><span class="ts">${fmtTime(ts)}</span><span class="msg">${escapeHtml(message)}</span></div>`;
}

async function refreshLogs() {
  try {
    const logs = (await api("/api/logs?limit=300")).reverse();
    const level = $("#log-level").value;
    const filtered = level ? logs.filter((l) => l.level === level) : logs;
    $("#logs").innerHTML = filtered.map((l) => logLine(l.level, l.message, l.ts)).join("") || '<div class="log-line"><span class="msg muted">Sin registros</span></div>';
  } catch (e) { console.error(e); }
}

$("#log-level").addEventListener("change", refreshLogs);

function connectStream() {
  const url = state.password ? `/api/stream?key=${encodeURIComponent(state.password)}` : "/api/stream";
  const es = new EventSource(url);
  es.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data);
      if (data.kind === "prices") {
        for (const [symbol, info] of Object.entries(data.data || {})) {
          const list = state.livePrices[symbol] = state.livePrices[symbol] || [];
          list.push(info.price);
          if (list.length > 120) list.shift();
          const priceEl = document.getElementById(`price-${symbol}`);
          const changeEl = document.getElementById(`change-${symbol}`);
          if (priceEl) priceEl.textContent = fmtPrice(info.price);
          if (changeEl) {
            const ch = info.change_24h;
            changeEl.className = `change ${ch >= 0 ? "up" : "down"}`;
            changeEl.innerHTML = `${ch >= 0 ? "+" : ""}${ch.toFixed(2)}% <span class="muted">24h</span>`;
          }
        }
        if ($("#tab-dashboard").classList.contains("active")) {
          state.status.assets.forEach((a) => {
            const info = data.data[a.symbol];
            if (info) { a.price = info.price; a.change_24h = info.change_24h; }
          });
          state.status.assets.forEach((a) => drawSparkline(a.symbol));
        }
      }
      if (data.kind === "alert") {
        const text = data.message.replace(/<[^>]+>/g, "");
        toast(text, "alert", 8000);
        if ("Notification" in window && Notification.permission === "granted" && document.hidden) {
          new Notification("Crypto Monitor", { body: text, tag: "monitor-alert" });
        }
        refreshStatus();
        if ($("#tab-alerts").classList.contains("active")) refreshAlertsTab();
      }
      if (data.kind === "digest") {
        toast("📊 Resumen diario generado (míralo en Logs)", "info");
      }
      if (data.kind === "log") refreshStatus();
    } catch (e) { console.error(e); }
  };
  es.onerror = () => {
    es.close();
    setTimeout(connectStream, 5000);
  };
}

function refreshAll() {
  refreshStatus();
  refreshKinds().then(updateRuleHint).catch(() => {});
  refreshAlertsTab();
  refreshSettings();
  refreshLogs();
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

$("#btn-push").addEventListener("click", async () => {
  if (!("Notification" in window)) {
    toast("Tu navegador no soporta notificaciones", "error");
    return;
  }
  const perm = await Notification.requestPermission();
  if (perm === "granted") {
    toast("Notificaciones activadas 🔔", "success");
    new Notification("Crypto Monitor", { body: "Te avisaré aquí cuando salte una alerta." });
  } else {
    toast("Notificaciones bloqueadas en el navegador", "error");
  }
});

refreshAll();
setInterval(refreshStatus, 10000);
setInterval(() => {
  if ($("#tab-alerts").classList.contains("active")) refreshAlertsTab();
}, 20000);
setInterval(() => {
  if ($("#tab-chart").classList.contains("active")) refreshChart();
}, 60000);
connectStream();
window.addEventListener("resize", () => {
  state.status && state.status.assets.forEach((a) => drawSparkline(a.symbol));
  if ($("#tab-chart").classList.contains("active")) refreshChart();
});
