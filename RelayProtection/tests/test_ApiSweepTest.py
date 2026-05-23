"""
test_ApiSweepTest.py — 扫频测试 API 验证脚本

通过完整的 TestCtrl → USEEngine 栈测试 ApiSweepTest 的所有工作模式。
支持真机硬件和 Mock 模拟两种运行环境。

Usage:
    python v3/tests/test_ApiSweepTest.py [full|single|reset|pre|all]
    
    默认: all
"""

import sys
import os
import asyncio
import logging
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from logic.USEEngine import USEEngine
from logic.TestCtrl import TestCtrl

logging.basicConfig(level=logging.DEBUG)

USE_REAL_HARDWARE = os.getenv("USE_REAL_HARDWARE", "True").lower() in ("true", "1", "yes")


# ── Mock classes ──

class MockHWGateway:
    def __init__(self):
        self.engine = None

    def SendBytes(self, frames):
        if self.engine:
            asyncio.create_task(self._ack())

    async def _ack(self):
        await asyncio.sleep(0.005)
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
        t = msg.get("type", "")
        if t == "report":
            print(f"\n[WS] ═══ REPORT ═══")
            for k, v in msg.get("data", {}).items():
                print(f"  {k}: {v}")
            print(f"[WS] ═══════════════\n")
        elif t == "stop":
            print(f"[WS] STOP (module={msg.get('module', '?')})")
        elif t == "error":
            print(f"[WS] ERROR: {msg.get('msg', '')}")
        elif t == "value_update" and "static" in msg:
            s = msg["static"]
            ch0 = s.get("0", None)
            if ch0:
                print(f"[WS] ch0={ch0}")


# ── Engine setup ──

async def setup_engine():
    """Create and configure the engine stack. Returns (engine, ctrl, ws_send)."""
    if USE_REAL_HARDWARE:
        from comms.HWGateway import HWGateway
        gateway = HWGateway()
    else:
        gateway = MockHWGateway()

    protect = MockHWProtect()
    ws_send = MockWsSend()
    engine = USEEngine(gateway, None)
    ctrl = TestCtrl(engine, protect, ws_send)
    gateway.engine = engine

    if USE_REAL_HARDWARE:
        asyncio.create_task(gateway.Connect())
        await asyncio.sleep(0.5)

    engine._emit = ctrl.onEvent

    # TX frame logging with command decoding
    _SYS_NAMES = {0x00: "START", 0x01: "STOP", 0x04: "RESET", 0x05: "UPDATE",
                  0x06: "SYNC", 0x20: "SET_DBNC", 0x21: "SET_DO"}
    _DDS_NAMES = {0x10: "WR_SHADOW", 0x11: "WR_STAGE",
                  0x14: "STEP_SHADOW", 0x15: "STEP_STAGE",
                  0x1F: "PHASE_GATE"}
    _original_send = gateway.SendBytes

    def _logged_send(data):
        if len(data) == 4 and data[0] == 0x5A:
            cmd = data[1]
            name = _SYS_NAMES.get(cmd, f"0x{cmd:02X}")
            param = data[2]
            print(f"[TX] SYS {name}" + (f" p={param}" if param else ""))
        elif len(data) == 12 and data[0] == 0xA5:
            cmd = data[1]
            name = _DDS_NAMES.get(cmd, f"0x{cmd:02X}")
            layer = (data[2] + 1) & 0xFF
            ch_mask = (data[3] << 8) | data[4]
            amp = (data[5] << 8) | data[6]
            phase = (data[7] << 24) | (data[8] << 16) | (data[9] << 8) | data[10]
            chs = [str(i) for i in range(16) if ch_mask & (1 << i)]
            ch_str = ",".join(chs) if len(chs) <= 4 else f"ALL({ch_mask:04X})"
            print(f"[TX] DDS {name} L{layer} ch=[{ch_str}] amp=0x{amp:04X} phase=0x{phase:08X}")
        else:
            print(f"[TX] RAW {data.hex().upper()}")
        _original_send(data)

    gateway.SendBytes = _logged_send
    engine._send = _logged_send

    asyncio.create_task(engine.coreLoop())
    await asyncio.sleep(0.1)

    return engine, ctrl, ws_send


