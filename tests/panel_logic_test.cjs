"use strict";

const assert = require("node:assert/strict");

// The production file registers a custom element. Pure helpers are tested in
// Node with only these two browser globals; no DOM implementation is needed.
global.HTMLElement = class {};
global.customElements = { define() {} };

const logic = require("../custom_components/enocean_custom/www/enocean-custom-panel.js");
const NOW = Date.parse("2026-11-05T12:00:00.000Z");

assert.equal(logic.MAX_TELEGRAMS, 100);
assert.equal(logic.signalLevel(null), 0);
assert.equal(logic.signalLevel(-45), 4);
assert.equal(logic.signalLevel(-60), 4);
assert.equal(logic.signalLevel(-61), 3);
assert.equal(logic.signalLevel(-72), 3);
assert.equal(logic.signalLevel(-73), 2);
assert.equal(logic.signalLevel(-85), 2);
assert.equal(logic.signalLevel(-86), 1);
assert.equal(logic.signalTone(-60), "good");
assert.equal(logic.signalTone(-80), "warning");
assert.equal(logic.signalTone(-95), "critical");
assert.equal(logic.signalLabel(-45), "Excellent");
assert.equal(logic.signalLabel(-65), "Good");
assert.equal(logic.signalLabel(-80), "Fair");
assert.equal(logic.signalLabel(-95), "Weak");
assert.equal(logic.rssi(-45), "−45 dBm");
assert.equal(logic.rssi(0), "0 dBm");
assert.equal(logic.rssi(null), "—");
assert.equal(logic.repeaterLabel(0), null);
assert.equal(logic.repeaterLabel(1), "via 1 repeater");
assert.equal(logic.repeaterLabel(2), "via 2 repeaters");
assert.equal(logic.telegramHopLabel(null), "hops —");
assert.equal(logic.telegramHopLabel(0), "direct");
assert.equal(logic.telegramHopLabel(1), "1 hop");
assert.equal(logic.telegramHopLabel(2), "2 hops");

assert.equal(logic.deviceIcon(["sensor"], "A5-02-05"), "thermometer");
assert.equal(logic.deviceIcon(["sensor"], "A5-04-01"), "thermometer");
assert.equal(logic.deviceIcon(["sensor"], "A5-10-03"), "thermometer");
assert.equal(logic.deviceIcon(["binary_sensor"], "D5-00-01"), "contact");
assert.equal(logic.deviceIcon(["binary_sensor"], "F6-10-00"), "contact");
assert.equal(logic.deviceIcon(["sensor"], "A5-14-01"), "contact");
assert.equal(logic.deviceIcon(["switch"], "F6-02-01"), "rocker");
assert.equal(logic.deviceIcon(["cover"], "D2-05-00"), "cover");
assert.equal(logic.deviceIcon(["switch"], "D2-01-12"), "socket");
assert.equal(logic.deviceIcon(["light"], "D2-01-12"), "light");
assert.equal(logic.deviceIcon(["switch"], null), "socket");
assert.equal(logic.deviceIcon(["light"], null), "light");
assert.equal(logic.deviceIcon(["climate"], "A5-20-04"), "climate");
assert.equal(logic.deviceIcon(["sensor"], null), "radio");
assert.equal(logic.deviceIcon(["cover"], null), "radio");
assert.equal(logic.deviceIcon(["sensor"], "D2-01-12"), "socket");

assert.equal(logic.relativeTime(null, NOW), "Never observed");
assert.equal(logic.relativeTime(NOW - 8000, NOW), "8s ago");
assert.equal(logic.relativeTime(NOW - 3 * 3600000, NOW), "3h ago");
assert.equal(logic.relativeTime(NOW - 2 * 86400000, NOW), "2d ago");

assert.deepEqual(logic.freshness(NOW - 30000, NOW), {
  text: "Just now",
  tone: "fresh",
  active: true,
  silent: false,
});
assert.deepEqual(logic.freshness(NOW - 3 * 3600000, NOW), {
  text: "3h ago",
  tone: "fresh",
  active: false,
  silent: true,
});
assert.deepEqual(logic.freshness(NOW - 8 * 3600000, NOW), {
  text: "Silent for 8h",
  tone: "warning",
  active: false,
  silent: true,
});
assert.deepEqual(logic.freshness(NOW - 2 * 86400000, NOW), {
  text: "Silent for 2d",
  tone: "critical",
  active: false,
  silent: true,
});
assert.equal(logic.freshness(null, NOW).tone, "critical");
assert.equal(logic.freshness(null, NOW).silent, true);
assert.equal(logic.deviceAccent({ available: true, last_seen: NOW - 30000 }, NOW), "neutral");
assert.equal(logic.deviceAccent({ available: true, last_seen: NOW - 8 * 3600000 }, NOW), "warning");
assert.equal(logic.deviceAccent({ available: true, last_seen: NOW - 2 * 86400000 }, NOW), "critical");
assert.equal(logic.deviceAccent({ available: false, last_seen: NOW - 30000 }, NOW), "critical");
assert.equal(logic.deviceAccent({ last_seen: NOW - 30000 }, NOW), "neutral");

