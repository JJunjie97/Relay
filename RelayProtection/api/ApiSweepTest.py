"""
ApiSweepTest.py — 扫频测试 API (全新重写)

物理发波采用极简三节点预编译模型，所有节点在 setup 阶段一次性
预编译并下发到硬件，运行期间零动态计算、零手动 Trig，完全由
硬件 FSM 自驱跳转。

节点定义:
  Node 1 (可选): 实验前复归静态 → 超时自动进入 Node 2
  Node 2:        正向扫频 (mode=2 或 mode=3)
  Node 3 (可选): 反向扫频 (mode=2 或 mode=3)，仅 changeMode==1

DI 事件由 API 层全程追踪计算（不管是动作返回还是全程扫描），
用于生成最终测试报告。
"""

import logging
import asyncio
from typing import Dict, Any

from api.BaseApi import BaseApi, ApiNodeData

logger = logging.getLogger("ApiSweepTest")


class ApiSweepTest(BaseApi):
    MODULE_KEYS = ["ac_test", "dc_test", "harmonic_test", "steps_gradient_test", "acdc_test"]

    def __init__(self):
        super().__init__()
        self._phase = "ACTION"
        self._tripTime = None
        self._tripVals = None
        self._returnTime = None
        self._returnVals = None
        self._peakTick = 0
        self._lastTick = 0
        self._lastValueTs = 0
        self._lastDi = 0
        self._fsmState = "IDLE"

    # ── Setup ──

    def _onSetup(self, params: Dict[str, Any]):
        # ── System config ──
        sys_cfg = params.get("sys", {})
        self.mode = sys_cfg.get("mode", 1)
        self.statics = params.get("statics", {})
        self.steps = params.get("steps", {})
        self.count = params.get("count", 1)
        self.payload = params.get("payload", {})

        self.changeMode = sys_cfg.get("changeMode", 0)
        self.returnMode = sys_cfg.get("returnMode", 0)
        self.stepTime = sys_cfg.get("stepTime", 1000)
        self.logicMask = sys_cfg.get("logicMask", 255)
        self.doMask = sys_cfg.get("doMask", 0)
        self.doCtrlMask = sys_cfg.get("doCtrlMask", 0)

        # ── Payload (reset config) ──
        self.enablePreTestReset = self.payload.get("enablePreTestReset", False)
        self.preTestResetTime = self.payload.get("preTestResetTime", 1000)
        self.enableStepReset = self.payload.get("enableStepReset", False)
        self.stepResetMode = self.payload.get("stepResetMode", 0)
        self.stepResetTime = self.payload.get("stepResetTime", 100)
        self.resetTableData = self.payload.get("resetTableData", {})

        # ── Ensure stepped channels exist in statics ──
        for ch_str, layers in self.steps.items():
            if ch_str not in self.statics:
                self.statics[ch_str] = {}
            for l_str in layers:
                if l_str not in self.statics[ch_str]:
                    self.statics[ch_str][l_str] = [0.0, 0.0]

        # ── Telemetry state ──
        self._phase = "ACTION"
        self._tripTime = None
        self._tripVals = None
        self._returnTime = None
        self._returnVals = None
        self._peakTick = 0
        self._lastTick = 0
        self._lastValueTs = 0
        self._lastDi = 0
        self._fsmState = "WAIT_NODE_0"

        # ── Compile registers ──
        reg_statics = self.physDictToReg(self.statics)
        reg_steps_fwd = self.physDictToReg(self.steps, is_delta=True)

        # Reverse steps (negate all deltas)
        steps_rev_phys = {}
        for ch, layers in self.steps.items():
            steps_rev_phys[ch] = {}
            for l, vals in layers.items():
                steps_rev_phys[ch][l] = [-vals[0], -vals[1]]
        reg_steps_rev = self.physDictToReg(steps_rev_phys, is_delta=True)

        # Full reset: cover all channels/layers in statics+steps, fill defaults
        full_reset_phys = self._buildFullReset()
        reg_reset_full = self.physDictToReg(full_reset_phys) if full_reset_phys else None

        # ── Build all run nodes ──
        nodes = {}

        if self.mode == 0:
            self._manualActiveNode = 1
            n1 = ApiNodeData(mode=1)
            n1.base = reg_statics
            if self.doMask:
                n1.doActions = [self.doMask & 0xFF]
            nodes[1] = n1
            self._nodesMap = nodes
            self._startNode = 1
            self._fsmState = "PREHEAT"
            asyncio.create_task(self._preheatAndStart())
            return

        # Node 1: Pre-test reset (optional static)
        if self.enablePreTestReset:
            n1 = ApiNodeData(mode=1)
            n1.base = self.physDictToReg(full_reset_phys)
            n1.timeoutMs = self.preTestResetTime
            n1.timeoutId = 2
            if self.doMask:
                n1.doActions = [self.doMask & 0xFF]
            nodes[1] = n1

        # Node 2: Forward sweep
        n2 = ApiNodeData(mode=3 if self.enableStepReset else 2)
        n2.base = reg_statics
        n2.steps = [reg_steps_fwd] * self.count
        n2.interval = self.stepTime

        if self.enableStepReset:
            n2.resetTime = self.stepResetTime
            n2.reset = reg_reset_full
            n2.resetDo = (self.doCtrlMask & 0xFF) << 8 | (self.doMask & 0xFF)
        else:
            if self.doMask:
                n2.doActions = [self.doMask & 0xFF]

        if self.changeMode == 0:
            # Single direction: DI/countOver → stop
            n2.diMatchMask = self.logicMask
            n2.diMatchId = 0xFFFF
            n2.countOverId = 0xFFFF
        elif self.changeMode == 1:
            if self.returnMode == 0:
                # DI → Node 3 (reverse); countOver without trip → stop
                n2.diMatchMask = self.logicMask
                n2.diMatchId = 3
                n2.countOverId = 0xFFFF
            else:
                # Ignore DI jump; countOver → Node 3 (reverse)
                n2.countOverId = 3

        nodes[2] = n2

        # Node 3: Reverse sweep (only if bidirectional)
        if self.changeMode == 1:
            use_return_reset = self.enableStepReset and self.stepResetMode == 1
            n3 = ApiNodeData(mode=3 if use_return_reset else 2)
            # base={} : 空字典使 baseFrame=[], 跳转时仅 SYS_UPDATE,
            # 硬件通过 SYS_SYNC 自动继承 Node 2 结束时的寄存器状态
            n3.base = {}
            n3.steps = [reg_steps_rev] * self.count
            n3.interval = self.stepTime

            if use_return_reset:
                n3.resetTime = self.stepResetTime
                n3.reset = reg_reset_full
                n3.resetDo = (self.doCtrlMask & 0xFF) << 8 | (self.doMask & 0xFF)
            else:
                if self.doMask:
                    n3.doActions = [self.doMask & 0xFF]

            if self.returnMode == 0:
                # Polarity-inverted DI match: detect contact restore → stop
                n3.diMatchMask = self.logicMask | 0x500
                n3.diMatchId = 0xFFFF
                n3.countOverId = 0xFFFF
            else:
                # Full reverse sweep: countOver → stop
                n3.countOverId = 0xFFFF

            nodes[3] = n3

        self._nodesMap = nodes
        self._startNode = 1 if self.enablePreTestReset else 2

        # 默认 Node 0（零校准 0V/50Hz + SYS_START）已由 TestCtrl.startTest 触发
        # 不需要覆盖它，直接启动预热计时
        self._fsmState = "PREHEAT"
        asyncio.create_task(self._preheatAndStart())

    # ── Reset fill ──

    def _buildFullReset(self) -> dict:
        """Build reset dict covering ALL channels/layers from statics+steps.

        Front-end sends resetTableData sparsely (only explicitly set values).
        For any channel/layer present in statics or steps but missing from
        resetTableData, we fill defaults:
          layer 0 → [0.0, 50.0]  (0 amplitude + 50Hz)
          layer >0 → [0.0, 0.0]  (0 amplitude + 0 phase)
        This ensures the reset frame fully zeroes all active channels.
        """
        involved = {}
        for src in (self.statics, self.steps):
            for ch, layers in src.items():
                if ch not in involved:
                    involved[ch] = set()
                involved[ch].update(layers.keys())

        if not involved:
            return {}

        full_reset = {}
        for ch, layer_set in involved.items():
            full_reset[ch] = {}
            for l in layer_set:
                if ch in self.resetTableData and l in self.resetTableData[ch]:
                    full_reset[ch][l] = list(self.resetTableData[ch][l])
                else:
                    full_reset[ch][l] = [0.0, 50.0] if int(l) == 0 else [0.0, 0.0]
        return full_reset

    # ── Preheat ──

    async def _preheatAndStart(self):
        """500ms preheat in Node 0, then upload all run nodes and trigger."""
        logger.info("Entering 500ms Node 0 preheat...")
        await asyncio.sleep(0.5)
        if not self.isActive:
            return

        self._fsmState = "RUNNING"
        logger.info(f"Preheat done. Uploading nodes: {list(self._nodesMap.keys())}")
        self.ctrl.upsertNodes(self._nodesMap)

        # 50ms communication flush delay before triggering
        await asyncio.sleep(0.05)
        if not self.isActive:
            return

        logger.info(f"Triggering start node {self._startNode}.")
        self.ctrl.trigNode(self._startNode)

    # ── Physics calculation ──

    def _physicsAt(self, tick: int) -> dict:
        """Calculate theoretical physical values at a given tick in current phase."""
        vals = {}
        for ch_str, layers in self.statics.items():
            vals[ch_str] = {}
            for l_str, base in layers.items():
                step = self.steps.get(ch_str, {}).get(l_str, [0.0, 0.0])
                if self._phase == "ACTION":
                    effective = tick
                else:
                    effective = self._peakTick - tick
                amp = base[0] + step[0] * effective
                ang = base[1] + step[1] * effective
                vals[ch_str][l_str] = [round(amp, 4), round(ang, 4)]
        return vals

    # ── Engine callbacks ──

    def onUpdate(self, nodeId: int, tick: int, hw_ts: int):
        # Node 0 is just zero-calibration, skip
        if nodeId == 0x0000:
            return

        # Track phase based on active node
        if nodeId == 2:
            self._phase = "ACTION"
        elif nodeId == 3 and self._phase != "RETURN":
            self._phase = "RETURN"
            # For full sweep (returnMode=1), forward completed all count steps
            if self.returnMode == 1:
                self._peakTick = self.count

        # Skip reset phase ticks (tick < 0 from mode=3 reset intervals)
        if tick < 0:
            return

        self._lastTick = tick
        self._lastValueTs = hw_ts

    def onDi(self, di: int, hw_ts: int):
        """Track DI events for both action return and full sweep modes."""
        old_di = self._lastDi
        self._lastDi = di

        if self._fsmState != "RUNNING":
            return

        changed = (di ^ old_di) & 0xFF
        mask = self.logicMask & 0xFF
        if not (changed & mask):
            return

        # Compute time since last value update
        dt_ms = 0.0
        if self._lastValueTs > 0:
            dt_ms = round(((hw_ts - self._lastValueTs) & 0xFFFFFFFF) / 1000.0, 1)

        if self.mode == 0:
            # Manual Mode: purely driven by Python DI logic, report immediately
            if not self._tripTime:
                self._tripTime = dt_ms
                self._tripVals = self.statics  # Static manual holding values
                logger.info(f"Manual DI Trip: time={dt_ms}ms")
                self.ctrl.sendReport({"tripTime": self._tripTime, "tripValues": self._tripVals})
            elif self._tripTime and not self._returnTime:
                self._returnTime = dt_ms
                self._returnVals = self.statics
                logger.info(f"Manual DI Return: time={dt_ms}ms")
                self.ctrl.sendReport({
                    "tripTime": self._tripTime, "tripValues": self._tripVals,
                    "returnTime": self._returnTime, "returnValues": self._returnVals
                })
            return

        if self._phase == "ACTION" and self._tripTime is None:
            self._tripTime = dt_ms
            self._tripVals = self._physicsAt(self._lastTick)
            # For action return mode, record the peak tick for reverse physics
            if self.returnMode == 0:
                self._peakTick = self._lastTick
            logger.info(f"DI Trip: time={dt_ms}ms, tick={self._lastTick}")

        elif self._phase == "RETURN" and self._returnTime is None:
            self._returnTime = dt_ms
            self._returnVals = self._physicsAt(self._lastTick)
            logger.info(f"DI Return: time={dt_ms}ms, tick={self._lastTick}")

    # ── Stop & Report ──

    def _onStop(self):
        """Generate final test report with trip/return data."""
        report = {
            "tripTime": self._tripTime,
            "tripValues": self._tripVals,
        }

        if self.changeMode == 1:
            report["returnTime"] = self._returnTime
            report["returnValues"] = self._returnVals

            # Compute return ratio (return value / trip value)
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

        self.ctrl.sendReport(report)

    def onWebCommand(self, msg: Dict[str, Any]):
        cmd = msg.get("cmd")
        
        if cmd == "update_static" and getattr(self, "mode", 1) == 0:
            new_statics = msg.get("static", {})
            # Cache for reporting
            for ch, layers in new_statics.items():
                if ch not in self.statics:
                    self.statics[ch] = {}
                for l, vals in layers.items():
                    self.statics[ch][l] = vals

            reg_statics = self.physDictToReg(new_statics)
            
            # Ping-Pong logic
            next_node_id = 2 if self._manualActiveNode == 1 else 1
            n = ApiNodeData(mode=1)
            n.base = reg_statics
            if self.doMask:
                n.doActions = [self.doMask & 0xFF]
            
            # Reset DI tracking on manual update
            self._tripTime = None
            self._tripVals = None
            self._returnTime = None
            self._returnVals = None
            
            logger.info(f"Manual Mode: update_static received, Ping-Pong to Node {next_node_id}")
            self.ctrl.upsertNodes({next_node_id: n})
            self.ctrl.trigNode(next_node_id)
            self._manualActiveNode = next_node_id
