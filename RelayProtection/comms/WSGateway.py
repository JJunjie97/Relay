import asyncio
import orjson
import websockets
from typing import Optional
from utils.SysLogger import GetLogger

class WSGateway:
    HOST = '0.0.0.0'
    PORT = 8080
    def __init__(self, host: str = HOST, port: int = PORT):
        self.logger = GetLogger("WSGateway")
        self.host = host
        self.port = port
        self.dispatcher = None
        self.activeClient: Optional[websockets.WebSocketServerProtocol] = None
        self.txQueue = asyncio.Queue(maxsize=1000)

    def StartServer(self):
        asyncio.create_task(self._TxWorkerLoop())
        self.logger.info(f"Service listening on ws://{self.host}:{self.port}")
        return websockets.serve(
            self.Handler, 
            self.host, 
            self.port,
            ping_interval=3.0,  
            ping_timeout=6.0,
            compression=None,
            max_size=1048576
        )

    async def Handler(self, websocket: websockets.WebSocketServerProtocol) -> None:
        if self.activeClient is not None:
            self.logger.warning("Preempting old connection for rapid F5 support.")
            try: 
                await asyncio.wait_for(self.activeClient.close(), timeout=1.0)
            except Exception: 
                pass
                
        self.activeClient = websocket
        self.logger.info(f"Client connected from {websocket.remote_address}")
        
        try:
            async for message in websocket:
                self.logger.debug(f"[WS RX] {message}")
                if self.dispatcher:
                    try:
                        await self.dispatcher.HandleCommand(orjson.loads(message))
                    except orjson.JSONDecodeError:
                        self.logger.warning("Dropped malformed JSON message.")
                    except Exception as e:
                        self.logger.error(f"Dispatcher error: {e}")
        except websockets.exceptions.ConnectionClosed:
            self.logger.info("Connection closed by remote client.")
        except Exception as e:
            self.logger.error(f"Unexpected edge error: {e}")
        finally:
            if self.activeClient == websocket:
                self.activeClient = None
                self.logger.warning("Active client disconnected. Triggering emergency anchor.")
                if self.dispatcher:
                    await self.dispatcher.HandleCommand({"cmd": "stop"})
            else:
                self.logger.info("Old preempted connection closed. Ignoring emergency trigger.")

    async def _TxWorkerLoop(self):
        try:
            while True:
                try:
                    first = await self.txQueue.get()
                    batch = [first]
                    self.txQueue.task_done()
                    
                    while not self.txQueue.empty() and len(batch) < 100:
                        batch.append(self.txQueue.get_nowait())
                        self.txQueue.task_done()
                    
                    client = self.activeClient
                    if client:
                        send = client.send
                        dumps = orjson.dumps
                        for frame in batch:
                            data = dumps(frame).decode()
                            self.logger.debug(f"[WS TX] {data}")
                            await send(data)
                        
                except websockets.exceptions.ConnectionClosed:
                    self.activeClient = None
                except Exception as e:
                    self.logger.error(f"WS TX Worker error: {e}")
        except asyncio.CancelledError:
            self.logger.info("WS TX Worker gracefully cancelled.")

    async def SendToClient(self, payloadDict: dict) -> None:
        try:
            self.txQueue.put_nowait(payloadDict)
        except asyncio.QueueFull:
            try:
                await asyncio.wait_for(self.txQueue.put(payloadDict), timeout=1.0)
            except asyncio.TimeoutError:
                self.logger.warning("TX Queue full. Dropping message to prevent DoS.")