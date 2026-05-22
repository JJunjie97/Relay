import asyncio
import time
import importlib
import pkgutil
import inspect
from typing import Dict, Any

from utils.SysLogger import GetLogger
from api.BaseApi import ApiNodeData
from logic.Calibration import calib
from api.BaseApi import BaseApi
import api
from logic.USEEngine import USENode
from logic.FPGACodec import HWCodec, HWConfig

logger = GetLogger("TestCtrl")


class TestCtrl:
    def __init__(self, engine, hwProtect, wsSend):
        self.engine = engine
        self._hwProtect = hwProtect
        self._wsSend = wsSend

        self.running = False
        self.stopping = False
        self._module = None
        self.activeApi = None
        self.di = 0
        self.do = 0
        self.state = {i: {} for i in range(16)}
        self.nodes = {}
        self.errorReason = None
        self._lastTelemetryTs = 0.0
        self._stopEvent = asyncio.Event()
        self._stopEvent.set()
        self._startLock = asyncio.Lock()
        
        self._preloadZeroCalibration()

    def _preloadZeroCalibration(self):
        baseline_dict = {}
        for ch_idx in range(12):  # Only V channels (0, 2, 4, 6, 8, 10) and I channels (1, 3, 5, 7, 9, 11)
            hw_ch = HWConfig.MapChannel(ch_idx)
            dc_amp_reg, freq_reg = calib.PhysToReg(hw_ch, 0, 0.0, 50.0)
            baseline_dict[hw_ch] = {
                0: [dc_amp_reg, freq_reg]  # Only calibrate layer 0 (DC static bias / frequency)
            }
        
        calib_frames = self._compileDictToFrames(baseline_dict, HWCodec.DDS_WR_SHADOW)
        n0 = self.engine.nodes.get(0x0000)
        if n0:
            n0.baseFrame = [
                HWCodec.FRAME_SYS_RESET,
                HWCodec.BuildSystemFrame(HWCodec.SYS_SET_DBNC, 61)
            ] + calib_frames + [
                HWCodec.FRAME_SYS_START
            ]
            logger.info(f"Preloaded 12-channel zero calibration frames into Node 0x0000 (total {len(calib_frames)} frames)")

    # ── Engine event callback (injected as emitEvent) ──

    async def onEvent(self, evt):
        code = evt[0]

        if code == 0:  # VALUE_UPDATE
            nodeId, tick, ts = evt[1], evt[2], evt[3]
            if nodeId == 0xFFFF and self.running:
                if self.engine.trigTarget == 0x0000 or self.engine.nodeId == 0x0000:
                    return
                self.errorReason = self.errorReason or self.engine.errorReason
                self.engine.errorReason = None
                self._doStop()
                return
            
            self._handleValueUpdate(nodeId, tick)

            if self.activeApi and self.activeApi.isActive:
                self.activeApi.onUpdate(nodeId, tick, ts)

        elif code == 1:  # DI_CHANGE
            self.di = evt[1]
            self.sendDi()
            if self.activeApi and self.activeApi.isActive:
                self.activeApi.onDi(evt[1], evt[2])

        elif code == 2:  # DO_CHANGE
            self.do = evt[1]
            self.sendDo()

    # ── Lifecycle ──

    async def startTest(self, msg):
        async with self._startLock:
            if self.running:
                self.engine.manualTrig(0xFFFF)
                await self._stopEvent.wait()

            self._stopEvent.clear()
            self._module = msg.get("module", "Unknown")
            self.di = 0
            self.do = 0
            self.state = {i: {} for i in range(16)}
            self.nodes = {}
            dbncPhys = msg.get("debounce", 20)
            self.engine.setDebounce(HWConfig.ConvertDbncToReg(dbncPhys))

            self.activeApi = self._createApi(self._module)
            if not self.activeApi:
                self.errorReason = f"API module '{self._module}' not found."
                self._doStop()
                return
                
            self.running = True
            self.stopping = False
            self.setAmplifier(True)
            self.engine.manualTrig(0x0000)

            await asyncio.sleep(0.1)

            self.activeApi.setup(self, msg.get("params", {}))

    def stopTest(self, reason: str = None):
        if not self.running or self.stopping:
            return
        self.stopping = True
        if reason:
            self.errorReason = reason
        self.setAmplifier(False)
        self.engine.manualTrig(0xFFFF)

    def _doStop(self):
        self.setAmplifier(False)
        reason = self.errorReason
        self.errorReason = None
        if self.activeApi:
            self.activeApi.onStop()
            self.activeApi = None
        if reason:
            logger.error(f"Engine forced terminal: {reason}")
            self.sendError(reason)
        self.sendStop()
        self.state = {i: {} for i in range(16)}
        self.nodes = {}
        self.running = False
        self.stopping = False
        self._module = None
        self._stopEvent.set()

    # ── API module interface ──

    def upsertNodes(self, apiNodes: Dict[int, ApiNodeData]) -> bool:
        if self.stopping or not self.running:
            return False
        compiledNodes = {}
        for nodeId, apiNode in apiNodes.items():
            self.nodes[nodeId] = apiNode
            try:
                compiledNodes[nodeId] = self._compileNode(apiNode)
                print(f"[TestCtrl] compiled Node {nodeId}")
            except Exception as e:
                import traceback
                print(f"[TestCtrl] Failed to compile Node {nodeId}: {e}")
                traceback.print_exc()
        self.engine.upsertNodes(compiledNodes)
        return True

    def trigNode(self, nodeId: int) -> bool:
        if self.stopping or not self.running:
            return False
        self.engine.manualTrig(nodeId)
        return True

    def sendUpdate(self, state: dict):
        self.state = state
        self._send({"type": "value_update", "static": state})

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

    # ── Amplifier control ──

    def setAmplifier(self, active: bool):
        if self._hwProtect:
            self._hwProtect.SetAmplifierEnable(active)

    # ── Internal ──

    def _send(self, data: dict):
        if self._module:
            data["module"] = self._module
        asyncio.ensure_future(self._wsSend(data))

    def _createApi(self, module: str):
        for _, moduleName, _ in pkgutil.iter_modules(api.__path__):
            if not moduleName.startswith("Api") or moduleName == "ApiNodeData":
                continue
                
            try:
                mod = importlib.import_module(f"api.{moduleName}")
                apiClasses = inspect.getmembers(mod, lambda c: inspect.isclass(c) and issubclass(c, BaseApi) and c is not BaseApi)
                
                for _, obj in apiClasses:
                    keys = getattr(obj, "MODULE_KEYS", [])
                    if not keys and hasattr(obj, "MODULE_KEY"):
                        keys = [obj.MODULE_KEY]
                    if module in keys:
                        return obj()
            except Exception as e:
                logger.warning(f"Failed to load API module {moduleName}: {e}")
                    
        logger.error(f"API module '{module}' not implemented or not found.")
        return None

    def _compileDictToFrames(self, dDict: dict, cmdCode: int) -> list:
        if not dDict: return []
        
        masks = {}
        for ch, layers in dDict.items():
            chBit = 1 << ch
            for layer, vals in layers.items():
                key = (layer, vals[0], vals[1])
                masks[key] = masks.get(key, 0) | chBit
                
        frames = []
        for (layer, aRegU32, pRegU32), chMask in masks.items():
            regIndex = (layer - 1) & 0xFF
            frames.append(HWCodec.BuildParamFrame(cmdCode, regIndex, chMask, aRegU32, pRegU32))
        return frames

    def _compileNode(self, n: ApiNodeData) -> USENode:
        baseFrame = self._compileDictToFrames(n.base, HWCodec.DDS_WR_SHADOW) if n.base else []
        
        # Guard zero-calibration & SYS_START prefix for Node 0 to prevent dry-flatline hardware output
        # If compiling Node 0, we retrieve the preloaded hardware reset, debounce, and calibration frames
        # and append the API's custom static registers, finalized by a guaranteed SYS_START.
        is_node_0 = False
        for nid, node_data in self.nodes.items():
            if node_data is n and nid == 0:
                is_node_0 = True
                break
                
        if is_node_0:
            existing_n0 = self.engine.nodes.get(0x0000)
            if existing_n0 and existing_n0.baseFrame:
                prefix = []
                for f in existing_n0.baseFrame:
                    if f != HWCodec.FRAME_SYS_START:
                        prefix.append(f)
                baseFrame = prefix + baseFrame + [HWCodec.FRAME_SYS_START]
                logger.info("Successfully merged zero-calibration and SYS_START into newly compiled Node 0")

        resetFrame = self._compileDictToFrames(n.reset, HWCodec.DDS_WR_STAGE) if n.reset else []
        stepFrames = [self._compileDictToFrames(step, HWCodec.DDS_STEP_SHADOW) for step in n.steps] if n.steps else None
        gateFrames = None
        if n.gate and n.steps and n.interval is not None and n.base:
            ch = n.gate[0]
            currentPhase = n.gate[1]
            fU32 = n.base[ch][0][1]
            deltaPhaseU32 = int(fU32 * HWConfig.PHASE_PER_FREQ_MS * n.interval)
            
            gateFrames = []
            for _ in range(len(n.steps)):
                gateFrames.append([HWCodec.BuildPhaseGateFrame(ch, currentPhase & 0xFFFFFFFF)])
                currentPhase += deltaPhaseU32
            
        return USENode(
            mode=n.mode,
            interval=n.interval,
            resetTime=n.resetTime,
            baseFrame=baseFrame,
            resetFrame=resetFrame,
            stepFrames=stepFrames,
            gateFrames=gateFrames,
            resetDo=n.resetDo,
            doActions=n.doActions,
            countOverId=n.countOverId,
            diMatchMask=n.diMatchMask,
            diMatchId=n.diMatchId,
            timeoutMs=n.timeoutMs,
            timeoutId=n.timeoutId
        )

    def _handleValueUpdate(self, nodeId: int, tick: int):
        if nodeId == 0x0000 or nodeId == 0xFFFF:
            self.state = {i: {} for i in range(16)}
            return
            
        apiNode = self.nodes.get(nodeId)
        if not apiNode:
            return

        targetDict = None
        if tick == 0:
            targetDict = apiNode.base
            for ch, layers in targetDict.items():
                for l, vals in layers.items():
                    self.state[ch][l] = list(vals)
        elif tick == -1:
            targetDict = apiNode.reset
            # Do NOT overwrite self.state. tick=-1 is a temporary shadow phase.
        elif tick > 0 and apiNode.steps:
            targetDict = apiNode.steps[tick - 1]
            for ch, layers in targetDict.items():
                for l, vals in layers.items():
                    self.state[ch][l][0] = (self.state[ch][l][0] + vals[0]) & 0xFFFFFFFF
                    self.state[ch][l][1] = (self.state[ch][l][1] + vals[1]) & 0xFFFFFFFF

        now = time.monotonic()
        if targetDict and ((tick <= 0) or (apiNode.steps and tick == len(apiNode.steps)) or now - self._lastTelemetryTs >= 0.05):
            self._lastTelemetryTs = now
            self._send({
                "type": "value_update",
                "static": {
                    str(HWConfig.UnmapChannel(ch)): {
                        str(l): list(calib.RegToPhys(ch, l, *targetDict[ch][l])) if tick == -1 else list(calib.RegToPhys(ch, l, *self.state[ch][l]))
                        for l in layers
                    }
                    for ch, layers in targetDict.items()
                }
            })

    # ── Web command entry ──

    async def HandleCommand(self, msg: dict):
        cmd = msg.get("cmd")
        if cmd == "start":
            await self.startTest(msg)
        elif cmd == "stop":
            self.stopTest()
        else:
            if self.activeApi:
                self.activeApi.onWebCommand(msg)
