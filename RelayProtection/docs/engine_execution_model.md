# USEEngine 状态机执行模型

本文档描述 `USEEngine` 的底层执行机制。引擎是一个零编译、完全依靠节点跳转驱动的永久状态机（FSM），运行在独立的子进程（CPU Core 3）中。

对应代码：[USEEngine.py](file:///home/pi/Desktop/Relay/RelayProtection/logic/USEEngine.py)

---

## 1. 核心架构：永久循环与节点跳转

### 1.1 永久状态机

引擎启动后进入无限循环 `coreLoop`，不存在"停止状态"。所谓的待机和停止，是停留在内置的功能节点上：

- **`Node 0x0000`（待机节点）**：Mode 1（Static），下发硬件复位、零偏校准、防抖配置和 `SYS_START` 启动波形发生器。引擎在此节点通过 `sleepForever` 无限期挂起，等待外部触发。
- **`Node 0xFFFF`（终止节点）**：Mode 1（Static），下发 `SYS_RESET` 重置所有缓冲区并切断输出。

外部系统（`TestCtrl`）通过 `engine.manualTrig(nodeId)` 驱动状态跳转：
- `manualTrig(0x0000)` → 进入待机
- `manualTrig(firstNodeId)` → 启动测试
- `manualTrig(0xFFFF)` → 急停

### 1.2 USENode 数据结构

所有波形数据由 API 层预编译为二进制帧数组，引擎不做任何解析计算：

| 字段 | 帧类型 | 说明 |
|:-----|:-------|:-----|
| `baseFrame` | `WR_SHADOW` 帧 | 基准波形参数（写入 Base + Shadow） |
| `stepFrames` | `STEP_SHADOW` 帧二维数组 | 每个 Tick 的步进增量（支持非均匀步进） |
| `resetFrame` | `WR_STAGE` 帧 | 复归态参数（仅写 Shadow，不动 Base） |
| `gateFrames` | `PHASE_GATE` 帧二维数组 | 相位门控指令 |

---

## 2. 四种执行模式

### 2.1 Mode 1: Static（静态稳态）

输出一段恒定不变的波形。

**执行流程**：
1. `flush(baseFrame + [SYS_UPDATE])` → 写入参数并翻转生效
2. `sleepForever` → 无限期挂起，等待触发

### 2.2 Mode 2: Sweep（步进扫频）

输出随时间阶梯变化的波形。`stepFrames` 为二维数组，支持每个 Tick 不同的步进量。

**执行流程**：
1. `flush(baseFrame + [SYS_UPDATE])` → 基准波形生效
2. `flush([SYS_SYNC])` → 三区对齐，洗净 Shadow 中的脏数据
3. `flush(stepFrames[0])` → 预载第一步到 Shadow
4. `sleep(interval)` → 等待第一个 Tick
5. 循环：`flush([SYS_UPDATE])` → 翻转生效 → `flush(stepFrames[N])` → 预载下一步 → `sleep(interval)`

> [!NOTE]
> `SYS_SYNC` 在第 0 步的对齐确保了：未参与步进的通道在乒乓翻转时完全保持原值，不会受到 Shadow 残留数据的干扰。

### 2.3 Mode 3: Reset（故障-复归交替）

模拟"故障 → 恢复 → 故障"交替波形（如多次重合闸），每个 Tick 分为故障相和复归相。

**隔离策略**：
- `baseFrame`（`WR_SHADOW`）将故障基准写入 Base + Shadow
- `resetFrame`（`WR_STAGE`）将复归参数仅写入 Shadow（不动 Base）
- 效果：Base 始终保留纯粹的故障基底值，后续 `STEP_SHADOW` 的累加锚点不被复归数据污染

**执行流程**：
1. `flush(baseFrame + resetFrame + [SYS_UPDATE])` → 复归波形生效（Active = 复归值 R）
2. DO 切换到 enterDo
3. `flush(baseFrame)` → 将 Shadow 重写为故障基准 F
4. `sleepWait(resetTime)` → 等待复归时间
5. `flush([SYS_UPDATE])` → 翻转，故障波形 F 生效
6. DO 切换到 exitDo
7. 循环：翻转回复归相 → 在复归期间 `flush(stepFrames[N])` 悄悄累加下一步故障 → 翻转回故障相

> [!NOTE]
> 复归参数 R 在 Active 和 Shadow 之间来回弹跳，不触碰 Base。而 Base 在幕后通过 `STEP_SHADOW` 进行故障态的平滑步进累加。两者互不干扰。

### 2.4 Mode 4: DcComp（门控直流补偿）

在指定通道达到目标相位时，以硬件纳秒级精度瞬间切入直流偏移并衰减。

> [!WARNING]
> 当前代码中 `coreLoop` 的 `match-case` 已将 Mode 4 注释掉（`# case 4`）。以下为协议设计文档，供后续启用参考。

**执行流程**：
1. 将 `baseFrame + stepFrames[0] + gateFrames[0] + SYS_SYNC + stepFrames[1]` 一次性全部发出
2. `gateFrames[0]`（`PHASE_GATE`）阻塞 FIFO，等待目标相位到达
3. 相位命中 → 硬件自动翻转 → 后续积压指令（`SYS_SYNC` + 下一步步进）依次执行
4. 重复：发送 `gateFrames[N] + stepFrames[N+1]` → 等待 ACK

---

## 3. 触发评估与等待机制

引擎有两种等待方法，响应范围不同：

| 方法 | 响应 `manualTrig` | 响应 DI 变位 | 响应 Timeout |
|:-----|:----------------:|:------------:|:------------:|
| `sleepWait` | ✓ | ✗ | ✗ |
| `sleep` | ✓ | ✓ | ✓ |

- **`sleepWait`** 用于 Mode 3 的复归等待期，只允许外部急停打断，屏蔽 DI 和超时
- **`sleep`** 用于正常运行期，通过 `_evalTrig()` 按优先级评估所有触发源

`_evalTrig()` 的触发优先级：
1. 外部 `manualTrig`（最高优先级）
2. 超时 `timeoutMs`
3. DI 变位匹配 `diMatchMask`

---

## 4. CoreLoop 统一清理

### 4.1 节点进入前清洗

每次进入新节点时，`coreLoop` 执行两步清理：
1. **取消遗留 DO 任务**：`self._doTask.cancel()` — 防止上一节点的继电器延时动作在跳转后突然执行
2. **三区对齐**：`flush([SYS_SYNC])` — 将当前 Active 值反写 Base 和 Shadow，清除历史脏数据

### 4.2 节点垃圾回收

当 `nextId` 跳转至 `0x0000` 或 `0xFFFF` 时：
```python
self.nodes = {0x0000: self.nodes[0x0000], 0xFFFF: self.nodes[0xFFFF]}
self.ackCounter = 0
self.sentCount = 0
```
切断所有临时节点引用，触发 Python GC 释放内存。下次测试前 `TestCtrl` 必须重新执行 `upsertNodes` 上传新节点。

---

## 5. 并发安全设计

- **`hwLock`**：互斥锁保护多帧突发发送（Burst Frames）的原子性，防止 DO 操作帧插入突发队列
- **`asyncio.shield`**：保护 DO 执行路径中的 `setDo()` 通信发送，防止协程被 `cancel` 时 `sentCount` 未累加导致 ACK 计数失衡
- **DI 沿触发机制**：通过 `diMatchMask` 的高位标志控制：
  - `0x100`：AND/OR 逻辑（多路同时变位 vs 任意变位）
  - `0x200`：基准选择（前序态 `diPrev` vs 开始态 `diStart`）
  - `0x400`：极性反转（适配常开/常闭接线）
