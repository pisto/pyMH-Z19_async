from enum import IntEnum
import asyncio
from itertools import islice
from struct import unpack, pack
from typing import Optional

import serial_asyncio

"""
Protocol is implemented from https://revspace.nl/MH-Z19B and available spreadsheets
(see https://github.com/WifWaf/MH-Z19/tree/master/extras/Datasheets).
"""


class MHZ19CODES(IntEnum):
    SET_ABC = 0x79                  # bool: on/off
    GET_ABC = 0x7D
    SET_MEASURE_INTERVAL = 0x7E     # short: measurement cycle interval in seconds
    WRITE_CONFIG = 0x80             # offset < 1024, byte[4] (value): write value in configuration area @ offset
    WRITE_CONFIG_0x000 = 0x80       # byte (offset), byte[4] (value): write value in configuration area @ offset + 0x000
    WRITE_CONFIG_0x100 = 0x81       # byte (offset), byte[4] (value): write value in configuration area @ offset + 0x100
    WRITE_CONFIG_0x200 = 0x82       # byte (offset), byte[4] (value): write value in configuration area @ offset + 0x200
    WRITE_CONFIG_0x300 = 0x83       # byte (offset), byte[4] (value): write value in configuration area @ offset + 0x300
    RESET = 0x8D
    READ_CONFIG = 0x90              # offset < 1024: read value in configuration area @ offset
    READ_CONFIG_0x000 = 0x90        # byte: read value in configuration area @ offset + 0x000
    READ_CONFIG_0x100 = 0x91        # byte: read value in configuration area @ offset + 0x100
    READ_CONFIG_0x200 = 0x92        # byte: read value in configuration area @ offset + 0x200
    READ_CONFIG_0x300 = 0x93        # byte: read value in configuration area @ offset + 0x300
    GET_FIRMWARE_VERSION = 0xA0


class MHZ19Protocol(asyncio.Protocol):

    def __init__(self):
        self._transport = None
        self._leftover = []

    @staticmethod
    def checksum(data: bytes) -> int:
        return ((0xFF - (sum(data) & 0xFF)) + 1) & 0xFF

    def command(self, code: MHZ19CODES, *args, raw: Optional[bytes] = None) -> None:
        message = bytes([0xFF, 1, code])
        if raw is not None:
            message += raw
        else:
            # synthetic codes for config area access: extract bits 9-10 from offset and apply to command code
            if code == MHZ19CODES.READ_CONFIG or code == MHZ19CODES.WRITE_CONFIG:
                code = MHZ19CODES(code + (args[0] >> 8))
                args = list(args)
                args[0] &= 0xFF
                message = bytes([0xFF, 1, int(code)])
            match code:
                case MHZ19CODES.SET_ABC:
                    message += pack("Bxxxx", 0xA0 if args[0] else 0)
                case MHZ19CODES.SET_MEASURE_INTERVAL:
                    message += pack("BHxx", 2, args[0])
                case code if MHZ19CODES.WRITE_CONFIG_0x000 <= code <= MHZ19CODES.WRITE_CONFIG_0x300:
                    message += pack("BI", args[0], args[1])
                case code if MHZ19CODES.READ_CONFIG_0x000 <= code <= MHZ19CODES.READ_CONFIG_0x300:
                    message += pack("Bxxxx", args[0])
                case _:
                    message += pack("xxxxx")
        message += bytes([MHZ19Protocol.checksum(message[1:])])
        assert len(message) == 9
        self._transport.write(message)

    def connection_made(self, transport: serial_asyncio.SerialTransport) -> None:
        self._transport = transport

    def data_received(self, data: bytes) -> None:
        self._leftover += data
        """
        Try parsing the input data without assuming boundaries: check the message for a valid
        header (0xFF) and checksum. If parsing fails, advance one byte forward and retry.
        If not enough bytes are available, return (wait for more data).
        """
        while True:
            next_start = 0
            try:
                data = iter(self._leftover)
                header = next(data)
                event = {'code': next(data), 'raw': bytes(islice(data, 6)), 'checksum': next(data)}
                if header != 0xFF or \
                        event['checksum'] != MHZ19Protocol.checksum(bytes([event['code']]) + event['raw']):
                    next_start = 1
                    continue
                next_start = 9
            except StopIteration:
                return
            finally:
                self._leftover = self._leftover[next_start:]

            try:
                event['code'] = MHZ19CODES(event['code'])
                match event['code']:
                    case MHZ19CODES.GET_ABC:
                        event['ABC'] = unpack("xxxxx?", event['raw'])[0]
                    case MHZ19CODES.GET_FIRMWARE_VERSION:
                        event['version'] = unpack("4sxx", event['raw'])[0].decode("ascii")
            except Exception as exc:
                event['parse_error'] = str(exc)
            self.event_received(event)

    def event_received(self, event: dict) -> None:
        ...
