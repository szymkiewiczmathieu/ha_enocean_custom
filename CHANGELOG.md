# Changelog

## 1.2.4 - 2026-07-28

- Stop and join the serial communicator outside Home Assistant's event loop.
- Abort config-entry reload if the old serial reader remains alive.
- Guarantee serial descriptor closure when the worker exits.
- Bound serial writes and actively cancel pending reads/writes during shutdown.
- Drop malformed outbound packets without terminating the serial worker.
- Close temporary serial descriptors opened by config-flow validation.
- Open the USB communicator in Home Assistant's executor.
- Remove the unsafe raw `send_packet` service.
- Declare all runtime dependencies and enforce a single config entry.
- Cancel periodic climate control callbacks when an entity is removed.
- Remove the accidental dependency on the native `enocean` integration.
- Add deterministic lifecycle tests plus HACS and hassfest CI.

## 1.2.3 - 2026-07-28

- Initial bounded `stop()` and `join()` fix for config-entry unload.
