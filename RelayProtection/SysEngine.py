import asyncio
import os
import time

try:
    os.sched_setaffinity(0, {3})
    print("CPU Affinity locked to Core 3.")
except AttributeError:
    pass

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    print("uvloop successfully activated.")
except ImportError:
    pass

os.environ["USE_LOG_LEVEL"] = "DEBUG"

from comms.HWGateway import HWGateway
from comms.WSGateway import WSGateway
from logic.USEEngine import USEEngine
from logic.TestCtrl import TestCtrl
from logic.FPGACodec import HWCodec
from logic.HWProtect import HWProtect
from utils.SysLogger import GetLogger

logger = GetLogger("SysEngine")


async def main() -> None:
    logger.info("Initialization started...")
    os.system(f"fuser -k {WSGateway.PORT}/tcp >/dev/null 2>&1 || true")
    os.system(f"fuser -k {HWGateway.PORT} >/dev/null 2>&1 || true")
    time.sleep(0.5)

    hwGateway = HWGateway()
    wsGateway = WSGateway()
    hwProtect = HWProtect()

    engine = USEEngine(hwGateway, None)
    hwGateway.engine = engine
    testCtrl = TestCtrl(engine, hwProtect, wsGateway.SendToClient)
    engine._emit = testCtrl.onEvent

    wsGateway.dispatcher = testCtrl
    hwProtect.testCtrl = testCtrl

    asyncio.create_task(hwGateway.Connect())
    await asyncio.sleep(1.0)
    engine.start()

    async with wsGateway.StartServer():
        logger.info("All gateways online. Awaiting commands...")
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("\nCtrl+C detected. Triggering hardware hard-reset...")
        try:
            import serial
            with serial.Serial(HWGateway.PORT, HWGateway.BAUDRATE, timeout=1) as ser:
                ser.write(HWCodec.FRAME_SYS_RESET)
        except Exception as e:
            logger.error(f"Emergency Serial reset failed: {e}")
        finally:
            logger.info("Hardware resettled. Service terminated.")
