import asyncio
import json
import sys
import time
from asyncio import AbstractEventLoop

import aiofiles
import serial_asyncio

from .mhz19 import MHZ19Protocol


class MHZ19ProtocolConsole(MHZ19Protocol):
    def __init__(self, loop: AbstractEventLoop):
        super().__init__()
        self.reader_task_future = loop.create_future()
        self.last_command_timestamp = float('-inf')

    async def read_input(self):
        async for line in aiofiles.stdin:
            req = json.loads(line)
            command = req['command']
            if isinstance(command, str):
                command = MHZ19Protocol.Codes[req['command']]
            args = req.get('args')
            raw_args = req.get('raw_args')
            if raw_args is not None:
                self.send_command(command, raw_args=raw_args)
            elif isinstance(args, list):
                self.send_command(command, *args)
            else:
                self.send_command(command, args)
            self.last_command_timestamp = time.monotonic()
            # throttle commands to 20/s
            await asyncio.sleep(0.05)

    def event_received(self, event: dict):
        if isinstance(event['command'], MHZ19Protocol.Codes):
            event['command'] = event['command'].name
        del event['checksum']
        event['raw'] = event['raw'].hex().upper()
        # print() blocks, prevents partial writes and throttle the program.
        print(json.dumps(event))

    def connection_made(self, transport: serial_asyncio.SerialTransport) -> None:
        super().connection_made(transport)
        self.reader_task_future.set_result(asyncio.create_task(self.read_input()))

    def connection_lost(self, exc: Exception | None) -> None:
        # TODO PL2302 USB-to-serial doesn't seem to generate disconnect even when unplugging
        if self.reader_task_future.done():
            # interrupt stdin reader when connection is lost
            reader_task = self.reader_task_future.result()
            if not reader_task.done():
                reader_task.cancel(str(exc))
        else:
            self.reader_task_future.cancel(str(exc))
        super().connection_lost(exc)


async def main() -> int:
    loop = asyncio.get_event_loop()
    transport, protocol = await serial_asyncio.create_serial_connection(
        loop, lambda: MHZ19ProtocolConsole(loop), sys.argv[1], exclusive=True, **MHZ19Protocol.SERIAL_OPTIONS)
    await protocol.reader_task_future
    reader_task = protocol.reader_task_future.result()
    # wait for end of stdin, or exception
    await reader_task
    # rethrow exception here if the reader_task was interrupted somehow
    reader_task.result()
    # wait at most 200 ms to retrieve the last response
    await asyncio.sleep(max(0.2 - (time.monotonic() - protocol.last_command_timestamp), 0))
    return 0


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <serial-device>")
        sys.exit(1)
    sys.exit(asyncio.run(main()))
