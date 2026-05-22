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
    # emitEvent codes:
    # 0: VALUE_UPDATE  [0, nodeId, tick, timestamp]
    # 1: DI_CHANGE     [1, diMask, timestamp]
    # 2: DO_CHANGE     [2, doMask, timestamp]
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

        self.t0 = 0.0
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


    def start(self):
        self._spawn(self.coreLoop())

    def setDebounce(self, dbnc: int):
        self.dbncUs = int(dbnc * 327.68)
        self.nodes[0x0000].baseFrame[1] = HWCodec.BuildSystemFrame(HWCodec.SYS_SET_DBNC, dbnc)

    def manualTrig(self, targetId: int):
        self.trigTarget = targetId
        self.triggerEvent.set()

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
            self._spawn(self._emit([1, self.diNow, timestamp - self.dbncUs]))

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
            if self.ackCounter >= target:
                break
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
            doTs = await asyncio.shield(self.flush([HWCodec.BuildSystemFrame(HWCodec.SYS_SET_DO, packed & 0xFF)]))
            self._spawn(self._emit([2, packed & 0xFF, doTs]))

    def _prepTriggers(self):
        n = self.node
        self._timeoutS = n.timeoutMs / 1000.0 if n.timeoutMs is not None else None
        self._timeoutId = n.timeoutId
        dm = n.diMatchMask
        if dm is not None:
            self._diMask = dm & 0xFF
            self._diIsAnd = bool(dm & 0x100)
            self._diRef = (self.diPrev if dm & 0x200 else self.diStart) ^ (0xFF if dm & 0x400 else 0)
            self._diMatchId = n.diMatchId
        else:
            self._diMask = 0


    def _emitUp(self, tick: int, tUp: int):
        self._spawn(self._emit([0, self.nodeId, tick, tUp]))

    def _consumeManualTrig(self) -> Optional[int]:
        if self.trigTarget is not None:
            tgt = self.trigTarget
            self.trigTarget = None
            self.triggerEvent.clear()
            return tgt
        return None

    def _evalTrig(self) -> Optional[int]:
        tgt = self._consumeManualTrig()
        if tgt is not None: return tgt
        if self._timeoutAt is not None and time.perf_counter() >= self._timeoutAt:
            return self._timeoutId
        if self._diMask:
            val = (self.diNow ^ self._diRef) & self._diMask
            if (val == self._diMask) if self._diIsAnd else val:
                return self._diMatchId
        return None

    async def sleepWait(self, wake: float) -> Optional[int]:
        while True:
            tgt = self._consumeManualTrig()
            if tgt is not None: return tgt
            remain = wake - time.perf_counter()
            if remain <= 0.005:
                await asyncio.sleep(0)
                while time.perf_counter() < wake: pass
                return self._consumeManualTrig()
            self.triggerEvent.clear()
            try: await asyncio.wait_for(self.triggerEvent.wait(), timeout=min(remain, 0.025))
            except asyncio.TimeoutError: pass

    async def sleep(self, wake: float) -> Optional[int]:
        if self._timeoutAt is not None and self._timeoutAt < wake:
            wake = self._timeoutAt
        while True:
            r = self._evalTrig()
            if r is not None: return r
            remain = wake - time.perf_counter()
            if remain <= 0.005:
                await asyncio.sleep(0)
                while time.perf_counter() < wake: pass
                return self._evalTrig()
            self.triggerEvent.clear()
            try: await asyncio.wait_for(self.triggerEvent.wait(), timeout=min(remain, 0.025))
            except asyncio.TimeoutError: pass

    async def runStatic(self) -> int:
        tUp = await self.flush(self.node.baseFrame + [HWCodec.FRAME_SYS_UPDATE])
        self.t0 = time.perf_counter()
        self._timeoutAt = self.t0 + self._timeoutS if self._timeoutS else None
        self.spawnDo(self.t0)
        self._emitUp(0, tUp)
        r = await self.sleep(self.t0 + 1000)
        return r if r is not None else 0xFFFF

    async def runSweep(self) -> int:
        if not self.node.stepFrames:
            return await self.runStatic()
        count = len(self.node.stepFrames)
        tUp = await self.flush(self.node.baseFrame + [HWCodec.FRAME_SYS_UPDATE])
        self.t0 = time.perf_counter()
        self._timeoutAt = self.t0 + self._timeoutS if self._timeoutS else None
        self.spawnDo(self.t0)
        await self.flush([HWCodec.FRAME_SYS_SYNC])
        await self.flush(self.node.stepFrames[0])
        self._emitUp(0, tUp)

        iv = self.node.interval / 1000.0
        tSw = self.t0 + iv
        r = await self.sleep(tSw)
        if r is not None: return r

        for tick in range(1, count):
            tUp = await self.flush([HWCodec.FRAME_SYS_UPDATE])
            await self.flush(self.node.stepFrames[tick])
            self._emitUp(tick, tUp)

            tSw += iv
            r = await self.sleep(tSw)
            if r is not None: return r

        tUp = await self.flush([HWCodec.FRAME_SYS_UPDATE])
        self._emitUp(count, tUp)
        tSw += iv
        r = await self.sleep(tSw)
        if r is not None: return r

        if self.node.countOverId is not None:
            return self.node.countOverId
        r = await self.sleep(self.t0 + 1000)
        return r if r is not None else 0xFFFF

    async def runReset(self) -> int:
        count = len(self.node.stepFrames)

        enterDo = self.node.resetDo & 0xFF
        exitDo = (self.node.resetDo >> 8) & 0xFF

        tUp = await self.flush(self.node.baseFrame + self.node.resetFrame + [HWCodec.FRAME_SYS_UPDATE])
        self.t0 = time.perf_counter()
        self._timeoutAt = self.t0 + self._timeoutS if self._timeoutS else None

        await self.flush([HWCodec.BuildSystemFrame(HWCodec.SYS_SET_DO, enterDo)])
        self.spawnDo(self.t0)

        await self.flush(self.node.baseFrame)
        self._emitUp(-1, tUp)

        iv = self.node.interval / 1000.0
        rt = self.node.resetTime / 1000.0
        tSw = self.t0 + rt
        r = await self.sleepWait(tSw)
        if r is not None: return r

        tUp = await self.flush([HWCodec.FRAME_SYS_UPDATE])
        await self.flush([HWCodec.BuildSystemFrame(HWCodec.SYS_SET_DO, exitDo)])
        self._emitUp(0, tUp)

        tSw += iv
        r = await self.sleep(tSw)
        if r is not None: return r

        for tick in range(1, count + 1):
            tUp = await self.flush([HWCodec.FRAME_SYS_UPDATE])
            await self.flush([HWCodec.BuildSystemFrame(HWCodec.SYS_SET_DO, enterDo)])
            await self.flush(self.node.stepFrames[tick-1])
            self._emitUp(-1, tUp)

            tSw += rt
            r = await self.sleepWait(tSw)
            if r is not None: return r

            tUp = await self.flush([HWCodec.FRAME_SYS_UPDATE])
            await self.flush([HWCodec.BuildSystemFrame(HWCodec.SYS_SET_DO, exitDo)])
            self._emitUp(tick, tUp)

            tSw += iv
            r = await self.sleep(tSw)
            if r is not None: return r

        if self.node.countOverId is not None:
            return self.node.countOverId
        r = await self.sleep(self.t0 + 1000)
        return r if r is not None else 0xFFFF

    async def runDcComp(self) -> int:
        if not self.node.stepFrames or not self.node.gateFrames:
            return await self.runStatic()
        count = len(self.node.stepFrames)

        burst = self.node.baseFrame + self.node.stepFrames[0] + self.node.gateFrames[0]
        
        async with self.hwLock:
            target = self.sentCount + len(burst)
            full = burst + [HWCodec.FRAME_SYS_SYNC] + self.node.stepFrames[1]
            self.flushNoAck(full)
            tUp = await self.waitForAck(target)

        self.t0 = time.perf_counter()
        self._timeoutAt = self.t0 + self._timeoutS if self._timeoutS else None
        self.spawnDo(self.t0)
        self._emitUp(0, tUp)

        r = self._evalTrig()
        if r is not None: return r

        for N in range(2, count):
            async with self.hwLock:
                target = self.sentCount + len(self.node.gateFrames[N-1])
                batch = self.node.gateFrames[N-1] + self.node.stepFrames[N]
                self.flushNoAck(batch)
                tUp = await self.waitForAck(target)
            self._emitUp(N - 1, tUp)

            r = self._evalTrig()
            if r is not None: return r

        async with self.hwLock:
            target = self.sentCount + len(self.node.gateFrames[count-1])
            self.flushNoAck(self.node.gateFrames[count-1])
            tUp = await self.waitForAck(target)
        self._emitUp(count - 1, tUp)

        r = await self.sleep(self.t0 + 1000)
        return r if r is not None else 0xFFFF

    async def coreLoop(self):
        while True:
            self.node = self.nodes.get(self.nodeId)
            if self.node is None:
                self.nodeId = 0xFFFF
                self.node = self.nodes[0xFFFF]

            if self._doTask:
                self._doTask.cancel()
                self._doTask = None
            await self.flush([HWCodec.FRAME_SYS_SYNC])
            self.diPrev = self.diNow
            self._prepTriggers()

            if self.node.mode == 1:
                nextId = await self.runStatic()
            elif self.node.mode == 2:
                nextId = await self.runSweep()
            elif self.node.mode == 3:
                nextId = await self.runReset()
            elif self.node.mode == 4:
                nextId = await self.runDcComp()

            if self.nodeId == 0x0000: self.diStart = self.diNow
            if nextId == 0xFFFF and self.nodeId != 0xFFFF:
                self.nodes = {0x0000: self.nodes[0x0000], 0xFFFF: self.nodes[0xFFFF]}
                self.ackCounter = 0
                self.sentCount = 0
                self.ackTs = 0
            self.nodeId = nextId