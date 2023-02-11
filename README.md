# mhz19_async

Python library for communicating with the MH-Z19x sensors for CO2 with asyncio.

This sensor is very badly documented and there are conflicting reports on its working. To make things worse, several
hardware versions exist (including knockoffs), and there are also different firmware versions. **Testing was done on an
MH-Z19B sensor (green PCB) that reports firmware version 0502.** Only the commands that I could test personally are
implemented. Several other commands were tried (including the unlimited/raw CO2 readings, "background" and calibration
data readings), but results are inconsistent with docs or other implementations, and they are left out for now.

Here is a list of references used to implement the protocol:
- [some datasheets](https://github.com/WifWaf/MH-Z19/tree/master/extras/Datasheets), include operational limits
- reverse engineering of the [MH-Z19](https://revspace.nl/MHZ19) and (MH-Z19B)(https://revspace.nl/MH-Z19B) sensors, quite thorough
- https://github.com/WifWaf/MH-Z19: reference Arduino library, several inconsistencies with the above reverse engineering
- [raw braindump](https://docs.google.com/spreadsheets/d/1hSbtUwD5b78hpo37Z1yIxQ3oiaQXUNfCuivmhBwS0-E/edit#gid=495131982) of the author of the Arduino library
- [another blog post](https://habr.com/en/post/401363/) in Russian that sheds some light on CO2 readings and the MODBUS protocol.

Please note that it is unclear whether you can "brick" the sensor, or whether you can fix calibration values once they
are messed up.

## Features

This library provides
- a `class MHZ19Protocol(asyncio.Protocol)` to speak the protocol of the sensor
- an executable that reads commands and prints results in json format.

[Implemented commands](./src/mhz19_async/mhz19.py#L17-L39) documented in source, as well as [response parsing](./src/mhz19_async/mhz19.py#L128-L145). TODO better docs and pypi installation. Use [`__main__.py`](./src/__main__.py) is your reference for using the library, it is very simple.

Contrary to most implementations, this library makes no attempt to match input commands to responses from the sensor.
This is because the input and output dataframes do not have identifiers that allow a reliable match between requests and
responses. The intended use case is to send periodically CO2 read requests, in order to get readings at a user-defined
time interval.

### Command line operation with json

The module can be run as an executable. It reads commands from stdin and prints data from the sensor, one json per line.
```bash
export DEVICE=/dev/ttyUSB0
python -m mhz19_async "${DEVICE}"
# pipe commands to stdin, receive outputs in stdout
```

Input json fields:
- `command` (required): enum name of the command, or raw int
- `args`: scalar or list of scalars (depends on command)
- `raw_args`: list of 5 byte arguments to be appended as-is to the command (for debugging and hacking).

Output json fields:
- `command`: enum name of the received command, or raw int if not recognized
- `raw`: raw hex string of incoming arguments (6 bytes)
- if parsing is implemented, fields are extracted from the raw data and made available in additional fields
- `error`: error message generated during parsing.

Protocol examples (input json followed by output json):
```json lines
{"command": "GET_CO2_TEMPERATURE"}
{"command": "GET_CO2_TEMPERATURE", "raw": "03553B000000", "CO2": 853, "temperature": 19}

{"command": "SET_ABC", "args": false}
{"command": "SET_ABC", "raw": "010000000000", "ack": true}

{"command": "GET_FIRMWARE_VERSION"}
{"command": "GET_FIRMWARE_VERSION", "raw": "303530320000", "version": "0502"}
```
