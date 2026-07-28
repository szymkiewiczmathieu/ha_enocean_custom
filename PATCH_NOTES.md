# Maison Cador serial unload fix

Version `v1.2.3` adds one bounded lifecycle fix to upstream `v1.2.2`:

- stop the `SerialCommunicator` during config-entry unload;
- wait up to one second for its thread to exit and close the USB descriptor;
- warn if the thread remains alive.

This prevents a Home Assistant config-entry reload from creating two readers
for the same EnOcean ESP3 serial stream.  The integration domain and all entity,
event, service, and YAML contracts remain unchanged.