import logging
from dataclasses import dataclass
from typing import Dict, Any, List, Tuple, Optional

from logic.Calibration import calib
from logic.FPGACodec import HWConfig

logger = logging.getLogger("BaseApi")

@dataclass
class ApiNodeData:
    mode: int
    
    interval: Optional[int] = None
    resetTime: Optional[int] = None

    base: Optional[Dict[int, Dict[int, List[int]]]] = None
    reset: Optional[Dict[int, Dict[int, List[int]]]] = None
    steps: Optional[List[Dict[int, Dict[int, List[int]]]]] = None
    gate: Optional[List[int]] = None

    resetDo: Optional[int] = None
    doActions: Optional[List[int]] = None

    countOverId: Optional[int] = None
    diMatchMask: Optional[int] = None
    diMatchId: Optional[int] = None
    timeoutMs: Optional[int] = None
    timeoutId: Optional[int] = None
class BaseApi:
    def __init__(self):
        self.ctrl = None
        self.isActive = False

    def setup(self, ctrl, params: Dict[str, Any]):
        self.ctrl = ctrl
        self.isActive = True
        self._onSetup(params)

    def onStop(self):
        if not self.isActive:
            return
        self.isActive = False
        self._onStop()

    def buildChannelReg(self, ch_idx: int, amplitude: float, angle_deg: float, freq_hz: float) -> Tuple[int, Dict[int, List[int]]]:
        dc_amp_reg, freq_reg = calib.PhysToReg(ch_idx, 0, 0.0, freq_hz)
        ac_amp_reg, phase_reg = calib.PhysToReg(ch_idx, 1, amplitude, angle_deg)
        
        hw_ch = HWConfig.MapChannel(ch_idx)
        return hw_ch, {
            0: [dc_amp_reg, freq_reg],
            1: [ac_amp_reg, phase_reg]
        }

    def fillMissingChannels(self, static_dict: Dict[int, Dict[int, List[int]]], freq_hz: float = 50.0) -> Dict[int, Dict[int, List[int]]]:
        for ch_idx in range(16):
            hw_ch = HWConfig.MapChannel(ch_idx)
            if hw_ch not in static_dict:
                dc_amp_reg, freq_reg = calib.PhysToReg(ch_idx, 0, 0.0, freq_hz)
                static_dict[hw_ch] = {0: [dc_amp_reg, freq_reg]}
            elif 0 not in static_dict[hw_ch]:
                dc_amp_reg, freq_reg = calib.PhysToReg(ch_idx, 0, 0.0, freq_hz)
                static_dict[hw_ch][0] = [dc_amp_reg, freq_reg]
        return static_dict

    def physDictToReg(self, phys_dict: Dict[str, Dict[str, List[float]]], is_delta: bool = False) -> Dict[int, Dict[int, List[int]]]:
        reg_dict = {}
        for ch_str, layers in phys_dict.items():
            ch_idx = int(ch_str)
            hw_ch = HWConfig.MapChannel(ch_idx)
            reg_dict[hw_ch] = {}
            for l_str, vals in layers.items():
                l_idx = int(l_str)
                a_reg, p_reg = calib.PhysToReg(ch_idx, l_idx, vals[0], vals[1], is_delta)
                reg_dict[hw_ch][l_idx] = [a_reg, p_reg]
        return reg_dict

    def _onSetup(self, params: Dict[str, Any]):
        pass

    def _onStop(self):
        pass

    def onUpdate(self, nodeId: int, tick: int, hw_ts: int):
        pass

    def onDi(self, di: int, hw_ts: int):
        pass

    def onWebCommand(self, msg: Dict[str, Any]):
        pass
