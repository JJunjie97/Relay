import asyncio
import time
from typing import Dict
from utils.SysLogger import GetLogger
from api.BaseApi import BaseApi, ApiNodeData
from logic.Calibration import calib
from logic.USEEngine import USENode
from logic.FPGACodec import HWCodec, HWConfig

logger = GetLogger("TestCtrl")


class TestCtrl:
    def __init__(self, engine, hwProtect, wsSend):
        self.engine = engine
        self._hwProtect = hwProtect
        self._wsSend = wsSend

        self.running = False
        self._module = None
        self.activeApi = None
        self.di = 0
        self.do = 0
        self.state = {ch: {} for ch in HWConfig.ACTIVE_CHANNELS}
        self.nodes = {}
        self.errorReason = None
        self._lastTelemetryTs = 0.0
        self._stopEvent = asyncio.Event()
        self._stopEvent.set()
        self._startLock = asyncio.Lock()
        
        self._initBaseNode()

    def _initBaseNode(self):
        baselineDict = {hwCh: {0: list(calib.PhysToReg(hwCh, 0, 0.0, 50.0))} for hwCh in HWConfig.ACTIVE_CHANNELS}
        self.engine.nodes[0x0000].baseFrame = (
            [HWCodec.FRAME_SYS_RESET]
            + self._compileDictToFrames(baselineDict, HWCodec.DDS_WR_SHADOW)
            + [HWCodec.FRAME_SYS_START, HWCodec.BuildSystemFrame(HWCodec.SYS_SET_DBNC, 61)]
        )
        logger.info("Initialized base node 0x0000 with 12-channel calibration")

    async def onEvent(self, evt):
        match evt[0]:
            case 0:
                nodeId, tick, ts = evt[1], evt[2], evt[3]
                if nodeId == 0xFFFF:
                    self.errorReason = self.errorReason or self.engine.errorReason
                    self.engine.errorReason = None
                    self._doStop()
                    return
                self._handleValueUpdate(nodeId, tick)
                if a := self.activeApi:
                    a.onUpdate(nodeId, tick, ts)
            case 1:
                self.di = evt[1]
                self.sendDi()
                if a := self.activeApi:
                    a.onDi(evt[1], evt[2])
            case 2:
                self.do = evt[1]
                self.sendDo()

    async def startTest(self, msg):
        async with self._startLock:
            if not self._stopEvent.is_set():
                self.engine.manualTrig(0xFFFF)
                await self._stopEvent.wait()
            self._stopEvent.clear()

            self._module = msg.get("module")
            self.activeApi = self._createApi(self._module) if self._module else None
            if not self.activeApi:
                self.errorReason = f"API module '{self._module or 'None'}' not found."
                self._doStop()
                return

            self.di = 0
            self.do = 0
            self.state = {ch: {} for ch in HWConfig.ACTIVE_CHANNELS}
            self.nodes = {}
            self.engine.setDebounce(HWConfig.ConvertDbncToReg(msg.get("debounce", 20)))
            self.engine.manualTrig(0x0000)            
            self.running = True
            self.setAmplifier(True)

            await asyncio.sleep(0.1)
            self.activeApi.setup(self, msg.get("params", {}))

    def stopTest(self, reason: str = None):
        if not self.running:
            return
        self.running = False
        if reason:
            self.errorReason = reason
        self.setAmplifier(False)
        self.engine.manualTrig(0xFFFF)

    def _doStop(self):
        self.setAmplifier(False)
        if reason := self.errorReason:
            self.errorReason = None
            logger.error(f"Engine forced terminal: {reason}")
            self.sendError(reason)
        if a := self.activeApi:
            a.onStop()
            self.activeApi = None
        self.sendStop()
        self.state = {ch: {} for ch in HWConfig.ACTIVE_CHANNELS}
        self.nodes = {}
        self.running = False
        self._module = None
        self._stopEvent.set()

    def upsertNodes(self, apiNodes: Dict[int, ApiNodeData]) -> bool:
        if not self.running:
            return False
        compiledNodes = {}
        for nodeId, apiNode in apiNodes.items():
            self.nodes[nodeId] = apiNode
            try:
                compiledNodes[nodeId] = self._compileNode(apiNode)
                logger.debug(f"Compiled Node {nodeId}")
            except Exception as e:
                logger.error(f"Failed to compile Node {nodeId}: {e}", exc_info=True)
        self.engine.upsertNodes(compiledNodes)
        return True

    def trigNode(self, nodeId: int) -> bool:
        if not self.running:
            return False
        self.engine.manualTrig(nodeId)
        return True

    def sendDi(self):
        self._send({"type": "value_update", "di": self.di})

    def sendDo(self):
        self._send({"type": "value_update", "do": self.do})

    def sendReport(self, data: dict):
        self._send({"type": "report", "data": data})

    def sendError(self, msg: str):
        self._send({"type": "error", "msg": msg})

    def sendStop(self):
        self._send({"type": "stop"})

    def sendLoad(self, thermal: float, active: bool):
        self._send({"type": "load", "thermal": thermal, "active": active})

    def sendValueUpdate(self, targetDict: dict, src: dict = None):
        self._send({
            "type": "value_update",
            "static": {
                str(HWConfig.UnmapChannel(ch)): {
                    str(l): list(calib.RegToPhys(ch, l, *(src[ch][l] if src else v))) for l, v in layers.items()
                } for ch, layers in targetDict.items()
            }
        })

    def setAmplifier(self, active: bool):
        self._hwProtect.SetAmplifierEnable(active)

    def _send(self, data: dict):
        if self._module:
            data["module"] = self._module
        asyncio.ensure_future(self._wsSend(data))

    def _createApi(self, module: str):
        api_instance = BaseApi.create(module)
        if not api_instance:
            logger.error(f"API module '{module}' not found.")
        return api_instance

    def _compileDictToFrames(self, dDict: dict, cmdCode: int) -> list:
        masks = {}
        for ch, layers in (dDict or {}).items():
            for l, (a, p) in layers.items():
                masks[(l, a, p)] = masks.get((l, a, p), 0) | (1 << ch)
        return [
            HWCodec.BuildParamFrame(cmdCode, (l - 1) & 0xFF, m, a, p)
            for (l, a, p), m in masks.items()
        ]

    def _compileNode(self, n: ApiNodeData) -> USENode:
        gateFrames = None
        if n.gate and n.steps and n.interval is not None and n.base:
            ch, phase = n.gate[0], n.gate[1]
            delta = int(n.base[ch][0][1] * HWConfig.PHASE_PER_FREQ_MS * n.interval)
            gateFrames = [[HWCodec.BuildPhaseGateFrame(ch, (phase + i * delta) & 0xFFFFFFFF)] for i in range(len(n.steps))]
            
        return USENode(
            mode=n.mode,
            interval=n.interval,
            resetTime=n.resetTime,
            baseFrame=self._compileDictToFrames(n.base, HWCodec.DDS_WR_SHADOW),
            resetFrame=self._compileDictToFrames(n.reset, HWCodec.DDS_WR_STAGE),
            stepFrames=[self._compileDictToFrames(s, HWCodec.DDS_STEP_SHADOW) for s in n.steps] if n.steps else None,
            gateFrames=gateFrames,
            resetDo=n.resetDo,
            doActions=n.doActions,
            countOverId=n.countOverId,
            diMatchMask=n.diMatchMask,
            diMatchId=n.diMatchId,
            timeoutMs=n.timeoutMs,
            timeoutId=n.timeoutId,
        )

    def _handleValueUpdate(self, nodeId: int, tick: int):
        if not (n := self.nodes.get(nodeId)):
            return
        match tick:
            case 0:
                if target := n.base:
                    for ch, layers in target.items():
                        self.state[ch].update({l: list(v) for l, v in layers.items()})
                    self.sendValueUpdate(target)
            case -1:
                if target := n.reset:
                    self.sendValueUpdate(target)
            case _:
                if target := n.steps[tick - 1]:
                    for ch, layers in target.items():
                        for l, (a, p) in layers.items():
                            self.state[ch][l][0] = (self.state[ch][l][0] + a) & 0xFFFFFFFF
                            self.state[ch][l][1] = (self.state[ch][l][1] + p) & 0xFFFFFFFF
                    if tick == len(n.steps) or time.monotonic() - self._lastTelemetryTs >= 0.05:
                        self._lastTelemetryTs = time.monotonic()
                        self.sendValueUpdate(target, self.state)

    async def HandleCommand(self, msg: dict):
        match msg.get("cmd"):
            case "start":
                await self.startTest(msg)
            case "stop":
                self.stopTest()
            case _:
                if a := self.activeApi:
                    a.onWebCommand(msg)
