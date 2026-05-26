"""
ApiDifferentialTest.py — 差动试验 API

支持两种接线模式：
  - ext-differential (6I): 扩展差动，使用 compute_6ch → 6 路三相电流
  - phase-differential (2I): 分相差动，使用 compute_2i → 2 路标量电流（带 √3 校正）

3 节点 FSM 架构：
  Node 1 (故障前): 静态电压 + 零电流, 超时跳转 Node 2
  Node 2 (故障态): 计算的电流, DI 匹配或超时跳转 Node 3
  Node 3 (挂起):   静态电压 + 零电流, 等待 API 手动触发下一轮
"""

import logging
import asyncio
from typing import Dict, Any, List, Optional

from api.BaseApi import BaseApi, ApiNodeData
from calc.diff_bridge import compute_6ch, compute_2i

logger = logging.getLogger("ApiDifferentialTest")

# ══════════════════════════════════════════
# 通道映射常量
# ══════════════════════════════════════════
# 前端 API 通道索引: 0~5 = 电压 (Ua,Ub,Uc,Ux,Uy,Uz), 6~11 = 电流 (Ia,Ib,Ic,Ix,Iy,Iz)
# physDictToReg 内部会自动调用 MapChannel 转换到硬件通道

# 全部电流通道（用于归零）
ALL_CURRENT_CHANNELS = ["6", "7", "8", "9", "10", "11"]

# 扩展差动 (6I) 端子映射
TERMINAL_6I = {
    "high-y": ["6", "7", "8"],      # Ia, Ib, Ic
    "low-y":  ["9", "10", "11"],     # Ix, Iy, Iz
}

# 分相差动 (2I) 端子映射：每个端子选项 → 输出到哪些通道（并联）
TERMINAL_2I = {
    # 单通道
    "ia": ["6"], "ib": ["7"], "ic": ["8"],
    "ix": ["9"], "iy": ["10"], "iz": ["11"],
    # 两并
    "iab": ["6", "7"], "ibc": ["7", "8"], "ica": ["8", "6"],
    "ixy": ["9", "10"], "iyz": ["10", "11"], "izx": ["11", "9"],
    # 三并
    "iabc": ["6", "7", "8"],
    "ixyz": ["9", "10", "11"],
}


