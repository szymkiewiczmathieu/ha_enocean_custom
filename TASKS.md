# NodOn ASP-2-1-00 / D2-01-0A run

- [x] Audit release branch, origin/main and open remote branches before editing.
- [x] Keep D2 destination equal to captured actuator ID and sender equal to dongle Base ID; channel is configured and bounded (0-31).
- [x] Ensure ESP3 ACK never updates switch state; state changes only on matching D2-01 CMD 0x4 feedback.
- [x] Preserve PC2 identity and channel-aware unique IDs; no reset, deletion, deployment, or radio command performed.
- [x] Document bounded NodOn evidence and explicitly avoid unsupported manufacturer inference.
- [ ] Run HA-dependent tests, Ruff, Hassfest and HACS in an environment with their dependencies.
- [ ] Perform physical commissioning/switching only after Mathieu confirms the connected load is safe; this run does not claim hardware success.
- [ ] Independent review and user authorization remain required before push, merge, release, or deployment.

Evidence from this run: `python -m compileall -q custom_components tests` passed; `git diff --check` passed; `tests.test_d2_status` collected 5 tests but skipped all because Home Assistant is not installed.
