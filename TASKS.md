# NodOn ASP-2-1-00 / D2-01-0A run

- [x] Audit release branch, origin/main and open remote branches before editing.
- [x] Keep D2 destination equal to captured actuator ID and sender equal to dongle Base ID; channel is configured and bounded (0-31).
- [x] Ensure ESP3 ACK never updates switch state; state changes only on matching D2-01 CMD 0x4 feedback.
- [x] Preserve PC2 identity and channel-aware unique IDs; no reset, deletion, deployment, or radio command performed.
- [x] Document bounded NodOn evidence and explicitly avoid unsupported manufacturer inference.
- [x] Run HA-dependent tests in isolated `.venv-ha` with Home Assistant 2026.7.3, pyserial, BeautifulSoup4 and lxml; run Ruff (baseline currently reports pre-existing legacy violations).
- [ ] Run Hassfest and HACS validators; this host has no Docker daemon and no local HACS validator checkout.
- [ ] Perform physical commissioning/switching only after Mathieu confirms the connected load is safe; this run does not claim hardware success.
- [ ] Independent review and user authorization remain required before push, merge, release, or deployment.

## Guided physical commissioning protocol (not executed in this run)

1. Confirm the connected load is disconnected or demonstrably safe; do not test a
   mains-fed load without Mathieu's explicit confirmation.
2. Save the Home Assistant backup and record the current PC2 entity/options and
   captured EURID `01:A2:FE:F8`; do not reset the module or remove PC2.
3. Keep the actuator destination as `01:A2:FE:F8`, dongle Base ID as sender,
   and channel exactly `0`. Verify the generated D2-01 command before transmit.
4. Trigger one ON, wait for the matching D2-01 CMD `0x4` status from that sender
   and channel, then trigger one OFF and wait for the corresponding status.
   ESP3 ACK, timeout, or rejected transport alone is never a state result.
5. If no matching feedback arrives, stop radio tests, restore the backup if the
   entity was changed, and report unknown state; do not retry indefinitely.
6. Verify PC2 associations and the physical output only after the safe-load
   confirmation. Roll back by restoring the backup and reloading the prior
   integration version; this run performed no deployment or radio transmission.

Evidence from this run: `python -m compileall -q custom_components tests` passed; `git diff --check` passed; `tests.test_d2_status` collected 5 tests but skipped all because Home Assistant is not installed.
