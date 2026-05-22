"""
Archived compilation methods removed from FPGACodec.py.
These depend on FlatCodec and were used by NodeCompiler.
"""

from logic.FPGACodec import HWCodec
from logic.FlatCodec import FlatCodec


def CompileDdsWords(words: list) -> list:
    """U64 DDS words → FPGA DDS_WR_SHADOW param frames.
    Groups by (layer, amp, pf) and merges channel masks."""
    masks = {}
    for w in words:
        ch, layer, amp, pf = FlatCodec.UnpackDdsWord(w)
        key = (layer, amp, pf)
        masks[key] = masks.get(key, 0) | (1 << ch)
    return [HWCodec.BuildParamFrame(HWCodec.DDS_WR_SHADOW, (l - 1) & 0xFF, m, a, p)
            for (l, a, p), m in masks.items()]


def CompileStepWords(words: list) -> list:
    """U64 DDS step words → FPGA DDS_STEP_SHADOW param frames."""
    masks = {}
    for w in words:
        ch, layer, da, dp = FlatCodec.UnpackDdsWord(w)
        key = (layer, da, dp)
        masks[key] = masks.get(key, 0) | (1 << ch)
    return [HWCodec.BuildParamFrame(HWCodec.DDS_STEP_SHADOW, (l - 1) & 0xFF, m, da, dp)
            for (l, da, dp), m in masks.items()]


def CompilePhaseGate(pgWord: int) -> list:
    """U64 PhaseGate word → [BuildPhaseGateFrame]."""
    ch, phase = FlatCodec.UnpackPhaseGate(pgWord)
    return [HWCodec.BuildPhaseGateFrame(ch, phase)]


def NegStepFrame(f: bytes) -> bytes:
    a = (-(f[5] << 8 | f[6])) & 0xFFFF
    p = (-(f[7] << 24 | f[8] << 16 | f[9] << 8 | f[10])) & 0xFFFFFFFF
    v = (a << 32 | p).to_bytes(6, 'big')
    return f[:5] + v + bytes([f[0]^f[1]^f[2]^f[3]^f[4]^v[0]^v[1]^v[2]^v[3]^v[4]^v[5]])
