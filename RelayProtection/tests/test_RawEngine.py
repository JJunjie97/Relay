"""
test_RawEngine.py — Direct USEEngine waveform mode tester.

Bypasses ApiSweepTest entirely. Constructs register-level ApiNodeData
and feeds it through ApiRawTest → TestCtrl → USEEngine to verify each
of the 4 waveform modes produces correct hardware output.

Usage:
    python v3/tests/test_RawEngine.py [static|sweep|reset]
    
    Default: static
"""

import sys
import os
import asyncio
import logging
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from logic.USEEngine import USEEngine
from logic.TestCtrl import TestCtrl
from logic.Calibration import calib
from logic.FPGACodec import HWConfig

logging.basicConfig(level=logging.DEBUG)

USE_REAL_HARDWARE = os.getenv("USE_REAL_HARDWARE", "False").lower() in ("true", "1", "yes")

# ── Helpers ──

def make_ch_reg(ch_idx, amp_v, phase_deg, freq_hz=50.0):
    """Convert physical values to register-level dict for a single channel.
    
    Returns: (hw_ch, {layer: [amp_reg, phase_reg]})
    """
    hw_ch = HWConfig.MapChannel(ch_idx)
    dc_reg = list(calib.PhysToReg(ch_idx, 0, 0.0, freq_hz))
    ac_reg = list(calib.PhysToReg(ch_idx, 1, amp_v, phase_deg))
    return hw_ch, {0: dc_reg, 1: ac_reg}


def make_step_reg(ch_idx, delta_amp_v, delta_phase_deg=0.0):
    """Convert physical delta values to register-level step dict.
    
    Returns: {hw_ch: {layer: [delta_amp_reg, delta_phase_reg]}}
    """
    hw_ch = HWConfig.MapChannel(ch_idx)
    da_reg, dp_reg = calib.PhysToReg(ch_idx, 1, delta_amp_v, delta_phase_deg, is_delta=True)
    return {hw_ch: {1: [da_reg, dp_reg]}}


def make_full_base(ch_specs, freq_hz=50.0):
    """Build a full base dict with all 16 channels.
    
    ch_specs: list of (ch_idx, amp_v, phase_deg) for active channels.
    Remaining channels get 0V at freq_hz.
    """
    base = {}
    active_chs = set()
    for ch_idx, amp_v, phase_deg in ch_specs:
        hw_ch, layers = make_ch_reg(ch_idx, amp_v, phase_deg, freq_hz)
        base[hw_ch] = layers
        active_chs.add(ch_idx)
    
    # Fill remaining channels with DC=0, freq=50Hz, AC=0
    for ch_idx in range(16):
        if ch_idx not in active_chs:
            hw_ch = HWConfig.MapChannel(ch_idx)
            dc_reg = list(calib.PhysToReg(ch_idx, 0, 0.0, freq_hz))
            base[hw_ch] = {0: dc_reg}
    
    return base


def print_reg_values(label, hw_ch, layers):
    """Debug: print register values for a channel."""
    for l_idx, vals in layers.items():
        phys = calib.RegToPhys(HWConfig.UnmapChannel(hw_ch), l_idx, vals[0], vals[1])
        print(f"  [{label}] hw_ch={hw_ch} layer={l_idx}: reg=[0x{vals[0]:08X}, 0x{vals[1]:08X}] → phys={phys}")


# ── Mock classes (reused from test_ApiSweepTest.py) ──

class MockHWGateway:
    def __init__(self):
        self.engine = None

    def SendBytes(self, frames):
        print(f"[MockHW] TX: {frames.hex().upper()}")
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
        if t == "value_update" and "static" in msg:
            # Only print channel 0 for readability
            s = msg["static"]
            ch0 = s.get("0", None)
            if ch0:
                print(f"[WS] ch0={ch0}")
        elif t != "value_update":
            print(f"[WS] {msg}")


# ── Test cases ──

async def setup_engine():
    """Common engine setup, returns (engine, ctrl, gateway)."""
    if USE_REAL_HARDWARE:
        from comms.HWGateway import HWGateway
        gateway = HWGateway()
    else:
        gateway = MockHWGateway()

    if USE_REAL_HARDWARE:
        from logic.HWProtect import HWProtect
        protect = HWProtect()
    else:
        protect = MockHWProtect()

    ws_send = MockWsSend()
    engine = USEEngine(gateway, None)
    ctrl = TestCtrl(engine, protect, ws_send)
    gateway.engine = engine

    if USE_REAL_HARDWARE:
        asyncio.create_task(gateway.Connect())
        await asyncio.sleep(0.5)

    engine._emit = ctrl.onEvent
    
    # Wrap SendBytes for TX frame logging with command decoding
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
    await asyncio.sleep(0.1)  # Let engine settle in 0xFFFF

    return engine, ctrl