# ── Test cases ──

async def test_full_sweep(engine, ctrl, ws):
    """changeMode=1, returnMode=1: full forward 5 steps → full reverse 5 steps.
    
    ch0: 10V → 35V (5 steps of +5V) → 35V → 10V (5 reverse steps)
    Expected: smooth ramp up, smooth ramp down, report with no trip/return.
    """
    print("\n" + "=" * 60)
    print("  TEST: Full Sweep (changeMode=1, returnMode=1)")
    print("  ch0: 10V → 35V (5×+5V) → 35V → 10V (5×-5V)")
    print("=" * 60)

    msg = {
        "cmd": "start",
        "module": "ac_test",
        "params": {
            "sys": {
                "changeMode": 1,
                "returnMode": 1,
                "stepTime": 500,
                "logicMask": 255,
            },
            "statics": {"0": {"1": [10.0, 0.0]}},
            "steps": {"0": {"1": [5.0, 0.0]}},
            "count": 5,
            "payload": {}
        }
    }

    await ctrl.startTest(msg)
    # 0.5s preheat + 5*0.5s forward + 5*0.5s reverse + margin
    total = 0.5 + 5 * 0.5 + 5 * 0.5 + 1.0
    print(f"\n>>> Running for {total:.1f}s. Watch ch0 amplitude rise then fall...")
    await asyncio.sleep(total)
    print(">>> Full sweep test done.\n")


async def test_single_direction(engine, ctrl, ws):
    """changeMode=0: forward sweep only, 10 steps, DI/countOver → stop.
    
    ch0: 10V → 60V (10 steps of +5V)
    Expected: smooth ramp up, then stop. Report with no trip (mock has no DI).
    """
    print("\n" + "=" * 60)
    print("  TEST: Single Direction (changeMode=0)")
    print("  ch0: 10V → 60V (10×+5V)")
    print("=" * 60)

    msg = {
        "cmd": "start",
        "module": "ac_test",
        "params": {
            "sys": {
                "changeMode": 0,
                "returnMode": 0,
                "stepTime": 500,
                "logicMask": 255,
            },
            "statics": {"0": {"1": [10.0, 0.0]}},
            "steps": {"0": {"1": [5.0, 0.0]}},
            "count": 10,
            "payload": {}
        }
    }

    await ctrl.startTest(msg)
    total = 0.5 + 10 * 0.5 + 1.0
    print(f"\n>>> Running for {total:.1f}s. Watch ch0 amplitude rise...")
    await asyncio.sleep(total)
    print(">>> Single direction test done.\n")


async def test_with_reset(engine, ctrl, ws):
    """mode=3: step reset sweep, 5 steps with 200ms reset intervals.
    
    ch0: 10V, +1V steps, 200ms reset to 0V between steps.
    Expected: sawtooth waveform — reset to 0, jump to value, reset to 0, ...
    """
    print("\n" + "=" * 60)
    print("  TEST: Step Reset Sweep (mode=3)")
    print("  ch0: 10V, +1V steps, 200ms reset to 0V between steps")
    print("=" * 60)

    msg = {
        "cmd": "start",
        "module": "ac_test",
        "params": {
            "sys": {
                "changeMode": 0,
                "returnMode": 0,
                "stepTime": 500,
                "logicMask": 255,
            },
            "statics": {"0": {"1": [10.0, 0.0]}},
            "steps": {"0": {"1": [10.0, 0.0]}},
            "count": 5,
            "payload": {
                "enableStepReset": True,
                "stepResetTime": 200,
                "resetTableData": {}
            }
        }
    }

    await ctrl.startTest(msg)
    total = 0.5 + 5 * (0.5 + 0.2) + 1.0
    print(f"\n>>> Running for {total:.1f}s. Watch ch0 step with reset pauses...")
    await asyncio.sleep(total)
    print(">>> Step reset test done.\n")


