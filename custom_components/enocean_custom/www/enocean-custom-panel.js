/* EnOcean Custom admin panel. No build step, dependencies, telemetry or remote assets. */
const TABS = ["devices", "nearby", "telegrams", "gateway"];
const MAX_TELEGRAMS = 100;
const MASKED = "••:••:••:••";

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined && text !== null) element.textContent = String(text);
  if (className) element.className = className;
  return element;
}

function time(value) {
  if (!value) return "Never observed";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString();
}

function rssi(value) {
  return value === null || value === undefined ? "—" : `${value} dBm`;
}

class EnOceanCustomPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = "devices";
    this._snapshot = null;
    this._telegrams = [];
    this._anonymize = true;
    this._streamActive = false;
    this._streamUnsubscribe = null;
    this._loading = false;
    this._error = "";
  }

  set hass(hass) {
    this._hass = hass;
  }

  connectedCallback() {
    this._rootListener = (event) => this._onClick(event);
    this.shadowRoot.addEventListener("click", this._rootListener);
    this._refresh();
    this._refreshTimer = window.setInterval(() => this._refresh(), 30000);
  }

  disconnectedCallback() {
    window.clearInterval(this._refreshTimer);
    this.shadowRoot.removeEventListener("click", this._rootListener);
    this._stopStream();
  }

  async _refresh() {
    if (!this._hass || this._loading) return;
    this._loading = true;
    try {
      this._snapshot = await this._hass.callWS({
        type: "enocean_custom/panel",
        anonymize: this._anonymize,
      });
      this._error = "";
    } catch (error) {
      this._error = error.message || "Unable to read the EnOcean gateway.";
    } finally {
      this._loading = false;
      this._render();
    }
  }

  async _startStream() {
    if (this._streamActive || !this._hass) return;
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
    }
    this._render();
  }

  _stopStream() {
    this._streamActive = false;
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

  async _onClick(event) {
    const button = event.target.closest("button");
    if (!button) return;
    const action = button.dataset.action;
    if (button.dataset.tab) {
      this._tab = button.dataset.tab;
      this._render();
    } else if (action === "refresh") {
      this._refresh();
    } else if (action === "privacy") {
      this._anonymize = !this._anonymize;
      if (this._streamActive) {
        this._stopStream();
        this._telegrams = [];
        await this._startStream();
      }
      await this._refresh();
    } else if (action === "stream") {
      if (this._streamActive) this._stopStream();
      else await this._startStream();
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

  _render() {
    const style = document.createElement("style");
    style.textContent = `
      :host { display:block; min-height:100%; background:var(--primary-background-color); color:var(--primary-text-color); }
      .wrap { max-width:1240px; margin:auto; padding:24px; box-sizing:border-box; }
      header { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; margin-bottom:20px; }
      h1 { margin:0; font-size:28px; } h2 { margin:0 0 14px; font-size:18px; }
      .muted { color:var(--secondary-text-color); } .small { font-size:12px; }
      nav { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:18px; }
      button { border:1px solid var(--divider-color); border-radius:8px; padding:9px 13px; background:var(--card-background-color); color:var(--primary-text-color); cursor:pointer; font:inherit; }
      button:hover { border-color:var(--primary-color); } button[aria-selected="true"] { background:var(--primary-color); color:var(--text-primary-color); }
      button.primary { background:var(--primary-color); color:var(--text-primary-color); }
      .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:14px; }
      .card { background:var(--card-background-color); border:1px solid var(--divider-color); border-radius:12px; padding:16px; box-sizing:border-box; }
      .facts { display:grid; grid-template-columns:minmax(110px,auto) 1fr; gap:8px 14px; margin:0; }
      dt { color:var(--secondary-text-color); } dd { margin:0; overflow-wrap:anywhere; }
      .row { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; }
      .badge { display:inline-block; border-radius:999px; padding:2px 8px; font-size:12px; background:var(--secondary-background-color); margin-left:6px; }
      .simulated { color:var(--warning-color); border:1px solid var(--warning-color); }
      .ok { color:var(--success-color); } .error { color:var(--error-color); margin:12px 0; }
      .table { overflow-x:auto; } table { width:100%; border-collapse:collapse; }
      th,td { text-align:left; padding:9px; border-bottom:1px solid var(--divider-color); white-space:nowrap; }
      code { font-family:var(--code-font-family,monospace); overflow-wrap:anywhere; }
      @media (max-width:600px) { .wrap { padding:14px; } h1 { font-size:23px; } nav { display:grid; grid-template-columns:1fr 1fr; } .card { padding:12px; } }
    `;
    const wrap = node("main", undefined, "wrap");
    const header = node("header");
    const title = node("div");
    title.append(node("h1", "EnOcean"), node("div", "Local-first device and radio diagnostics", "muted"));
    const actions = node("div", undefined, "row");
    const privacy = node("button", this._anonymize ? "IDs masked" : "IDs visible", "primary");
    privacy.dataset.action = "privacy";
    privacy.title = "Change masking for this panel only. Nothing is persisted.";
    actions.append(privacy);
    const adopt = node("button", "Open commissioning");
    adopt.dataset.action = "adopt";
    actions.append(adopt);
    const refresh = node("button", this._loading ? "Loading…" : "Refresh");
    refresh.dataset.action = "refresh";
    actions.append(refresh);
    header.append(title, actions);
    wrap.append(header);
    if (this._error) wrap.append(node("p", this._error, "error"));
    const nav = node("nav");
    nav.setAttribute("role", "tablist");
    for (const tab of TABS) {
      const label = { devices: "Devices", nearby: "Nearby", telegrams: "Telegrams", gateway: "Gateway" }[tab];
      const button = node("button", label);
      button.dataset.tab = tab;
      button.setAttribute("role", "tab");
      button.setAttribute("aria-selected", String(this._tab === tab));
      nav.append(button);
    }
    wrap.append(nav);
    if (!this._snapshot) {
      wrap.append(node("p", this._loading ? "Loading gateway facts…" : "No gateway facts available.", "muted"));
    } else if (this._tab === "devices") {
      this._renderDevices(wrap);
    } else if (this._tab === "nearby") {
      this._renderNearby(wrap);
    } else if (this._tab === "telegrams") {
      this._renderTelegrams(wrap);
    } else {
      this._renderGateway(wrap);
    }
    this.shadowRoot.replaceChildren(style, wrap);
  }

  _fact(list, label, value, className) {
    list.append(node("dt", label), node("dd", value, className));
  }

  _renderDevices(parent) {
    const grid = node("section", undefined, "grid");
    if (!this._snapshot.devices.length) grid.append(node("p", "No configured radio devices.", "muted"));
    for (const device of this._snapshot.devices) {
      const card = node("article", undefined, "card");
      const heading = node("div", undefined, "row");
      heading.append(node("h2", device.name));
      if (device.entity_id) {
        const more = node("button", "Details");
        more.dataset.action = "more-info";
        more.dataset.entityId = device.entity_id;
        heading.append(more);
      }
      card.append(heading);
      const facts = node("dl", undefined, "facts");
      this._fact(facts, "Sender", device.sender);
      this._fact(facts, "State", device.state || "Unknown", device.available ? "" : "error");
      this._fact(facts, "Platforms", device.platforms.join(", "));
      this._fact(facts, "Profile", device.eep || "Not declared");
      this._fact(facts, "Last seen", time(device.last_seen));
      this._fact(facts, "Packets", device.packet_count);
      this._fact(facts, "RSSI", rssi(device.last_rssi_dbm));
      this._fact(facts, "Repeater hops", device.last_repeater_count ?? "—");
      card.append(facts);
      grid.append(card);
    }
    parent.append(grid);
  }

  _renderNearby(parent) {
    const card = node("section", undefined, "card table");
    card.append(node("h2", "Observed transmitters"), node("p", "Passive observation only. A sender is never paired or inferred from its product family. Use Open commissioning to adopt it explicitly.", "muted"));
    if (!this._snapshot.nearby.length) {
      card.append(node("p", "No transmitters observed in the current inbox.", "muted"));
    } else {
      const table = node("table");
      const head = node("tr");
      ["Sender", "Status", "RORG", "Last seen", "Packets", "RSSI", "Repeater hops"].forEach((label) => head.append(node("th", label)));
      const thead = node("thead"); thead.append(head); table.append(thead);
      const body = node("tbody");
      for (const row of this._snapshot.nearby) {
        const tr = node("tr");
        [row.sender, row.configured ? "Configured" : "Unconfigured", row.rorgs.join(", "), time(row.last_seen), row.packet_count, rssi(row.last_rssi_dbm), row.last_repeater_count ?? "—"].forEach((value) => tr.append(node("td", value)));
        body.append(tr);
      }
      table.append(body); card.append(table);
    }
    parent.append(card);
  }

  _renderTelegrams(parent) {
    const card = node("section", undefined, "card");
    const controls = node("div", undefined, "row");
    const text = node("div");
    text.append(node("h2", "Live decoded telegrams"), node("p", `Off by default. In-memory only, newest ${MAX_TELEGRAMS}; clearing on disconnect. ${this._anonymize ? "Identifiers are masked." : "Identifiers are visible for this session."}`, "muted"));
    const toggle = node("button", this._streamActive ? "Stop live stream" : "Start live stream", this._streamActive ? "" : "primary");
    toggle.dataset.action = "stream";
    controls.append(text, toggle); card.append(controls);
    if (!this._telegrams.length) {
      card.append(node("p", this._streamActive ? "Waiting for radio traffic…" : "The live stream is stopped.", "muted"));
    } else {
      const list = node("div");
      for (const record of [...this._telegrams].reverse()) {
        const item = node("article", undefined, "card");
        const heading = node("div", undefined, "row");
        heading.append(node("strong", record.summary));
        if (record.simulated) heading.append(node("span", "SIMULATED", "badge simulated"));
        item.append(heading);
        const facts = node("dl", undefined, "facts");
        this._fact(facts, "Received", time(record.received_at));
        this._fact(facts, "Sender", record.sender);
        this._fact(facts, "RORG / profile", `${record.rorg} / ${record.eep || "not declared"}`);
        this._fact(facts, "Data", record.data ? record.data.map((value) => value.toString(16).padStart(2, "0").toUpperCase()).join(" ") : "Redacted");
        this._fact(facts, "RSSI", rssi(record.rssi_dbm));
        this._fact(facts, "Repeater hops", record.repeater_count ?? "—");
        item.append(facts); list.append(item);
      }
      card.append(list);
    }
    parent.append(card);
  }

  _renderGateway(parent) {
    const gateway = this._snapshot.gateway;
    const card = node("section", undefined, "card");
    card.append(node("h2", "Serial gateway"));
    const facts = node("dl", undefined, "facts");
    this._fact(facts, "Availability", gateway.available ? "Available" : "Unavailable", gateway.available ? "ok" : "error");
    this._fact(facts, "Worker", gateway.worker_alive ? "Running" : "Stopped", gateway.worker_alive ? "ok" : "error");
    this._fact(facts, "Base ID", gateway.base_id || MASKED);
    this._fact(facts, "Application", gateway.app_version || "Not read");
    this._fact(facts, "API", gateway.api_version || "Not read");
    this._fact(facts, "Chip version", gateway.chip_version || "Not read");
    this._fact(facts, "Description", gateway.app_description || "Not read");
    this._fact(facts, "Repeater", gateway.repeater ? `${gateway.repeater.enabled ? "On" : "Off"}, level ${gateway.repeater.level}` : "Not read");
    this._fact(facts, "Received packets", gateway.received_radio_packets);
    this._fact(facts, "Last packet", time(gateway.last_packet_at));
    this._fact(facts, "Last error", gateway.last_error || "None");
    this._fact(facts, "Simulated telegrams", this._snapshot.simulation_allowed ? "Opt-in enabled" : "Disabled");
    card.append(facts, node("p", "Chip identifiers and the serial path are deliberately omitted. The masked Base ID is shown only to administrators.", "muted small"));
    parent.append(card);
  }
}

customElements.define("enocean-custom-panel", EnOceanCustomPanel);
