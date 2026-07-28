# EnOcean serial lifecycle hardening

Version `v1.2.4` hardens the serial lifecycle on top of upstream `v1.2.2`:

- stop the `SerialCommunicator` during config-entry unload;
- interrupt pending serial reads/writes, then wait up to one second for the
  worker to exit and close the USB descriptor;
- warn and abort reload if the previous reader remains alive;
- close config-flow probe descriptors;
- remove the unsafe raw packet service;
- declare the vendored library's runtime dependencies and MIT notice;
- cancel climate control timers when their entity is removed;
- remove the accidental dependency on the native `enocean` integration.

This prevents a Home Assistant config-entry reload from creating two readers
for the same EnOcean ESP3 serial stream. The only intentional API removal is
`enocean_custom.send_packet`; normal entities use the internal dispatcher and
are unaffected.