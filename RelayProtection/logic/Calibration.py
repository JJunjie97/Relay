import os

import json
import logging
from typing import Tuple
from logic.FPGACodec import HWConfig

logger = logging.getLogger("Calibration")

class Calibration:
    def __init__(self):
        self.calib_filepath = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 
            "..", "config", "calibration.json"
        )
        self.calib_data = {}
        self.LoadCalib()

    def LoadCalib(self):
        self._factors = [1.0] * 16
        self._biases = [0.0] * 16
        try:
            if os.path.exists(self.calib_filepath):
                with open(self.calib_filepath, 'r', encoding='utf-8') as f:
                    self.calib_data = json.load(f)
                    for ch_str, data in self.calib_data.items():
                        ch_idx = int(ch_str)
                        if 0 <= ch_idx < 16:
                            self._factors[ch_idx] = data.get("factor", 1.0)
                            self._biases[ch_idx] = data.get("bias", 0.0)
            else:
                self.calib_data = {}
        except Exception as e:
            logger.error(f"Failed to load calibration file: {e}")
            self.calib_data = {}

    def PhysToReg(self, ch_idx: int, layer_idx: int, amplitude: float, freq_or_phase: float, is_delta: bool = False) -> Tuple[int, int]:
        is_cur = ch_idx >= 6
        factor = self._factors[ch_idx]
        bias = self._biases[ch_idx]
        
        if layer_idx == 0:
            a_calib = (-amplitude * factor) if is_delta else (bias - amplitude * factor)
            p_reg = HWConfig.ConvertFreqToReg(freq_or_phase)
        else:
            a_calib = -amplitude * factor
            p_reg = HWConfig.ConvertPhaseToReg(freq_or_phase)
            
        if abs(a_calib) < 0.000001:
            a_calib = 0.0
            
        a_reg = HWConfig.ConvertAmpToReg(a_calib, is_cur)
        return a_reg & 0xFFFFFFFF, p_reg & 0xFFFFFFFF

    def RegToPhys(self, ch_idx: int, layer_idx: int, a_reg_u32: int, p_reg_u32: int) -> Tuple[float, float]:
        is_cur = ch_idx >= 6
        scale = HWConfig.CURRENT if is_cur else HWConfig.VOLTAGE
        
        a_reg_signed = a_reg_u32 - 0x100000000 if (a_reg_u32 & 0x80000000) else a_reg_u32
        p_reg_signed = p_reg_u32 - 0x100000000 if (p_reg_u32 & 0x80000000) else p_reg_u32

        a_calib = a_reg_signed / scale
        factor = self._factors[ch_idx]
        if factor == 0: factor = 1.0
        bias = self._biases[ch_idx]

        if layer_idx == 0:
            amplitude = (bias - a_calib) / factor
            p_phys = p_reg_signed / HWConfig.FREQ
        else:
            amplitude = -a_calib / factor
            p_phys = p_reg_signed / HWConfig.PHASE
            
        if abs(amplitude) < 0.00001:
            amplitude = 0.0
            
        return round(amplitude, 4), round(p_phys, 4)

calib = Calibration()
