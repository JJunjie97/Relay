import logging
import asyncio
from typing import Dict, Any

from api.BaseApi import BaseApi, ApiNodeData
from logic.FPGACodec import HWConfig

logger = logging.getLogger("ApiSweepTest")

class ApiSweepTest(BaseApi):
    MODULE_KEYS = ["ac_test", "dc_test", "harmonic_test", "steps_gradient_test", "acdc_test"]

    def _onSetup(self, params: Dict[str, Any]):
        self.sys_cfg = params.get("sys", {})
        self.statics = params.get("statics", {})
        self.steps = params.get("steps", {})
        self.count = params.get("count", 1)
        self.payload = params.get("payload", {})
        
        self.mode = self.sys_cfg.get("mode", 1)
        self.changeMode = self.sys_cfg.get("changeMode", 0)
        self.returnMode = self.sys_cfg.get("returnMode", 0)
        self.stepTime = self.sys_cfg.get("stepTime", 1000)
        self.logicMask = self.sys_cfg.get("logicMask", 255)
        self.doMask = self.sys_cfg.get("doMask", 0)
        self.doCtrlMask = self.sys_cfg.get("doCtrlMask", 0)
        
        self.enablePreTestReset = self.payload.get("enablePreTestReset", False)
        self.preTestResetTime = self.payload.get("preTestResetTime", 1000)
        self.enableStepReset = self.payload.get("enableStepReset", False)
        self.stepResetMode = self.payload.get("stepResetMode", 0)
        self.stepResetTime = self.payload.get("stepResetTime", 100)
        self.resetTableData = self.payload.get("resetTableData", {})
        
        # Internal state tracking
        self._tripTime = None
        self._tripVals = None
        self._returnTime = None
        self._returnVals = None
        self._peakTick = 0
        self._phase = "ACTION"
        self._lastTick = 0
        self._lastDiTs = 0
        self._lastValueTs = 0
        self._lastDi = 0
        
        # Assign Node IDs: 1 for Action, 2 for Hold, 3 for Return
        self._actionId = 1
        self._holdId = 2
        self._returnId = 3

        # Ensure all stepped channels exist in statics for reporting and base values
        for ch_str, layers in self.steps.items():
            if ch_str not in self.statics:
                self.statics[ch_str] = {}
            for l_str in layers:
                if l_str not in self.statics[ch_str]:
                    self.statics[ch_str][l_str] = [0.0, 0.0]

        # Translate physical values to hardware register dictionaries using BaseApi tools
        reg_reset = self.fillMissingChannels(self.physDictToReg(self.resetTableData), 50.0)
        reg_statics = self.fillMissingChannels(self.physDictToReg(self.statics), 50.0)
        # Steps are deltas, use is_delta=True to avoid double-subtracting bias on layer 0
        reg_steps_fwd = self.physDictToReg(self.steps, is_delta=True)
        
        # Pre-calculate inverted steps for the return node
        steps_rev_phys = {}
        for ch, layers in self.steps.items():
            steps_rev_phys[ch] = {}
            for l, vals in layers.items():
                steps_rev_phys[ch][l] = [-vals[0], -vals[1]]
        self._reg_steps_rev = self.physDictToReg(steps_rev_phys, is_delta=True)
        self._reg_reset = reg_reset
        
        # Node 1: Forward sweep (action phase)
        n1 = ApiNodeData(mode=3 if self.enableStepReset else 2)
        n1.base = reg_statics
        n1.steps = [reg_steps_fwd] * self.count
        n1.interval = self.stepTime
        n1.doActions = [] if self.enableStepReset else ([(0 << 8) | (self.doCtrlMask & 0xFF)] if self.doCtrlMask else [])
        
        if self.enableStepReset:
            n1.resetTime = self.stepResetTime
            n1.reset = reg_reset
            n1.resetDo = (self.doMask & 0xFF) | ((self.doCtrlMask & 0xFF) << 8)
            
        # 无论 returnMode 统一进入 Node 2 中转（如果有反向扫频的话）
        if self.changeMode in (1, 2):
            tgt = self._holdId
        else:
            tgt = 0xFFFF
            
        n1.diMatchMask = self.logicMask
        n1.diMatchId = tgt
        n1.countOverId = tgt
        
        # Configure Node 0
        n0 = ApiNodeData(mode=1)
        n0.base = reg_statics
        
        self._actionNode = n1
        self._holdNode = None
        if self.changeMode in (1, 2):
            self._holdNode = ApiNodeData(mode=1)
            self._holdNode.base = {}  # Empty baseFrame implies HWCodec.FRAME_SYS_UPDATE only -> perfect latch!
            
        self._returnNode = None
        self._fsmState = "WAIT_NODE_0"
        
        # 1. Configure and transition to Node 0 (static initial mode with reg_statics)
        # We upload Node 0 alone first because the Engine automatically purges all other 
        # nodes from its cache upon transitioning to Node 0.
        self.ctrl.upsertNodes({0: n0})
        self.ctrl.trigNode(0)
        
    async def _preheatAndStart(self):
        """Perform 500ms preheat inside Node 0 to allow DDS registers to stabilize flatly at 10V before launching sweeps."""
        logger.info("[ApiSweepTest] Entering 500ms Node 0 stable preheat...")
        await asyncio.sleep(0.5)
        
        if not self.isActive:
            logger.warning("[ApiSweepTest] Setup cancelled because API was stopped during preheat.")
            return
            
        self._fsmState = "RUNNING"
        logger.info("[ApiSweepTest] Preheat completed. Uploading Node 1 and Node 2.")
        
        nodesToUpload = {self._actionId: self._actionNode}
        if self._holdNode:
            nodesToUpload[self._holdId] = self._holdNode
        self.ctrl.upsertNodes(nodesToUpload)
        
        # 2. Crucial Step: Sleep for a safe 50ms to allow all compiled packets to fully flush 
        # and be processed by the FPGA buffer before triggering the jump to Node 1.
        # This completely eradicates physical race conditions where the FSM jumps before 
        # parameters are fully received.
        await asyncio.sleep(0.05)
        
        if not self.isActive:
            return
            
        logger.info("[ApiSweepTest] Triggering Node 1.")
        self.ctrl.trigNode(self._actionId)

    def _buildReturnNode(self) -> ApiNodeData:
        """Dynamically build the reverse sweep node from the latest peak tick."""
        peak_phys = self._physicsAt(self._peakTick)
        reg_peak = self.fillMissingChannels(self.physDictToReg(peak_phys), 50.0)
        
        useReturnReset = (self.stepResetMode == 1 and self.enableStepReset)
        
        n = ApiNodeData(mode=3 if useReturnReset else 2)
        n.base = reg_peak
        
        if self._peakTick > 0:
            n.steps = [self._reg_steps_rev] * self._peakTick
            n.interval = self.stepTime
        else:
            n.mode = 1  # 0 steps means static mode
            n.timeoutMs = self.stepTime # Just hold for 1 step duration
            
        n.doActions = [] if useReturnReset else ([(0 << 8) | (self.doCtrlMask & 0xFF)] if self.doCtrlMask else [])
        
        if useReturnReset:
            n.resetTime = self.stepResetTime
            n.reset = self._reg_reset
            n.resetDo = (self.doMask & 0xFF) | ((self.doCtrlMask & 0xFF) << 8)
            
        # For the reverse sweep, we must catch the return (restore) of the contacts.
        # Mask 0x400 inverses polarity, Mask 0x100 enforces AND logic (all contacts restore).
        tgt = 0xFFFF
        if self.returnMode == 0:
            n.diMatchMask = self.logicMask | 0x500
            n.diMatchId = tgt
            n.countOverId = tgt
            logger.info(f"[ApiSweepTest] Built Return Node. diMatchMask={n.diMatchMask}, diMatchId={n.diMatchId}")
        else:
            n.countOverId = tgt
            
        return n

    def _physicsAt(self, tick: int) -> dict:
        """Calculate the theoretical physical value at a given tick."""
        vals = {}
        for ch_str, layers in self.statics.items():
            vals[ch_str] = {}
            for l_str, base in layers.items():
                step = self.steps.get(ch_str, {}).get(l_str, [0.0, 0.0])
                if self._phase == "ACTION":
                    amp = base[0] + step[0] * tick
                else:
                    peak_amp = base[0] + step[0] * self._peakTick
                    amp = peak_amp - step[0] * tick
                vals[ch_str][l_str] = [round(amp, 4), base[1]]
        return vals

    def onUpdate(self, nodeId: int, tick: int, hw_ts: int):
        """Called by Engine FSM to sync real-time telemetry."""
        if nodeId == 0x0000:
            if hasattr(self, "_fsmState") and self._fsmState == "WAIT_NODE_0":
                self._fsmState = "RUNNING_PREHEAT"
                asyncio.create_task(self._preheatAndStart())
            return
            
        if nodeId == self._actionId:
            self._phase = "ACTION"
        elif nodeId == self._returnId:
            self._phase = "RETURN"
            
        if tick < 0:
            return  # Ignore negative ticks for value calculations
            
        self._lastTick = tick
        self._lastValueTs = hw_ts
            
        # Freeze physical telemetry while FSM is parked in the Hold Node
        if nodeId == self._holdId:
            if self._phase == "ACTION" and self.changeMode in (1, 2):
                if self.returnMode == 1:
                    self._peakTick = self.count
                    self._phase = "RETURN"
                    asyncio.create_task(self._trigReturnNodeWithDelay())
                else:
                    if self._tripTime is not None:
                        self._phase = "RETURN"
                        asyncio.create_task(self._trigReturnNodeWithDelay())
                    else:
                        logger.warning("[ApiSweepTest] Forward sweep completed without DI trip. Terminating.")
                        self.ctrl.trigNode(0xFFFF)
            return

    async def _trigReturnNodeWithDelay(self):
        """Asynchronously compiles, uploads and triggers the Return Node after a 50ms communications flush delay."""
        n4 = self._buildReturnNode()
        self.ctrl.upsertNodes({self._returnId: n4})
        await asyncio.sleep(0.05)
        if not self.isActive:
            return
        logger.info("[ApiSweepTest] Triggering Node 3 (Return).")
        self.ctrl.trigNode(self._returnId)

    def onDi(self, di: int, hw_ts: int):
        """Called by Engine FSM immediately upon any DI contact variation."""
        old_di = self._lastDi
        self._lastDiTs = hw_ts
        self._lastDi = di
        
        # Only process DI transitions when FSM is actively RUNNING to ignore initial physical state reports
        if getattr(self, "_fsmState", None) != "RUNNING":
            return
            
        changed = (di ^ old_di) & 0xFF
        mask = self.logicMask & 0xFF
        
        if (changed & mask):
            dt_ms = 0
            if self._lastValueTs > 0:
                # 32-bit timestamp rollover safety
                dt_ms = round(((hw_ts - self._lastValueTs) & 0xFFFFFFFF) / 1000.0, 1)
                
            if self._phase == "ACTION" and self._tripTime is None:
                # Capture the FIRST trip
                self._tripTime = dt_ms
                self._tripVals = self._physicsAt(self._lastTick)
                self._peakTick = self._lastTick
                logger.info(f"[ApiSweepTest] Captured DI Trip: tripTime={dt_ms}ms, peakTick={self._peakTick}")
                    
            elif self._phase == "RETURN" and self._returnTime is None:
                # Capture the FIRST return
                self._returnTime = dt_ms
                self._returnVals = self._physicsAt(self._lastTick)
                logger.info(f"[ApiSweepTest] Captured DI Return: returnTime={dt_ms}ms, lastTick={self._lastTick}")

    def _onStop(self):
        """Called when FSM is fully terminated (Node 0xFFFF)."""
        report = {
            "tripTime": self._tripTime,
            "tripValues": self._tripVals
        }
        if self.changeMode in (1, 2) or self.mode == 0:
            report["returnTime"] = self._returnTime
            report["returnValues"] = self._returnVals
            
            # Compute heuristic return ratio
            if self._tripVals and self._returnVals:
                try:
                    ch_str = list(self.steps.keys())[0]
                    l_str = list(self.steps[ch_str].keys())[0]
                    tv = self._tripVals[ch_str][l_str][0]
                    rv = self._returnVals[ch_str][l_str][0]
                    if abs(tv) > 0.0001:
                        report["returnRatio"] = round(rv / tv, 4)
                except Exception:
                    pass
                    
        self.ctrl._send({
            "module": self.ctrl._module,
            "type": "report",
            "data": report
        })
        self.ctrl._send({
            "module": self.ctrl._module,
            "type": "stop"
        })
