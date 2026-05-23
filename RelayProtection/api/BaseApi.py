import logging
from dataclasses import dataclass
from typing import Dict, Any, List, Tuple, Optional
from logic.Calibration import calib
from logic.FPGACodec import HWConfig
import pkgutil
import importlib
import inspect
import api

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
    @staticmethod
    def create(module_name: str) -> 'BaseApi':
        for _, name, _ in pkgutil.iter_modules(api.__path__):
            if not name.startswith("Api") or name == "ApiNodeData":
                continue
            try:
                mod = importlib.import_module(f"api.{name}")
                for _, cls in inspect.getmembers(mod, lambda c: inspect.isclass(c) and issubclass(c, BaseApi) and c is not BaseApi):
                    if module_name in (getattr(cls, "MODULE_KEYS", None) or [getattr(cls, "MODULE_KEY", None)]):
                        return cls()
            except Exception as e:
                logger.warning(f"Failed to load API module {name}: {e}")
        return None

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

    def physDictToReg(self, phys_dict: Dict[str, Dict[str, List[float]]], is_delta: bool = False) -> Dict[int, Dict[int, List[int]]]:
        return {
            (hw_ch := HWConfig.MapChannel(int(ch_str))): {
                int(l_str): list(calib.PhysToReg(hw_ch, int(l_str), vals[0], vals[1], is_delta))
                for l_str, vals in layers.items()
            }
            for ch_str, layers in phys_dict.items()
        }

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