async def test_pre_test_reset(engine, ctrl, ws):
    """Pre-test reset 1s → full sweep forward+reverse.
    
    1s at 0V reset → 10V+5V*5 forward → reverse back
    Expected: flat 0V for 1s, then ramp up, then ramp down.
    """
    print("\n" + "=" * 60)
    print("  TEST: Pre-test Reset + Full Sweep")
    print("  1s reset to 0V → 10V+5V×5 forward → reverse")
    print("=" * 60)

    msg = {
        "cmd": "start",
        "module": "ac_test",
        "params": {
            "sys": {
                "changeMode": 1,
                "returnMode": 1,
                "stepTime": 500,
                "logicMask": 255,
            },
            "statics": {"0": {"1": [10.0, 0.0]}},
            "steps": {"0": {"1": [5.0, 0.0]}},
            "count": 5,
            "payload": {
                "enablePreTestReset": True,
                "preTestResetTime": 2000,
                "resetTableData": {"0": {"1": [30.0, 0.0]}}
            }
        }
    }

    await ctrl.startTest(msg)
    total = 0.5 + 1.0 + 5 * 0.5 + 5 * 0.5 + 1.0
    print(f"\n>>> Running for {total:.1f}s. 1s reset → sweep up → sweep down...")
    await asyncio.sleep(total)
    print(">>> Pre-test reset + full sweep done.\n")


async def test_bidirectional_action_return(engine, ctrl, ws):
    """changeMode=1, returnMode=0, with step reset (mode=3).
    
    ch0: 10V, +2V steps, 5 steps, step reset to 0V, 100ms reset interval.
    On real HW: forward until DI trip → hardware jumps to reverse node.
    On mock: forward completes (no DI), countOver → 0xFFFF → stop.
    """
    print("\n" + "=" * 60)
    print("  TEST: Bidirectional Action Return with Reset (changeMode=1, returnMode=0, mode=3)")
    print("  ch0: 10V, +2V steps, step reset to 0V, 100ms reset")
    print("=" * 60)

    msg = {
        "cmd": "start",
        "module": "ac_test",
        "params": {
            "sys": {
                "changeMode": 1,
                "returnMode": 0,
                "stepTime": 500,
                "logicMask": 255,
            },
            "statics": {"0": {"1": [10.0, 0.0]}},
            "steps": {"0": {"1": [10.0, 0.0]}},
            "count": 5,
            "payload": {
                "enableStepReset": True,
                "stepResetTime": 100,
                "stepResetMode": 1,
                "resetTableData": {}
            }
        }
    }

    await ctrl.startTest(msg)
    # Forward: 5 * (0.5 + 0.1) = 3.0s. If no DI, countOver → 0xFFFF (stop)
    total = 0.5 + 5 * (0.5 + 0.1) + 1.0
    print(f"\n>>> Running for {total:.1f}s. Forward sweep with resets...")
    await asyncio.sleep(total)
    print(">>> Bidirectional action return test done.\n")


# ── Main ──

async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    engine, ctrl, ws = await setup_engine()

    try:
        if mode == "full":
            await test_full_sweep(engine, ctrl, ws)
        elif mode == "single":
            await test_single_direction(engine, ctrl, ws)
        elif mode == "reset":
            await test_with_reset(engine, ctrl, ws)
        elif mode == "pre":
            await test_pre_test_reset(engine, ctrl, ws)
        elif mode == "action":
            await test_bidirectional_action_return(engine, ctrl, ws)
        elif mode == "all":
            await test_full_sweep(engine, ctrl, ws)
            await asyncio.sleep(1.0)
            await test_single_direction(engine, ctrl, ws)
            await asyncio.sleep(1.0)
            await test_with_reset(engine, ctrl, ws)
            await asyncio.sleep(1.0)
            await test_pre_test_reset(engine, ctrl, ws)
            await asyncio.sleep(1.0)
            await test_bidirectional_action_return(engine, ctrl, ws)
        else:
            print(f"Unknown mode: {mode}")
            print("Usage: python test_ApiSweepTest.py [full|single|reset|pre|action|all]")
    except Exception as e:
        import traceback
        traceback.print_exc()
    finally:
        print("Cleaning up: stopping test and engine...")
        ctrl.stopTest()
        await asyncio.sleep(0.3)


if __name__ == "__main__":
    asyncio.run(main())
