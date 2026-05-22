# USE 波形引擎 V2 — 四模式伪代码

> `SYS_SYNC`: Active → Base + Shadow（三缓冲区对齐）
> `EmitValueUpdate(nodeId, tick, t)`: 值更新报告（非阻塞），nodeId 变化即表示触发跳转
> `EmitDoChange(do, t)`: DO 状态变更（SpawnDo 定时器循环内发送）
> CoreLoop 在每次状态转换时统一 `Flush(SYS_SYNC)`，运行器零 Teardown

---

## CoreLoop

```python
async def CoreLoop():
    while True:
        nd = nodes[nid]
        nid = await RunNode(nd)

        # ── 统一 Teardown ──
        CancelBgTasks()
        await Flush(SYS_SYNC)
```

---

## Mode 1: Static

```python
async def RunStatic(nd):
    await Flush(Ss)                            # WR_SHADOW
    tUp = await Ack(Up)
    t = CurrentTime()
    SpawnDo(t)
    EmitValueUpdate(nodeId, 0, tUp)
    r = await Sleep(-1)
    return r.nextId
```

---

## Mode 2: Sweep

```python
async def RunSweep(nd):
    count = len(Steps)                         # count >= 1

    # ── Tick 0 ──
    await Flush(Ss)                            # WR_SHADOW
    tUp = await Ack(Up)
    t = CurrentTime()
    SpawnDo(t)
    await Ack(Sync)                            # 首拍对齐: 清洗 Prev
    await Flush(Steps[0])                      # STEP_SHADOW
    EmitValueUpdate(nodeId, 0, tUp)
    t += iv
    r = await Sleep(t)
    if r: return r.nextId

    # ── Tick 1..count-1: 预载 + 激活 ──
    for tick in range(1, count):
        tUp = await Ack(Up)
        await Flush(Steps[tick])               # STEP_SHADOW, 无需 Sync
        EmitValueUpdate(nodeId, tick, tUp)
        t += iv
        r = await Sleep(t)
        if r: return r.nextId

    # ── 最终 Up: 激活最后一帧预载 ──
    tUp = await Ack(Up)
    EmitValueUpdate(nodeId, count, tUp)
    t += iv
    r = await Sleep(t)
    if r: return r.nextId

    if nd.countOverId:
        return nd.countOverId
    r = await Sleep(-1)
    return r.nextId
```

---

## Mode 3: Reset

> Ss: WR_SHADOW, Rs: WR_STAGE（仅写 Shadow, Base 不变）
> 复归期纯等待不轮询, 触发仅在输出期判定

```python
async def RunReset(nd):
    count = len(Steps)                         # count >= 1

    await Flush(Ss)                            # WR_SHADOW → Base=Ss_Base
    await Flush(Rs)                            # WR_STAGE  → Shadow=Rst
    tUp = await Ack(Up)                        # Active=Rst
    t = CurrentTime()
    await SetDo(rD)
    SpawnDo(t)

    # ── T0 复归期 ──
    await Flush(Ss)                            # WR_STAGE → Shadow=Out(0)
    EmitValueUpdate(nodeId, -1, tUp)
    t += rT
    await SleepUntil(t)                        # 纯等待, 不轮询

    # ── T0 输出期 ──
    tUp = await Ack(Up)                        # Active=Out(0)
    await SetDo(oD)
    EmitValueUpdate(nodeId, 0, tUp)
    t += iv
    r = await Sleep(t)
    if r: return r.nextId

    # ── Tick 1..count ──
    for tick in range(1, count + 1):
        # 复归期
        tUp = await Ack(Up)                    # Active=Rst
        await SetDo(rD)
        await Flush(Steps[tick-1])             # STEP_SHADOW
        EmitValueUpdate(nodeId, -1, tUp)
        t += rT
        await SleepUntil(t)

        # 输出期
        tUp = await Ack(Up)                    # Active=Out(tick)
        await SetDo(oD)
        EmitValueUpdate(nodeId, tick, tUp)
        t += iv
        r = await Sleep(t)
        if r: return r.nextId

    if nd.countOverId:
        return nd.countOverId
    r = await Sleep(-1)
    return r.nextId
```

---

## Mode 4: DcComp

> 首帧 Sync 清洗 Prev, 后续 Pg 无需 Sync
> M = len(Steps), M ≥ 2

```python
async def RunDcComp(nd):
    count = len(Steps)                         # count >= 2

    # ── 首帧突发 ──
    burst = Ss + Steps[0] + Pg[0] + [Sync] + Steps[1]
    await FlushNoAck(burst)
    tUp = await WaitForAck(pg0_seq)
    t = CurrentTime()
    SpawnDo(t)
    EmitValueUpdate(nodeId, 0, tUp)
    if CheckTrigsNow(): return nextId

    # ── Pg[1..count-2] ──
    for N in range(2, count):
        await FlushNoAck(Pg[N-1] + Steps[N])
        tUp = await WaitForAck(pgN_seq)
        EmitValueUpdate(nodeId, N - 1, tUp)
        if CheckTrigsNow(): return nextId

    # ── Pg[count-1] 尾步 ──
    await FlushNoAck(Pg[count-1])
    tUp = await WaitForAck(pgM_seq)            # 防御性
    EmitValueUpdate(nodeId, count - 1, tUp)
    r = await Sleep(-1)
    return r.nextId
```
