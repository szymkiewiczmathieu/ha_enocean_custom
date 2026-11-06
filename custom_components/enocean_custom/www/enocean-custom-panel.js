/* EnOcean Custom admin panel. No build step, dependencies, telemetry or remote assets. */
const TABS = ["devices", "nearby", "telegrams", "gateway"];
const FILTERS = ["all", "active", "silent", "unconfigured"];
const MAX_TELEGRAMS = 100;
const MASKED = "••:••:••:••";
const ACTIVE_WINDOW_MS = 60 * 60 * 1000;
const SILENCE_WARNING_MS = 6 * 60 * 60 * 1000;
const SILENCE_CRITICAL_MS = 24 * 60 * 60 * 1000;
const CLOCK_TICK_MS = 1000;
const ACTIVITY_SAMPLE_LIMIT = 120;
const ACTIVITY_MINUTES = 30;

const PLATFORM_META = {
  light: { label: "Lights" },
  switch: { label: "Switches & rockers" },
  cover: { label: "Shutters" },
  binary_sensor: { label: "Sensors & contacts" },
  sensor: { label: "Sensors & contacts" },
  climate: { label: "Climate" },
  other: { label: "Other devices" },
  unconfigured: { label: "Unconfigured transmitters" },
};

const ICON_PATHS = {
  light: "M9 21h6v-1H9v1Zm3-20a7 7 0 0 0-4 12.74V17h8v-3.26A7 7 0 0 0 12 1Zm2 12.2V15h-4v-1.8A5 5 0 1 1 14 13.2Z",
  socket: "M6 2h12v20H6V2Zm2 2v16h8V4H8Zm2 3h1v5h-1V7Zm3 0h1v5h-1V7Zm-4 8h6v2H9v-2Z",
  rocker: "M4 3h16v18H4V3Zm2 2v14h12V5H6Zm6 2 4 5H8l4-5Zm0 10-4-5h8l-4 5Z",
  cover: "M5 3h14v2H5V3Zm0 4h14v2H5V7Zm0 4h14v2H5v-2Zm0 4h14v2H5v-2Zm2 4h10v2H7v-2Z",
  contact: "M4 3h16v18H4V3Zm2 2v14h5V5H6Zm7 0v14h5V5h-5Z",
  thermometer: "M15 13.26V5a3 3 0 0 0-6 0v8.26a5 5 0 1 0 6 0ZM12 4a1 1 0 0 1 1 1v9.1l.55.35a3 3 0 1 1-3.1 0L11 14.1V5a1 1 0 0 1 1-1Z",
  climate: "M5 4h14a2 2 0 0 1 2 2v13h-2v2h-2v-2H7v2H5v-2H3V6a2 2 0 0 1 2-2Zm0 2v11h14V6H5Zm2 2h2v7H7V8Zm4 0h2v7h-2V8Zm4 0h2v7h-2V8Z",
  radio: "M11 2h2v8.27a4 4 0 1 1-2 0V2Zm1 12a2 2 0 1 0 0 4 2 2 0 0 0 0-4ZM5.64 5.64 4.22 4.22a11 11 0 0 0 0 15.56l1.42-1.42a9 9 0 0 1 0-12.72Zm12.72 0a9 9 0 0 1 0 12.72l1.42 1.42a11 11 0 0 0 0-15.56l-1.42 1.42ZM8.46 8.46 7.05 7.05a7 7 0 0 0 0 9.9l1.41-1.41a5 5 0 0 1 0-7.08Zm7.08 0a5 5 0 0 1 0 7.08l1.41 1.41a7 7 0 0 0 0-9.9l-1.41 1.41Z",
  gateway: "M12 2a3 3 0 0 0-1 5.83V11H7a3 3 0 0 0-3 3v7h2v-7a1 1 0 0 1 1-1h4v2.18a3 3 0 1 0 2 0V13h4a1 1 0 0 1 1 1v7h2v-7a3 3 0 0 0-3-3h-4V7.83A3 3 0 0 0 12 2Zm0 2a1 1 0 1 1 0 2 1 1 0 0 1 0-2Zm0 14a1 1 0 1 1 0 2 1 1 0 0 1 0-2Z",
  search: "M9.5 3a6.5 6.5 0 1 0 3.98 11.65L19.85 21 21 19.85l-6.35-6.37A6.5 6.5 0 0 0 9.5 3Zm0 2a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9Z",
  devices: "M4 4h7v7H4V4Zm9 0h7v7h-7V4ZM4 13h7v7H4v-7Zm9 0h7v7h-7v-7ZM6 6v3h3V6H6Zm9 0v3h3V6h-3ZM6 15v3h3v-3H6Zm9 0v3h3v-3h-3Z",
  nearby: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm0 2a2 2 0 1 1 0 4 2 2 0 0 1 0-4ZM2.93 2.93 1.51 4.34a10.83 10.83 0 0 0 0 15.32l1.42-1.42a8.83 8.83 0 0 1 0-12.48l1.41-1.41a6.83 6.83 0 0 1 0 9.65l1.42 1.42a8.83 8.83 0 0 0 0-12.49L2.93 2.93Zm18.14 0-2.83 2.83a8.83 8.83 0 0 1 0 12.49l1.42 1.41a8.83 8.83 0 0 0 0-12.48l1.41 1.42a10.83 10.83 0 0 0 0-15.32l-1.41 1.41a6.83 6.83 0 0 1 0 9.65l1.41-1.41Z",
  telegrams: "M3 5h18v14H3V5Zm2 2v10h14V7H5Zm2 2h7v2H7V9Zm0 4h10v2H7v-2Z",
  activity: "M2 12h4l2-7 4 14 3-10 2 3h5v2h-6.2l-.8-1.2L12 22 8 8l-1 3-1 3H2v-2Z",
  packets: "M4 17h3v4H4v-4Zm6-5h3v9h-3v-9Zm6-4h3v13h-3V8ZM3 3h18v2H3V3Z",
  signal: "M4 16h3v5H4v-5Zm5-4h3v9H9v-9Zm5-4h3v13h-3V8Zm5-5h2v18h-2V3Z",
  eye: "M12 5c5.5 0 9.5 5.25 10 7-.5 1.75-4.5 7-10 7S2.5 13.75 2 12c.5-1.75 4.5-7 10-7Zm0 2C8 7 5.2 10.1 4.1 12 5.2 13.9 8 17 12 17s6.8-3.1 7.9-5C18.8 10.1 16 7 12 7Zm0 2a3 3 0 1 1 0 6 3 3 0 0 1 0-6Zm0 2a1 1 0 1 0 0 2 1 1 0 0 0 0-2Z",
  eyeOff: "m3.3 2 18 18-1.4 1.4-3-3A12.7 12.7 0 0 1 12 19c-5.5 0-9.5-5.25-10-7a16.7 16.7 0 0 1 4.1-5.6L1.9 3.3 3.3 2ZM12 5c5.5 0 9.5 5.25 10 7a16.4 16.4 0 0 1-3.2 4.7l-1.5-1.4c1.1-1.1 2-2.4 2.6-3.3C18.8 10.1 16 7 12 7c-.7 0-1.4.1-2 .3L8.4 5.7c1.1-.4 2.3-.7 3.6-.7ZM4.1 12c.6-1 1.4-2.2 2.5-3.2l1.6 1.6a4 4 0 0 0 5.4 5.4l1.4 1.4c-.9.5-1.9.8-3 .8-4 0-6.8-3.1-7.9-5Z",
  refresh: "M17.65 6.35A8 8 0 1 0 19.73 14h-2.08a6 6 0 1 1-1.23-6.42L13 11h8V3l-3.35 3.35Z",
  simulated: "M9 2h6v2h-1v5.2l5.4 9.35A2.3 2.3 0 0 1 17.4 22H6.6a2.3 2.3 0 0 1-2-3.45L10 9.2V4H9V2Zm3 8.45L8.1 16h7.8L12 10.45ZM7 18l-.6 1h11.2l-.6-1H7Z",
};

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined && text !== null) element.textContent = String(text);
  if (className) element.className = className;
  return element;
}

function icon(name, className = "icon") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", className);
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", ICON_PATHS[name] || ICON_PATHS.radio);
  path.setAttribute("fill", "currentColor");
  path.setAttribute("fill-rule", "evenodd");
  svg.append(path);
  return svg;
}

function parsedTime(value) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = new Date(value).valueOf();
  return Number.isFinite(parsed) ? parsed : null;
}

function ageMilliseconds(value, now = Date.now()) {
  const parsed = parsedTime(value);
  return parsed === null ? null : Math.max(0, now - parsed);
}

function time(value) {
  if (!value) return "Never observed";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? String(value) : parsed.toLocaleString();
}

function compactDuration(milliseconds) {
  if (milliseconds < 60000) return `${Math.max(1, Math.floor(milliseconds / 1000))}s`;
  if (milliseconds < 3600000) return `${Math.floor(milliseconds / 60000)}m`;
  if (milliseconds < 86400000) return `${Math.floor(milliseconds / 3600000)}h`;
  return `${Math.floor(milliseconds / 86400000)}d`;
}

function relativeTime(value, now = Date.now()) {
  const age = ageMilliseconds(value, now);
  if (age === null) return "Never observed";
  if (age < 1000) return "Just now";
  return `${compactDuration(age)} ago`;
}

