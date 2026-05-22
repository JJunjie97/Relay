# USEEngine3 — V2 引擎架构变更说明

> **文件**: `logic/USEEngine3.py`
> **替换**: `logic/USEEngine.py`（V1 引擎）

---

## 1. 核心架构变更：从 Start/Stop 到永久死循环

### V1 架构
```
用户 Start → Engine.Start() → _StartCoreLoop() → 运行节点 → Stop() → 退出
```

引擎有明确的生命周期：Start 启动、Stop 停止、isRunning 状态标志。每次测试需要启动和销毁引擎。

### V3 架构
```
上电 → coreLoop() → 永久循环：Node 0(待机) ↔ 测试节点 ↔ Node 0xFFFF(急停)
                    ↺ 无限循环，永不退出
```

引擎化身为**永久运行的状态机**。没有 Start/Stop 概念，启停通过节点跳转实现：
- **Node 0**：待机节点（Static 模式），发送 Reset + Debounce + Start 指令，然后无限等待
- **Node 0xFFFF**：终止节点（Static 模式），发送 SYS_RESET，然后等待下次跳转
- **开始测试**：`manualTrig(firstNodeId)` 跳转到第一个测试节点
- **停止测试**：`manualTrig(0xFFFF)` 跳转到终止节点

---

## 2. 节点数据模型变更

### V1：运行时编译 SequenceNode
```python
class SequenceNode:
    interval: int        # 步进间隔
    count: int           # 步数
    resetTime: int       # 复归时间
    statics: List[U64]   # 原始 DDS 控制字
    steps: List[U64]     # 均匀步进控制字
    triggers: List[tuple] # 已解包触发器
```
节点在 `_StartCoreLoop` 中从 FlatNode 字典编译为 SequenceNode，运行器负责从控制字生成硬件帧。

### V3：预编译 USENode（零运算节点）
```python
@dataclass
class USENode:
    nodeId: int
    modeType: int                    # 1=Static, 2=Sweep, 3=Reset, 4=DcComp

    # 时间参数
    stepIntervalMs: int              # 步进间隔
    resetDurationMs: int             # 复归时长

    # 硬件帧缓存（已编译，直接下发）
    baseFrame: List[bytes]           # Ss (WR_SHADOW 帧集合)
    resetFrame: List[bytes]          # Rs (WR_STAGE 帧集合)
    stepFrames: List[List[bytes]]    # Steps[N] (STEP_SHADOW 帧集合/拍)
    phaseGateFrames: List[List[bytes]]  # Pg[N] (PHASE_GATE 帧/拍)

    # DO 控制
    resetDoU32: int                  # (ExitMask << 16) | EnterMask
    doActions: List[int]             # (Delay_ms << 16) | Mask

    # 触发器（已解构为独立字段）
    countOverId: int                 # COUNT_OVER 跳转目标
    diMatchMask: int                 # DI_MATCH 掩码配置
    diMatchId: int                   # DI_MATCH 跳转目标
    timeoutMs: int                   # TIMEOUT 超时时间
    timeoutId: int                   # TIMEOUT 跳转目标
```

**关键变更**：

| 方面 | V1 | V3 |
|------|-----|-----|
| 帧生成 | 运行时从 U64 控制字编译为硬件帧 | **前端预编译**，节点存储已编好的 `bytes` 帧 |
| 步进模型 | 均匀步进（一份 step × N 次） | **非均匀步进**（`stepFrames[N]` 每拍独立帧集） |
| 触发器 | 通用 `triggers[]` 数组，运行时遍历 | **解构为独立字段**（countOverId / diMatchMask / timeoutMs），零遍历 |
| Teardown | 运行器负责计算 S0/neg_Steps | **CoreLoop 统一 `flush(SYS_SYNC)`** 替代所有 teardown |

---

## 3. CoreLoop 统一 Teardown

### V1 Teardown（复杂）

每个运行器在 sleep 被触发打断时，需要自行发送清理帧序列：
```python
# V1: _RunSweep 中的 teardown
if tgt is not None:
    return await c.exit(tgt, c.nst(tick) + c.f["Up"] + S0s + S0t)
                              ↑ neg_Steps  ↑ Update  ↑ S0 同步锁
```

每种模式有不同的 teardown 路径（Static/Sweep/Reset/DcComp 各不同），极易出 bug。

### V3 Teardown（统一）

```python
async def coreLoop(self):
    while True:
        nd = nodes[currentNodeId]
        self._cancelBgTasks()                    # 取消所有后台任务
        await self.flush([SYS_SYNC])             # ← 统一 Teardown：一帧 Sync 搞定

        currentNodeId = await RunNode(nd)
```

**SYS_SYNC 的作用**：强制 `Active = Base + Shadow` 三缓冲区对齐。无论当前处于哪个步进状态，Sync 后所有缓冲区归一，下一个节点的 Ss 写入从干净状态开始。

> 运行器不再需要任何 teardown 逻辑。每个 `RunXxx` 直接返回 nextId，CoreLoop 在下一轮自动 Sync。

---

## 4. 四大模式执行流程对比

### Mode 1: Static