async def test_static(engine, ctrl):
    """Case 1: Mode 1 Static — ch0 outputs 14.14V 50Hz sine, held indefinitely."""
    print("\n" + "=" * 60)
    print("  TEST: Static (Mode 1) — ch0 = 14.14V @ 50Hz")
    print("=" * 60)

    # Build base: ch0 = 14.14V @ 0°, 50Hz
    base = make_full_base([(0, 14.142, 0.0)])

    # Print register values for ch0
    hw_ch0 = HWConfig.MapChannel(0)
    print_reg_values("ch0", hw_ch0, base[hw_ch0])

    payload = {
        "module": "raw_test",
        "params": {
            "startNode": 2,
            "nodes": {
                "2": {
                    "mode": 1,
                    "base": base,
                    "timeoutMs": 5000,
                    "timeoutId": 0xFFFF,
                }
            }
        }
    }

    await ctrl.startTest(payload)
    await asyncio.sleep(0.3)  # Let engine settle into init node
    engine.manualTrig(2)      # Jump to target waveform node
    print("\n>>> Outputting for 5 seconds. Check ch0 on oscilloscope...")
    await asyncio.sleep(5.5)
    print(">>> Static test done.\n")


async def test_sweep(engine, ctrl):
    """Case 2: Mode 2 Sweep — ch0 starts at 0V, steps +10V every 500ms, 5 steps."""
    print("\n" + "=" * 60)
    print("  TEST: Sweep (Mode 2) — ch0 = 0→50V step, 500ms interval")
    print("=" * 60)

    base = make_full_base([(0, 10.0, 0.0)])
    step = make_step_reg(0, 10.0)

    # Print step register values
    hw_ch0 = HWConfig.MapChannel(0)
    print_reg_values("step", hw_ch0, step[hw_ch0])

    payload = {
        "module": "raw_test",
        "params": {
            "startNode": 1,
            "nodes": {
                "1": {
                    "mode": 2,
                    "base": base,
                    "steps": [step] * 5,
                    "interval": 500,
                    "countOverId": 0xFFFF,
                }
            }
        }
    }

    await ctrl.startTest(payload)
    await asyncio.sleep(0.3)
    engine.manualTrig(1)
    print("\n>>> Sweeping for 3 seconds. Watch ch0 amplitude increase...")
    await asyncio.sleep(3.5)
    print(">>> Sweep test done.\n")


async def test_reset(engine, ctrl):
    """Case 3: Mode 3 Reset Sweep — ch0 steps +10V with 200ms reset phases."""
    print("\n" + "=" * 60)
    print("  TEST: Reset Sweep (Mode 3) — ch0 steps +10V, resetTime=200ms")
    print("=" * 60)

    base = make_full_base([(0, 0.0, 0.0)])
    reset_base = make_full_base([(0, 0.0, 0.0)])
    step = make_step_reg(0, 10.0)

    payload = {
        "module": "raw_test",
        "params": {
            "startNode": 2,
            "nodes": {
                "2": {
                    "mode": 3,
                    "base": base,
                    "reset": reset_base,
                    "steps": [step] * 5,
                    "interval": 500,
                    "resetTime": 200,
                    "resetDo": 0x0000,  # enterDo=0x00, exitDo=0x00
                    "countOverId": 0xFFFF,
                }
            }
        }
    }

    await ctrl.startTest(payload)
    await asyncio.sleep(0.3)
    engine.manualTrig(2)
    print("\n>>> Reset sweep for 5 seconds. Watch ch0 step with reset pauses...")
    await asyncio.sleep(5.5)
    print(">>> Reset sweep test done.\n")


# ── Main ──

async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    engine, ctrl = await setup_engine()

    if mode == "static":
        await test_static(engine, ctrl)
    elif mode == "sweep":
        await test_sweep(engine, ctrl)
    elif mode == "reset":
        await test_reset(engine, ctrl)
    elif mode == "all":
        await test_sweep(engine, ctrl)
        await asyncio.sleep(1.0)
        await test_static(engine, ctrl)
        await asyncio.sleep(1.0)
        await test_sweep(engine, ctrl)
        await asyncio.sleep(1.0)
        await test_reset(engine, ctrl)
    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python test_RawEngine.py [static|sweep|reset|all]")


if __name__ == "__main__":
    asyncio.run(main())