function freshness(value, now = Date.now()) {
  const age = ageMilliseconds(value, now);
  if (age === null) {
    return { text: "Never seen", tone: "critical", active: false, silent: true };
  }
  const active = age < ACTIVE_WINDOW_MS;
  const tone = age >= SILENCE_CRITICAL_MS
    ? "critical"
    : age >= SILENCE_WARNING_MS
      ? "warning"
      : "fresh";
  const text = age < 60000
    ? "Just now"
    : age >= SILENCE_WARNING_MS
      ? `Silent for ${compactDuration(age)}`
      : relativeTime(value, now);
  return { text, tone, active, silent: !active };
}

function deviceAccent(device, now = Date.now()) {
  if (device.available === false) return "critical";
  const tone = freshness(device.last_seen, now).tone;
  return tone === "critical" || tone === "warning" ? tone : "neutral";
}

function rssi(value) {
  return typeof value === "number" && Number.isFinite(value)
    ? `${String(value).replace("-", "−")} dBm`
    : "—";
}

// Four-bar radio scale: >= -60 excellent, -61..-72 good, -73..-85 fair,
// <= -86 weak. These are presentation thresholds, not link-quality guarantees.
function signalLevel(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return 0;
  if (value >= -60) return 4;
  if (value >= -72) return 3;
  if (value >= -85) return 2;
  return 1;
}

function signalTone(value) {
  const level = signalLevel(value);
  if (level >= 3) return "good";
  if (level === 2) return "warning";
  if (level === 1) return "critical";
  return "unknown";
}

function signalLabel(value) {
  return ["No measurement", "Weak", "Fair", "Good", "Excellent"][signalLevel(value)];
}

function repeaterLabel(value) {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return null;
  return `via ${value} ${value === 1 ? "repeater" : "repeaters"}`;
}

function telegramHopLabel(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "hops —";
  if (value === 0) return "direct";
  return `${value} ${value === 1 ? "hop" : "hops"}`;
}

function averageRssi(rows) {
  const values = rows
    .map((row) => row.last_rssi_dbm)
    .filter((value) => typeof value === "number" && Number.isFinite(value));
  if (!values.length) return null;
  return Math.round(values.reduce((total, value) => total + value, 0) / values.length);
}

function rorgName(value) {
  const rorg = String(value || "").toUpperCase();
  return { "0XF6": "RPS", "0XD5": "1BS", "0XA5": "4BS", "0XD2": "VLD" }[rorg] || "Other";
}

function primaryPlatform(platforms) {
  const values = Array.isArray(platforms) ? platforms : [];
  for (const platform of ["cover", "climate", "light", "switch", "binary_sensor", "sensor"]) {
    if (values.includes(platform)) return platform;
  }
  return "other";
}

function deviceIcon(platforms, eep) {
  const platform = primaryPlatform(platforms);
  const declared = typeof eep === "string" ? eep.toUpperCase() : "";
  if (["A5-02-", "A5-04-", "A5-10-"].some((prefix) => declared.startsWith(prefix))) {
    return "thermometer";
  }
  if (["D5-00-", "F6-10-", "A5-14-"].some((prefix) => declared.startsWith(prefix))) {
    return "contact";
  }
  if (declared.startsWith("F6-02-")) return "rocker";
  if (declared.startsWith("D2-05-")) return "cover";
  if (declared.startsWith("D2-01-")) return platform === "light" ? "light" : "socket";
  if (platform === "climate") return "climate";
  if (platform === "light") return "light";
  if (platform === "switch") return "socket";
  return "radio";
}

function filterEntries(entries, query, filter, now = Date.now()) {
  const needle = String(query || "").trim().toLocaleLowerCase();
  return entries.filter((entry) => {
    if (filter === "unconfigured" && entry.configured) return false;
    if (filter === "active" && !freshness(entry.last_seen, now).active) return false;
    if (filter === "silent" && !freshness(entry.last_seen, now).silent) return false;
    if (!needle) return true;
    const haystack = [
      entry.name,
      entry.sender,
      entry.eep,
      entry.state,
      ...(entry.platforms || []),
      ...(entry.rorgs || []),
    ].filter((value) => value !== null && value !== undefined).join(" ").toLocaleLowerCase();
    return haystack.includes(needle);
  });
}

function stateLabel(device) {
  if (!device.available) return "Unavailable";
  if (!device.state) return "Unknown";
  const label = String(device.state).replaceAll("_", " ");
  return label.charAt(0).toUpperCase() + label.slice(1);
}

function stateTone(device) {
  if (!device.available || device.state === "unavailable") return "critical";
  if (!device.state || device.state === "unknown") return "unknown";
  if (["on", "open", "heat", "pressed", "alarm"].includes(device.state)) return "good";
  return "neutral";
}

function stateDisplay(device, now = Date.now()) {
  const label = stateLabel(device);
  const hasState = Boolean(device.available && device.state && !["unknown", "unavailable"].includes(device.state));
  const unit = hasState && typeof device.unit === "string" && device.unit ? ` ${device.unit}` : "";
  const age = ageMilliseconds(device.last_seen, now);
  const stale = Boolean(hasState && age !== null && age >= SILENCE_CRITICAL_MS);
  return {
    text: `${label}${unit}${stale ? " · last known" : ""}`,
    tone: stale ? "neutral" : stateTone(device),
    stale,
  };
}

