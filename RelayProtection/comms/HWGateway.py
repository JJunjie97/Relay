import asyncio
import serial_asyncio
from typing import Optional
from utils.SysLogger import GetLogger
from logic.FPGACodec import HWCodec

class HWGateway:
    PORT = '/dev/ttyAMA0'
    BAUDRATE = 230400
    def __init__(self, port: str = PORT, baudrate: int = BAUDRATE):
        self.logger = GetLogger("HWGateway")
        self.port = port
        self.baudrate = baudrate
        self.engine = None
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None

    async def Connect(self) -> None:
        self.logger.info(f"Connecting to {self.port} at {self.baudrate} baud...")
        try:
            self.reader, self.writer = await serial_asyncio.open_serial_connection(url=self.port, baudrate=self.baudrate)
            self.logger.info(f"Connected to {self.port}")
        except Exception as e:
            self.logger.error(f"Connection failed {self.port}. Reason: {e}")
            return
        await self._ListenLoop()

    async def _ListenLoop(self) -> None:
        readUntil, readExactly = self.reader.readuntil, self.reader.readexactly
        parse, feedback = HWCodec.ParseFeedbackFrame, self.engine.HandleHwFeedback
        SYNC = b'\x55'
        while True:
            try:
                await readUntil(SYNC)
                feedback(*parse(await readExactly(6)))
            except asyncio.LimitOverrunError:
                self.logger.warning("High noise detected, safe cleared overloaded buffer.")
                await self.reader.read(65536)
            except asyncio.exceptions.IncompleteReadError:
                self.logger.warning("Incomplete read error. Recovering...")
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Uncaught read exception: {e}")
                await asyncio.sleep(0.01)

    def SendBytes(self, byteData: bytes) -> None:
        self.writer.write(byteData)