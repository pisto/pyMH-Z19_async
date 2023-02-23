from enum import IntEnum
import asyncio
from itertools import islice
from struct import unpack, pack
from typing import Optional

import serial
import serial_asyncio

"""
Protocol is implemented from https://revspace.nl/MH-Z19B and available spreadsheets
(see https://github.com/WifWaf/MH-Z19/tree/master/extras/Datasheets).
"""


class MHZ19Protocol(asyncio.Protocol):
    class Codes(IntEnum):
        GET_CO2 = 0x85
        GET_CLAMPED_CO2_TEMPERATURE = 0x86

        SET_ABC = 0x79  # bool: on/off
        GET_ABC = 0x7D
        SET_CALIBRATION_400PPM = 0x87
        SET_CALIBRATION_SPAN = 0x88  # short: calibration span
        SET_RANGE = 0x99  # int: set max range (one of 2000, 5000, 10000)
        GET_RANGE = 0x9B

        RESET = 0x8D
        GET_FIRMWARE_VERSION = 0xA0
        WRITE_CONFIG_0x000 = 0x80  # byte (offset), int (value): write value in configuration area @ offset + 0x000
        WRITE_CONFIG_0x100 = 0x81  # byte (offset), int (value): write value in configuration area @ offset + 0x100
        WRITE_CONFIG_0x200 = 0x82  # byte (offset), int (value): write value in configuration area @ offset + 0x200
        WRITE_CONFIG_0x300 = 0x83  # byte (offset), int (value): write value in configuration area @ offset + 0x300
        READ_CONFIG_0x000 = 0x90  # byte: read value in configuration area @ offset + 0x000
        READ_CONFIG_0x100 = 0x91  # byte: read value in configuration area @ offset + 0x100
        READ_CONFIG_0x200 = 0x92  # byte: read value in configuration area @ offset + 0x200
        READ_CONFIG_0x300 = 0x93  # byte: read value in configuration area @ offset + 0x300
        # synthetic codes for config area access: extract bits 9-10 from offset and apply to command code
        WRITE_CONFIG = 0x80  # offset < 1024, int (value): write value in configuration area @ offset
        READ_CONFIG = 0x90  # offset < 1024: read value in configuration area @ offset

    SERIAL_OPTIONS = {
        "baudrate": 9600,
        "bytesize": serial.EIGHTBITS,
        "parity": serial.PARITY_NONE,
        "stopbits": serial.STOPBITS_ONE
    }
    MESSAGE_SIZE = 9
    ACK_PAYLOAD = bytes([1, 0, 0, 0, 0, 0])
    TEMPERATURE_OFFSET = 40

    @staticmethod
    def checksum(data: bytes) -> int:
        return ((0xFF - (sum(data) & 0xFF)) + 1) & 0xFF

    def __init__(self):
        self._transport = None
        self._leftover = []
        self._version = None

    @property
    def version(self):
        return self._version

    def send_command(self, command: Codes | int, *args, raw_args: Optional[bytes] = None) -> None:
        codes = MHZ19Protocol.Codes
        message = bytes([0xFF, 1, command])
        if raw_args is not None:
            message += raw_args
        else:
            if command == codes.READ_CONFIG or command == codes.WRITE_CONFIG:
                shift = args[0] >> 8
                assert shift < 4
                command = codes(command + shift)
                message = bytes([0xFF, 1, command])
                args = list(args)
                args[0] &= 0xFF
            match command:
                case codes.SET_ABC:
                    message += pack(">Bxxxx", 0xA0 if args[0] else 0)
                case codes.SET_CALIBRATION_SPAN:
                    message += pack(">Hxxx", args[0])
                case codes.SET_RANGE:
                    assert args[0] in [2000, 5000, 10000]
                    message += pack(">xI", args[0])
                case command if codes.WRITE_CONFIG_0x000 <= command <= codes.WRITE_CONFIG_0x300:
                    message += pack(">BI", *args)
                case command if codes.READ_CONFIG_0x000 <= command <= codes.READ_CONFIG_0x300:
                    message += pack(">Bxxxx", args[0])
                case _:
                    message += pack(">xxxxx")
        message += bytes([MHZ19Protocol.checksum(message[1:])])
        assert len(message) == MHZ19Protocol.MESSAGE_SIZE
        self._transport.write(message)

    def event_received(self, event: dict) -> None:
        ...

    def connection_made(self, transport: serial_asyncio.SerialTransport) -> None:
        self._transport = transport

    def data_received(self, data: bytes) -> None:
        codes = MHZ19Protocol.Codes
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
                event = {'command': next(data), 'raw': bytes(islice(data, 6)), 'checksum': next(data)}
                if header != 0xFF or \
                        event['checksum'] != MHZ19Protocol.checksum(bytes([event['command']]) + event['raw']):
                    # malformed packet, advanced one byte and try to reinterpret
                    next_start = 1
                    continue
                # good packet, discard these bytes from self._leftover
                next_start = MHZ19Protocol.MESSAGE_SIZE
            except StopIteration:
                # not enough data, give up and keep received bytes in self._leftover
                return
            finally:
                self._leftover = self._leftover[next_start:]

            try:
                event['command'] = codes(event['command'])
                _ = tuple([None])
                match event['command']:
                    case codes.GET_ABC:
                        event['ABC'], _ = unpack(">xxxxx?", event['raw']) + _
                    case codes.GET_CLAMPED_CO2_TEMPERATURE:
                        event['CO2'], event['temperature'] = unpack(">HBxxx", event['raw'])
                        event['temperature'] -= MHZ19Protocol.TEMPERATURE_OFFSET
                    case codes.GET_CO2:
                        event['CO2'], _ = unpack(">xxHxx", event['raw']) + _
                    case codes.GET_FIRMWARE_VERSION:
                        event['version'], _ = unpack(">4sxx", event['raw']) + _
                        self._version = event['version'] = event['version'].decode("ascii")
                    case codes.GET_RANGE:
                        event['max'], _ = unpack(">xxHxx", event['raw']) + _
                    case codes.SET_ABC | codes.SET_RANGE:
                        event['ack'] = event['raw'] == MHZ19Protocol.ACK_PAYLOAD
            except Exception as exc:
                event['error'] = str(exc)

            self.event_received(event)