function shortTime(value) {
  const parsed = parsedTime(value);
  if (parsed === null) return "--:--:--";
  return new Date(parsed).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function telegramData(record) {
  if (!Array.isArray(record.data)) return "Data redacted";
  if (!record.data.length) return "No payload";
  return record.data
    .map((value) => Number(value).toString(16).padStart(2, "0").toUpperCase())
    .join(" ");
}

function recordActivitySample(samples, count, at) {
  const current = Array.isArray(samples) ? samples : [];
  if (typeof count !== "number" || !Number.isFinite(count) || count < 0 || !Number.isFinite(at)) {
    return [...current];
  }
  return [...current, { count, at }]
    .sort((left, right) => left.at - right.at)
    .slice(-ACTIVITY_SAMPLE_LIMIT);
}

function activityBuckets(samples, now = Date.now(), minutes = ACTIVITY_MINUTES) {
  const valid = (Array.isArray(samples) ? samples : [])
    .filter((sample) => sample && Number.isFinite(sample.at) && Number.isFinite(sample.count) && sample.count >= 0)
    .sort((left, right) => left.at - right.at);
  const values = Array(minutes).fill(0);
  const start = now - minutes * 60000;
  for (let index = 1; index < valid.length; index += 1) {
    const previous = valid[index - 1];
    const current = valid[index];
    const elapsed = current.at - previous.at;
    const delta = current.count - previous.count;
    if (elapsed <= 0 || delta < 0 || current.at <= start || previous.at >= now) continue;
    const intervalStart = Math.max(previous.at, start);
    const intervalEnd = Math.min(current.at, now);
    for (let bucket = 0; bucket < minutes; bucket += 1) {
      const bucketStart = start + bucket * 60000;
      const bucketEnd = bucketStart + 60000;
      const overlap = Math.max(0, Math.min(intervalEnd, bucketEnd) - Math.max(intervalStart, bucketStart));
      if (overlap > 0) values[bucket] += delta * overlap / elapsed;
    }
  }
  return {
    ready: valid.length >= 3,
    values: values.map((value) => Math.round(value * 10) / 10),
  };
}

function updateRelativeElement(element) {
  const value = element.dataset.relativeTime || null;
  if (element.dataset.relativeKind === "seen") {
    const status = freshness(value);
    element.textContent = status.text;
    element.classList.remove("tone-fresh", "tone-warning", "tone-critical");
    element.classList.add(`tone-${status.tone}`);
    element.title = value ? time(value) : "No telegram observed";
  } else {
    element.textContent = value ? `Last packet ${relativeTime(value)}` : "No packet observed";
    element.title = value ? time(value) : "No radio packet observed";
  }
}

class EnOceanCustomPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = "devices";
    this._snapshot = null;
    this._telegrams = [];
    this._telegramKeys = new Set();
    this._anonymize = true;
    this._streamActive = false;
    this._streamStarting = false;
    this._streamUnsubscribe = null;
    this._loading = false;
    this._error = "";
    this._query = "";
    this._filter = "all";
    this._activitySamples = [];
  }

  set hass(hass) {
    this._hass = hass;
  }

  connectedCallback() {
    this._rootListener = (event) => this._onClick(event);
    this._rootInputListener = (event) => this._onInput(event);
    this._rootKeydownListener = (event) => this._onKeydown(event);
    this.shadowRoot.addEventListener("click", this._rootListener);
    this.shadowRoot.addEventListener("input", this._rootInputListener);
    this.shadowRoot.addEventListener("keydown", this._rootKeydownListener);
    this._refresh();
    this._refreshTimer = window.setInterval(() => this._refresh(), 30000);
    this._clockTimer = window.setInterval(() => this._updateRelativeTimes(), CLOCK_TICK_MS);
  }

  disconnectedCallback() {
    window.clearInterval(this._refreshTimer);
    window.clearInterval(this._clockTimer);
    this.shadowRoot.removeEventListener("click", this._rootListener);
    this.shadowRoot.removeEventListener("input", this._rootInputListener);
    this.shadowRoot.removeEventListener("keydown", this._rootKeydownListener);
    this._stopStream();
    this._telegrams = [];
    this._telegramKeys.clear();
    this._activitySamples = [];
  }

  async _refresh() {
    if (!this._hass || this._loading) return;
    this._loading = true;
    this._render();
    try {
      this._snapshot = await this._hass.callWS({
        type: "enocean_custom/panel",
        anonymize: this._anonymize,
      });
      this._activitySamples = recordActivitySample(
        this._activitySamples,
        this._snapshot.gateway.received_radio_packets,
        Date.now()
      );
      this._error = "";
    } catch (error) {
      this._error = error.message || "Unable to read the EnOcean gateway.";
    } finally {
      this._loading = false;
      this._render();
    }
  }

  async _startStream() {
    if (this._streamActive || this._streamStarting || !this._hass) return;
    this._streamStarting = true;
    this._render();
    try {
      this._streamUnsubscribe = await this._hass.subscribeMessage(
        (event) => {
          this._telegrams.push(event);
          if (this._telegrams.length > MAX_TELEGRAMS) {
            this._telegrams.splice(0, this._telegrams.length - MAX_TELEGRAMS);
          }
          this._render();
        },
        {
          type: "enocean_custom/telegram_stream/subscribe",
          anonymize: this._anonymize,
        },
        { resubscribe: true }
      );
      this._streamActive = true;
      this._error = "";
    } catch (error) {
      this._error = error.message || "Unable to start the telegram stream.";
      this._streamActive = false;
    } finally {
      this._streamStarting = false;
      this._render();
    }
  }

  _stopStream() {
    this._streamActive = false;
    this._streamStarting = false;
    if (this._streamUnsubscribe) {
      const unsubscribe = this._streamUnsubscribe;
      this._streamUnsubscribe = null;
      Promise.resolve(unsubscribe()).catch(() => {});
    }
  }

  async _openCommissioning() {
    try {
      await this._hass.callService("enocean_custom", "learn", {});
      window.history.pushState(null, "", "/config/integrations/integration/enocean_custom");
      window.dispatchEvent(new Event("location-changed"));
    } catch (error) {
      this._error = error.message || "Unable to open commissioning.";
      this._render();
    }
  }

  _onInput(event) {
    if (!event.target.matches("[data-search]")) return;
    this._query = event.target.value;
    this._render();
  }

  _onKeydown(event) {
    const tab = event.target.closest("[role='tab']");
    if (!tab || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const current = TABS.indexOf(tab.dataset.tab);
    const next = event.key === "Home"
      ? 0
      : event.key === "End"
        ? TABS.length - 1
        : (current + (event.key === "ArrowRight" ? 1 : -1) + TABS.length) % TABS.length;
    this._tab = TABS[next];
    this._render();
    this.shadowRoot.querySelector(`[data-tab='${this._tab}']`)?.focus();
  }

  async _onClick(event) {
    const button = event.target.closest("button");
    if (!button) return;
    const action = button.dataset.action;
    if (button.dataset.tab) {
      this._tab = button.dataset.tab;
      this._render();
    } else if (button.dataset.filter) {
      this._filter = button.dataset.filter;
      this._render();
    } else if (action === "refresh") {
      this._refresh();
    } else if (action === "privacy") {
      this._anonymize = !this._anonymize;
      if (this._streamActive) {
        this._stopStream();
        this._telegrams = [];
        this._telegramKeys.clear();
        await this._startStream();
      }
      await this._refresh();
    } else if (action === "stream") {
      if (this._streamActive) this._stopStream();
      else await this._startStream();
      this._render();
    } else if (action === "clear") {
      this._telegrams = [];
      this._telegramKeys.clear();
      this._render();
    } else if (action === "adopt") {
      await this._openCommissioning();
    } else if (action === "more-info") {
      this.dispatchEvent(new CustomEvent("hass-more-info", {
        bubbles: true,
        composed: true,
        detail: { entityId: button.dataset.entityId },
      }));
    }
  }

  _control(label, action, className = "", focusKey = action) {
    const control = node("button", label, className);
    control.type = "button";
    control.dataset.action = action;
    control.dataset.focusKey = focusKey;
    return control;
  }

  _relative(value, kind, className) {
    const element = node("span", undefined, className);
    element.dataset.relativeTime = value || "";
    element.dataset.relativeKind = kind;
    updateRelativeElement(element);
    return element;
  }

  _updateRelativeTimes() {
    this.shadowRoot.querySelectorAll("[data-relative-time]").forEach(updateRelativeElement);
  }

  _identifier(value, className = "sender") {
    if (!value || value === MASKED) return null;
    return node("code", value, className);
  }

  _signal(value, compact = false) {
    const level = signalLevel(value);
    const tone = signalTone(value);
    const gauge = node("span", undefined, `signal tone-${tone}${compact ? " compact" : ""}`);
    const description = level
      ? `${signalLabel(value)} signal, ${rssi(value)}`
      : "No signal measurement";
    gauge.title = description;
    gauge.setAttribute("role", "img");
    gauge.setAttribute("aria-label", description);
    const bars = node("span", undefined, "signal-bars");
    bars.setAttribute("aria-hidden", "true");
    for (let index = 1; index <= 4; index += 1) {
      bars.append(node("span", undefined, `signal-bar bar-${index}${index <= level ? " active" : ""}`));
    }
    const label = node("span", rssi(value), "signal-value");
    if (level) label.dataset.value = String(value);
    gauge.append(bars, label);
    return gauge;
  }

  _badge(text, tone = "neutral", className = "") {
    return node("span", text, `badge tone-${tone}${className ? ` ${className}` : ""}`);
  }

  _empty(title, description) {
    const empty = node("section", undefined, "empty-state");
    empty.append(icon("radio", "empty-icon"), node("h3", title), node("p", description));
    return empty;
  }

  _styles() {
    const style = document.createElement("style");
    style.textContent = `
      :host {
        display: block;
        min-height: 100%;
        max-width: 100%;
        background: var(--primary-background-color, #fafafa);
        color: var(--primary-text-color, #1c1b1f);
        font-family: var(--paper-font-body1_-_font-family, Roboto, Arial, sans-serif);
      }
      * { box-sizing: border-box; }
      .wrap { width: 100%; max-width: 1280px; min-width: 0; margin: 0 auto; padding: 20px 24px 36px; }
      .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }
      .panel-header { display: flex; align-items: center; justify-content: space-between; gap: 14px; flex-wrap: wrap; margin-bottom: 14px; }
      .title-block { min-width: 0; }
      h1 { margin: 0; font-size: 29px; line-height: 1.15; letter-spacing: -0.6px; font-weight: 700; }
      h2, h3, p { margin-top: 0; }
      h2 { font-size: 19px; line-height: 1.3; }
      h3 { font-size: 16px; line-height: 1.35; }
      .subtitle { margin: 5px 0 0; color: var(--secondary-text-color); font-size: 14px; }
      .muted { color: var(--secondary-text-color); }
      .small { font-size: 12px; }
      .header-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; flex-wrap: wrap; min-width: 0; }
      button, input { font: inherit; }
      button {
        min-width: 44px;
        min-height: 44px;
        border: 1px solid var(--divider-color);
        border-radius: 11px;
        padding: 9px 13px;
        background: var(--card-background-color);
        color: var(--primary-text-color);
        cursor: pointer;
        font-weight: 550;
        transition: background-color 150ms ease, border-color 150ms ease, box-shadow 150ms ease, transform 150ms ease;
      }
      button:hover { border-color: var(--primary-color); box-shadow: 0 3px 10px rgba(0,0,0,.08); }
      button:active { transform: translateY(1px); }
      button:disabled { cursor: default; opacity: .55; box-shadow: none; transform: none; }
      button.primary { border-color: var(--primary-color); background: var(--primary-color); color: var(--text-primary-color); }
      button.primary-outline { border-color: var(--primary-color); background: transparent; color: var(--primary-color); box-shadow: inset 0 0 0 1px var(--primary-color); }
      button:focus-visible, input:focus-visible { outline: 3px solid var(--primary-color); outline-offset: 3px; }
      .icon { width: 24px; height: 24px; display: block; flex: 0 0 auto; }
      .icon-button { display: inline-grid; place-items: center; width: 44px; padding: 9px; }
      .icon-button .icon { width: 21px; height: 21px; }
      .privacy-toggle { display: inline-flex; align-items: center; justify-content: center; gap: 7px; }
      .privacy-toggle .icon { width: 19px; height: 19px; }
      .privacy-state { padding-left: 7px; border-left: 1px solid currentColor; color: var(--secondary-text-color); font-size: 10px; font-weight: 500; text-transform: uppercase; }
      .tabs { display: inline-flex; max-width: 100%; gap: 4px; margin-bottom: 16px; padding: 4px; border: 1px solid var(--divider-color); border-radius: 14px; background: var(--secondary-background-color); }
      .tabs button { display: inline-flex; align-items: center; justify-content: center; gap: 7px; min-width: 100px; border: 0; background: transparent; box-shadow: none; color: var(--secondary-text-color); }
      .tabs button .icon { width: 19px; height: 19px; }
      .tabs button[aria-selected="true"] { background: var(--card-background-color); color: var(--primary-color); box-shadow: 0 2px 8px rgba(0,0,0,.12); }
      .tab-panel { min-width: 0; }
      .card { min-width: 0; background: var(--card-background-color); border: 1px solid var(--divider-color); border-radius: 18px; box-shadow: 0 5px 18px rgba(0,0,0,.055); }
      .health-card { padding: 18px 20px; margin-bottom: 16px; background: linear-gradient(135deg, var(--card-background-color), var(--secondary-background-color)); }
      .health-top { display: flex; align-items: center; gap: 12px; min-width: 0; }
      .gateway-mark { display: grid; place-items: center; width: 46px; height: 46px; flex: 0 0 auto; border-radius: 14px; background: var(--card-background-color); color: var(--primary-color); box-shadow: 0 3px 12px rgba(0,0,0,.08); }
      .gateway-mark .icon { width: 27px; height: 27px; }
      .health-heading { flex: 1; min-width: 0; }
      .health-title-line { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
      .health-title-line h2 { margin: 0; }
      .health-heading p { margin: 4px 0 0; color: var(--secondary-text-color); font-size: 13px; }
      .status-dot { width: 12px; height: 12px; flex: 0 0 auto; border-radius: 50%; background: var(--error-color); box-shadow: 0 0 0 4px rgba(244,67,54,.12); }
      .status-dot.healthy { background: var(--success-color, #137333); animation: gateway-pulse 2.2s ease-out infinite; }
      .health-state { display: inline-flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 650; color: var(--error-color); }
      .health-state.healthy { color: var(--success-color, #137333); }
      .last-packet { flex: 0 0 auto; color: var(--secondary-text-color); font-size: 13px; text-align: right; }
      .health-metrics { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 9px; margin-top: 14px; }
      .metric { position: relative; min-width: 0; padding: 10px 12px; border: 1px solid var(--divider-color); border-radius: 13px; background: var(--card-background-color); }
      .metric-header { display: flex; align-items: center; justify-content: space-between; height: 20px; color: var(--secondary-text-color); }
      .metric-header .icon { width: 18px; height: 18px; }
      .metric-value { display: block; min-width: 0; margin-top: 3px; font-size: 20px; line-height: 1.2; font-weight: 700; letter-spacing: -.3px; overflow-wrap: anywhere; }
      .metric-label { display: block; margin-top: 3px; color: var(--secondary-text-color); font-size: 11px; line-height: 1.3; font-weight: 600; }
      .metric-detail { display: block; margin-top: 2px; color: var(--secondary-text-color); font-size: 11px; }
      .metric.tone-good .metric-value, .metric.tone-good .metric-header { color: var(--success-color, #137333); }
      .metric.tone-warning .metric-value, .metric.tone-warning .metric-header { color: var(--warning-color, #8a4300); }
      .metric.tone-critical .metric-value, .metric.tone-critical .metric-header { color: var(--error-color, #b3261e); }
      .activity-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--disabled-color, #737d8c); }
      .activity-dot.active { background: var(--success-color, #137333); box-shadow: 0 0 0 3px rgba(19,115,51,.13); }
      .activity-chart-block { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--divider-color); }
      .activity-heading { display: flex; align-items: center; gap: 7px; margin-bottom: 5px; color: var(--secondary-text-color); font-size: 12px; }
      .activity-heading .icon { width: 16px; height: 16px; }
      .activity-status { margin-left: auto; font-size: 12px; }
      .activity-chart { display: block; width: 100%; height: 54px; overflow: visible; }
      .activity-bar { fill: var(--primary-color); opacity: .78; }
      .activity-bar.empty { fill: var(--panel-signal-empty, #737d8c); opacity: .32; }
      .activity-baseline { stroke: var(--divider-color); stroke-width: 1; }
      .error { margin: 0 0 16px; padding: 12px 14px; border: 1px solid var(--error-color); border-radius: 12px; color: var(--error-color); background: var(--card-background-color); }
      .device-tools { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 11px; margin-bottom: 3px; }
      .search-box { position: relative; flex: 1 1 240px; min-width: 0; }
      .search-box .icon { position: absolute; left: 13px; top: 12px; width: 21px; height: 21px; color: var(--secondary-text-color); pointer-events: none; }
      .search-box input { width: 100%; min-height: 46px; padding: 10px 12px 10px 43px; border: 1px solid var(--divider-color); border-radius: 12px; background: var(--primary-background-color); color: var(--primary-text-color); }
      .search-box input::placeholder { color: var(--secondary-text-color); opacity: 1; }
      .filters { display: flex; max-width: 100%; gap: 6px; flex-wrap: nowrap; overflow-x: auto; overscroll-behavior-inline: contain; scrollbar-width: thin; }
      .filters button { flex: 0 0 auto; min-height: 44px; padding: 8px 12px; font-size: 13px; }
      .filters button[aria-pressed="true"] { border-color: var(--primary-color); color: var(--primary-color); box-shadow: inset 0 0 0 1px var(--primary-color); }
      .results-count { flex-basis: 100%; margin: -2px 2px 0; color: var(--secondary-text-color); font-size: 12px; }
      .platform-group { min-width: 0; margin-top: 16px; }
      .group-heading { display: flex; align-items: center; gap: 9px; margin: 0 2px 10px; }
      .group-heading h2 { margin: 0; font-size: 17px; }
      .group-count { display: inline-grid; place-items: center; min-width: 25px; height: 25px; padding: 0 7px; border-radius: 999px; background: var(--secondary-background-color); color: var(--primary-text-color); font-size: 12px; font-weight: 700; }
      .device-grid, .nearby-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 13px; min-width: 0; }
      .device-card, .nearby-card { display: flex; flex-direction: column; padding: 14px; transition: border-color 160ms ease, box-shadow 160ms ease, transform 160ms ease; }
      .device-card:hover, .nearby-card:hover { border-color: var(--primary-color); box-shadow: 0 8px 24px rgba(0,0,0,.09); transform: translateY(-1px); }
      .device-card.accent-warning { border-color: var(--warning-color, #8a4300); }
      .device-card.accent-critical { border-color: var(--error-color, #b3261e); box-shadow: inset 0 0 0 1px var(--error-color, #b3261e), 0 5px 18px rgba(0,0,0,.055); }
      .device-heading { display: flex; flex-wrap: wrap; align-items: flex-start; gap: 8px 10px; min-width: 0; }
      .device-icon { display: grid; place-items: center; width: 40px; height: 40px; flex: 0 0 auto; border-radius: 12px; background: var(--secondary-background-color); color: var(--primary-color); }
      .device-title { flex: 1 1 130px; min-width: 0; }
      .device-title h3 { margin: 1px 0 3px; overflow-wrap: anywhere; }
      .sender { display: block; color: var(--secondary-text-color); font-family: var(--code-font-family, monospace); font-size: 12px; overflow-wrap: anywhere; }
      .badge { display: inline-flex; align-items: center; justify-content: center; min-height: 24px; max-width: 100%; padding: 3px 9px; border: 1px solid var(--divider-color); border-radius: 999px; background: var(--card-background-color); color: var(--primary-text-color); font-size: 11px; line-height: 1.25; font-weight: 650; white-space: normal; text-align: center; }
      .device-heading > .badge { flex: 0 1 auto; margin-top: 2px; }
      .badge.tone-good { border-color: var(--success-color, #137333); color: var(--success-color, #137333); }
      .badge.tone-fresh { color: var(--secondary-text-color); }
      .badge.tone-warning { border-color: var(--warning-color, #8a4300); color: var(--warning-color, #8a4300); }
      .badge.tone-critical { border-color: var(--error-color, #b3261e); color: var(--error-color, #b3261e); }
      .state-value.is-stale { border-color: var(--divider-color); background: var(--secondary-background-color); color: var(--secondary-text-color); font-weight: 550; }
      .device-tags { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin: 11px 0 10px; }
      .eep, .hop-badge, .rorg-badge { font-family: var(--code-font-family, monospace); font-size: 10px; }
      .freshness { border-width: 1px; gap: 6px; }
      .freshness::before { content: ""; width: 7px; height: 7px; flex: 0 0 auto; border-radius: 50%; background: currentColor; }
      .device-meta { display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; margin-top: auto; margin-bottom: 9px; color: var(--secondary-text-color); font-size: 12px; }
      .device-footer { display: flex; align-items: center; justify-content: space-between; gap: 10px; min-width: 0; padding-top: 8px; border-top: 1px solid var(--divider-color); }
      button.details-button { flex: 0 0 auto; min-height: 44px; padding: 8px 4px; border-color: transparent; background: transparent; color: var(--primary-color); box-shadow: none; font-size: 12px; font-weight: 550; text-decoration: underline; text-underline-offset: 3px; }
      button.details-button:hover { border-color: transparent; background: var(--secondary-background-color); box-shadow: none; }
      .signal { display: inline-flex; align-items: flex-end; gap: 9px; min-width: 0; }
      .signal-bars { display: inline-flex; align-items: flex-end; gap: 3px; height: 22px; flex: 0 0 auto; color: var(--panel-signal-empty, #737d8c); }
      .signal-bar { display: block; width: 6px; border-radius: 2px 2px 1px 1px; background: currentColor; opacity: 1; }
      .signal-bar.bar-1 { height: 6px; } .signal-bar.bar-2 { height: 11px; }
      .signal-bar.bar-3 { height: 16px; } .signal-bar.bar-4 { height: 22px; }
      .signal.tone-good .signal-bar.active { color: var(--success-color, #137333); }
      .signal.tone-warning .signal-bar.active { color: var(--warning-color, #8a4300); }
      .signal.tone-critical .signal-bar.active { color: var(--error-color, #b3261e); }
      .signal-value { color: var(--secondary-text-color); font-size: 14px; line-height: 1.4; font-weight: 650; white-space: nowrap; }
      .signal.tone-good .signal-value { color: var(--success-color, #137333); }
      .signal.tone-warning .signal-value { color: var(--warning-color, #8a4300); }
      .signal.tone-critical .signal-value { color: var(--error-color, #b3261e); }
      .empty-state { display: grid; justify-items: center; padding: 38px 22px; text-align: center; }
      .empty-icon { width: 58px; height: 58px; margin-bottom: 14px; color: var(--secondary-text-color); opacity: .65; }
      .empty-state h3 { margin-bottom: 7px; }
      .empty-state p { max-width: 480px; margin: 0; color: var(--secondary-text-color); }
      .passive-note { display: flex; align-items: flex-start; gap: 12px; padding: 14px 16px; margin-bottom: 15px; }
      .passive-note .icon { color: var(--primary-color); }
      .passive-note h2 { margin: 0 0 4px; font-size: 16px; }
      .passive-note p { margin: 0; color: var(--secondary-text-color); font-size: 13px; }
      .nearby-card h3 { margin: 0 0 4px; }
      .nearby-card .device-tags { margin-bottom: 12px; }
      .nearby-card .adopt-button { width: 100%; margin-top: 11px; }
      .nearby-signal { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding-top: 10px; border-top: 1px solid var(--divider-color); }
      .signal-quality { color: var(--primary-text-color); font-size: 12px; font-weight: 650; }
      .telegram-shell { overflow: hidden; }
      .telegram-controls { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 16px 18px; }
      .telegram-heading { min-width: 0; }
      .telegram-title { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
      .telegram-title h2 { margin: 0; }
      .telegram-heading p { margin: 5px 0 0; color: var(--secondary-text-color); font-size: 12px; }
      .stream-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--disabled-color, #737d8c); }
      .stream-dot.active { background: var(--success-color, #137333); box-shadow: 0 0 0 4px rgba(19,115,51,.13); }
      .telegram-actions { display: flex; align-items: center; gap: 8px; flex: 0 0 auto; }
      .line-count { padding: 6px 9px; border-radius: 8px; background: var(--secondary-background-color); color: var(--primary-text-color); font-family: var(--code-font-family, monospace); font-size: 12px; white-space: nowrap; }
      .telegram-list { border-top: 1px solid var(--divider-color); background: var(--secondary-background-color); }
      .telegram-row { display: grid; grid-template-columns: 64px 50px minmax(0,1fr) 78px; align-items: center; gap: 11px; min-width: 0; height: 80px; padding: 10px 16px; border-bottom: 1px solid var(--divider-color); }
      .telegram-row:last-child { border-bottom: 0; }
      .telegram-row.is-new { animation: telegram-in 240ms ease-out; }
      .telegram-time { color: var(--secondary-text-color); font-family: var(--code-font-family, monospace); font-size: 12px; }
      .rorg-chip { display: inline-flex; align-items: center; justify-content: center; width: 50px; height: 24px; padding: 2px 6px; border-radius: 7px; font-family: var(--code-font-family, monospace); font-size: 11px; line-height: 16px; font-weight: 800; background: var(--card-background-color); }
      .rorg-rps { color: var(--panel-rorg-rps, #175cd3); border: 1px solid var(--panel-rorg-rps, #175cd3); }
      .rorg-1bs { color: var(--panel-rorg-1bs, #6d28d9); border: 1px solid var(--panel-rorg-1bs, #6d28d9); }
      .rorg-4bs { color: var(--panel-rorg-4bs, #00758a); border: 1px solid var(--panel-rorg-4bs, #00758a); }
      .rorg-vld { color: var(--panel-rorg-vld, #4b5565); border: 1px solid var(--panel-rorg-vld, #4b5565); }
      .rorg-other { color: var(--secondary-text-color); border: 1px solid var(--divider-color); }
      .telegram-main { min-width: 0; overflow: hidden; }
      .telegram-summary { display: flex; align-items: center; gap: 7px; flex-wrap: nowrap; min-width: 0; height: 24px; font-size: 13px; line-height: 20px; }
      .telegram-summary strong { min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
      .simulated { flex: 0 0 auto; gap: 4px; height: 22px; min-height: 22px; padding: 2px 7px; border: 1px dashed var(--panel-simulated, #a60883); color: var(--panel-simulated, #a60883); background: var(--card-background-color); border-radius: 6px; font-size: 10px; line-height: 16px; font-weight: 850; letter-spacing: .35px; white-space: nowrap; }
      .simulated-icon { width: 13px; height: 13px; }
      .telegram-meta { height: 18px; margin-top: 4px; color: var(--secondary-text-color); font-family: var(--code-font-family, monospace); font-size: 12px; line-height: 18px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
      .telegram-rssi { justify-self: end; font-family: var(--code-font-family, monospace); font-size: 13px; font-weight: 700; white-space: nowrap; }
      .telegram-rssi.tone-good { color: var(--success-color, #137333); }
      .telegram-rssi.tone-warning { color: var(--warning-color, #8a4300); }
      .telegram-rssi.tone-critical { color: var(--error-color, #b3261e); }
      .telegram-rssi.tone-unknown { color: var(--secondary-text-color); }
      .telegram-empty { border-top: 1px solid var(--divider-color); }
      .gateway-grid { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 16px; }
      .gateway-card { padding: 20px; }
      .gateway-card h2 { margin-bottom: 15px; }
      .facts { display: grid; grid-template-columns: minmax(105px,.8fr) minmax(0,1.2fr); gap: 10px 16px; margin: 0; font-size: 13px; }
      dt { color: var(--secondary-text-color); }
      dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
      .gateway-note { margin: 18px 0 0; padding-top: 14px; border-top: 1px solid var(--divider-color); color: var(--secondary-text-color); font-size: 12px; }
      .ok { color: var(--success-color, #137333); } .bad { color: var(--error-color, #b3261e); }
      code { font-family: var(--code-font-family, monospace); }
      @keyframes gateway-pulse {
        0% { box-shadow: 0 0 0 0 rgba(19,115,51,.38); }
        65%, 100% { box-shadow: 0 0 0 9px rgba(19,115,51,0); }
      }
      @keyframes telegram-in {
        from { opacity: 0; transform: translateY(-5px); }
        to { opacity: 1; transform: translateY(0); }
      }
      @media (min-width: 901px) {
        .wrap { padding-top: 16px; }
        .panel-header { margin-bottom: 10px; }
        .tabs { margin-bottom: 12px; }
        .health-card { display: grid; grid-template-columns: minmax(280px,.85fr) minmax(0,1.65fr); gap: 10px 18px; padding: 14px 16px; margin-bottom: 12px; }
        .health-top { grid-column: 1; grid-row: 1; }
        .health-metrics { grid-column: 2; grid-row: 1 / span 2; margin-top: 0; }
        .activity-chart-block { grid-column: 1; grid-row: 2; margin-top: 0; padding-top: 7px; }
        .metric { padding: 9px 10px; }
        .metric-value { font-size: 19px; }
        .device-tools { flex-wrap: nowrap; padding: 10px; margin-bottom: 0; }
        .results-count { flex: 0 0 auto; margin: 0 2px; white-space: nowrap; }
        .platform-group { margin-top: 12px; }
        .device-card, .nearby-card { padding: 12px; }
        .device-tags { margin: 8px 0; }
        .device-meta { margin-bottom: 6px; }
        .device-footer { padding-top: 6px; }
      }
      @media (max-width: 800px) {
        .health-top { flex-wrap: wrap; }
        .gateway-grid { grid-template-columns: minmax(0,1fr); }
        .last-packet { flex-basis: 100%; text-align: left; padding-left: 58px; margin-top: -8px; }
      }
      @media (max-width: 600px) {
        .wrap { padding: 10px 10px calc(74px + env(safe-area-inset-bottom, 0px)); }
        .panel-header { display: grid; grid-template-columns: minmax(0,1fr); gap: 8px; margin-bottom: 8px; }
        h1 { font-size: 23px; }
        .subtitle { display: none; }
        .header-actions { display: grid; grid-template-columns: minmax(0,1fr) auto 44px; width: 100%; gap: 6px; }
        .header-actions button { min-width: 0; padding-inline: 8px; font-size: 12px; white-space: nowrap; }
        .header-actions .icon-button { min-width: 44px; width: 44px; padding: 9px; }
        .privacy-toggle { gap: 5px; }
        .privacy-toggle .icon { width: 17px; height: 17px; }
        .privacy-state { padding-left: 5px; font-size: 9px; }
        .tabs { position: fixed; z-index: 20; left: 0; right: 0; bottom: 0; display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); width: 100%; max-width: none; gap: 2px; margin: 0; padding: 5px 6px calc(5px + env(safe-area-inset-bottom, 0px)); border: 0; border-top: 1px solid var(--divider-color); border-radius: 0; background: var(--card-background-color); box-shadow: 0 -5px 18px rgba(0,0,0,.1); }
        .tabs button { flex-direction: column; gap: 2px; min-width: 0; min-height: 50px; padding: 4px 2px; font-size: 11px; line-height: 14px; }
        .tabs button .icon { width: 19px; height: 19px; }
        .health-card { padding: 8px; margin-bottom: 8px; border-radius: 15px; }
        .health-top { display: grid; grid-template-columns: 36px minmax(0,1fr); gap: 5px 8px; }
        .gateway-mark { grid-row: 1 / span 2; width: 36px; height: 36px; border-radius: 11px; }
        .gateway-mark .icon { width: 22px; height: 22px; }
        .health-title-line { gap: 7px; flex-wrap: nowrap; }
        .health-title-line h2 { font-size: 16px; }
        .health-heading > p { display: none; }
        .health-state { gap: 6px; font-size: 12px; }
        .status-dot { width: 9px; height: 9px; }
        .last-packet { grid-column: 2; padding: 0; margin: 0; font-size: 11px; line-height: 14px; }
        .health-metrics { grid-template-columns: repeat(4, minmax(0,1fr)); gap: 4px; margin-top: 6px; }
        .metric { height: 63px; padding: 5px 6px; border-radius: 10px; }
        .metric-header { height: 15px; }
        .metric-header .icon { width: 15px; height: 15px; }
        .metric-value { margin-top: 2px; font-size: 14px; line-height: 17px; letter-spacing: -.2px; }
        .metric-label { margin-top: 1px; font-size: 9px; line-height: 11px; letter-spacing: 0; }
        .metric-detail { display: none; }
        .activity-dot { width: 7px; height: 7px; }
        .activity-chart-block { margin-top: 5px; padding-top: 5px; }
        .activity-heading { gap: 5px; margin-bottom: 3px; font-size: 11px; }
        .activity-heading .icon { width: 14px; height: 14px; }
        .activity-status { font-size: 11px; }
        .activity-chart { height: 27px; }
        .device-tools { padding: 8px; gap: 4px; margin-bottom: 0; }
        .search-box { flex-basis: 100%; }
        .search-box input { min-height: 44px; padding-block: 9px; }
        .search-box .icon { top: 11px; }
        .filters { width: 100%; flex-wrap: nowrap; overflow-x: auto; padding-bottom: 1px; }
        .filters button { width: auto; padding-inline: 11px; }
        .results-count { display: none; }
        .platform-group { margin-top: 6px; }
        .group-heading { margin-bottom: 7px; }
        .group-heading h2 { font-size: 15px; }
        .group-count { height: 22px; min-width: 22px; }
        .device-grid, .nearby-grid { gap: 10px; }
        .device-card, .nearby-card { padding: 10px; border-radius: 15px; }
        .device-card:hover, .nearby-card:hover { transform: none; }
        .device-heading { gap: 8px; }
        .device-icon { width: 36px; height: 36px; }
        .device-icon .icon { width: 21px; height: 21px; }
        .device-title h3 { font-size: 15px; }
        .device-heading > .badge { font-size: 10px; padding-inline: 7px; }
        .device-tags { gap: 5px; margin: 7px 0 6px; }
        .badge { min-height: 22px; padding-block: 2px; }
        .device-meta { margin-bottom: 5px; font-size: 11px; line-height: 15px; }
        .device-footer { gap: 7px; padding-top: 5px; }
        .signal-bars { height: 20px; }
        .signal-bar.bar-4 { height: 20px; }
        .signal-value { font-size: 13px; }
        .passive-note { padding: 11px; }
        .telegram-controls { align-items: flex-start; flex-direction: column; padding: 12px; gap: 10px; }
        .telegram-actions { width: 100%; flex-wrap: wrap; }
        .telegram-actions button { flex: 1 1 105px; }
        .line-count { display: inline-flex; align-items: center; min-height: 44px; }
        .telegram-row { grid-template-columns: 50px 44px minmax(0,1fr) 66px; gap: 6px; height: 88px; padding: 10px 8px; }
        .telegram-time { font-size: 11px; }
        .rorg-chip { width: 44px; font-size: 10px; }
        .telegram-rssi { font-size: 12px; }
        .telegram-summary { font-size: 12px; gap: 5px; }
        .simulated { padding-inline: 5px; font-size: 9px; letter-spacing: 0; }
        .simulated-icon { width: 11px; height: 11px; }
        .telegram-meta { font-size: 12px; }
        .gateway-card { padding: 16px; }
        .facts { grid-template-columns: minmax(90px,.8fr) minmax(0,1.2fr); gap: 9px 12px; }
      }
      @media (max-width: 370px) {
        .privacy-state { display: none; }
        .header-actions { gap: 5px; }
        .header-actions button { padding-inline: 6px; }
        .metric { padding-inline: 4px; }
        .metric-value { font-size: 13px; }
      }
      @media (prefers-reduced-motion: reduce) {
        *, *::before, *::after { animation-duration: .01ms !important; animation-iteration-count: 1 !important; transition-duration: .01ms !important; scroll-behavior: auto !important; }
        .status-dot.healthy { animation: none; }
        .device-card:hover, .nearby-card:hover { transform: none; }
      }
    `;
    return style;
  }

  _render() {
    const active = this.shadowRoot.activeElement;
    const focusKey = active?.dataset.focusKey;
    const selectionStart = active?.selectionStart;
    const selectionEnd = active?.selectionEnd;
    const wrap = node("main", undefined, "wrap");
    const header = node("header", undefined, "panel-header");
    const title = node("div", undefined, "title-block");
    title.append(
      node("h1", "EnOcean"),
      node("p", "Local-first device and radio diagnostics", "subtitle")
    );
    const actions = node("div", undefined, "header-actions");
    const adopt = this._control("Open commissioning", "adopt", "primary");
    const privacy = this._control("", "privacy", "privacy-toggle");
    privacy.append(
      icon(this._anonymize ? "eye" : "eyeOff"),
      node("span", this._anonymize ? "Show IDs" : "Hide IDs"),
      node("span", this._anonymize ? "masked" : "visible", "privacy-state")
    );
    privacy.title = "Change masking for this panel only. Nothing is persisted.";
    privacy.setAttribute("aria-pressed", String(!this._anonymize));
    privacy.setAttribute(
      "aria-label",
      `${this._anonymize ? "Show IDs" : "Hide IDs"}; currently ${this._anonymize ? "masked" : "visible"}`
    );
    const refresh = this._control("", "refresh", "icon-button refresh-button");
    refresh.append(icon("refresh"));
    refresh.setAttribute("aria-label", this._loading ? "Refreshing panel" : "Refresh panel");
    refresh.title = this._loading ? "Refreshing panel" : "Refresh panel";
    refresh.disabled = this._loading;
    actions.append(adopt, privacy, refresh);
    header.append(title, actions);
    wrap.append(header);

    if (this._error) {
      const error = node("p", this._error, "error");
      error.setAttribute("role", "alert");
      wrap.append(error);
    }

    const nav = node("nav", undefined, "tabs");
    nav.setAttribute("role", "tablist");
    nav.setAttribute("aria-label", "EnOcean panel sections");
    for (const tab of TABS) {
      const label = { devices: "Devices", nearby: "Nearby", telegrams: "Telegrams", gateway: "Gateway" }[tab];
      const button = node("button");
      button.type = "button";
      button.dataset.tab = tab;
      button.dataset.focusKey = `tab:${tab}`;
      button.id = `tab-${tab}`;
      button.setAttribute("role", "tab");
      button.setAttribute("aria-selected", String(this._tab === tab));
      button.setAttribute("aria-controls", `panel-${tab}`);
      button.tabIndex = this._tab === tab ? 0 : -1;
      button.append(icon(tab), node("span", label));
      nav.append(button);
    }
    wrap.append(nav);

    if (this._snapshot) this._renderHealth(wrap);
    const panel = node("section", undefined, "tab-panel");
    panel.id = `panel-${this._tab}`;
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", `tab-${this._tab}`);
    if (!this._snapshot) {
      panel.append(this._empty(
        this._loading ? "Reading gateway health" : "No gateway facts available",
        this._loading ? "The local admin snapshot is loading." : "Refresh when the EnOcean config entry is loaded."
      ));
    } else if (this._tab === "devices") {
      this._renderDevices(panel);
    } else if (this._tab === "nearby") {
      this._renderNearby(panel);
    } else if (this._tab === "telegrams") {
      this._renderTelegrams(panel);
    } else {
      this._renderGateway(panel);
    }
    wrap.append(panel);
    this.shadowRoot.replaceChildren(this._styles(), wrap);

    if (focusKey) {
      const replacement = [...this.shadowRoot.querySelectorAll("[data-focus-key]")]
        .find((element) => element.dataset.focusKey === focusKey);
      if (replacement) {
        replacement.focus({ preventScroll: true });
        if (replacement.matches("input") && selectionStart !== null && selectionEnd !== null) {
          replacement.setSelectionRange(selectionStart, selectionEnd);
        }
      }
    }
  }

  _renderHealth(parent) {
    const gateway = this._snapshot.gateway;
    const healthy = Boolean(gateway.available && gateway.worker_alive);
    const observed = this._snapshot.nearby.length ? this._snapshot.nearby : this._snapshot.devices;
    const active = this._snapshot.devices.filter((device) => freshness(device.last_seen).active).length;
    const nearby = this._snapshot.nearby.filter((row) => !row.configured).length;
    const configured = this._snapshot.devices.length;
    const average = averageRssi(observed);
    const metrics = [
      {
        icon: "devices",
        value: configured + nearby,
        label: "radio entries",
        detail: `${configured} configured · ${nearby} nearby`,
        tone: "neutral",
      },
      {
        icon: "activity",
        value: active,
        label: "Active < 1 hour",
        detail: "recently observed",
        tone: "neutral",
        activity: active > 0,
      },
      {
        icon: "packets",
        value: gateway.received_radio_packets.toLocaleString(),
        label: "Packets received",
        detail: "gateway counter",
        tone: "neutral",
      },
      {
        icon: "signal",
        value: rssi(average),
        label: `${signalLabel(average)} · average signal`,
        detail: "observed transmitters",
        tone: signalTone(average),
      },
    ];
    const card = node("section", undefined, "card health-card");
    card.setAttribute("aria-label", "Gateway health");
    const top = node("div", undefined, "health-top");
    const mark = node("span", undefined, "gateway-mark");
    mark.append(icon("gateway"));
    const heading = node("div", undefined, "health-heading");
    const titleLine = node("div", undefined, "health-title-line");
    titleLine.append(node("h2", "Gateway"));
    const status = node("span", undefined, `health-state${healthy ? " healthy" : ""}`);
    status.append(node("span", undefined, `status-dot${healthy ? " healthy" : ""}`), node("span", healthy ? "Online" : "Attention needed"));
    status.firstChild.setAttribute("aria-hidden", "true");
    titleLine.append(status);
    heading.append(
      titleLine,
      node("p", healthy ? "Serial worker alive · radio reception available" : `${gateway.available ? "Radio available" : "Radio unavailable"} · worker ${gateway.worker_alive ? "alive" : "stopped"}`)
    );
    top.append(mark, heading, this._relative(gateway.last_packet_at, "packet", "last-packet"));
    card.append(top);
    const grid = node("div", undefined, "health-metrics");
    for (const item of metrics) {
      const metric = node("div", undefined, `metric tone-${item.tone}`);
      metric.setAttribute("aria-label", `${item.value} ${item.label} · ${item.detail}`);
      const metricHeader = node("span", undefined, "metric-header");
      metricHeader.append(icon(item.icon));
      if (item.activity !== undefined) {
        const dot = node("span", undefined, `activity-dot${item.activity ? " active" : ""}`);
        dot.setAttribute("aria-hidden", "true");
        metricHeader.append(dot);
      }
      metric.append(
        metricHeader,
        node("strong", item.value, "metric-value"),
        node("span", item.label, "metric-label"),
        node("span", item.detail, "metric-detail")
      );
      grid.append(metric);
    }
    card.append(grid, this._activityChart());
    parent.append(card);
  }

  _activityChart() {
    const container = node("section", undefined, "activity-chart-block");
    container.setAttribute("aria-label", "Radio activity over the last 30 minutes");
    const data = activityBuckets(this._activitySamples);
    const heading = node("div", undefined, "activity-heading");
    heading.append(
      icon("activity"),
      node("span", "Packets / min · last 30 min"),
      node("span", data.ready ? "client samples" : "collecting…", "activity-status")
    );
    container.append(heading);
    if (!data.ready) return container;
    const chart = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    chart.setAttribute("class", "activity-chart");
    chart.setAttribute("viewBox", "0 0 600 54");
    chart.setAttribute("preserveAspectRatio", "none");
    chart.setAttribute("role", "img");
    chart.setAttribute("aria-label", `Packets per minute, 30 client-side buckets; latest ${data.values.at(-1)}`);
    const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
    title.textContent = "Estimated from the received-packet counter at each refresh; gaps assume a uniform rate.";
    chart.append(title);
    const maximum = Math.max(1, ...data.values);
    data.values.forEach((value, index) => {
      const bar = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      const height = value > 0 ? Math.max(2, value / maximum * 48) : 2;
      bar.setAttribute("x", String(index * 20 + 2));
      bar.setAttribute("y", String(52 - height));
      bar.setAttribute("width", "16");
      bar.setAttribute("height", String(height));
      bar.setAttribute("rx", "2");
      bar.setAttribute("class", `activity-bar${value > 0 ? "" : " empty"}`);
      const barTitle = document.createElementNS("http://www.w3.org/2000/svg", "title");
      barTitle.textContent = `${30 - index} min ago: ${value} packets/min`;
      bar.append(barTitle);
      chart.append(bar);
    });
    const baseline = document.createElementNS("http://www.w3.org/2000/svg", "line");
    baseline.setAttribute("x1", "0");
    baseline.setAttribute("x2", "600");
    baseline.setAttribute("y1", "52.5");
    baseline.setAttribute("y2", "52.5");
    baseline.setAttribute("class", "activity-baseline");
    chart.append(baseline);
    container.append(chart);
    return container;
  }

  _deviceEntries() {
    const configured = this._snapshot.devices.map((device) => ({ ...device, configured: true }));
    const unconfigured = this._snapshot.nearby
      .filter((row) => !row.configured)
      .map((row) => ({
        ...row,
        name: "Observed transmitter",
        available: true,
        state: null,
        entity_id: null,
        platforms: [],
        eep: null,
        configured: false,
      }));
    return [...configured, ...unconfigured];
  }

  _renderDevices(parent) {
    const allEntries = this._deviceEntries();
    const entries = filterEntries(allEntries, this._query, this._filter);
    const tools = node("section", undefined, "card device-tools");
    const search = node("div", undefined, "search-box");
    const inputId = "enocean-device-search";
    const label = node("label", "Search devices and transmitters", "sr-only");
    label.htmlFor = inputId;
    const input = node("input");
    input.id = inputId;
    input.type = "search";
    input.value = this._query;
    input.placeholder = "Search name, EEP, platform or ID";
    input.autocomplete = "off";
    input.dataset.search = "true";
    input.dataset.focusKey = "device-search";
    search.append(label, icon("search"), input);
    const filters = node("div", undefined, "filters");
    filters.setAttribute("role", "group");
    filters.setAttribute("aria-label", "Filter devices");
    const labels = { all: "All", active: "Active", silent: "Silent", unconfigured: "Unconfigured" };
    for (const filter of FILTERS) {
      const button = node("button", labels[filter]);
      button.type = "button";
      button.dataset.filter = filter;
      button.dataset.focusKey = `filter:${filter}`;
      button.setAttribute("aria-pressed", String(this._filter === filter));
      filters.append(button);
    }
    tools.append(search, filters, node("div", `${entries.length} of ${allEntries.length} shown`, "results-count"));
    parent.append(tools);

    if (!entries.length) {
      const hasFilters = Boolean(this._query.trim()) || this._filter !== "all";
      parent.append(this._empty(
        hasFilters ? "No matching radio entries" : "No radio devices observed",
        hasFilters ? "Try another search or choose the All filter." : "Configured devices and passively observed transmitters will appear here."
      ));
      return;
    }

    const groups = new Map();
    for (const device of entries) {
      const platform = device.configured ? primaryPlatform(device.platforms) : "unconfigured";
      const labelKey = platform === "sensor" ? "binary_sensor" : platform;
      if (!groups.has(labelKey)) groups.set(labelKey, []);
      groups.get(labelKey).push(device);
    }
    const sortedGroups = [...groups.entries()].sort((left, right) => {
      const countDifference = right[1].length - left[1].length;
      return countDifference || PLATFORM_META[left[0]].label.localeCompare(PLATFORM_META[right[0]].label);
    });
    for (const [platform, devices] of sortedGroups) {
      const meta = PLATFORM_META[platform] || PLATFORM_META.other;
      const group = node("section", undefined, "platform-group");
      const heading = node("div", undefined, "group-heading");
      heading.append(node("h2", meta.label), node("span", devices.length, "group-count"));
      const grid = node("div", undefined, "device-grid");
      for (const device of devices) grid.append(this._deviceCard(device));
      group.append(heading, grid);
      parent.append(group);
    }
  }

  _deviceCard(device) {
    const accent = deviceAccent(device);
    const card = node("article", undefined, `card device-card accent-${accent}`);
    const heading = node("div", undefined, "device-heading");
    const deviceMark = node("span", undefined, "device-icon");
    deviceMark.append(icon(device.configured ? deviceIcon(device.platforms, device.eep) : "radio"));
    const title = node("div", undefined, "device-title");
    title.append(node("h3", device.name));
    const sender = this._identifier(device.sender);
    if (sender) title.append(sender);
    heading.append(deviceMark, title);
    if (device.configured) {
      const state = stateDisplay(device);
      heading.append(this._badge(state.text, state.tone, `state-value${state.stale ? " is-stale" : ""}`));
    } else {
      heading.append(this._badge("Unconfigured", "warning"));
    }
    card.append(heading);

    const tags = node("div", undefined, "device-tags");
    tags.append(this._relative(device.last_seen, "seen", "badge freshness"));
    if (device.eep) tags.append(this._badge(device.eep, "neutral", "eep"));
    const repeater = repeaterLabel(device.last_repeater_count);
    if (repeater) tags.append(this._badge(repeater, "neutral", "hop-badge"));
    if (!device.configured) {
      for (const rorg of device.rorgs || []) tags.append(this._badge(rorgName(rorg), "neutral", "rorg-badge"));
    }
    card.append(tags);
    const packetLabel = `${device.packet_count || 0} ${device.packet_count === 1 ? "packet" : "packets"}`;
    const metaLine = node("div", undefined, "device-meta");
    metaLine.append(node("span", packetLabel));
    if (!device.configured) metaLine.append(node("span", "Passive observation"));
    card.append(metaLine);
    const footer = node("div", undefined, "device-footer");
    footer.append(this._signal(device.last_rssi_dbm));
    if (device.entity_id) {
      const more = this._control("Details", "more-info", "details-button", `more-info:${device.entity_id}`);
      more.dataset.entityId = device.entity_id;
      more.setAttribute("aria-label", `Details for ${device.name}`);
      footer.append(more);
    } else if (!device.configured) {
      footer.append(this._control("Adopt", "adopt", "primary", `adopt:${device.sender}:${device.last_seen}`));
    }
    card.append(footer);
    return card;
  }

  _renderNearby(parent) {
    const note = node("section", undefined, "card passive-note");
    note.append(icon("radio"));
    const text = node("div");
    text.append(
      node("h2", "Passive radio observation"),
      node("p", "Nothing is paired or inferred from a product family. Adopt a transmitter only through the explicit commissioning flow.")
    );
    note.append(text);
    parent.append(note);
    if (!this._snapshot.nearby.length) {
      parent.append(this._empty("The airwaves are quiet", "Observed transmitters will appear here without any active scan or radio write."));
      return;
    }
    const rows = [...this._snapshot.nearby].sort((left, right) => {
      const leftRssi = typeof left.last_rssi_dbm === "number" ? left.last_rssi_dbm : -Infinity;
      const rightRssi = typeof right.last_rssi_dbm === "number" ? right.last_rssi_dbm : -Infinity;
      return rightRssi - leftRssi;
    });
    const grid = node("section", undefined, "nearby-grid");
    grid.setAttribute("aria-label", "Observed transmitters sorted by signal strength");
    for (const [index, row] of rows.entries()) {
      const card = node("article", undefined, `card nearby-card accent-${deviceAccent(row)}`);
      const heading = node("div", undefined, "device-heading");
      const mark = node("span", undefined, "device-icon");
      mark.append(icon("radio"));
      const title = node("div", undefined, "device-title");
      title.append(node("h3", "Observed transmitter"));
      const sender = this._identifier(row.sender);
      if (sender) title.append(sender);
      heading.append(mark, title, this._badge(row.configured ? "Configured" : "Unconfigured", row.configured ? "good" : "warning"));
      card.append(heading);
      const tags = node("div", undefined, "device-tags");
      tags.append(this._relative(row.last_seen, "seen", "badge freshness"));
      for (const rorg of row.rorgs || []) tags.append(this._badge(rorgName(rorg), "neutral", "rorg-badge"));
      const repeater = repeaterLabel(row.last_repeater_count);
      if (repeater) tags.append(this._badge(repeater, "neutral", "hop-badge"));
      card.append(tags, node("p", `${row.packet_count || 0} packets observed`, "muted small"));
      const signal = node("div", undefined, "nearby-signal");
      signal.append(this._signal(row.last_rssi_dbm), node("span", signalLabel(row.last_rssi_dbm), "signal-quality"));
      card.append(signal);
      if (!row.configured) {
        const adopt = this._control("Adopt transmitter", "adopt", "primary adopt-button", `nearby-adopt:${index}`);
        adopt.setAttribute("aria-label", `Adopt observed transmitter ${index + 1}`);
        card.append(adopt);
      }
      grid.append(card);
    }
    parent.append(grid);
  }

  _renderTelegrams(parent) {
    const card = node("section", undefined, "card telegram-shell");
    const controls = node("div", undefined, "telegram-controls");
    const heading = node("div", undefined, "telegram-heading");
    const title = node("div", undefined, "telegram-title");
    title.append(
      node("span", undefined, `stream-dot${this._streamActive ? " active" : ""}`),
      node("h2", "Live telegram stream"),
      this._badge(this._streamActive ? "Receiving" : this._streamStarting ? "Starting" : "Stopped", this._streamActive ? "good" : "neutral")
    );
    title.firstChild.setAttribute("aria-hidden", "true");
    const subtitle = this._streamActive
      ? `Live · memory only · newest ${MAX_TELEGRAMS} · ${this._anonymize ? "IDs masked" : "IDs visible for this session"}`
      : `Off by default · memory only · newest ${MAX_TELEGRAMS} · ${this._anonymize ? "IDs masked" : "IDs visible for this session"}`;
    heading.append(title, node("p", subtitle));
    const actions = node("div", undefined, "telegram-actions");
    actions.append(node("span", `${this._telegrams.length} / ${MAX_TELEGRAMS} lines`, "line-count"));
    const clear = this._control("Clear", "clear");
    clear.disabled = !this._telegrams.length;
    const toggle = this._control(
      this._streamStarting ? "Starting…" : this._streamActive ? "Stop stream" : "Start stream",
      "stream",
      this._streamActive ? "primary-outline" : "primary"
    );
    toggle.disabled = this._streamStarting;
    toggle.setAttribute("aria-pressed", String(this._streamActive));
    actions.append(clear, toggle);
    controls.append(heading, actions);
    card.append(controls);

    if (!this._telegrams.length) {
      const empty = this._empty(
        this._streamActive ? "Listening for radio traffic" : "The live stream is stopped",
        this._streamActive ? "New decoded telegrams will fade in here." : "Start the stream explicitly to record up to 100 telegrams in memory."
      );
      empty.classList.add("telegram-empty");
      card.append(empty);
    } else {
      const list = node("div", undefined, "telegram-list");
      list.setAttribute("aria-label", "Decoded telegrams, newest first");
      const visibleKeys = new Set();
      for (const record of [...this._telegrams].reverse()) {
        const key = `${record.received_at}|${record.sender}|${record.summary}|${record.rorg}`;
        const isNew = !this._telegramKeys.has(key);
        visibleKeys.add(key);
        list.append(this._telegramRow(record, isNew));
      }
      this._telegramKeys = visibleKeys;
      card.append(list);
    }
    parent.append(card);
  }

  _telegramRow(record, isNew) {
    const rorg = rorgName(record.rorg);
    const row = node("article", undefined, `telegram-row${isNew ? " is-new" : ""}`);
    row.append(node("time", shortTime(record.received_at), "telegram-time"));
    row.firstChild.dateTime = record.received_at || "";
    row.append(node("span", rorg, `rorg-chip rorg-${rorg.toLowerCase()}`));
    const main = node("div", undefined, "telegram-main");
    const summary = node("div", undefined, "telegram-summary");
    const summaryText = node("strong", record.summary || "Radio telegram");
    summaryText.title = record.summary || "Radio telegram";
    summary.append(summaryText);
    if (record.simulated) {
      const simulated = this._badge("SIMULATED", "simulated", "simulated");
      simulated.prepend(icon("simulated", "simulated-icon"));
      summary.append(simulated);
    }
    main.append(summary);
    const metaParts = [];
    const sender = this._identifier(record.sender);
    if (sender) metaParts.push(sender.textContent);
    metaParts.push(
      record.eep || "EEP not declared",
      telegramData(record),
      telegramHopLabel(record.repeater_count)
    );
    const meta = node("div", metaParts.join("  ·  "), "telegram-meta");
    meta.title = metaParts.join("  ·  ");
    main.append(meta);
    const strength = node("span", rssi(record.rssi_dbm), `telegram-rssi tone-${signalTone(record.rssi_dbm)}`);
    const accessible = signalLevel(record.rssi_dbm)
      ? `${signalLabel(record.rssi_dbm)} signal, ${rssi(record.rssi_dbm)}`
      : "No signal measurement";
    strength.setAttribute("role", "img");
    strength.setAttribute("aria-label", accessible);
    strength.title = accessible;
    row.append(main, strength);
    return row;
  }

  _fact(list, label, value, className) {
    list.append(node("dt", label), node("dd", value, className));
  }

  _renderGateway(parent) {
    const gateway = this._snapshot.gateway;
    const grid = node("div", undefined, "gateway-grid");
    const identity = node("section", undefined, "card gateway-card");
    identity.append(node("h2", "Serial gateway"));
    const facts = node("dl", undefined, "facts");
    this._fact(facts, "Availability", gateway.available ? "Available" : "Unavailable", gateway.available ? "ok" : "bad");
    this._fact(facts, "Worker", gateway.worker_alive ? "Running" : "Stopped", gateway.worker_alive ? "ok" : "bad");
    if (gateway.base_id && gateway.base_id !== MASKED) this._fact(facts, "Base ID", gateway.base_id);
    this._fact(facts, "Application", gateway.app_version || "Not read");
    this._fact(facts, "API", gateway.api_version || "Not read");
    this._fact(facts, "Chip version", gateway.chip_version || "Not read");
    this._fact(facts, "Description", gateway.app_description || "Not read");
    identity.append(facts);
    grid.append(identity);

    const radio = node("section", undefined, "card gateway-card");
    radio.append(node("h2", "Radio diagnostics"));
    const radioFacts = node("dl", undefined, "facts");
    this._fact(radioFacts, "Repeater", gateway.repeater ? `${gateway.repeater.enabled ? "On" : "Off"}, level ${gateway.repeater.level}` : "Not read");
    this._fact(radioFacts, "Received packets", gateway.received_radio_packets);
    this._fact(radioFacts, "Last packet", time(gateway.last_packet_at));
    this._fact(radioFacts, "Last error", gateway.last_error || "None");
    this._fact(radioFacts, "Simulated telegrams", this._snapshot.simulation_allowed ? "Opt-in enabled" : "Disabled");
    this._fact(radioFacts, "Stream buffer", `${this._snapshot.stream.size} / ${MAX_TELEGRAMS}`);
    radio.append(radioFacts, node("p", "Chip identifiers and the serial path are deliberately omitted. The Base ID is visible only to administrators and masked by default.", "gateway-note"));
    grid.append(radio);
    parent.append(grid);
  }
}

customElements.define("enocean-custom-panel", EnOceanCustomPanel);

// CommonJS export is inert in the browser and lets the repository test pure
// presentation helpers without adding a DOM emulator or a build dependency.
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    MAX_TELEGRAMS,
    signalLevel,
    signalTone,
    signalLabel,
    rssi,
    repeaterLabel,
    telegramHopLabel,
    relativeTime,
    freshness,
    deviceAccent,
    stateDisplay,
    averageRssi,
    rorgName,
    primaryPlatform,
    deviceIcon,
    filterEntries,
    recordActivitySample,
    activityBuckets,
  };
}