assert.deepEqual(logic.stateDisplay({
  available: true, state: "21.4", unit: "°C", last_seen: NOW - 30000,
}, NOW), { text: "21.4 °C", tone: "neutral", stale: false });
assert.deepEqual(logic.stateDisplay({
  available: true, state: "21.4", unit: "°C", last_seen: NOW - 2 * 86400000,
}, NOW), { text: "21.4 °C · last known", tone: "neutral", stale: true });
assert.deepEqual(logic.stateDisplay({
  available: true, state: "21.4", last_seen: NOW - 30000,
}, NOW), { text: "21.4", tone: "neutral", stale: false });
assert.deepEqual(logic.stateDisplay({
  available: false, state: "unavailable", unit: "°C", last_seen: NOW - 30000,
}, NOW), { text: "Unavailable", tone: "critical", stale: false });

let activity = [];
activity = logic.recordActivitySample(activity, 100, NOW - 120000);
activity = logic.recordActivitySample(activity, 160, NOW - 60000);
assert.equal(logic.activityBuckets(activity, NOW).ready, false);
activity = logic.recordActivitySample(activity, 280, NOW);
const buckets = logic.activityBuckets(activity, NOW);
assert.equal(buckets.ready, true);
assert.equal(buckets.values.length, 30);
assert.equal(buckets.values[28], 60);
assert.equal(buckets.values[29], 120);
assert.ok(buckets.values.slice(0, 28).every((value) => value === 0));
const reset = logic.activityBuckets([
  { count: 500, at: NOW - 120000 },
  { count: 20, at: NOW - 60000 },
  { count: 40, at: NOW },
], NOW);
assert.equal(reset.ready, true);
assert.equal(reset.values[28], 0);
assert.equal(reset.values[29], 20);
let bounded = [];
for (let index = 0; index < 130; index += 1) {
  bounded = logic.recordActivitySample(bounded, index, NOW + index * 1000);
}
assert.equal(bounded.length, 120);
assert.equal(bounded[0].count, 10);

assert.equal(logic.averageRssi([
  { last_rssi_dbm: -45 },
  { last_rssi_dbm: null },
  { last_rssi_dbm: -75 },
]), -60);
assert.equal(logic.averageRssi([{ last_rssi_dbm: null }]), null);
assert.equal(logic.rorgName("0xF6"), "RPS");
assert.equal(logic.rorgName("0xD5"), "1BS");
assert.equal(logic.rorgName("0xA5"), "4BS");
assert.equal(logic.rorgName("0xD2"), "VLD");
assert.equal(logic.rorgName("0xD4"), "Other");
assert.equal(logic.primaryPlatform(["binary_sensor", "cover"]), "cover");
assert.equal(logic.primaryPlatform(["switch"]), "switch");
assert.equal(logic.primaryPlatform([]), "other");

const entries = [
  { name: "Living room light", configured: true, last_seen: NOW - 1000,
    sender: "masked", eep: "D2-01-01", platforms: ["light"], state: "on" },
  { name: "Attic sensor", configured: true, last_seen: NOW - 2 * 86400000,
    sender: "masked", eep: "A5-02-05", platforms: ["sensor"], state: "20" },
  { name: "Observed transmitter", configured: false, last_seen: NOW - 1000,
    sender: "masked", eep: null, platforms: [], state: null, rorgs: ["0xF6"] },
];
assert.equal(logic.filterEntries(entries, "", "all", NOW).length, 3);
assert.equal(logic.filterEntries(entries, "", "active", NOW).length, 2);
assert.equal(logic.filterEntries(entries, "", "silent", NOW).length, 1);
assert.equal(logic.filterEntries(entries, "", "unconfigured", NOW).length, 1);
assert.equal(logic.filterEntries(entries, "attic", "all", NOW).length, 1);
assert.equal(logic.filterEntries(entries, "a5-02", "all", NOW).length, 1);
assert.equal(logic.filterEntries(entries, "missing", "all", NOW).length, 0);

console.log("PANEL_LOGIC_OK");
