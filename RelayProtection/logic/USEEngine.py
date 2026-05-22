import asyncio
import time
from dataclasses import dataclass
from typing import List, Optional
from logic.FPGACodec import HWCodec

@dataclass(slots=True)
class USENode:
    mode: int
    
    interval: Optional[int] = None
    resetTime: Optional[int] = None

    baseFrame: Optional[List[bytes]] = None
    resetFrame: Optional[List[bytes]] = None
    stepFrames: Optional[List[List[bytes]]] = None
    gateFrames: Optional[List[List[bytes]]] = None

    resetDo: Optional[int] = None
    doActions: Optional[List[int]] = None

    countOverId: Optional[int] = None
    diMatchMask: Optional[int] = None
    diMatchId: Optional[int] = None
    timeoutMs: Optional[int] = None
    timeoutId: Optional[int] = None

class USEEngine:
    def __init__(self, hwGateway, emitEvent):
        self._emit = emitEvent
        self._spawn = asyncio.create_task
        self._send = hwGateway.SendBytes
        
        self.triggerEvent = asyncio.Event()
        self._doTask = None

        self.nodes = {
            0x0000: USENode(mode=1, baseFrame=[HWCodec.FRAME_SYS_RESET, HWCodec.BuildSystemFrame(HWCodec.SYS_SET_DBNC, 61), HWCodec.FRAME_SYS_START]),
            0xFFFF: USENode(mode=1, baseFrame=[HWCodec.FRAME_SYS_RESET])
        }
        self.nodeId = 0xFFFF
        self.trigTarget = None

        self.ackEvent = asyncio.Event()
        self.ackTs = 0
        self.ackCounter = 0
        self.sentCount = 0
        self.hwLock = asyncio.Lock()

        self.diNow = 0
        self.diStart = 0
        self.diPrev = 0
        self.dbncUs = int(61 * 327.68)

        self._timeoutAt = None
        self._timeoutS = None
        self._timeoutId = 0
        self._diMask = 0
        self._diRef = 0
        self._diIsAnd = False
        self._diMatchId = 0

        self.errorReason = None
        self.upFrame = [HWCodec.FRAME_SYS_UPDATE]
        self.syncFrame = [HWCodec.FRAME_SYS_SYNC]

    def start(self):
        self._spawn(self.coreLoop())

    def setDebounce(self, dbnc: int):
        self.dbncUs = int(dbnc * 327.68)
        self.nodes[0x0000].baseFrame[-1] = HWCodec.BuildSystemFrame(HWCodec.SYS_SET_DBNC, dbnc)

    def upsertNodes(self, newNodes: dict):
        self.nodes.update(newNodes)

    def HandleHwFeedback(self, timestamp: int, statusCode: int) -> None:
        if statusCode == 0x0000:
            self.ackTs = timestamp
            self.ackCounter += 1
            self.ackEvent.set()
        elif statusCode == 0xFFFF:
            self.ackTs = -1
            self.ackEvent.set()
        else:
            self.diNow = statusCode & 0xFF
            self.triggerEvent.set()
            self._emitDi(self.diNow, timestamp - self.dbncUs)

    def flushNoAck(self, frames: List[bytes]):
        send = self._send
        for f in frames:
            send(f)
            self.sentCount += 1

    async def flush(self, frames: List[bytes]) -> int:
        if not frames: return self.ackTs
        async with self.hwLock:
            target = self.sentCount + len(frames)
            self.flushNoAck(frames)
            return await self.waitForAck(target)

    async def waitForAck(self, target: int) -> int:
        while self.ackCounter < target:
            self.ackEvent.clear()
            try:
                await asyncio.wait_for(self.ackEvent.wait(), timeout=0.1)
            except asyncio.TimeoutError:
                self.errorReason = "ACK timeout"
                self.manualTrig(0xFFFF)
                return -1
            if self.ackTs < 0:
                self.errorReason = "HW communication error"
                self.manualTrig(0xFFFF)
                return -1
        return self.ackTs

    def spawnDo(self, t0: float):
        if not self.node.doActions: return
        self._doTask = self._spawn(self._doLoop(t0))

    async def _doLoop(self, t0: float):
        for packed in self.node.doActions:
            await asyncio.sleep(t0 + (((packed >> 8) & 0xFFFF) / 1000.0) - time.perf_counter())
            self._emitDo(packed & 0xFF, await asyncio.shield(self.setDo(packed & 0xFF)))

    async def setDo(self, doValue: int) -> int:
        return await self.flush([HWCodec.BuildSystemFrame(HWCodec.SYS_SET_DO, doValue)])

    # emitEvent codes:
    # 0: VALUE_UPDATE  [0, nodeId, tick, timestamp]
    # 1: DI_CHANGE     [1, diMask, timestamp]
    # 2: DO_CHANGE     [2, doMask, timestamp]
    def _emitUp(self, tick: int, tUp: int):
        self._spawn(self._emit([0, self.nodeId, tick, tUp]))

    def _emitDo(self, doValue: int, timestamp: int):
        self._spawn(self._emit([2, doValue, timestamp]))

    def _emitDi(self, diValue: int, timestamp: int):
        self._spawn(self._emit([1, diValue, timestamp]))

    def startTimeout(self, t: float):
        self._timeoutAt = t + self._timeoutS if self._timeoutS else None

    async def sleepForever(self, t: float) -> int:
        return r if (r := await self.sleep(t + 1000)) is not None else 0xFFFF

    def manualTrig(self, targetId: int):
        self.trigTarget = targetId

    def _popManualTrig(self) -> Optional[int]:
        if (tgt := self.trigTarget) is not None:
            self.trigTarget = None
            return tgt
        return None

    def _evalTrig(self) -> Optional[int]:
        if (tgt := self._popManualTrig()) is not None: return tgt
        if self._timeoutAt is not None and time.perf_counter() >= self._timeoutAt:
            return self._timeoutId
        if self._diMask:
            val = (self.diNow ^ self._diRef) & self._diMask
            if (val == self._diMask) if self._diIsAnd else val:
                return self._diMatchId
        return None

    async def sleepWait(self, wake: float) -> Optional[int]:
        while (remain := wake - time.perf_counter()) > 0.005:
            if (tgt := self._popManualTrig()) is not None: return tgt
            await asyncio.sleep(min(remain, 0.025))
        await asyncio.sleep(0)
        while time.perf_counter() < wake: pass
        return self._popManualTrig()

    async def sleep(self, wake: float) -> Optional[int]:
        if self._timeoutAt is not None and self._timeoutAt < wake:
            wake = self._timeoutAt
        while (remain := wake - time.perf_counter()) > 0.005:
            self.triggerEvent.clear()
            if (r := self._evalTrig()) is not None: return r
            try: await asyncio.wait_for(self.triggerEvent.wait(), timeout=min(remain, 0.025))
            except asyncio.TimeoutError: pass
        await asyncio.sleep(0)
        while time.perf_counter() < wake: pass
        return self._evalTrig()

    async def runStatic(self) -> int:
        self._emitUp(0, await self.flush(self.node.baseFrame + self.upFrame))
        t = time.perf_counter()
        self.spawnDo(t)
        self.startTimeout(t)
        return await self.sleepForever(t)

    async def runSweep(self) -> int:
        if not self.node.stepFrames: return await self.runStatic()

        count = len(self.node.stepFrames)
        iv = self.node.interval / 1000.0

        self._emitUp(0, await self.flush(self.node.baseFrame + self.upFrame))
        t = time.perf_counter()
        self.spawnDo(t)
        self.startTimeout(t)
        await self.flush(self.syncFrame + self.node.stepFrames[0])
        if (r := await self.sleep(t := t + iv)) is not None: return r

        for tick in range(1, count + 1):
            self._emitUp(tick, await self.flush(self.upFrame))
            if tick < count:
                await self.flush(self.node.stepFrames[tick])
            if (r := await self.sleep(t := t + iv)) is not None: return r

        if self.node.countOverId is not None:
            return self.node.countOverId
        return await self.sleepForever(t)

    async def runReset(self) -> int:
        count = len(self.node.stepFrames)
        enterDo, exitDo = self.node.resetDo & 0xFF, (self.node.resetDo >> 8) & 0xFF
        iv, rt = self.node.interval / 1000.0, self.node.resetTime / 1000.0

        self._emitUp(-1, await self.flush(self.node.baseFrame + self.node.resetFrame + self.upFrame))
        t = time.perf_counter()
        self._emitDo(enterDo, await self.setDo(enterDo))
        await self.flush(self.node.baseFrame)
        self.startTimeout(t)
        # self.spawnDo(t)
        if (r := await self.sleepWait(t := t + rt)) is not None: return r

        self._emitUp(0, await self.flush(self.upFrame))
        self._emitDo(exitDo, await self.setDo(exitDo))
        if (r := await self.sleep(t := t + iv)) is not None: return r

        for tick in range(1, count + 1):
            self._emitUp(-1, await self.flush(self.upFrame))
            self._emitDo(enterDo, await self.setDo(enterDo))
            await self.flush(self.node.stepFrames[tick-1])
            if (r := await self.sleepWait(t := t + rt)) is not None: return r

            self._emitUp(tick, await self.flush(self.upFrame))
            self._emitDo(exitDo, await self.setDo(exitDo))
            if (r := await self.sleep(t := t + iv)) is not None: return r

        if self.node.countOverId is not None:
            return self.node.countOverId
        return await self.sleepForever(t)

    async def runDcComp(self) -> int:
        if not self.node.stepFrames or not self.node.gateFrames:
            return await self.runStatic()
        count = len(self.node.stepFrames)

        burst = self.node.baseFrame + self.node.stepFrames[0] + self.node.gateFrames[0]
        
        async with self.hwLock:
            target = self.sentCount + len(burst)
            full = burst + self.syncFrame + self.node.stepFrames[1]
            self.flushNoAck(full)
            self._emitUp(0, await self.waitForAck(target))

        t = time.perf_counter()
        self.startTimeout(t)
        self.spawnDo(t)

        if (r := self._evalTrig()) is not None: return r

        for N in range(2, count):
            async with self.hwLock:
                target = self.sentCount + len(self.node.gateFrames[N-1])
                batch = self.node.gateFrames[N-1] + self.node.stepFrames[N]
                self.flushNoAck(batch)
                self._emitUp(N - 1, await self.waitForAck(target))

            if (r := self._evalTrig()) is not None: return r

        async with self.hwLock:
            target = self.sentCount + len(self.node.gateFrames[count-1])
            self.flushNoAck(self.node.gateFrames[count-1])
            self._emitUp(count - 1, await self.waitForAck(target))

        return await self.sleepForever(t)

    async def coreLoop(self):
        while True:
            if (n := self.nodes.get(self.nodeId)) is None:
                self.nodeId = 0xFFFF
                n = self.nodes[0xFFFF]
            self.node = n

            if self._doTask:
                self._doTask.cancel()
                self._doTask = None

            self.triggerEvent.clear()

            await self.flush(self.syncFrame)

            self.diPrev = self.diNow

            if (tMs := n.timeoutMs) is not None:
                self._timeoutS = tMs / 1000.0
                self._timeoutId = n.timeoutId
            else:
                self._timeoutS = None

            if (dm := n.diMatchMask) is not None:
                self._diMask = dm & 0xFF
                self._diIsAnd = bool(dm & 0x100)
                self._diRef = (self.diPrev if dm & 0x200 else self.diStart) ^ (0xFF if dm & 0x400 else 0)
                self._diMatchId = n.diMatchId
            else:
                self._diMask = 0

            match n.mode:
                case 1: nextId = await self.runStatic()
                case 2: nextId = await self.runSweep()
                case 3: nextId = await self.runReset()
                # case 4: nextId = await self.runDcComp()
                case _: nextId = 0xFFFF

            if self.nodeId == 0x0000: self.diStart = self.diNow
            if nextId in (0x0000, 0xFFFF):
                self.nodes = {0x0000: self.nodes[0x0000], 0xFFFF: self.nodes[0xFFFF]}
                self.ackCounter = 0
                self.sentCount = 0
            self.nodeId = nextId