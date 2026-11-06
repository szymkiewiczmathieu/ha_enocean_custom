# EnOcean admin panel

The admin-only side panel is a single dependency-free JavaScript asset. It uses
Home Assistant theme variables, inline SVG icons and the existing authenticated
WebSocket commands. It loads no remote asset and adds no radio write or service.

## Views and thresholds

- The gateway banner combines availability, worker health and relative
  last-packet time with four icon KPI tiles: radio entries (configured plus
  unconfigured nearby), recently active devices, received packets and average
  RSSI. Recently active means observed less than one hour ago.
- Signal bars use presentation thresholds, not a link-quality guarantee: four
  bars/Excellent at -60 dBm or higher, three/Good from -61 to -72, two/Fair from
  -73 to -85, and one/Weak below that. The exact dBm value and an accessible
  quality label remain visible.
- Freshness is neutral below six hours, orange from six hours, and red from 24
  hours. Critical silence and an unavailable device use the same uniform red
  card border. After 24 hours, a value is muted and labelled `last known`. The
  Active and Silent filters split at one hour; a never-observed transmitter is
  silent.
- Device icons use only the configured platform and declared EEP, never the
  sender ID: A5-02/A5-04/A5-10 temperature, D5-00/F6-10/A5-14 contact, F6-02
  rocker, D2-05 shutter, D2-01 light/socket, and climate radiator. Other or
  undeclared combinations use a generic radio icon.
- Every device and Nearby grid uses `repeat(auto-fill, minmax(260px, 1fr))`.
  Details is a compact ghost control with a 44-pixel target; the signal gauge
  and exact value are the primary footer information.
- Devices can be searched and filtered client-side. Nearby cards are sorted by
  descending RSSI and adoption opens the existing explicit commissioning flow.
- The telegram stream is off until Start is clicked. It holds at most 100
  in-memory lines and offers outlined Stop and neutral Clear controls. RPS,
  1BS, 4BS and VLD have separate non-status hues; SIMULATED uses a unique
  magenta dashed marker with an icon. RSSI appears only once per row.

Each device entry in the snapshot has an additive `unit` field copied from the
entity state's `unit_of_measurement` attribute, or `null` when there is no state
or unit. It is not treated as an identifier and is present in anonymized and
revealed snapshots. The panel appends it to the state only when supplied.

Sender IDs and the Base ID are masked by default. Masked placeholder rows are
not rendered; full identifiers appear only after **Show IDs** is selected.
Revealing them affects only the current panel instance and is not persisted.
The stream uses the same privacy mode. No partial identifier is shown.

## Packet activity chart

The health banner includes 30 inline SVG bars estimating packets per minute for
the last 30 minutes. This is computed only in browser memory from the existing
`received_radio_packets` counter each time the panel refreshes (every 30
seconds); there is no new backend field and no persistence. Fewer than three
samples displays `collecting…`.

The chart is an estimate, not a radio history database. A sleeping or closed
panel misses counter transitions, and packets between two samples are spread
uniformly over that interval. A decreasing counter (for example after a gateway
restart) is treated as a reset rather than a negative rate. The standalone demo
seeds a fictional 30-minute series so the chart can be reviewed immediately.

## Mobile and accessibility

At 600 pixels and below, KPI tiles use a compact four-column grid, filters form
one horizontally scrollable row inside their container, and four labelled SVG
tabs stay at the bottom. Content bottom padding includes the fixed tab height
and `env(safe-area-inset-bottom)`. The Gateway tab retains detailed facts while
the health card remains a summary.

The preview viewport meta tag includes `viewport-fit=cover` so safe-area CSS can
be exercised in device emulation. The production asset is a Home Assistant
custom element, not a document: Home Assistant owns the real page viewport and
this preview setting is deliberately not injected into it.

## Standalone preview

Open `scripts/panel_preview.html` from a browser. It loads the production asset
from `../custom_components/enocean_custom/www/enocean-custom-panel.js` and uses
only generic fictional devices and identifiers. No Home Assistant instance or
network service is needed.

URL parameters select the demonstration:

- `tab=devices`, `tab=nearby`, `tab=telegrams` or `tab=gateway`
- `theme=light` or `theme=dark`
- `stream=1` clicks Start and emits five demo telegrams, including one simulated
- `filter=all`, `filter=active`, `filter=silent` or `filter=unconfigured`
- `search=attic` demonstrates client-side search
- `reveal=1` demonstrates the session-only identifier reveal

The demo A5-02-05 telegram uses DB1 `0x76`, decoded by the vendored profile as
`tmp=21.49`; its summary is exactly that decoder output. The separate stale
device-state example is `21.4 °C`. The D2-01 telegram uses output byte `0x64`
and decodes as channel 0, output 100%. Python tests extract and decode these
exact preview bytes.

For a desktop capture, run from the repository root:

```sh
chromium-browser --headless --no-sandbox --disable-gpu --hide-scrollbars \
  --virtual-time-budget=5000 --window-size=1280,900 \
  --screenshot=/tmp/panel-devices.png \
  'file:///absolute/path/to/repo/scripts/panel_preview.html?tab=devices&theme=light'
```

Some Chromium builds enforce a 500-pixel minimum CLI window width. The shipped
390×844 mobile capture therefore uses exact CDP device emulation, not cropping.
`window.panelAudit()` reports document width, clipped or overflowing containers,
44-pixel touch targets, telegram row heights, fixed-tab clearance, activity
bars and the number of fully visible device cards. The required mobile gates
are `document.documentElement.scrollWidth <= viewport width` and at least two
fully visible cards at 390×844.

The preview is a test harness, not a substitute for checking the panel inside
the real Home Assistant frontend.

## Checks

```sh
node --check custom_components/enocean_custom/www/enocean-custom-panel.js
python -m unittest tests.test_panel tests.test_panel_js -v
```

The Node-backed unittest runs only when `node` is available. It checks signal
quality and formatting, Unicode minus signs, freshness/card accents, units and
last-known state, declared-EEP icon selection, repeater singular/plural labels,
RORG names, activity sampling/buckets and filters without adding a DOM library
or build step.

Preview images:

- [Devices, light desktop](images/panel-devices.png)
- [Telegrams, dark desktop](images/panel-telegrams-dark.png)
- [Nearby, dark desktop](images/panel-nearby-dark.png)
- [Devices, 390×844 mobile](images/panel-mobile.png)
