import sys
import os
import asyncio
import logging
import time
from typing import List, Dict

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from logic.USEEngine import USEEngine
from logic.TestCtrl import TestCtrl

logging.basicConfig(level=logging.DEBUG)

USE_REAL_HARDWARE = os.name != 'nt'

class MockHWGateway:
    def __init__(self):
        self.sent_frames = []
        self.engine = None
        self._ack_task = None
    
    def SendBytes(self, frames):
        print(f"[MockHWGateway] TX: {frames.hex().upper()}")
        self.sent_frames.extend(frames)
        if self.engine:
            asyncio.create_task(self._delayed_ack())
            
    async def _delayed_ack(self):
        await asyncio.sleep(0.01)
        if self.engine:
            self.engine.HandleHwFeedback(int(time.monotonic() * 1000000), 0x0000)

class MockHWProtect:
    def SetAmplifierEnable(self, enable):
        pass

class MockWsSend:
    def __init__(self):
        self.messages = []
    async def __call__(self, msg):
        self.messages.append(msg)
        print(f"[WS_SEND] {msg}")

async def run_test():
    if USE_REAL_HARDWARE:
        from comms.HWGateway import HWGateway
        gateway = HWGateway()
    else:
        gateway = MockHWGateway()
    if USE_REAL_HARDWARE:
        from logic.HWProtect import HWProtect
        protect = HWProtect()
        protect.SetAmplifierEnable(True)
    else:
        protect = MockHWProtect()
        
    ws_send = MockWsSend()
    
    engine = USEEngine(gateway, None)
    
    original_runStatic = engine.runStatic
    async def debug_runStatic():
        print(f"[Engine] runStatic Node {engine.nodeId}")
        return await original_runStatic()
    engine.runStatic = debug_runStatic
    
    original_runSweep = engine.runSweep
    async def debug_runSweep():
        print(f"[Engine] runSweep Node {engine.nodeId}")
        return await original_runSweep()
    engine.runSweep = debug_runSweep
    

    ctrl = TestCtrl(engine, protect, ws_send)
    gateway.engine = engine
    
    if USE_REAL_HARDWARE:
        asyncio.create_task(gateway.Connect())
        await asyncio.sleep(0.5) # Wait for connect
        
    engine._emit = ctrl.onEvent
    
    asyncio.create_task(engine.coreLoop())

    payload = {
        "module": "ac_test",
        "params": {
            "sys": {
                "mode": 0,
                "changeMode": 1,
                "returnMode": 1,
                "stepTime": 200,
                "logicMask": 255,
            },
            "statics": {
                "0": {"0": [0, 50.0], "1": [1.4142*0, 0.0]}
            },
            "steps": {
                "0": {"1": [10.0, 0.0]}
            },
            "count": 5,
            "payload": {
                "enableStepReset": True,
                "stepResetMode": 1,
                "preTestResetTime": 100,
                "stepResetTime": 200,
                "resetTableData": {
                    "0": {"0": [0.0, 50.0], "1": [1.4142*0, 0.0]}
                }
            }
        }
    }
    
    print("\n--- Sending start command ---")
    await asyncio.sleep(0.1) # Wait for engine to settle in Node 0xFFFF
    await ctrl.startTest(payload)
    
    print("\n--- Wait for forward sweep (Tick 0-5) ---")
    await asyncio.sleep(1.5)
    
    print("\n--- Simulate DI Trip (0 -> 1) ---")
    ts = int(time.monotonic() * 1000000)
    engine.HandleHwFeedback(ts, 0x01) 
    
    print("\n--- Wait for forward sweep to finish and reverse sweep to start ---")
    await asyncio.sleep(3.0)
    
    print("\n--- Result ---")
    if ctrl.activeApi:
        print(f"Test completed.")

if __name__ == '__main__':
    asyncio.run(run_test())
