import os
import sys

import json
import logging
from typing import Tuple
from logic.FPGACodec import HWConfig

logger = logging.getLogger("Calibration")

class Calibration:
    def __init__(self):
        if "__compiled__" in globals():
            base = os.path.dirname(os.path.abspath(sys.argv[0]))
        else:
            base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.calibPath = os.path.join(base, "config", "calibration.json")
        self.calibData = {}
        self.LoadCalib()

    def LoadCalib(self):
        self._factorsDc = [-0.95] * 16
        self._factorsAc = [-0.95] * 16
        self._biases = [0.0] * 16
        try:
            with open(self.calibPath, 'r', encoding='utf-8') as f:
                self.calibData = json.load(f)
                for chStr, entry in self.calibData.items():
                    ch = int(chStr)
                    self._biases[ch] = entry[0]
                    self._factorsDc[ch] = entry[1]
                    self._factorsAc[ch] = entry[2]
        except Exception as e:
            logger.error(f"Failed to load calibration: {e}")

    def PhysToReg(self, chIdx: int, layerIdx: int, amplitude: float, freqOrPhase: float, isDelta: bool = False) -> Tuple[int, int]:
        if layerIdx == 0:
            return (
                HWConfig.ConvertAmpToReg(
                    (amplitude * self._factorsDc[chIdx]) if isDelta else (amplitude * self._factorsDc[chIdx] + self._biases[chIdx]), 
                    chIdx in HWConfig.I_CHANNELS
                ), 
                HWConfig.ConvertFreqToReg(freqOrPhase)
            )
        else:
            return (
                HWConfig.ConvertAmpToReg(
                    amplitude * self._factorsAc[chIdx], 
                    chIdx in HWConfig.I_CHANNELS
                ), 
                HWConfig.ConvertPhaseToReg(freqOrPhase + (0.0 if isDelta else 180.0))
            )

    def RegToPhys(self, chIdx: int, layerIdx: int, aRegU32: int, pRegU32: int) -> Tuple[float, float]:
        aReg16 = aRegU32 >> 16
        if aReg16 & 0x8000:
            aReg16 -= 0x10000    
        aCalib = aReg16 / (HWConfig.CURRENT if chIdx in HWConfig.I_CHANNELS else HWConfig.VOLTAGE)
        
        if layerIdx == 0:
            amp = (aCalib - self._biases[chIdx]) / self._factorsDc[chIdx]
            pPhys = pRegU32 / HWConfig.FREQ
        else:
            amp = aCalib / self._factorsAc[chIdx]
            pPhys = (((pRegU32 >> 16) & 0xFFFF) ^ 0x8000) / HWConfig.PHASE
        return round(amp, 4), round(pPhys, 4)

calib = Calibration()