class ApiDifferentialTest(BaseApi):
    MODULE_KEY = "differential_test"

    def __init__(self):
        super().__init__()
        self._payload: Dict = {}
        self._statics_base: Dict = {}  # 前端下发的静态通道（电压等）
        self._points: List[Dict] = []
        self._active_project: str = ""
        self._search_cfg: Dict = {}

        # 时序参数
        self._fault_duration_ms: int = 1000
        self._pre_fault_duration_ms: int = 1000
        self._simulate_pre_fault: bool = True

        # 硬件参数
        self._logicMask: int = 7
        self._conn_type: str = "ext-differential"
        self._i1_terminal: str = "high-y"
        self._i2_terminal: str = "low-y"

        # 运行状态
        self._fsmState: str = "IDLE"
        self._di_tripped: bool = False
        self._current_node: int = 0
        self._node_done: Optional[asyncio.Event] = None

    # ══════════════════════════════════════════
    # Setup
    # ══════════════════════════════════════════

    def _onSetup(self, params: Dict[str, Any]):
        sys_cfg = params.get("sys", {})
        self._statics_base = params.get("statics", {})
        self._payload = params.get("payload", {})

        self._logicMask = sys_cfg.get("logicMask", 7)

        proj = self._payload.get("projectSettings", {})
        self._active_project = proj.get("activeProject", "ratio-fixed")
        self._points = proj.get("points", [])
        self._search_cfg = proj.get("search", {})

        test_params = self._payload.get("testParams", {})
        self._fault_duration_ms = int(test_params.get("faultDuration", 1.0) * 1000)
        self._pre_fault_duration_ms = int(test_params.get("preFaultDuration", 1.0) * 1000)
        self._simulate_pre_fault = test_params.get("simulatePreFault", True)

        conn = self._payload.get("connectionSettings", {})
        self._conn_type = conn.get("type", "ext-differential")
        self._i1_terminal = conn.get("i1Terminal", "high-y")
        self._i2_terminal = conn.get("i2Terminal", "low-y")

        if not self._points:
            logger.warning("No test points, stopping.")
            self.ctrl.stopTest("No test points provided")
            return

        logger.info(f"DiffTest setup: project={self._active_project}, "
                    f"connType={self._conn_type}, "
                    f"i1={self._i1_terminal}, i2={self._i2_terminal}, "
                    f"points={len(self._points)}, "
                    f"preFault={self._simulate_pre_fault}/{self._pre_fault_duration_ms}ms, "
                    f"fault={self._fault_duration_ms}ms")

        self._node_done = asyncio.Event()
        self._fsmState = "PREHEAT"
        asyncio.create_task(self._run())

    # ══════════════════════════════════════════
    # 通道解析
    # ══════════════════════════════════════════

    def _resolve_channels(self, terminal: str) -> List[str]:
        """根据接线类型和端子名称，返回 API 通道索引列表"""
        if self._conn_type == "ext-differential":
            return TERMINAL_6I.get(terminal, ["6", "7", "8"])
        else:
            # phase-differential (2I)
            return TERMINAL_2I.get(terminal, ["6"])

    # ══════════════════════════════════════════
    # 节点构建
    # ══════════════════════════════════════════

    def _build_zero_current_statics(self) -> Dict:
        """构建零电流静态字典：保留前端电压通道，所有电流通道归零"""
        statics = {}
        # 保留前端的电压通道
        for ch, layers in self._statics_base.items():
            statics[ch] = dict(layers)
        # 电流通道强制归零 (layer "1" = amplitude + phase)
        for ch_key in ALL_CURRENT_CHANNELS:
            statics[ch_key] = {"1": [0.0, 0.0]}
        return statics

    def _build_fault_statics_6i(self, channels: List[List[float]]) -> Dict:
        """6I 模式：将 compute_6ch 的 6 路电流合并到 statics"""
        statics = {}
        for ch, layers in self._statics_base.items():
            statics[ch] = dict(layers)

        # 先归零所有电流通道 (layer "1" = amplitude + phase)
        for ch_key in ALL_CURRENT_CHANNELS:
            statics[ch_key] = {"1": [0.0, 0.0]}

        # 填入计算的电流
        ch1_keys = self._resolve_channels(self._i1_terminal)
        ch2_keys = self._resolve_channels(self._i2_terminal)
        for i, ch_key in enumerate(ch1_keys):
            statics[ch_key] = {"1": list(channels[i])}
        for i, ch_key in enumerate(ch2_keys):
            statics[ch_key] = {"1": list(channels[3 + i])}
        return statics

    def _build_fault_statics_2i(self, side1: List[float], side2: List[float]) -> Dict:
        """2I 模式：将 compute_2i 的 2 路电流输出到对应的并联通道"""
        statics = {}
        for ch, layers in self._statics_base.items():
            statics[ch] = dict(layers)

        # 先归零所有电流通道 (layer "1" = amplitude + phase)
        for ch_key in ALL_CURRENT_CHANNELS:
            statics[ch_key] = {"1": [0.0, 0.0]}

        # 侧1：所有并联通道输出相同电流
        ch1_keys = self._resolve_channels(self._i1_terminal)
        for ch_key in ch1_keys:
            statics[ch_key] = {"1": list(side1)}

        # 侧2：所有并联通道输出相同电流
        ch2_keys = self._resolve_channels(self._i2_terminal)
        for ch_key in ch2_keys:
            statics[ch_key] = {"1": list(side2)}

        return statics

    def _build_prefault_node(self) -> ApiNodeData:
        """Node 1: 故障前状态 — 零电流 + 电压，超时跳转 Node 2"""
        zero_statics = self._build_zero_current_statics()
        n = ApiNodeData(mode=1)
        n.base = self.physDictToReg(zero_statics)
        n.timeoutMs = self._pre_fault_duration_ms
        n.timeoutId = 2
        return n

    def _build_fault_node_6i(self, channels: List[List[float]]) -> ApiNodeData:
        """Node 2 (6I): 故障态 — 6 路电流，DI 匹配或超时跳转 Node 3"""
        fault_statics = self._build_fault_statics_6i(channels)
        n = ApiNodeData(mode=1)
        n.base = self.physDictToReg(fault_statics)
        n.diMatchMask = self._logicMask
        n.diMatchId = 3
        n.timeoutMs = self._fault_duration_ms
        n.timeoutId = 3
        return n

    def _build_fault_node_2i(self, side1: List[float], side2: List[float]) -> ApiNodeData:
        """Node 2 (2I): 故障态 — 2 路电流（并联），DI 匹配或超时跳转 Node 3"""
        fault_statics = self._build_fault_statics_2i(side1, side2)
        n = ApiNodeData(mode=1)
        n.base = self.physDictToReg(fault_statics)
        n.diMatchMask = self._logicMask
        n.diMatchId = 3
        n.timeoutMs = self._fault_duration_ms
        n.timeoutId = 3
        return n

    def _build_idle_node(self) -> ApiNodeData:
        """Node 3: 挂起态 — 零电流 + 电压，无超时无 DI，等待手动触发"""
        zero_statics = self._build_zero_current_statics()
        n = ApiNodeData(mode=1)
        n.base = self.physDictToReg(zero_statics)
        return n

    # ══════════════════════════════════════════
    # 计算并构建故障节点（统一入口）
    # ══════════════════════════════════════════

    def _compute_and_build_fault(self, ir: float, id_val: float) -> ApiNodeData:
        """根据接线类型计算电流并构建 Node 2"""
        if self._conn_type == "ext-differential":
            channels = compute_6ch(ir, id_val, self._payload)
            return self._build_fault_node_6i(channels)
        else:
            sides = compute_2i(ir, id_val, self._payload)
            return self._build_fault_node_2i(sides[0], sides[1])

    def _compute_report_currents(self, ir: float, id_val: float) -> tuple:
        """计算用于 report 的 i1/i2 幅值"""
        if self._conn_type == "ext-differential":
            channels = compute_6ch(ir, id_val, self._payload)
            return channels[0][0], channels[3][0]
        else:
            sides = compute_2i(ir, id_val, self._payload)
            return sides[0][0], sides[1][0]

    # ══════════════════════════════════════════
    # 主运行循环
    # ══════════════════════════════════════════

    async def _run(self):
        """主协程：预热 → 构建固定节点 → 逐点执行"""
        logger.info("Preheat 500ms...")
        await asyncio.sleep(0.5)
        if not self.isActive:
            return

        self._fsmState = "RUNNING"

        # 预编译 Node 1 和 Node 3（整个试验中不变）
        nodes_fixed = {}
        if self._simulate_pre_fault:
            nodes_fixed[1] = self._build_prefault_node()
        nodes_fixed[3] = self._build_idle_node()
        self.ctrl.upsertNodes(nodes_fixed)

        await asyncio.sleep(0.05)
        if not self.isActive:
            return

        # 根据项目类型执行
        if self._active_project == "ratio-fixed":
            await self._run_fixed()
        elif self._active_project == "ratio-search":
            await self._run_search()
        else:
            logger.warning(f"Unsupported project: {self._active_project}")

        # 全部完成
        if self.isActive:
            self._fsmState = "DONE"
            self.ctrl.stopTest()

    # ══════════════════════════════════════════
    # 单次测试循环（公共逻辑）
    # ══════════════════════════════════════════

    async def _test_one_point(self, ir: float, id_val: float) -> bool:
        """
        执行一次单点测试：计算电流 → 写入 Node 2 → 触发 → 等待结果
        返回：是否 DI 动作 (True = 动作)
        """
        if not self.isActive:
            return False

        # 1. 计算并构建 Node 2（自动区分 6I/2I）
        fault_node = self._compute_and_build_fault(ir, id_val)

        # 2. 更新 Node 2
        self.ctrl.upsertNodes({2: fault_node})

        await asyncio.sleep(0.05)
        if not self.isActive:
            return False

        # 3. 重置状态，触发测试
        self._di_tripped = False
        self._current_node = 0
        self._node_done.clear()

        start_node = 1 if self._simulate_pre_fault else 2
        self.ctrl.trigNode(start_node)

        # 4. 等待 Node 3 被激活（说明 Node 2 已完成），30s 超时保护
        try:
            await asyncio.wait_for(self._node_done.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            logger.error("Node 3 wait timeout (30s), aborting point")
            return False

        return self._di_tripped

    # ══════════════════════════════════════════
    # ratio-fixed: 定点测试
    # ══════════════════════════════════════════

    async def _run_fixed(self):
        """对每个定点 {id, x, y}，执行一次测试并上报"""
        for pt in self._points:
            if not self.isActive:
                return

            pt_id = pt.get("id", 0)
            ir = pt.get("x", 0.0)
            id_val = pt.get("y", 0.0)

            logger.info(f"Fixed #{pt_id}: ir={ir}, id={id_val}")

            tripped = await self._test_one_point(ir, id_val)

            # 发送 value_update（右图轨迹点）
            self.ctrl._send({
                "type": "value_update",
                "id": pt_id,
                "x": ir,
                "y": round(id_val, 6)
            })

            # 只有动作了才上报 report
            if tripped:
                i1_amp, i2_amp = self._compute_report_currents(ir, id_val)
                self.ctrl.sendReport({
                    "points": [{
                        "id": pt_id,
                        "x": ir,
                        "y": round(id_val, 6),
                        "i1": round(i1_amp, 4),
                        "i2": round(i2_amp, 4),
                    }]
                })

    # ══════════════════════════════════════════
    # ratio-search: 二分搜索
    # ══════════════════════════════════════════

    async def _run_search(self):
        """对每个搜索点 {id, x, y:[y0,y1]}，二分搜索动作边界"""
        precision = self._search_cfg.get("precision", 0.01)
        method = self._search_cfg.get("method", "binary")

        for pt in self._points:
            if not self.isActive:
                return

            pt_id = pt.get("id", 0)
            x_val = pt.get("x")
            y_val = pt.get("y")

            # 判断搜索方向：哪个是区间就搜索哪个
            if isinstance(y_val, list) and len(y_val) >= 2:
                # ratio-search: 固定 x=ir, 搜索 y=id
                ir_fixed = x_val
                search_min, search_max = y_val[0], y_val[1]
                await self._binary_search(
                    pt_id, ir_fixed, search_min, search_max, precision,
                    axis="y"
                )
            elif isinstance(x_val, list) and len(x_val) >= 2:
                # harmonic-search: 固定 y=id, 搜索 x
                id_fixed = y_val
                search_min, search_max = x_val[0], x_val[1]
                await self._binary_search(
                    pt_id, id_fixed, search_min, search_max, precision,
                    axis="x"
                )
            else:
                logger.warning(f"Point #{pt_id}: neither x nor y is a range, skipping")

    async def _binary_search(self, pt_id: int, fixed_val: float,
                              search_min: float, search_max: float,
                              precision: float, axis: str):
        """
        通用二分搜索。
        axis="y": 固定 ir=fixed_val, 搜索 id 在 [search_min, search_max]
        axis="x": 固定 id=fixed_val, 搜索 ir 在 [search_min, search_max]
        """
        lo, hi = search_min, search_max
        result_val = lo
        result_i1 = 0.0
        result_i2 = 0.0
        found_trip = False

        logger.info(f"Search #{pt_id}: axis={axis}, fixed={fixed_val}, "
                    f"range=[{lo}, {hi}], precision={precision}")

        while (hi - lo) > precision:
            if not self.isActive:
                return

            mid = (lo + hi) / 2.0

            # 根据搜索轴确定 ir 和 id
            if axis == "y":
                ir, id_val = fixed_val, mid
            else:
                ir, id_val = mid, fixed_val

            # 发送 value_update（右图轨迹）
            self.ctrl._send({
                "type": "value_update",
                "id": pt_id,
                "x": round(ir, 6),
                "y": round(id_val, 6)
            })

            tripped = await self._test_one_point(ir, id_val)

            if tripped:
                hi = mid
                found_trip = True
                result_val = mid
                result_i1, result_i2 = self._compute_report_currents(ir, id_val)
            else:
                lo = mid

            logger.info(f"  #{pt_id} mid={mid:.4f} tripped={tripped} → [{lo:.4f}, {hi:.4f}]")

        # 只有找到动作边界才上报 report
        if found_trip:
            if axis == "y":
                report_x = fixed_val
                report_id = result_val
            else:
                report_x = result_val
                report_id = fixed_val

            self.ctrl.sendReport({
                "points": [{
                    "id": pt_id,
                    "x": round(report_x, 6),
                    "y": round(report_id, 6),
                    "i1": round(result_i1, 4),
                    "i2": round(result_i2, 4),
                }]
            })

    # ══════════════════════════════════════════
    # 硬件回调
    # ══════════════════════════════════════════

    def onUpdate(self, nodeId: int, tick: int, hw_ts: int):
        """节点跳转回调：当 nodeId=3 且 tick=0 时，说明 Node 2 已结束"""
        self._current_node = nodeId
        if nodeId == 3 and tick == 0 and self._node_done:
            self._node_done.set()

    def onDi(self, di: int, hw_ts: int):
        """
        DI 变化回调。
        OR/AND 逻辑已由硬件 FSM 的 diMatchMask 处理。
        此处仅做标记，用于区分 Node 2 是 DI 触发跳转还是超时跳转。
        """
        if self._fsmState != "RUNNING":
            return
        if self._current_node == 2:
            self._di_tripped = True

    def _onStop(self):
        self._fsmState = "IDLE"
        if self._node_done:
            self._node_done.set()
        logger.info("DifferentialTest stopped.")

    def onWebCommand(self, msg: Dict[str, Any]):
        cmd = msg.get("cmd")
        if cmd == "stop":
            self.ctrl.stopTest()
