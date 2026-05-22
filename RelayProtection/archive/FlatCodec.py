from enum import IntEnum

class NodeKey(IntEnum):
    Id          = 0x10
    DoActions   = 0x11
    Static      = 0x12
    Interval    = 0x13
    Steps       = 0x14
    PhaseGate   = 0x15
    ResetTime   = 0x16
    ResetStatic = 0x17
    ResetDo     = 0x18
    Triggers    = 0x19

class TrigCond(IntEnum):
    ConDiMatch   = 0
    ConTimeout   = 1
    ConCountOver = 2

class EngineEvt(IntEnum):
    EvtValueUpdate = 0x21
    EvtTrigger     = 0x22
    EvtDiChange    = 0x23
    EvtDoChange    = 0x24

class FlatCodec:

    @staticmethod
    def PackDdsWord(ch: int, layer: int, amp: int, phase_freq: int) -> int:
        return (((ch & 0xF) << 6 | (layer & 0x3F)) << 64) | ((amp & 0xFFFFFC00) << 22) | (phase_freq & 0xFFFFFFFF)

    @staticmethod
    def UnpackDdsWord(word: int) -> tuple:
        return (word >> 70) & 0xF, (word >> 64) & 0x3F, ((word >> 22) & 0xFFFFFC00 ^ 0x80000000) - 0x80000000, word & 0xFFFFFFFF

    @staticmethod
    def PackDoAction(delay_ms: int, mask: int) -> int:
        return ((delay_ms & 0xFFFF) << 16) | (mask & 0xFFFF)

    @staticmethod
    def UnpackDoAction(val: int) -> tuple:
        return (val >> 16) & 0xFFFF, val & 0xFFFF

    @staticmethod
    def PackResetDo(exit_mask: int, enter_mask: int) -> int:
        return ((exit_mask & 0xFFFF) << 16) | (enter_mask & 0xFFFF)

    @staticmethod
    def UnpackResetDo(val: int) -> tuple:
        return (val >> 16) & 0xFFFF, val & 0xFFFF

    @staticmethod
    def PackTrigger(condition: int, next_id: int, union_data: int) -> int:
        return ((condition & 0xFFFF) << 48) | ((next_id & 0xFFFF) << 32) | (union_data & 0xFFFFFFFF)

    @staticmethod
    def UnpackTrigger(val: int) -> tuple:
        return (val >> 48) & 0xFFFF, (val >> 32) & 0xFFFF, val & 0xFFFFFFFF

    @staticmethod
    def PackPhaseGate(channel: int, phase: int) -> int:
        return ((channel & 0xFFFFFFFF) << 32) | (phase & 0xFFFFFFFF)

    @staticmethod
    def UnpackPhaseGate(val: int) -> tuple:
        return (val >> 32) & 0xFFFFFFFF, val & 0xFFFFFFFF
