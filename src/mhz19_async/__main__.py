import asyncio
import json
import signal
import sys
import time

import aiofiles
import serial_asyncio

from .mhz19 import MHZ19Protocol


class MHZ19ProtocolConsole(MHZ19Protocol):
    def __init__(self, loop: asyncio.AbstractEventLoop):
        super().__init__(loop)
        self._last_command_timestamp = float('-inf')

    async def read_input(self, rate: int):
        async for line in aiofiles.stdin:
            line = line.strip()
            if not bool(line):
                continue
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
            self._last_command_timestamp = time.monotonic()
            await asyncio.sleep(1 / rate)

    async def graceful_close(self, grace_time: float):
        await asyncio.sleep(max(grace_time - (time.monotonic() - self._last_command_timestamp), 0))
        self._transport.close()

    def event_received(self, event: dict):
        if isinstance(event['command'], MHZ19Protocol.Codes):
            event['command'] = event['command'].name
        del event['checksum']
        event['raw'] = event['raw'].hex().upper()
        # print() blocks, prevents partial writes and throttles the program.
        print(json.dumps(event))


COMMAND_RATE = 20
SHUTDOWN_GRACE_TIME = 0.2


async def main() -> int:
    loop = asyncio.get_event_loop()
    transport, protocol = await serial_asyncio.create_serial_connection(
        loop, lambda: MHZ19ProtocolConsole(loop), sys.argv[1], exclusive=True, **MHZ19Protocol.SERIAL_OPTIONS)
    await protocol.connected.wait()
    reader_task = asyncio.create_task(protocol.read_input(COMMAND_RATE))
    loop.add_signal_handler(signal.SIGINT, lambda: reader_task.cancel())
    loop.add_signal_handler(signal.SIGTERM, lambda: reader_task.cancel())
    not_done = {reader_task, protocol.eof}
    while bool(not_done):
        done, not_done = await asyncio.wait(not_done, return_when=asyncio.FIRST_COMPLETED)
        if reader_task in done and protocol.eof in not_done:
            not_done.add(asyncio.create_task(protocol.graceful_close(SHUTDOWN_GRACE_TIME)))
        if protocol.eof in done:
            reader_task.cancel()

    # rethrow exception if present, give priority to reader_task
    if not reader_task.cancelled():
        reader_task.result()
    protocol.eof.result()
    return 0


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <serial-device>")
        sys.exit(1)
    sys.exit(asyncio.run(main()))
