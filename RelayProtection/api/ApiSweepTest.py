import logging
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
        
        # Assign Node IDs to avoid system reserved 0x0000 and 0xFFFF
        self._actionId = 2 if self.enablePreTestReset else 1
        self._returnId = self._actionId + 2  # Node 4 (Node 3 is Hold Node)
        self._holdId = self._actionId + 1    # Node 3

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
        
        nodesToUpload = {}
        
        # Node 1: Pre-test reset (if enabled)
        if self.enablePreTestReset:
            n1 = ApiNodeData(mode=1)
            n1.base = reg_reset
            n1.timeoutMs = self.preTestResetTime
            n1.timeoutId = self._actionId
            n1.doActions = [(0 << 8) | (self.doMask & 0xFF)] if self.doMask else []
            nodesToUpload[1] = n1
            
        # Node 2: Forward sweep (action phase)
        n2 = ApiNodeData(mode=3 if self.enableStepReset else 2)
        n2.base = reg_statics
        # Python list multiplication generates references, extremely memory-efficient
        n2.steps = [reg_steps_fwd] * self.count
        n2.interval = self.stepTime
        n2.doActions = [] if self.enableStepReset else ([(0 << 8) | (self.doCtrlMask & 0xFF)] if self.doCtrlMask else [])
        
        if self.enableStepReset:
            n2.resetTime = self.stepResetTime
            n2.reset = reg_reset
            # 16-bit resetDo: exitDo (doCtrlMask) << 8 | enterDo (doMask)
            n2.resetDo = (self.doMask & 0xFF) | ((self.doCtrlMask & 0xFF) << 8)
            
        if self.returnMode == 0:
            # Trip & Stop: Trigger targets the Hold node (or Stop)
            tgt = self._holdId if self.changeMode in (1, 2) else 0xFFFF
            n2.diMatchMask = self.logicMask
            n2.diMatchId = tgt
            n2.countOverId = tgt
        else:
            # Full Sweep: Ignore triggers, always run full count
            tgt = self._returnId if self.changeMode in (1, 2) else 0xFFFF
            n2.countOverId = tgt
            
        nodesToUpload[self._actionId] = n2
        
        # Node 3: Pre-load the Hold Node (to solve FSM race condition upon tripping)
        if self.changeMode in (1, 2) and self.returnMode == 0:
            n3 = ApiNodeData(mode=1)
            n3.base = {}  # Empty baseFrame implies HWCodec.FRAME_SYS_UPDATE only -> perfect latch!
            nodesToUpload[self._holdId] = n3
            
        # Node 4: Pre-load Return Node (only if full sweep is guaranteed)
        if self.changeMode in (1, 2) and self.returnMode == 1:
            self._peakTick = self.count
            n4 = self._buildReturnNode()
            nodesToUpload[self._returnId] = n4

        # Dispatch nodes to engine FSM
        self.ctrl.upsertNodes(nodesToUpload)
        
    def _buildReturnNode(self) -> ApiNodeData:
        """Dynamically build the reverse sweep node from the latest peak tick."""
        peak_phys = self._physicsAt(self._peakTick)
        reg_peak = self.physDictToReg(peak_phys)
        
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
            print(f"[ApiSweepTest] Built Return Node. diMatchMask={n.diMatchMask}, diMatchId={n.diMatchId}")
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
            # Engine initialization complete; kick off the test workflow.
            startId = 1 if self.enablePreTestReset else 1
            self.ctrl.trigNode(startId)
            return
            
        if nodeId == self._actionId:
            self._phase = "ACTION"
        elif nodeId == self._returnId:
            if self._phase == "ACTION":
                self._peakTick = self._lastTick
            self._phase = "RETURN"
            
        if tick < 0:
            return  # Ignore negative ticks for value calculations
            
        self._lastTick = tick
        self._lastValueTs = hw_ts
            
        # Freeze physical telemetry while FSM is parked in the Hold Node
        if nodeId == self._holdId:
            return
            

    def onDi(self, di: int, hw_ts: int):
        """Called by Engine FSM immediately upon any DI contact variation."""
        old_di = self._lastDi
        self._lastDiTs = hw_ts
        self._lastDi = di
        
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
                
                # If we're returning dynamically, Engine is now parked in Node 3 (Hold).
                # Compute Node 4 and perform Hot Update.
                if self.changeMode in (1, 2) and self.returnMode == 0:
                    n4 = self._buildReturnNode()
                    self.ctrl.upsertNodes({self._returnId: n4})
                    self.ctrl.trigNode(self._returnId)
                    
            elif self._phase == "RETURN" and self._returnTime is None:
                # Capture the FIRST return
                self._returnTime = dt_ms
                self._returnVals = self._physicsAt(self._lastTick)

    def _onStop(self):
        """Called FSM is fully terminated (Node 0xFFFF)."""
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
