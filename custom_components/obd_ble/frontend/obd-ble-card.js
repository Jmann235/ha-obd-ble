/* obd-ble-card — "Garage Hero" vehicle tile for the OBD BLE integration.
 *
 * Implements the chosen design direction (C) from the Claude Design handoff
 * bundle in design/home-assistant-obd: photo header, big range numeral,
 * segmented/ring/bar battery gauge, charging animations, and a slide-up
 * service sheet with urgency ring gauges. Dark-theme HA dashboard styling.
 *
 * No build step, no dependencies: a single vanilla custom element.
 */

const CARD_VERSION = "0.2.0";

const T = {
  card: "#1c1c1f",
  card2: "#232327",
  divider: "rgba(255,255,255,.08)",
  text: "#e8e8ea",
  sub: "#9b9ba3",
  green: "#4caf50",
  amber: "#ffa726",
  red: "#ef5350",
  font: "system-ui, -apple-system, 'Segoe UI', sans-serif",
  num: "'Space Grotesk', system-ui, sans-serif",
};

const DEFAULTS = {
  layout: "hero", // hero | wide | compact
  gauge: "segments", // segments | ring | bar
  accent: "#2dd4bf", // teal, chosen in the design session
  units: "mi", // mi | km
  charge_limit: 80,
  max_range: null, // full-charge range in display units (range fallback)
  usable_kwh: null, // usable pack kWh (time-to-limit + kWh-now)
  capacity_new: null, // new-pack capacity in the capacity entity's unit
  name: "Vehicle",
  image: null,
  charger_label: "",
  maintenance: [],
  entities: {},
};

const KM_PER_MI = 1.609344;

function urgencyColor(pct) {
  if (pct < 0.15) return T.red;
  if (pct < 0.4) return T.amber;
  return T.green;
}

function esc(value) {
  return String(value).replace(
    /[&<>"']/g,
    (ch) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch],
  );
}

function fmtInt(n) {
  return Math.round(n).toLocaleString("en-US");
}

function fmtHours(h) {
  const hh = Math.floor(h);
  const mm = Math.round((h - hh) * 60);
  return hh > 0 ? `${hh}h ${mm}m` : `${mm}m`;
}

/* Due-date formatting from the design: "Jul 5" this year, "Mar ’27" within
 * two years, bare "2029" beyond. */
function fmtDue(date) {
  const now = new Date();
  const month = date.toLocaleString("en-US", { month: "short" });
  if (date.getFullYear() === now.getFullYear()) return `${month} ${date.getDate()}`;
  if (date.getFullYear() <= now.getFullYear() + 2)
    return `${month} ’${String(date.getFullYear() % 100).padStart(2, "0")}`;
  return String(date.getFullYear());
}

