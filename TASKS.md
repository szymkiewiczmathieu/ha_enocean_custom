# NodOn ASP-2-1-00 / D2-01-0A run

- [x] Audit release branch, origin/main and open remote branches before editing.
- [x] Keep D2 destination equal to captured actuator ID and sender equal to dongle Base ID; ASP D2-01-0A is explicitly restricted to actuator channel 0 (other EEPs retain generic 0-31 validation).
- [x] Ensure ESP3 ACK never updates switch state; state changes only on matching D2-01 CMD 0x4 feedback.
- [x] Preserve PC2 identity and channel-aware unique IDs; no reset, deletion, deployment, or radio command performed.
- [x] Document bounded NodOn evidence and explicitly avoid unsupported manufacturer inference.
- [x] Run HA-dependent tests in isolated `.venv-ha` with Home Assistant 2026.7.3, pyserial, BeautifulSoup4 and lxml; targeted suite (including persisted UI options and YAML/setup boundaries) passes 59/59; Ruff check/format pass on touched targets.
- [x] Enforce the commissioning boundary in the real options-flow path: matching feedback requires the exact sender, channel, D2 CMD 0x4 and requested ON output; ACK, timeout, wrong channel, and OFF feedback cannot complete pairing. Pure boundary regression tests cover this contract.
- [x] Enforce D2-01-0A channel 0 at YAML and persisted UI boundaries; read EEP from `radio_metadata.eep` during config-entry setup; other EEPs retain channels 0-31.
- [ ] Run Hassfest and HACS validators; local execution attempted 2026-10-06 using the exact workflow-pinned Docker images. Hassfest started successfully but scanned the installed Home Assistant tree as well as this custom integration and exited 1 with 1466 unrelated invalid core integrations; the output does not provide a clean repository-only pass. HACS image started but exited 1 because no `GITHUB_TOKEN` was available. No CI result or exact-head check is claimed for this SHA.
- [x] Audit the deployed HA version through the authorized read-only HA path: `update.home_assistant_core_update` reports installed/latest Core `2026.9.4`; entity inventory is reachable (530 entities), including `switch.pc2` currently `unknown`. No service call, radio command, or deployment was performed.
- [ ] Perform physical commissioning/switching only after Mathieu confirms the connected load is safe; this run does not claim hardware success.
- [ ] Independent review remains required; push, merge, release, and deployment remain unperformed.

## Official commissioning boundary

The NodOn ASP-2-1-00 documentation identifies the SmartPlug as a bidirectional
D2-01-0A actuator and requires a teach-in/association action before normal
control. The software path is deliberately bounded: the options flow exposes the
implemented `Pair an actuator (guided)` and `Commission an existing D2 relay
(assisted)` actions, persists the existing PC2 switch/options, and sends directed
D2 commands only through the normal switch path. It does not claim that a
commissioning association was persisted: the flow has no generic `Transmit`
dialog or association-verification result. Any success claim still requires a
matching D2-01 status telegram (same sender/channel); transport ACK alone is
rejected as proof. The action is bounded by a timeout and offers keep/rollback
rather than deleting or resetting the actuator. This contract is tested by
`tests.test_pairing_wizard` and `tests.test_ute_teach_in_policy`; no physical
teach-in was run here.

Sources: NodOn ASP-2-1-00 product documentation
(https://support.nodon.fr/support/solutions/articles/150000192097-prise-intelligente-enocean-asp-2-1-00-)
and the ASP-2-1-x0 manual
(https://doc.eedomus.com/files/NodOn_ASP-2-1-x0_EnOcean_20141118_FR.pdf).
The sources establish the product/EEP and commissioning requirement; they do
not prove that EURID `01:A2:FE:F8` is NodOn, so attribution remains unknown.

## Guided physical commissioning protocol (not executed in this run)

1. Confirm the connected load is disconnected or demonstrably safe; do not test a
   mains-fed load without Mathieu's explicit confirmation.
2. Save the Home Assistant backup and record the current PC2 entity/options and
   captured EURID `01:A2:FE:F8`; do not reset the module or remove PC2.
3. In Home Assistant open `Settings → Devices & services → EnOcean → PC2 →
   Configure`, choose the captured device `01:A2:FE:F8`, and select the actual
   `Pair an actuator (guided)` or `Commission an existing D2 relay (assisted)`
   action. The flow sends the directed D2 command only when the user explicitly
   continues; it does not send UTE automatically, does not expose a generic
   `Transmit` dialog, and does not claim that an association was persisted. Put
   the ASP in the physical teach-in mode described by the NodOn manual before
   using the flow. The observable success criterion is a matching D2-01 status
   telegram in the integration log; an ESP3 transport ACK alone is not
   confirmation. Record controller and actuator IDs before leaving teach-in.
4. Keep the actuator destination as `01:A2:FE:F8`, dongle Base ID as sender,
   and channel exactly `0`. The flow's generated D2 command is directed to
   that destination and channel; it must reject a non-zero ASP channel or a
   destination mismatch before radio I/O. A persisted HA option row is only
   configuration evidence, not proof of a NodOn association. These boundaries
   are covered by the pairing and channel-policy tests.
5. Trigger one ON, wait for the matching D2-01 CMD `0x4` status from that sender
   and channel, then trigger one OFF and wait for the corresponding status.
   ESP3 ACK, timeout, or rejected transport alone is never a state result.
6. If no matching feedback arrives, stop radio tests, restore the backup if the
   entity was changed, and report unknown state; do not retry indefinitely.
7. Verify PC2 associations and the physical output only after the safe-load
   confirmation. Roll back by restoring the backup and reloading the prior
   integration version; this run performed no deployment or radio transmission.

The association step is a commissioning prerequisite, not an automatic action
of this integration. UTE teach-in is an alternative only when the actual device
and controller documentation explicitly supports it; it must not be inferred
from D2-01-0A or performed silently.

Evidence from this run: `.venv-ha/bin/python -B -m unittest tests.test_d2_channel_policy tests.test_d2_protocol_pure tests.test_d2_status tests.test_pairing_wizard tests.test_ute_teach_in_policy tests.test_d2_ui_options tests.test_d2_setup_boundaries -v` passed (59 tests, zero skipped); `.venv-ha/bin/ruff check` and `ruff format --check` passed on the 9 checked Python targets; `git diff --check` passed. Verification was rerun on 2026-10-06 after the review delta, with the same 59/59 result. A repository-wide Ruff run still reports 123 pre-existing legacy/vendor violations outside the changed targets. Hassfest/HACS and physical commissioning remain pending and no radio/deployment was performed.
