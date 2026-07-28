# Maison Cador serial unload fix

Version `v1.2.4` hardens the serial lifecycle on top of upstream `v1.2.2`:

- stop the `SerialCommunicator` during config-entry unload;
- wait up to one second for its thread to exit and close the USB descriptor;
- warn and abort reload if the previous reader remains alive;
- close config-flow probe descriptors;
- unregister the raw packet service during unload;
- remove the accidental dependency on the native `enocean` integration.

This prevents a Home Assistant config-entry reload from creating two readers
for the same EnOcean ESP3 serial stream.  The integration domain and all entity,
event, service, and YAML contracts remain unchanged.