/* ---------- icons: line glyphs ported from the design (24 viewBox) ---------- */
function svgIcon(inner, { size = 20, color = "currentColor", cls = "", fill = false } = {}) {
  return `<svg class="${cls}" width="${size}" height="${size}" viewBox="0 0 24 24"
    fill="${fill ? color : "none"}" stroke="${color}" stroke-width="1.8"
    stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;
}

const ICONS = {
  bolt: (o) =>
    svgIcon(`<path d="M13 2.5 6.5 13.5 H11 L10 21.5 17.5 10.5 H13 Z"/>`, o),
  drop: (o) =>
    svgIcon(
      `<path d="M12 3.5 c-3.1 3.9-5.8 7-5.8 10 a5.8 5.8 0 0 0 11.6 0 c0-3-2.7-6.1-5.8-10 Z"/>`,
      o,
    ),
  tire: (o) =>
    svgIcon(
      `<circle cx="12" cy="12" r="8.2"/><circle cx="12" cy="12" r="2.6"/>
       <path d="M12 3.8 V9.4 M12 14.6 V20.2 M3.8 12 H9.4 M14.6 12 H20.2"/>`,
      o,
    ),
  brake: (o) =>
    svgIcon(
      `<circle cx="12" cy="12" r="8.2"/><circle cx="12" cy="12" r="3.2"/>
       <circle cx="12" cy="6.6" r=".4"/><circle cx="12" cy="17.4" r=".4"/>
       <circle cx="6.6" cy="12" r=".4"/><circle cx="17.4" cy="12" r=".4"/>`,
      o,
    ),
  filter: (o) => svgIcon(`<path d="M5 7 h14 M7.5 12 h9 M10 17 h4"/>`, o),
  spark: (o) =>
    svgIcon(
      `<circle cx="12" cy="12" r="1.4"/>
       <path d="M12 4 v3.6 M12 16.4 V20 M4 12 h3.6 M16.4 12 H20 M6.6 6.6 l2.3 2.3 M15.1 15.1 l2.3 2.3 M17.4 6.6 l-2.3 2.3 M8.9 15.1 l-2.3 2.3"/>`,
      o,
    ),
  battery: (o) =>
    svgIcon(
      `<rect x="2.8" y="8" width="16" height="8.4" rx="2"/>
       <path d="M21.6 10.8 v2.8" stroke-width="2.2"/>`,
      o,
    ),
  plug: (o) =>
    svgIcon(
      `<path d="M9 6.5 V3 M15 6.5 V3"/>
       <path d="M7 6.5 h10 v3 a5 5 0 0 1-5 5 5 5 0 0 1-5-5 Z"/>
       <path d="M12 14.5 V17 c0 2-1.3 3.5-3.5 3.5"/>`,
      o,
    ),
  car: (o) =>
    svgIcon(
      `<path d="M4 15.5 v-3.6 c0-1.1.9-2 2-2 h12 c1.1 0 2 .9 2 2 v3.6"/>
       <path d="M6.5 9.9 8.3 6.8 c.3-.5.8-.8 1.4-.8 h4.6 c.6 0 1.1.3 1.4.8 l1.8 3.1"/>
       <path d="M4 15.5 h16"/>
       <circle cx="7.6" cy="15.5" r="1.9"/><circle cx="16.4" cy="15.5" r="1.9"/>`,
      o,
    ),
  chevr: (o) => svgIcon(`<path d="M9.5 6 15.5 12 9.5 18"/>`, o),
};

const STYLE = `
  :host { display: block; }
  * { box-sizing: border-box; }
  .card {
    background: ${T.card}; border-radius: 18px; overflow: hidden;
    font-family: ${T.font}; color: ${T.text}; position: relative;
  }
  .tappable { cursor: pointer; }
  .photo { position: relative; background: repeating-linear-gradient(45deg, #232328 0 10px, #1d1d21 10px 20px); }
  .photo img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .photo .ph-hint {
    position: absolute; inset: 0; display: grid; place-items: center;
    font-family: ui-monospace, monospace; font-size: 11px; color: rgba(255,255,255,.35);
  }
  .photo .shade {
    position: absolute; inset: 0; pointer-events: none;
    background: linear-gradient(to top, rgba(20,20,23,.92), transparent 55%);
  }
  .ident { position: absolute; left: 14px; bottom: 10px; pointer-events: none; }
  .ident .nm { font-size: 15px; font-weight: 600; }
  .ident .od { font-size: 11.5px; color: ${T.sub}; }
  .pill {
    position: absolute; display: flex; align-items: center; gap: 5px;
    background: rgba(11,11,13,.72); border-radius: 999px; padding: 4px 10px 4px 7px;
    pointer-events: none;
  }
  .pill span { font-size: 11.5px; font-weight: 600; }
  .numeral { font-family: ${T.num}; font-size: 40px; font-weight: 600; line-height: 1; letter-spacing: -0.02em; }
  .numeral small { font-size: 14px; font-weight: 500; color: ${T.sub}; margin-left: 4px; }
  .numsub { font-size: 11.5px; color: ${T.sub}; margin-top: 4px; }
  .row-split { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; }
  .row-mid { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  .segs { display: flex; gap: 3px; }
  .segs .seg { width: 7px; height: 22px; border-radius: 2.5px; background: rgba(255,255,255,.10); transition: background .4s; }
  .segs.mini { gap: 2.5px; }
  .segs.mini .seg { width: 4.5px; height: 15px; border-radius: 2px; }
  .gsub { font-size: 11.5px; color: ${T.sub}; margin-top: 5px; }
  .gsub b { color: ${T.text}; font-weight: 600; }
  .barwrap { height: 8px; border-radius: 8px; background: rgba(255,255,255,.10); overflow: hidden; position: relative; }
  .barfill { height: 100%; border-radius: 8px; position: relative; overflow: hidden; transition: width .8s ease; }
  .barmark { position: absolute; top: 0; bottom: 0; width: 2px; background: rgba(255,255,255,.45); }
  .stripes {
    position: absolute; top: 0; bottom: 0; left: -17px; right: 0;
    background: repeating-linear-gradient(-45deg, rgba(255,255,255,.26) 0 6px, transparent 6px 12px);
  }
  .ringpct {
    position: absolute; inset: 0; display: grid; place-items: center;
    font-size: 14px; font-weight: 600; font-family: ${T.num};
  }
  .status { display: flex; align-items: center; gap: 7px; margin-top: 11px; font-size: 12.5px; color: ${T.sub}; }
  .status .right { margin-left: auto; font-size: 12px; color: ${T.sub}; }
  .nextsvc {
    display: flex; align-items: center; gap: 7px;
    border-top: 1px solid ${T.divider}; margin-top: 12px; padding-top: 11px;
  }
  .dot { width: 7px; height: 7px; border-radius: 50%; flex: none; }
  .nextsvc .lbl { font-size: 12px; color: ${T.sub}; flex: 1; }
  .nextsvc .lbl b { color: ${T.text}; font-weight: 400; }
  /* wide */
  .wide { display: grid; grid-template-columns: 250px 1fr; }
  .wide .photo { min-height: 216px; }
  .wide .data { padding: 14px 16px; display: flex; flex-direction: column; }
  .wide .head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 12px; }
  .wide .head .nm { font-size: 15px; font-weight: 600; }
  .wide .head .od { font-size: 11.5px; color: ${T.sub}; }
  .wide .spacer { margin-top: auto; }
  @container (max-width: 430px) { .wide { grid-template-columns: 170px 1fr; } }
  /* compact */
  .compact { border-radius: 14px; padding: 10px 13px; }
  .compact .toprow { display: flex; align-items: center; gap: 11px; }
  .compact .circle {
    width: 42px; height: 42px; border-radius: 50%; overflow: hidden; flex: none;
    background: repeating-linear-gradient(45deg, #232328 0 10px, #1d1d21 10px 20px);
    display: grid; place-items: center; color: rgba(255,255,255,.4);
  }
  .compact .circle img { width: 100%; height: 100%; object-fit: cover; }
  .compact .mid { flex: 1; min-width: 0; }
  .compact .nm { font-size: 14px; font-weight: 600; }
  .compact .st { font-size: 12px; color: ${T.sub}; margin-top: 1px; }
  .compact .gpct { font-size: 10.5px; color: ${T.sub}; margin-top: 3px; text-align: right; }
  .compact .botrow {
    display: flex; align-items: center; gap: 7px;
    margin-top: 9px; padding-top: 9px; border-top: 1px solid ${T.divider};
  }
  .compact .botrow .dot { width: 6px; height: 6px; }
  .compact .botrow .lbl { font-size: 11.5px; color: ${T.sub}; }
  /* sheet */
  .overlay {
    position: fixed; inset: 0; background: rgba(0,0,0,.5); z-index: 999;
    display: flex; flex-direction: column; justify-content: flex-end;
  }
  .sheet {
    background: ${T.card2}; border-radius: 20px 20px 0 0; padding: 8px 18px 18px;
    max-width: 520px; width: 100%; margin: 0 auto;
    box-shadow: 0 -14px 44px rgba(0,0,0,.5);
    font-family: ${T.font}; color: ${T.text};
    max-height: 85vh; overflow-y: auto;
  }
  .sheet .handle { width: 38px; height: 4px; border-radius: 4px; background: rgba(255,255,255,.18); margin: 4px auto 12px; }
  .sheet .shead { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 6px; }
  .sheet .shead .ti { font-size: 15px; font-weight: 600; }
  .sheet .shead .od { font-size: 12px; color: ${T.sub}; }
  .chargebox {
    display: flex; align-items: center; gap: 12px;
    background: rgba(255,255,255,.05); border-radius: 14px; padding: 11px 13px; margin: 8px 0 12px;
  }
  .chargebox .main { flex: 1; }
  .chargebox .l1 { font-size: 13px; font-weight: 500; }
  .chargebox .l2 { font-size: 11.5px; color: ${T.sub}; }
  .chargebox .eta { text-align: right; }
  .chargebox .eta .v { font-size: 13px; font-weight: 600; font-family: ${T.num}; }
  .chargebox .eta .u { font-size: 11px; color: ${T.sub}; }
  .caprow { display: flex; justify-content: space-between; font-size: 12px; color: ${T.sub}; padding: 0 2px 10px; }
  .caprow b { color: ${T.text}; font-weight: 600; }
  .maint { border-top: 1px solid ${T.divider}; padding-top: 8px; }
  .mrow { display: flex; align-items: center; gap: 12px; padding: 8px 0; }
  .mring { position: relative; width: 34px; height: 34px; display: grid; place-items: center; flex: none; }
  .mring .mic { position: absolute; display: grid; place-items: center; }
  .mrow .mmid { flex: 1; }
  .mrow .mn { font-size: 13.5px; font-weight: 500; }
  .mrow .ms { font-size: 11.5px; color: ${T.sub}; }
  .mrow .mright { text-align: right; }
  .mrow .mmi { font-size: 13.5px; font-weight: 600; font-family: ${T.num}; }
  .mrow .mdue { font-size: 11px; color: ${T.sub}; }
  .unavail { opacity: .55; }
  /* animation */
  .fade { animation: vtFade .18s ease; }
  .up { animation: vtUp .32s cubic-bezier(.2,.8,.2,1); }
  @keyframes vtFade { from { opacity: 0; } to { opacity: 1; } }
  @keyframes vtUp { from { transform: translateY(100%); } to { transform: translateY(0); } }
  @media (prefers-reduced-motion: no-preference) {
    .stripes { animation: vtMove 1s linear infinite; }
    .pulse { animation: vtPulse 1.7s ease-in-out infinite; }
    .blink { animation: vtBlink 1.1s ease-in-out infinite; }
  }
  @keyframes vtMove { to { transform: translateX(17px); } }
  @keyframes vtPulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
  @keyframes vtBlink { 0%, 100% { opacity: .25; } 50% { opacity: 1; } }
`;

/* Inject the numeral font once per document (graceful fallback if offline). */
function ensureFont() {
  if (document.getElementById("obd-ble-card-font")) return;
  const link = document.createElement("link");
  link.id = "obd-ble-card-font";
  link.rel = "stylesheet";
  link.href =
    "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600&display=swap";
  document.head.appendChild(link);
}

class ObdBleCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._sheetOpen = false;
    this._lastSnapshot = null;
    this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
    this._onKey = (ev) => {
      if (ev.key === "Escape" && this._sheetOpen) {
        this._sheetOpen = false;
        this._render();
      }
    };
  }

  connectedCallback() {
    document.addEventListener("keydown", this._onKey);
  }

  disconnectedCallback() {
    document.removeEventListener("keydown", this._onKey);
  }

  static getStubConfig() {
    return {
      name: "Chevy Bolt",
      entities: { soc: "", charging: "", charge_power: "", odometer: "" },
      max_range: 259,
      usable_kwh: 65,
    };
  }

  setConfig(config) {
    if (!config || !config.entities || !config.entities.soc) {
      throw new Error("obd-ble-card: entities.soc is required");
    }
    this._config = { ...DEFAULTS, ...config, entities: { ...config.entities } };
    ensureFont();
    this._lastSnapshot = null;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    const snapshot = JSON.stringify(
      Object.values(this._config?.entities ?? {}).map((id) => {
        const st = id && hass.states[id];
        return st ? [st.state, st.attributes.unit_of_measurement] : null;
      }),
    );
    if (snapshot !== this._lastSnapshot) {
      this._lastSnapshot = snapshot;
      this._render();
    }
  }

  getCardSize() {
    return { hero: 6, wide: 4, compact: 2 }[this._config?.layout] ?? 6;
  }

  /* ---------- state assembly ---------- */

  _num(key) {
    const id = this._config.entities[key];
    if (!id || !this._hass) return null;
    const st = this._hass.states[id];
    if (!st || st.state === "unavailable" || st.state === "unknown") return null;
    const value = parseFloat(st.state);
    return Number.isFinite(value) ? value : null;
  }

  _unit(key) {
    const id = this._config.entities[key];
    const st = id && this._hass?.states[id];
    return st?.attributes?.unit_of_measurement ?? "";
  }

  _bool(key) {
    const id = this._config.entities[key];
    const st = id && this._hass?.states[id];
    return st ? st.state === "on" : null;
  }

  _toDisplayDistance(value, unit) {
    const mi = this._config.units === "mi";
    if (value == null) return null;
    const isKm = (unit || "").toLowerCase() === "km";
    if (mi && isKm) return value / KM_PER_MI;
    if (!mi && !isKm && unit) return value * KM_PER_MI;
    return value;
  }

  _vm() {
    const c = this._config;
    const soc = this._num("soc");
    const charging = this._bool("charging") ?? false;
    const rate = this._num("charge_power");
    const odo = this._toDisplayDistance(this._num("odometer"), this._unit("odometer"));
    let range = this._toDisplayDistance(this._num("range"), this._unit("range"));
    if (range == null && soc != null && c.max_range) range = (soc / 100) * c.max_range;
    const capacity = this._num("capacity");
    const limit = c.charge_limit;

    let timeToLimit = null;
    if (charging && soc != null && rate > 0.2 && c.usable_kwh && soc < limit) {
      timeToLimit = fmtHours((((limit - soc) / 100) * c.usable_kwh) / rate);
    }

    return {
      soc,
      charging,
      rate,
      odo,
      range,
      capacity,
      capacityUnit: this._unit("capacity"),
      limit,
      timeToLimit,
      kWhNow: soc != null && c.usable_kwh ? ((soc / 100) * c.usable_kwh).toFixed(1) : null,
      unit: c.units,
      maint: this._maintenance(odo),
    };
  }

  /* pct = fraction of interval left, miles or time — whichever is lower. */
  _maintenance(odoNow) {
    const items = [];
    for (const raw of this._config.maintenance || []) {
      if (!raw || !raw.name) continue;
      let miLeft = null;
      let miPct = null;
      if (raw.interval_miles && raw.last_miles != null && odoNow != null) {
        miLeft = Math.max(0, raw.last_miles + raw.interval_miles - odoNow);
        miPct = miLeft / raw.interval_miles;
      }
      let due = null;
      let timePct = null;
      if (raw.interval_months && raw.last_date) {
        const last = new Date(raw.last_date);
        if (!Number.isNaN(last.getTime())) {
          due = new Date(last);
          due.setMonth(due.getMonth() + raw.interval_months);
          const total = due.getTime() - last.getTime();
          timePct = Math.max(0, (due.getTime() - Date.now()) / total);
        }
      }
      const pcts = [miPct, timePct].filter((p) => p != null);
      if (!pcts.length) continue;
      items.push({
        name: raw.name,
        icon: ICONS[raw.icon] ? raw.icon : "car",
        mi: miLeft,
        due,
        pct: Math.min(1, Math.min(...pcts)),
      });
    }
    return items.sort((a, b) => a.pct - b.pct);
  }

  /* ---------- fragments ---------- */

  _gaugeSegments(vm, { mini = false } = {}) {
    const segs = mini ? 8 : 12;
    const pct = vm.soc ?? 0;
    const filled = (pct / 100) * segs;
    const accent = this._config.accent;
    let html = `<div class="segs${mini ? " mini" : ""}">`;
    for (let i = 0; i < segs; i += 1) {
      const full = i < Math.floor(filled);
      const edge = i === Math.floor(filled) && vm.charging;
      html += `<div class="seg${edge ? " blink" : ""}" style="${
        full || edge ? `background:${accent}` : ""
      }"></div>`;
    }
    return `${html}</div>`;
  }

  _gaugeRing(vm, size = 62) {
    const accent = this._config.accent;
    const pct = vm.soc ?? 0;
    const r = (size - 7) / 2;
    const circ = 2 * Math.PI * r;
    return `
      <div style="position:relative;width:${size}px;height:${size}px;flex:none">
        <svg width="${size}" height="${size}" style="transform:rotate(-90deg);display:block">
          <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="rgba(255,255,255,.10)" stroke-width="5"/>
          <circle class="${vm.charging ? "pulse" : ""}" cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none"
            stroke="${accent}" stroke-width="5" stroke-linecap="round"
            stroke-dasharray="${(circ * pct) / 100} ${circ}" style="transition:stroke-dasharray .8s"/>
        </svg>
        <div class="ringpct">${vm.soc != null ? Math.round(pct) : "–"}%</div>
      </div>`;
  }

  _gaugeBar(vm) {
    const accent = this._config.accent;
    const pct = Math.max(0, Math.min(100, vm.soc ?? 0));
    return `
      <div class="barwrap">
        <div class="barfill" style="width:${pct}%;background:${accent}">
          ${vm.charging ? '<div class="stripes"></div>' : ""}
        </div>
        ${vm.limit != null ? `<div class="barmark" style="left:${vm.limit}%"></div>` : ""}
      </div>`;
  }

  _numeral(vm) {
    const range = vm.range != null ? fmtInt(vm.range) : "–";
    return `
      <div>
        <div class="numeral">${range}<small>${vm.unit}</small></div>
        <div class="numsub">estimated range</div>
      </div>`;
  }

  _pctLine(vm) {
    const pct = vm.soc != null ? `${Math.round(vm.soc)}%` : "–%";
    const sub = vm.charging && vm.timeToLimit
      ? `${vm.timeToLimit} to ${vm.limit}%`
      : `limit ${vm.limit}%`;
    return { pct, sub };
  }

  _gaugeArea(vm) {
    const { pct, sub } = this._pctLine(vm);
    const gauge = this._config.gauge;
    if (gauge === "ring") {
      return `<div class="row-mid">${this._numeral(vm)}${this._gaugeRing(vm)}</div>`;
    }
    if (gauge === "bar") {
      return `
        <div class="row-split">
          ${this._numeral(vm)}
          <div style="text-align:right">
            <div style="font-family:${T.num};font-size:17px;font-weight:600">${pct}</div>
            <div style="font-size:11.5px;color:${T.sub}">${sub}</div>
          </div>
        </div>
        <div style="margin-top:11px">${this._gaugeBar(vm)}</div>`;
    }
    return `
      <div class="row-split">
        ${this._numeral(vm)}
        <div style="text-align:right">
          ${this._gaugeSegments(vm)}
          <div class="gsub"><b>${pct}</b> · ${sub}</div>
        </div>
      </div>`;
  }

  _statusRow(vm) {
    const accent = this._config.accent;
    const label = vm.charging
      ? `Charging ${vm.rate != null ? `${vm.rate.toFixed(1)} kW` : ""}${
          vm.timeToLimit ? ` · ${vm.timeToLimit} to ${vm.limit}%` : ""
        }`
      : "Not charging";
    const right =
      vm.kWhNow != null
        ? `<span class="right">${vm.kWhNow} / ${this._config.usable_kwh} kWh</span>`
        : "";
    return `
      <div class="status">
        ${ICONS.bolt({ size: 14, color: vm.charging ? accent : T.sub, fill: vm.charging, cls: vm.charging ? "pulse" : "" })}
        <span style="${vm.charging ? `color:${accent};font-weight:500` : ""}">${label}</span>
        ${right}
      </div>`;
  }

  _nextServiceRow(vm, compactStyle = false) {
    const next = vm.maint[0];
    if (!next) return "";
    const mi = next.mi != null ? `${fmtInt(next.mi)} ${vm.unit}` : "";
    const due = next.due ? fmtDue(next.due) : "";
    const tail = [mi && `<b>${mi}</b>`, due && `(${due})`].filter(Boolean).join(" ");
    if (compactStyle) {
      return `
        <div class="botrow">
          <span class="dot" style="background:${urgencyColor(next.pct)}"></span>
          <span class="lbl">${esc(next.name)} · ${mi}${due ? ` (${due})` : ""}</span>
        </div>`;
    }
    return `
      <div class="nextsvc">
        <span class="dot" style="background:${urgencyColor(next.pct)}"></span>
        <span class="lbl">${esc(next.name)} · ${tail}</span>
        ${ICONS.chevr({ size: 15, color: T.sub })}
      </div>`;
  }

  _chargePill(vm, pos) {
    if (!vm.charging) return "";
    const accent = this._config.accent;
    const label = vm.rate != null ? `${vm.rate.toFixed(1)} kW` : "Charging";
    return `
      <div class="pill" style="${pos};border:1px solid ${accent}55">
        ${ICONS.bolt({ size: 13, color: accent, fill: true, cls: "pulse" })}
        <span style="color:${accent}">${label}</span>
      </div>`;
  }

  _photo(vm, { heightPx = null } = {}) {
    const img = this._config.image
      ? `<img src="${esc(this._config.image)}" alt=""/>`
      : `<div class="ph-hint">set image: /local/your-car.png</div>`;
    return `
      <div class="photo" style="${heightPx ? `height:${heightPx}px` : "height:100%"}">
        ${img}
        <div class="shade"></div>
      </div>`;
  }

  _identity(vm) {
    const odo = vm.odo != null ? `${fmtInt(vm.odo)} ${vm.unit}` : "";
    return `
      <div class="ident">
        <div class="nm">${esc(this._config.name)}</div>
        <div class="od">${odo}</div>
      </div>`;
  }

  /* ---------- layouts ---------- */

  _hero(vm) {
    return `
      <div class="card">
        <div style="position:relative">
          ${this._photo(vm, { heightPx: 172 })}
          ${this._chargePill(vm, "top:10px;right:10px")}
          ${this._identity(vm)}
        </div>
        <div class="tappable" data-action="open" style="padding:13px 15px 14px">
          ${this._gaugeArea(vm)}
          ${this._statusRow(vm)}
          ${this._nextServiceRow(vm)}
        </div>
      </div>`;
  }

  _wide(vm) {
    const odo = vm.odo != null ? `${fmtInt(vm.odo)} ${vm.unit}` : "";
    return `
      <div class="card wide">
        <div style="position:relative">
          ${this._photo(vm)}
          ${this._chargePill(vm, "top:10px;left:10px")}
        </div>
        <div class="data tappable" data-action="open">
          <div class="head">
            <div class="nm">${esc(this._config.name)}</div>
            <div class="od">${odo}</div>
          </div>
          ${this._gaugeArea(vm)}
          ${this._statusRow(vm)}
          <div class="spacer">${this._nextServiceRow(vm)}</div>
        </div>
      </div>`;
  }

  _compact(vm) {
    const accent = this._config.accent;
    const range = vm.range != null ? `${fmtInt(vm.range)} ${vm.unit}` : `– ${vm.unit}`;
    const status = vm.charging
      ? `charging ${vm.rate != null ? `${vm.rate.toFixed(1)} kW` : ""}`
      : `${vm.soc != null ? Math.round(vm.soc) : "–"}%`;
    const img = this._config.image
      ? `<img src="${esc(this._config.image)}" alt=""/>`
      : ICONS.car({ size: 20, color: "rgba(255,255,255,.4)" });
    return `
      <div class="card compact tappable" data-action="open">
        <div class="toprow">
          <div class="circle">${img}</div>
          <div class="mid">
            <div class="nm">${esc(this._config.name)}</div>
            <div class="st" style="${vm.charging ? `color:${accent}` : ""}">${range} · ${status}</div>
          </div>
          <div>
            ${this._gaugeSegments(vm, { mini: true })}
            <div class="gpct">${vm.soc != null ? Math.round(vm.soc) : "–"}%</div>
          </div>
          ${ICONS.chevr({ size: 15, color: T.sub })}
        </div>
        ${this._nextServiceRow(vm, true)}
      </div>`;
  }

  /* ---------- service sheet ---------- */

  _maintRing(item) {
    const color = urgencyColor(item.pct);
    const size = 34;
    const r = (size - 5) / 2;
    const circ = 2 * Math.PI * r;
    return `
      <div class="mring">
        <svg width="${size}" height="${size}" style="transform:rotate(-90deg)">
          <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="rgba(255,255,255,.10)" stroke-width="3.5"/>
          <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${color}" stroke-width="3.5"
            stroke-linecap="round" stroke-dasharray="${circ * item.pct} ${circ}"/>
        </svg>
        <div class="mic" style="color:${color}">${ICONS[item.icon]({ size: 14, color })}</div>
      </div>`;
  }

  _sheet(vm) {
    const c = this._config;
    const accent = c.accent;
    const odo = vm.odo != null ? `${fmtInt(vm.odo)} ${vm.unit}` : "";

    let plugLine = c.charger_label;
    const acV = this._num("ac_voltage");
    const acA = this._num("ac_current");
    if (vm.charging && acV != null && acA != null && acV > 50) {
      plugLine = `AC ${Math.round(acV)} V · ${acA.toFixed(1)} A`;
    }

    const capParts = [];
    if (vm.capacity != null) {
      const unit = vm.capacityUnit || "";
      let cap = `Capacity <b>${vm.capacity.toFixed(1)} ${unit}</b>`;
      if (c.capacity_new) cap += ` of ${c.capacity_new} new`;
      capParts.push(`<span>${cap}</span>`);
      if (c.capacity_new) {
        const soh = Math.min(100, (vm.capacity / c.capacity_new) * 100);
        capParts.push(`<span>Health <b>${soh.toFixed(0)}%</b></span>`);
      }
    }

    const rows = vm.maint
      .map(
        (item) => `
        <div class="mrow">
          ${this._maintRing(item)}
          <div class="mmid">
            <div class="mn">${esc(item.name)}</div>
            <div class="ms">${Math.round(item.pct * 100)}% of interval left</div>
          </div>
          <div class="mright">
            <div class="mmi">${item.mi != null ? `${fmtInt(item.mi)} ${vm.unit}` : ""}</div>
            <div class="mdue">${item.due ? `or ${fmtDue(item.due)}` : ""}</div>
          </div>
        </div>`,
      )
      .join("");

    return `
      <div class="overlay fade" data-action="close">
        <div class="sheet up" data-stop="1">
          <div class="handle"></div>
          <div class="shead">
            <div class="ti">Battery &amp; service</div>
            <div class="od">${odo}</div>
          </div>
          <div class="chargebox">
            ${ICONS.plug({ size: 20, color: vm.charging ? accent : T.sub })}
            <div class="main">
              <div class="l1" style="${vm.charging ? `color:${accent}` : ""}">
                ${vm.charging ? `Charging${vm.rate != null ? ` · ${vm.rate.toFixed(1)} kW` : ""}` : "Not charging"}
              </div>
              ${plugLine ? `<div class="l2">${esc(plugLine)}</div>` : ""}
            </div>
            ${
              vm.charging && vm.timeToLimit
                ? `<div class="eta"><div class="v">${vm.timeToLimit}</div><div class="u">to ${vm.limit}%</div></div>`
                : ""
            }
          </div>
          ${capParts.length ? `<div class="caprow">${capParts.join("")}</div>` : ""}
          ${rows ? `<div class="maint">${rows}</div>` : ""}
        </div>
      </div>`;
  }

  /* ---------- render & events ---------- */

  _onClick(ev) {
    const target = ev.composedPath().find((el) => el.dataset?.action || el.dataset?.stop);
    if (!target) return;
    if (target.dataset.stop) return;
    if (target.dataset.action === "open") {
      this._sheetOpen = true;
      this._render();
    } else if (target.dataset.action === "close") {
      this._sheetOpen = false;
      this._render();
    }
  }

  _render() {
    if (!this._config) return;
    const vm = this._hass ? this._vm() : null;
    if (!vm) {
      this.shadowRoot.innerHTML = `<style>${STYLE}</style><div class="card" style="padding:16px;color:${T.sub}">obd-ble-card</div>`;
      return;
    }
    const layout =
      { hero: () => this._hero(vm), wide: () => this._wide(vm), compact: () => this._compact(vm) }[
        this._config.layout
      ] ?? (() => this._hero(vm));
    const stale = this._config.entities.soc &&
      this._hass.states[this._config.entities.soc] == null;
    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <div class="${stale ? "unavail" : ""}">${layout()}</div>
      ${this._sheetOpen ? this._sheet(vm) : ""}`;
  }
}

if (!customElements.get("obd-ble-card")) {
  customElements.define("obd-ble-card", ObdBleCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "obd-ble-card")) {
  window.customCards.push({
    type: "obd-ble-card",
    name: "OBD BLE Vehicle Card",
    description:
      "Garage Hero vehicle tile: photo, range, battery gauge, charging state, and a service sheet.",
  });
}

console.info(`%c OBD-BLE-CARD %c ${CARD_VERSION} `, "background:#2dd4bf;color:#0b0b0d;font-weight:700", "background:#1c1c1f;color:#2dd4bf");
