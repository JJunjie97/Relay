import struct
from typing import Tuple

class HWConfig:
    MAX_VOLTAGE = 200
    MAX_CURRENT = 60
    MAX_FREQ = 0x7FFFFFFF / 0x100000000 * 40000000 / 0x400
    MAX_DBNC = 0xFF / 50000 * 0x4000

    VOLTAGE  = 0x7FFF / MAX_VOLTAGE
    CURRENT  = 0x7FFF / MAX_CURRENT
    PHASE    = 0x10000 / 360
    FREQ     = 0x100000000 / 40000000 * 0x400
    DBNC     = 50000 / 0x4000
    PHASE_PER_FREQ_MS = 40000000 / 1000 / 0x400

    V_CHANNELS = (0, 2, 4, 6, 8, 10)
    I_CHANNELS = (1, 3, 5, 7, 9, 11)
    N_CHANNELS = (12, 13, 14, 15)
    ACTIVE_CHANNELS = V_CHANNELS + I_CHANNELS

    _CH_MAPPING = (0, 2, 4, 6, 8, 10, 1, 3, 5, 7, 9, 11, 12, 13, 14, 15)
    _REV_CH_MAPPING = {hw: api for api, hw in enumerate(_CH_MAPPING)}

    @classmethod
    def MapChannel(cls, api_ch: int) -> int:
        return cls._CH_MAPPING[api_ch]

    @classmethod
    def UnmapChannel(cls, hw_ch: int) -> int:
        return cls._REV_CH_MAPPING[hw_ch]

    @classmethod
    def ConvertAmpToReg(cls, amp: float, isCurrent: bool = False) -> int:
        scale = cls.CURRENT if isCurrent else cls.VOLTAGE
        return (int(round(amp * scale)) << 16) & 0xFFFFFFFF
        
    @classmethod
    def ConvertPhaseToReg(cls, phase: float) -> int:
        return (int(round(phase * cls.PHASE)) << 16) & 0xFFFFFFFF
        
    @classmethod
    def ConvertFreqToReg(cls, freq: float) -> int:
        return int(round(freq * cls.FREQ)) & 0xFFFFFFFF
        
    @classmethod
    def ConvertDbncToReg(cls, time: float) -> int:
        return int(round(time * cls.DBNC)) & 0xFF

class HWCodec:
    SYS_START        = 0x00  # Start waveform generation
    SYS_STOP         = 0x01  # [01] Stop generation
    SYS_RESET        = 0x04  # Global hardware hard-reset
    SYS_UPDATE       = 0x05  # Trigger Ping-Pong buffer inversion
    SYS_SYNC         = 0x06  # Active → Base + Shadow (全通道全层级, 三缓冲区对齐)

    SYS_SET_DBNC     = 0x20  # Config DI optical DBNC filter (1 unit = 0.32768ms @ 50MHz)
    SYS_SET_DO       = 0x21  # Write Digital Output mask

    DDS_WR_SHADOW    = 0x10  # [000] Write to Base + Shadow
    DDS_WR_STAGE     = 0x11  # [001] Write to Shadow only (Base unchanged)

    DDS_STEP_SHADOW  = 0x14  # [100] Base += Step; result → Shadow
    DDS_STEP_STAGE   = 0x15  # [101] Shadow += Step (Base unchanged)

    DDS_PHASE_GATE   = 0x1F  # Phase-gated buffer flip (Param Frame): delays Update until target phase reached

    ACK_SUCCESS = 0x0000
    ERR_ILLEGAL = 0xFFFF

    # FRAME_HEAD_SYSTEM = 0x5A

    FRAME_SYS_START   = bytes([0x5A, SYS_START  , 0x00, 0x5A ^ SYS_START  ])
    FRAME_SYS_STOP    = bytes([0x5A, SYS_STOP   , 0x00, 0x5A ^ SYS_STOP   ])
    FRAME_SYS_RESET   = bytes([0x5A, SYS_RESET  , 0x00, 0x5A ^ SYS_RESET  ])
    FRAME_SYS_UPDATE  = bytes([0x5A, SYS_UPDATE , 0x00, 0x5A ^ SYS_UPDATE ])
    FRAME_SYS_SYNC    = bytes([0x5A, SYS_SYNC   , 0x00, 0x5A ^ SYS_SYNC   ])

    @classmethod
    def BuildSystemFrame(cls, cmdCode: int, p_U8: int = 0) -> bytes:
        return bytes([0x5A, cmdCode, p_U8, 0x5A ^ cmdCode ^ p_U8])

    # FRAME_HEAD_PARAM  = 0xA5
    @classmethod
    def BuildParamFrame(cls, cmdCode: int, regIndex: int, chMask: int, p1_u32: int, p2_u32: int) -> bytes:
        b1 = (chMask >> 8)  & 0xFF
        b2 =  chMask        & 0xFF
        b3 = (p1_u32 >> 24) & 0xFF
        b4 = (p1_u32 >> 16) & 0xFF
        b5 = (p2_u32 >> 24) & 0xFF
        b6 = (p2_u32 >> 16) & 0xFF
        b7 = (p2_u32 >> 8)  & 0xFF
        b8 =  p2_u32        & 0xFF
        return bytes([
            0xA5, cmdCode, regIndex, b1, b2, b3, b4, b5, b6, b7, b8,
            0xA5^ cmdCode^ regIndex^ b1^ b2^ b3^ b4^ b5^ b6^ b7^ b8
        ])

    @classmethod
    def BuildPhaseGateFrame(cls, physical_ch: int, phase_u32: int) -> bytes:
        return cls.BuildParamFrame(cls.DDS_PHASE_GATE, 0, physical_ch << 6, 0, phase_u32)


    _SOE_PACKER = struct.Struct('<IH')
    @classmethod
    def ParseFeedbackFrame(cls, frameBytes: bytes) -> Tuple[int, int]:
        return cls._SOE_PACKER.unpack(frameBytes)