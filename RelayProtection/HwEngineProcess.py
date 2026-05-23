import asyncio
import os
import sys
import pickle
import time

# Linux-level orphan process prevention: auto kill self with SIGKILL when parent process dies
try:
    import ctypes
    import signal
    # PR_SET_PDEATHSIG = 1
    libc = ctypes.CDLL("libc.so.6")
    libc.prctl(1, signal.SIGKILL)
except Exception:
    pass


# Set Core 3 Affinity
try:
    os.sched_setaffinity(0, {3})
    print("[HwEngine] CPU affinity locked to Core 3.")
except AttributeError:
    pass

# Import uvloop
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    print("[HwEngine] uvloop successfully activated.")
except ImportError:
    pass

USE_REAL_HARDWARE = os.getenv("USE_REAL_HARDWARE", "True").lower() in ("true", "1", "yes")

# Mock classes for offline simulation testing
class MockHWGateway:
    def __init__(self):
        self.engine = None
    async def Connect(self):
        print("[HwEngine] Mocked Serial Port connected.")
    def SendBytes(self, frames):
        print(f"[MockHW] TX: {frames.hex().upper()}")
        if self.engine:
            asyncio.create_task(self._ack())
    async def _ack(self):
        await asyncio.sleep(0.005)
        if self.engine:
            self.engine.HandleHwFeedback(int(time.monotonic() * 1000000), 0x0000)

from logic.USEEngine import USEEngine

async def handle_client(reader, writer):
    print("[HwEngine] Client connected from parent process.")
    
    if USE_REAL_HARDWARE:
        from comms.HWGateway import HWGateway
        gateway = HWGateway()
    else:
        gateway = MockHWGateway()
        
    engine = USEEngine(gateway, None)
    gateway.engine = engine

    # Wire the engine's _emit callback to send event packets back to the parent process
    async def send_event(evt):
        try:
            data = pickle.dumps(("on_event", evt))
            writer.write(len(data).to_bytes(4, 'big') + data)
            await writer.drain()
        except Exception as e:
            print(f"[HwEngine] Failed to send event to parent: {e}")

    engine._emit = send_event

    # Connect hardware serial port
    asyncio.create_task(gateway.Connect())
    await asyncio.sleep(0.5)
    engine.start()

    try:
        while True:
            # Read 4-byte big-endian length header
            len_bytes = await reader.readexactly(4)
            length = int.from_bytes(len_bytes, 'big')
            
            # Read length bytes of pickled payload
            payload_bytes = await reader.readexactly(length)
            cmd, args = pickle.loads(payload_bytes)

            if cmd == "upsert_nodes":
                engine.upsertNodes(args)
            elif cmd == "manual_trig":
                engine.manualTrig(args)
            elif cmd == "set_debounce":
                engine.setDebounce(args)
            elif cmd == "start":
                # engine is already started during initialization
                pass
    except asyncio.IncompleteReadError:
        print("[HwEngine] Parent process disconnected. Exiting...")
    except Exception as e:
        print(f"[HwEngine] Error in handler loop: {e}")
        try:
            data = pickle.dumps(("error", str(e)))
            writer.write(len(data).to_bytes(4, 'big') + data)
            await writer.drain()
        except Exception:
            pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        print("[HwEngine] Shutdown complete.")
        sys.exit(0)

async def main():
    server = await asyncio.start_server(handle_client, '127.0.0.1', 8081, reuse_port=True)
    print("[HwEngine] IPC server listening on 127.0.0.1:8081...")
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("[HwEngine] Terminated by KeyboardInterrupt.")
