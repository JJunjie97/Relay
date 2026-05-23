import asyncio
import os
import time

try:
    os.sched_setaffinity(0, {0, 1, 2})
    print("CPU Affinity locked to Cores 0, 1, 2.")
except AttributeError:
    pass

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    print("uvloop successfully activated.")
except ImportError:
    pass

os.environ["USE_LOG_LEVEL"] = "DEBUG"

from comms.WSGateway import WSGateway
from logic.EngineProxy import EngineProxy
from logic.TestCtrl import TestCtrl
from logic.HWProtect import HWProtect
from utils.SysLogger import GetLogger

logger = GetLogger("SysEngine")


async def main() -> None:
    logger.info("Initialization started...")
    os.system(f"fuser -k {WSGateway.PORT}/tcp >/dev/null 2>&1 || true")
    # Subprocess runs on port 8081, make sure it is clean
    os.system(f"fuser -k 8081/tcp >/dev/null 2>&1 || true")
    time.sleep(0.5)

    wsGateway = WSGateway()
    hwProtect = HWProtect()

    # Use EngineProxy instead of USEEngine
    engine = EngineProxy(None, None)
    testCtrl = TestCtrl(engine, hwProtect, wsGateway.SendToClient)
    engine._emit = testCtrl.onEvent

    wsGateway.dispatcher = testCtrl
    hwProtect.testCtrl = testCtrl

    # Start EngineProxy which launches the HwEngineProcess child process on Core 3
    engine.start()
    await asyncio.sleep(1.0)

    async with wsGateway.StartServer():
        logger.info("All gateways online. Awaiting commands...")
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("\nCtrl+C detected. Terminating system...")
        logger.info("Service terminated.")