| | V1 | V3 |
|---|---|---|
| 帧发送 | `FlushFrames(Ss)` + `SendWithAck(Up)` | `flush(baseFrame)` + `ack(Up)` |
| DO | `FlushFrames(DoAction)` | `spawnDo(tZero, tUp)` |
| 等待 | `_EvaluateTriggers` 轮询 | `sleep(-1)` 事件驱动 |

### Mode 2: Sweep

V1 使用 `_RunSweep` 循环发送 `Steps + Up`，V3 改为预编译帧：

```python
# V3: RunSweep 核心
await flush(baseFrame)              # Ss
tUp = await ack(Up)                 # Tick 0 生效
await ack(Sync)                     # 首拍清洗 Prev
await flush(stepFrames[0])          # 预载 Tick 1

for tick in range(1, count):
    tUp = await ack(Up)             # Tick N 生效
    await flush(stepFrames[tick])    # 预载 Tick N+1
```

**关键差异**：首拍多了一个 `Sync` 指令，用于清洗 Prev 缓冲区（V1 靠 S0Ss 实现相同效果）。

### Mode 3: Reset

V1 的 `_RunReset` 手动管理 resetDo/overDo 和 teardown 补偿。V3 简化为：

```python
# V3: RunReset 核心
await flush(baseFrame)              # Ss (WR_SHADOW)
await flush(resetFrame)             # Rs (WR_STAGE)
tUp = await ack(Up)                 # Active = Rst

for tick in range(count):
    # 复归期
    flushNoAck(SET_DO(enterMask))
    await flush(stepFrames[tick])
    sleep(resetTime)
    # 输出期
    tUp = await ack(Up)             # Active = Out(tick)
    flushNoAck(SET_DO(exitMask))
    sleep(interval)
```

**ResetDo 打包变更**：V1 分 `resetDo` 和 `overDo` 两个字段，V3 合并为单个 `resetDoU32 = (ExitMask << 16) | EnterMask`。

### Mode 4: DcComp

V3 支持预编译的 `phaseGateFrames[N]`，核心流程与 V2 伪代码完全一致。

---

## 5. 触发器评估变更

### V1：数组遍历
```python
def _EvaluateTriggers(self, node, ms, entryDi):
    for cond, nid, data in node.triggers:
        if cond == ConDiMatch: ...
        elif cond == ConTimeout: ...
    return None
```

### V3：直接字段访问
```python
def _evaluateTriggers(self) -> Optional[int]:
    # 1. ManualTrig 最高优先级
    if self.manualTrigTarget is not None:
        return manualTrigTarget

    # 2. Timeout
    if nd.timeoutMs and elapsed >= nd.timeoutMs:
        return nd.timeoutId

    # 3. DI Match
    if nd.diMatchMask is not None:
        # 位运算评估...
        return nd.diMatchId
```

**优势**：无循环遍历，直接判断三个独立字段。ManualTrig 作为内置最高优先级，不再需要额外事件通道。

---

## 6. DI 基准变更

| | V1 | V3 |
|---|---|---|
| 初始 DI 基准 | `diEvent.wait()` 阻塞等首个 DI 帧 | `initDiShadow`（ManualTrig 时更新） |
| 节点进入 DI | `entryDi` = 进入节点时快照 | 同 V1：`entryDi = diStatusShadow` |
| bit9 参考系 | 0=初始 DI, 1=进入节点 DI | 同 V1 |

**V3 新增**：`manualTrig()` 时自动更新 `initDiShadow = diStatusShadow`，解决 V1 中 `lastNotifiedDi` 跨测试不重置的 bug。

---

## 7. 遥测事件变更

### V1
```python
[EvtValueUpdate, nodeId, tick, hwTimestamp]
[EvtTrigger, condition, nextId, hwTimestamp]
[EvtDiChange, di, hwTimestamp]
```

### V3
```python
[EvtValueUpdate, nodeId, tick, hwTimestamp]   # tick 变化
[EvtValueUpdate, -2, doMask, hwTimestamp]     # DO 变化（复用 EvtValueUpdate）
[EvtDiChange, di, hwTimestamp]                # DI 变化（不变）
```

**关键变更**：
- `EvtTrigger` 被移除。节点跳转通过 `EvtValueUpdate` 的 `nodeId` 变化隐式表达
- DO 变化复用 `EvtValueUpdate`（nodeId=-2 标记为 DO 事件）
- SpawnDo 改为后台 Task，在定时到达后发送 DO 帧并 emit 事件

---

## 8. 对 Handler 层（ACTestHdl）的影响

V3 引擎的事件格式变化需要 ACTestHdl 适配：

| 变更点 | 影响 |
|-------|------|
| 无 EvtTrigger 事件 | HDL 需要从 `EvtValueUpdate` 的 nodeId 变化推断触发（nodeId 从 actionId 变为 holdId 即表示 trigger） |
| DO 变化改为 EvtValueUpdate(-2) | `_OnValueUpdate` 需要区分 nodeId=-2（DO）和正常 nodeId |
| 节点编译层前移 | 之前 HDL 产出 JSON dict 由 CmdRouter 编译为 FlatNode，现在需要直接产出 `USENode` 对象（含预编译帧） |
| Start/Stop 改为 ManualTrig | CmdRouter 不再调用 `engine.Start()`/`engine.Stop()`，改为 `engine.manualTrig(firstNodeId)` 和 `engine.manualTrig(0xFFFF)` |
