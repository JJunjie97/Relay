# 差动试验 API 协议（6 路电流差动保护）

> **模块标识**: `"differential_test"`。字段名与前端 `useDifferentialTestStore().state` 对齐（`camelCase`）。  
> **入口命令**: `"cmd": "start"`（启动）/ `"cmd": "update_static"`（运行中更新静态量）  
> **适用前端类型**: `sendInitParams(IWsParams)`（推荐：`sys + statics + payload`；**试验点仅放在** `payload.projectSettings.points`，见 §2.1）

---

## 0. 文档目的与制定说明

### 0.1 计划（编写顺序）

1. 对照前端 Pinia 状态 [`differential-test-store`](../../src/views/general-test/store/differential-test-store.ts) 与配置页 [`DifferentialConfigTabs.vue`](../../src/views/general-test/module/differential-test/components/DifferentialConfigTabs.vue)，列出用户可输入项。
2. 区分 **需下发后端**（驱动仪器、执行试验序列、判据）与 **纯前端**（图表视图、报表辅助、本地推导）。
3. 约定 `IWsParams` 中 `sys`、`statics`、`payload` 的职责分工；电流与辅助电压经 **`statics` 矩阵** 下发（见 §2.4、§2.5）；**试验点列表不在顶层 `testPoints`**，仅在 **`payload.projectSettings.*.points`**。
4. 联调口径见 **§7（已决议）**。

### 0.2 当前代码现状（必读）

| 项目 | 说明 |
|------|------|
| WebSocket 模块枚举 | [`ENUM_WS_MODULE`](../../src/ws/enum.ts) 已包含 `DIFFERENTIAL_TEST = "differential_test"`。 |
| 路由 | `/differential-test-6ch` 与 `/differential-test-busbar` 共用同一 Store，通过当前路由区分 **6 路** / **母差**，并在下发 `payload.variant` 中显式标识。 |
| 下发链路 | `EventListBtn` 已接入差动 `start`；`differential-test-store` 已实现 `sendConfig()`（`start`）与 `sendStaticUpdate()`（`update_static`）。 |
| 本文档性质 | 作为 **目标协议**：字段来源于现有界面状态；试验点序列为 **`projectSettings.points`**（§2.6）；`statics` 为仪器量下发载体。 |

---

## 1. 实验概述

差动试验用于比率差动、谐波制动等项目的边界搜索或定点测试：前端配置保护方程、补偿系数、制动曲线、**`projectSettings.points` 试验点列表**、试验时序；仪器量经 **`statics` 通道矩阵** 下发；后端按点采样开入并回传报告。

```text
开始(start) -> 运行(value_update，实时量) -> 报告(report，试验点结果) -> 结束(stop)
```

前端路由为 `/differential-test-6ch`，`payload` 包含 `protectionSettings`、`connectionSettings`（扩展/分相 6I/2I）、`equationSettings`、`projectSettings`、`testParams`。

---

## 2. 前端 -> 后端（启动）

### 2.1 请求示例（推荐骨架）

说明：**试验点数据通过** `payload.projectSettings.points` 传递，**不在** `IWsParams.testPoints` 重复下发。下发时 points **仅包含已勾选行**，并移除纯前端字段 `selected` / `status`。电流与**辅助电压**均在 **`statics`** 中下发，**不要**在 `payload` 中带 `auxVoltageData` 作为下发音量载体。

**`params.sys`（`debounce`、`logicMask`）**：含义与赋值方式与全项目其它界面一致，见 [`general-test-start-interface.md`](../general-test-start-interface.md) **§1.1 `sys`**；差动场景不传开出相关 `doMask` / `doCtrlMask`。

```json
{
  "module": "differential_test",
  "cmd": "start",
  "params": {
    "sys": {
      "debounce": 15,
      "logicMask": 7
    },
    "statics": {
      "0": { "0": [5.0, 0.0], "1": [57.735, 0.0] },
      "1": { "0": [5.0, 180.0] },
      "3": { "1": [57.735, -120.0] }
    },
    "payload": {
      "variant": "six_channel",
      "protectionSettings": {
        "vectorGroupLetter1": "y",
        "vectorGroupLetter2": "y",
        "vectorGroupClock": "0",
        "phaseCorrection": "none",
        "zeroSequenceCorrection": false
      },
      "equationSettings": {
        "idEquation": "id_sum",
        "irEquation": "ir_diff_k",
        "kFactor": 2.0,
        "kp1": 1.0,
        "kp2": 1.0
      },
      "connectionSettings": {
        "type": "ext-differential",
        "i1Terminal": "high-y",
        "i1Angle": 0.0,
        "i2Terminal": "low-y",
        "i2Angle": 180.0
      },
      "projectSettings": {
        "activeProject": "ratio-search",
        "testPhase": "A",
        "frequency": 50.0,
        "search": {
          "precision": 0.01,
          "method": "binary"
        },
        "points": [
          { "id": 1, "x": 1.0, "y": [0.0, 0.72] }
        ]
      },
      "testParams": {
        "diffMode": 0,
        "calcModel": 0,
        "irConstant": 0.0,
        "ifConstant": 5.0,
        "faultDuration": 1.0,
        "preFaultDuration": 1.0,
        "simulatePreFault": true,
        "loadCurrent": 0.0
      }
    }
  }
}
```


### 2.3 字段说明（核心）

| 路径 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `params.sys.*` | `object` | 是 | 差动目前仅使用 `debounce`、`logicMask`；**不传** `doMask` / `doCtrlMask`（开出相关）。含义与赋值见 [`general-test-start-interface.md`](../general-test-start-interface.md) **§1.1**。 |
| `params.limits` | `object` | 按需 | 若需保护上限则下发；定义见 [`general-test-start-interface.md`](../general-test-start-interface.md) **§1.2**；**启动示例可不出现**。 |
| `params.statics` | `object` | **是** | **电流与辅助电压的下发载体**（[`StaticsConfig`](../../src/ws/types.ts)）；不在 `payload` 中用 `auxVoltageData` 数组承载下发音量。 |
| `params.testPoints` | — | **不使用** | 差动试验**不下发**顶层 `testPoints`；试验点仅见 **`payload.projectSettings.points`**。 |
| `params.steps` / `params.count` |  | 可选 | 递变/扫描时与全局约定一致。 |
| `params.payload.variant` | `string` | 是 | `six_channel`。 |
| `params.payload.equationSettings` | `object` | 是 | 动作/制动方程、`K`、KP1/KP2。 |
| `params.payload.connectionSettings` | `object` | 是 | 接线类型；**含 I1/I2 接测试仪端子选项及角度**。 |
| `params.payload.projectSettings` | `object` | 是 | `activeProject`、`testPhase`、`frequency`、`search`；**试验点序列** `points`（仅已选中点，且不含 `selected`/`status`）。 |
| `params.payload.testParams` | `object` | 是 | 差动类型、计算模型、故障/预故障时序、负荷电流等；**不含** `logic` / `logicMask` / `debounceTime`（该类由 **`params.sys`** 承担）。 |
| `params.payload.protectionSettings` | `object` | 是 | 仅含接线组别三字段 + 相位/零序校正（见 §6.1.10）。界面另有 `protectionObject`、`windingType` 等仅本地/导入导出，**不写入启动包**。 |
| `params.payload.kpCalcParams` | — | **不下发** | 仅前端辅助计算用途。 |
| `params.payload.auxVoltageData` | — | **不下发** | **禁止**作为启动包中的电压下发音量字段；电压写入 **`statics`**（§2.5）。Store 中同名数组仅 UI 编辑态，组包时换算进 `statics`。 |
| `params.payload.projectSettings.points` | `array` | 有选中点时 | 试验点列表；边界搜索为 `{ id, x, y:[y0,y1] }` 或 `{ id, y, x:[x0,x1] }`，定点为 `{ id, x, y }`；见 **§2.6**。 |

### 2.4 结构约束

- 使用 **`IWsParams`**：`sys` + **`limits`（按需）** + **`statics`** + `payload`，**不使用顶层 `testPoints`**；试验点仅存在于 **`payload.projectSettings.points`**。不使用 `IWsParamsNew.nodes[]`，除非后端改为多节点状态机。
- **`payload` 命名**：与 Store **camelCase** 对齐。
- **按界面条件收敛**：仅传当前项目界面显示且后端需要的字段。例如 `activeProject=ratio-fixed` 时，不传 `search`；`harmonic-*` 项目才传 `projectSettings.harmonic`。
- 参数收敛：`kpCalcParams`、`ratio.curveSegments`、`ratio.error` 为前端计算/展示参数，启动下发时不传。
- **`payload.terminals`**：**不下发**；前端已从 Store 与组包中移除该字段。

### 2.5 `statics` 与通道编号约定（交叉引用）

- **电流与辅助电压**均由 **`params.statics`**（及 `limits`）描述；结构为 [`StaticsConfig`](../../src/ws/types.ts)（通道索引 → `layer` → `[电流幅值, 相位]`），与同项目交流类试验一致。
- **差动试验数值口径**：界面右图坐标、**`points[]`**、**`statics` 电流层**、**`value_update` / `report` 回写** 均使用**同一套工程坐标值**，**不做** `turnValidValToAmp` / `turnAmpValToVaildVal` 有效值↔峰值换算（直流分量亦不做该换算）。仅做有限小数位 `round` 以保持 JSON 稳定。
- **通道编号**：与 **整组 / 状态序列** 下 **`static` 矩阵的通道索引约定相同**（`EndIdxList`，如 Ua、Ia 等），见 **`whole-group-test.md` §2.3** 中「`static` 的通道索引与状态序列相同」的说明。
- **阻抗试验**等对 `IWsParams` 与 `payload` 的分工，见 **`impedance-characteristic-test.md` §2.4**（差动**不使用**其顶层 `testPoints` 承载试验点的方式）。
- **`payload.connectionSettings`**（含 I1/I2 接测试仪端子选项、角度等）为 **保护/接线业务参数**，随启动包下发；**协议层面不规定**其与 `statics` 某通道键的一一对应，避免与整组通道枚举混为一谈。

### 2.6 试验点 `points[]`（右图线段 / 单点）

启动包中 **`projectSettings.points`** 为**数组**，元素与右图 [`DifferentialChart.vue`](../../src/views/general-test/module/differential-test/components/DifferentialChart.vue) 中**已勾选**的虚线线段或实心点一一对应。编码见 [`differential-points-payload.ts`](../../src/views/general-test/module/differential-test/utils/differential-points-payload.ts)。

**边界搜索 = 线段**（一轴标量、另一轴为扫描区间 `[start, end]`）：

| `activeProject` | 元素形态 | 含义 |
|-----------------|----------|------|
| `ratio-search` | `{ id, x, y: [y0, y1] }` | 竖线：`x` = **Ir**（固定）；`y` = **Id** 扫描区间（`id_boundary × ratio.search.start/end / 100`，已排序） |
| `harmonic-search` | `{ id, y, x: [x0, x1] }` | 横线：`y` = **Id**（`id_val`，固定）；`x` = **Harm%** 扫描区间（`harmonic.search.start`～`end`，已排序） |

**定点 = 单点**（`x`、`y` 均为标量）：

| `activeProject` | 元素形态 | 含义 |
|-----------------|----------|------|
| `ratio-fixed` | `{ id, x, y }` | `x` = **Ir**，`y` = **Id**（`id_val`，无实测时用 `id_boundary`） |
| `harmonic-fixed` | `{ id, x, y }` | `x` = **Harm%**（`harm`），`y` = **Id**（`id_val`） |

**示例 — 比率边界搜索**：

```json
"points": [
  { "id": 1, "x": 0.5, "y": [0.0, 0.6] }
]
```

**示例 — 谐波边界搜索**：

```json
"points": [
  { "id": 1, "y": 0.5, "x": [1.0, 2.0] }
]
```

**示例 — 比率定点**：

```json
"points": [
  { "id": 1, "x": 0.5, "y": 1.0 },
  { "id": 2, "x": 1.2, "y": 0.72 }
]
```

全局 **`search`** 仅下发 `precision`、`method`（策略与精度）；边界搜索的实际范围以各元素中的 **区间数组** 为准。`id` 与界面点表一致，供 **`report`** / **`value_update`** 回传时对齐行。

数值与界面、右图、**`statics` 电流层**同一口径，**原样**写入（不做有效值↔幅值换算）。



## 3. 仅前端 / 由运行推导的数据（一般不应作为启动唯一依据）

以下字段多为展示或运行期回填；**与 §7 已决议不冲突**处为准。

| 字段 | 说明 |
|------|------|
| `projectSettings.activePointIndex` | 当前选中行，仅 UI。 |
| `currentTrackingData` | 界面电流跟踪区（公式计算）；**不由** `value_update` 更新。 |
| `axisConfig` | 特性图坐标范围，仅视图。 |
| `searchSettings` | Store 预留，界面未绑定；可不下发。 |
| 点表 `id_boundary` | 用于计算 `ratio-search` 的 **`y: [y0,y1]`**；以前端值为准，**后端不强制按曲线重算**（除非后续协议变更）。 |
| 点表 `id_val` | **实测值**，来自 `report` 回填；定点时写入 `points[].y`，非启动必填。 |
| `payload` 内 **`auxVoltageData`** | 若存在，仅表示 Store/UI 状态；**下发音量以 `statics` 为准**（§2.3）。 |
| **`busbarKpParams`** | **仅界面**：母差支路 CT/KP 及 KP 计算弹窗；**不在启动 `payload` 中下发**（§7）。 |

---

## 4. 前端 -> 后端（运行中控制：第二个下发事件）

与其它模块一致：

### 4.1 停止

```json
{
  "module": "differential_test",
  "cmd": "stop"
}
```

### 4.2 `update_static`（运行中调值）

差动试验支持运行中通过 `update_static` 更新 `statics`（电流/辅助电压矩阵）；该事件不要求重复下发 `payload`。

```json
{
  "cmd": "update_static",
  "static": {
    "6": { "0": [0, 50], "1": [5.0, 0.0] },
    "9": { "0": [0, 50], "1": [4.8, 180.0] }
  }
}
```

> 与其它交流类界面一致：`static` 的通道索引与 `EndIdxList` 对齐（见 §2.5 引用）。

---

## 5. 后端 -> 前端（事件）

### 5.1 `value_update`（右图实测轨迹）

前端路由为差动页时，`ws.ts` 将 **整条 JSON 消息对象** 传给 `upsertResultFromValueUpdate`。差动专用字段（与 [`differential-ws-events.ts`](../../src/views/general-test/types/differential-ws-events.ts) 中 `DifferentialValueUpdatePayload` 一致）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `number` | 试验点 id，与启动包 `points[].id` 相同（后端原样回传，与协议对齐） |
| `x` | `number` | 右图横轴坐标（比率=Ir，谐波=Harm%） |
| `y` | `number` | 右图纵轴坐标（动作边界 Id） |

消息根级仍可有全站字段（如 `di`、`static`），由 [`ws.ts`](../../src/ws/ws.ts) 统一处理；**差动不再解析** `ir`/`id`/`i1`/`i2` 于 `value_update`。

根级示例：

```json
{
  "type": "value_update",
  "module": "differential_test",
  "id": 1,
  "x": 1.0,
  "y": 0.612
}
```

说明：

- **仅用于右图青色实测轨迹**：每条有效 `value_update` 在图内追加一点 **`(x, y)`**，与右图坐标轴一致。**实心圆**（`#00d9ff`），与预设试验点区分；数量有上限滚动丢弃旧点。
- **不**更新电流跟踪区（`currentTrackingData` 仍由界面公式计算）、**不**写入试验点表、**不**更新结果表。
- **清除实测轨迹的推荐时机**（前端已实现或可依赖）：
  1. **下一次「开始试验」**：下发 `start` 前清空（便于新一次试验从零轨迹开始）。
  2. **离开差动试验页**：组件卸载时清空（避免回来时误读上一班次轨迹）。
  3. **`stop`（试验结束）**：仅 **差动**：收到 `stop` 且当前为差动路由时清空 **`value_update` 流轨迹**（反时限不在此清除图上实测点，便于结束后对照曲线）。

  4. **重置界面 / 导入方案**：重置或导入后清空或重建状态。

---

### 5.2 `report`（试验点结果 → 结果视图表格）

WebSocket 上报时 **`report` 事件的载荷为 `message.data`**（即 `handleEventReport(receiveData.module, data.data)`）。前端 [`setReportFromEvent`](../../src/views/general-test/store/differential-test-store.ts) 解析 **`data.points[]`**，按元素 **`id`** 匹配点表行并回填；若根级无 `points` 数组，则将 **`data` 根级** 当作单条结果解析（逐点推送时的兼容路径）。

**`id` 语义**：与启动包 **`points[].id`** 一致；前端 `points.find(p => p.id === raw.id)` 定位行后，读取该行已有 **`id_boundary`（整定）** 等本地字段，并合并 report 实测值。

**结果视图列与数据来源**（[`DifferentialResultModal.vue`](../../src/views/general-test/module/differential-test/DifferentialResultModal.vue)）：

| 列 | 来源 |
|----|------|
| 序号 | 前端行序 `idx + 1` |
| 制动电流 Ir | **report** → 点行 `x`（比率=Ir，谐波=Harm%） |
| 动作边界(整定) | **点表** `id_boundary`（启动/曲线，不按 report 覆盖） |
| 动作边界 Id | **report** `y` → 点行 `id_val` |
| 相对误差 | 前端：`id_boundary` 与 `id_val` |
| Kzd | 前端：点行 `x`、`id_val` + 界面 `kzd` |
| I1 / I2 侧电流 | **report** 标量 `i1`、`i2` → 点行（**非**相量数组） |

#### `points[]` 元素字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | `number` | 是 | 与启动包 `points[].id` 一致，用于匹配点表行 |
| `x` | `number` | 是 | 右图横轴坐标（比率=Ir，谐波=Harm%），与启动包 `points[].x` 一致 |
| `y` | `number` | 是 | 实测动作边界 Id；可用 `id_val` / `idValue` 作别名 |
| `i1` | `number` | 是 | I1 侧电流（实数） |
| `i2` | `number` | 是 | I2 侧电流（实数） |
| `pointId` | `number` | 否 | 与 `id` 同义 |
| `rowIndex` | `number` | 否 | 仅当 `id` 缺失时 fallback 定位行 |
| `status` | `string` | 否 | 写入点表状态；结果表不展示 |
| `tripTimeMs` | `number` | 否 | 动作时间（ms），界面暂未展示 |

**不由后端返回**：`id_boundary`（整定）、相对误差、Kzd、序号——前端按同 `id` 点表行与界面参数计算。

#### `report` 载荷示例（推荐）

```json
{
  "points": [
    {
      "id": 1,
      "x": 1.0,
      "y": 0.612,
      "i1": 1.2,
      "i2": 1.15
    }
  ]
}
```

TypeScript 类型见 [`differential-ws-events.ts`](../../src/views/general-test/types/differential-ws-events.ts) 中 `DifferentialReportPoint`。

### 5.3 `error` / `stop` / `load`

与其它模块一致；`stop` 表示试验正常结束。

---

## 6. 兼容与版本说明

- **`module` 字符串**：[`ENUM_WS_MODULE`](../../src/ws/enum.ts) 已定义 **`DIFFERENTIAL_TEST = "differential_test"`**（单一模块名，与 §7 一致）。
- **枚举值**：与 [`differential-test-enum.ts`](../../src/views/general-test/enums/differential-test-enum.ts) 一致，完整取值见 **§6.1**；字符串 slug 建议后端按字面量解析，数字枚举见各小节 `ENUM_*` 定义。
- **`value_update` / `report` 字段**：可与 [`differential-ws-events.ts`](../../src/views/general-test/types/differential-ws-events.ts) 对齐做 TypeScript 校验（§5）。

---

## 6.1 枚举定义（与前端一致）

以下枚举与选项表均来自 [`differential-test-enum.ts`](../../src/views/general-test/enums/differential-test-enum.ts)。**启动包 `payload` 中字符串字段**（如 `idEquation`、`activeProject`）取下表 **value**；**数字字段**（如 `testParams.diffMode`）取下表 **数值**。


#### 6.1.2 试验项目（`payload.projectSettings.activeProject`）

- `payload.projectSettings.activeProject`（`differentialTestProjectOptions`）
  - `ratio-search`: 比率差动--边界搜索
  - `ratio-fixed`: 比率差动--定点测试
  - `harmonic-fixed`: 谐波制动--定点测试
  - `harmonic-search`: 谐波制动--边界搜索

#### 6.1.3 测试相别（`payload.projectSettings.testPhase`）

- `payload.projectSettings.testPhase`（`differentialTestPhaseOptions`）
  - `A`: A 相差动
  - `B`: B 相差动
  - `C`: C 相差动
  - `AB`: AB 相差动
  - `BC`: BC 相差动
  - `CA`: CA 相差动
  - `ABC`: ABC 相差动

#### 6.1.4 动作方程（`payload.equationSettings.idEquation`）

- `payload.equationSettings.idEquation`（`idEquationOptions`）
  - `id_i1`: Id = I1
  - `id_sum`: Id = |I1' + I2'|
  - `id_diff`: Id = |I1' - I2'|
  - `id_sum_sq`: Id = |I1' + I2'|^2
  - `id_diff_sq`: Id = |I1' - I2'|^2

#### 6.1.5 制动方程（`payload.equationSettings.irEquation`）

- `payload.equationSettings.irEquation`（`irOptionsForIdI1`，仅当 `idEquation=id_i1`）
  - `ir_i2`: Ir = I2
- `payload.equationSettings.irEquation`（`irOptionsGeneral`，六路常用）
  - `ir_diff_k`: Ir = |I1' - I2'|/K
  - `ir_sum_k`: Ir = |I1' + I2'|/K
  - `ir_max_k`: Ir = Max(|I1'|, |I2'|) * K
  - `ir_i2_k`: Ir = |I2'| * K
  - `ir_id_abs_diff`: Ir = |Id - |I1'| - |I2'||
  - `ir_sum_div_k`: Ir = (|I1'| + |I2'|) / K
  - `ir_imax_sum_k`: Ir = |Imax' - ΣIi'|/K, Ii' ≠ Imax'
  - `ir_sqrt_cos`: Ir = √(-|I1'×I2'| * cosθ)
  - `ir_sum_sq_cos`: Ir = -|I1' * I2'| * cosθ（配合 `id_sum_sq`）
  - `ir_diff_sq_cos`: Ir = |I1' * I2'| * cosθ（配合 `id_diff_sq`）


#### 6.1.6 制动系数与搜索方式

- `payload.projectSettings.search.method`（`searchMethodOptions`）
  - `binary`: 二分法搜索
  - `linear`: 线性搜索

#### 6.1.7 接线（`payload.connectionSettings`）

- `payload.connectionSettings.type`（`connectionTypeOptions`）
  - `ext-differential`: 扩展差动 (6I)
  - `phase-differential`: 分相差动 (2I)
- `payload.connectionSettings.i1Terminal` / `i2Terminal`（扩展差动：`terminalOptions6I`）
  - `high-y`: 接测试仪 Ia,Ib,Ic
  - `low-y`: 接测试仪 Ix,Iy,Iz
- `payload.connectionSettings.i1Terminal` / `i2Terminal`（分相差动：`terminalOptions2I`）
  - `ia`: 接测试仪 Ia
  - `ib`: 接测试仪 Ib
  - `ic`: 接测试仪 Ic
  - `ix`: 接测试仪 Ix
  - `iy`: 接测试仪 Iy
  - `iz`: 接测试仪 Iz
  - `iab`: 接测试仪 Iab两并
  - `ibc`: 接测试仪 Ibc两并
  - `ica`: 接测试仪 Ica两并
  - `iabc`: 接测试仪 Iabc三并
  - `ixy`: 接测试仪 Ixy两并
  - `iyz`: 接测试仪 Iyz两并
  - `izx`: 接测试仪 Izx两并
  - `ixyz`: 接测试仪 Ixyz三并



#### 6.1.9 试验参数（`payload.testParams`）

- `payload.testParams.diffMode`（`ENUM_DIFFERENTIAL_MODE` / `differentialModeOptions`）
  - `0`: 稳态差动 (故障电流不叠加负荷)
  - `1`: 零序差动
  - `2`: 工频变化量/突变量差动
- `payload.testParams.calcModel`（`ENUM_DIFFERENTIAL_CALC_MODEL` / `calcModelOptions`）
  - `0`: 恒定制动电流
  - `1`: 恒定故障电流

#### 6.1.9 保护设置（`payload.protectionSettings`）

启动包**仅下发**下列 5 个字段（与界面「保护设置」红框一致）。编码函数：`encodeVectorGroupTransmission`（[`differential-vector-group.ts`](../../src/views/general-test/module/differential-test/utils/differential-vector-group.ts)）。

| 字段 | 类型 | 说明 |
|------|------|------|
| `vectorGroupLetter1` | `string` | 铭牌第 1 段联结字母，小写（如 `Y` → `y`） |
| `vectorGroupLetter2` | `string` | 另一侧联结字母，小写；三绕组取**第 3 段**字母（跳过第 2 段） |
| `vectorGroupClock` | `string` | 钟点数字字符串；双绕组取第 2 段尾部数字，三绕组取第 3 段尾部数字 |
| `phaseCorrection` | `string` | `phaseCorrectionOptions`：`none` \| `y-side` \| `delta-side` |
| `zeroSequenceCorrection` | `boolean` | Y 侧零序校正 |

**接线组别 slug → 三字段**（界面 `vectorGroup` value + `windingType` 仅用于编码，不下发 slug）：

| 绕组 | 界面 slug（示例） | `letter1` | `letter2` | `clock` |
|------|-------------------|-----------|-----------|---------|
| 三绕组 | `Y,y,y0` | `y` | `y` | `0` |
| 三绕组 | `Y,yn,d1` | `y` | `d` | `1` |
| 双绕组 | `Y,d11` | `y` | `d` | `11` |
| 双绕组 | `Y,y0` | `y` | `y` | `0` |

**仅前端 Store / 导入导出、不下发启动包**：`protectionObject`、`windingType`、`vectorGroup`（逗号 slug）、`involvedWinding`、`involvedPairIndex`、`vectorGroupBalanceAngle`（仍用于计算 `connectionSettings.i2Angle` 等）。

**legacy 导入映射**（slug 规范化，仍用于 XML/界面）：`Y/Δ-11` → `Y,d11`；`high-low` + `Y,yn,d11` → `Y,d11`；`high-mid` → `Y,yn`；`mid-low` → `yn,d11`

#### 6.1.11 谐波项目（`payload.projectSettings.harmonic`，仅 `six_channel`）

- `payload.projectSettings.harmonic.order`（`harmonicOrderOptions`）
  - `2`～`20`: 2 次谐波 … 20 次谐波（数值为次数）
- `payload.projectSettings.harmonic.applySide`（`harmonicApplySideOptions`）
  - `i1`: I1 侧
  - `i2`: I2 侧
- `payload.projectSettings.harmonic.harmonicErrorType`（Store 字符串，界面相对/绝对误差）
  - `relative`: 相对误差（%）
  - `absolute`: 绝对误差

#### 6.1.12 试验点 `points[]`（§2.6）

- `ratio-search`：`{ id, x:number, y:[number,number] }`
- `harmonic-search`：`{ id, y:number, x:[number,number] }`
- `ratio-fixed` / `harmonic-fixed`：`{ id, x:number, y:number }`

**仅前端、不下发启动包**（供对照）：`ratio.curveSegments[].type` 为 `threshold` | `slope` | `instant`；`kpCalcParams`；点表 `selected` / `status`；`protectionSettings` 中除 §6.1.10 五字段外的保护类型/参与绕组等。

---

## 7. 已决议口径（联调用）

1. **`module`**：`"differential_test"`。  
2. **试验点列表**：**不下发** `IWsParams.testPoints`；试验点**仅**通过 **`payload.projectSettings.points`** **数组**传递（线段或单点，§2.6），后端按数组顺序与 **`id`** 执行。  
3. **电流下发**：使用 **`params.statics`**（及按需 `limits` / `steps` / `count`）。**`statics` 通道索引约定**与整组/状态序列一致（`EndIdxList`）。I1/I2 接线选项仅在 **`payload.connectionSettings`** 中作为业务字段下发。  
4. **`id_boundary`**：**以前端给出的值为准**；`ratio-search` 时用于计算各元素 **`y: [y0,y1]`**；后端默认**不**用 `curveSegments` 覆盖重算。  
5. **辅助电压**：在 **`statics` 中定义**；**不在 `payload` 中**用 `auxVoltageData` 作为下发音量载体。  
6. **参数收敛**：`kpCalcParams`、`curveSegments`、`error`、`isPerUnit`、`idMin`、`idInst`、`kzd`、`search.start/end` 不下发；启动包 `points[]` 仅含已选线段/点（§2.6，无 `selected`/`status`）。`report` 按 **`id`** 回写点行实测字段。  
7. **`payload.terminals`**：**不约定、不下发**。  
8. **边界搜索**：`ratio-search` 为 **`{ id, x, y:[y0,y1] }`**；`harmonic-search` 为 **`{ id, y, x:[x0,x1] }`**（§2.6），配合全局 **`search`**（仅 `precision`、`method`）。

---

## 8. 右图绘制规则（现状）

本节用于说明“添加测试点后，差动试验右侧图形如何绘制”的前端现行规则，便于联调与回归核对。实现主入口见 [`DifferentialChart.vue`](../../src/views/general-test/module/differential-test/components/DifferentialChart.vue) 与 [`DifferentialConfigTabs.vue`](../../src/views/general-test/module/differential-test/components/DifferentialConfigTabs.vue)。

### 8.1 数据来源优先级

1. 右图测试点取 `projectSettings.points`，由 `activeProject` 区分比率或谐波。
2. “添加”按钮生成测试点时，数据取自“添加测试点弹窗”（`addPointForm`），不是下方搜索参数输入框。
3. 下方搜索参数（`search.start/end/precision/method`）当前主要用于启动包 `payload`，不会在“点击添加”时直接生成或重排右图静态点。

### 8.2 四种项目的点位映射与搜索线

| `activeProject` | 右图坐标 | 搜索线（界面） | 启动包 `points[]` 元素 |
|---|---|---|---|
| `ratio-search` | 竖线 | `x=ir`，`y` 从 `id_boundary×search.start%` 到 `id_boundary×search.end%` | `{ id, x: ir, y: [y0, y1] }` |
| `ratio-fixed` | 单点 | 无 | `{ id, x: ir, y: id_val }` |
| `harmonic-fixed` | 单点 | 无 | `{ id, x: harm, y: id_val }` |
| `harmonic-search` | 横线 | `y=id_val`，`x` 从 `harmonic.search.start` 到 `end` | `{ id, y: id_val, x: [x0, x1] }` |

其中，测试点只有 `selected=true` 时才绘制；当前选中行（`activePointIndex`）会额外绘制十字光标与外圈高亮。组包时仅已选线段/点进入 **`points[]`**（见 §2.6）。

### 8.3 比率边界 `id_boundary` 的生成规则

- `ratio-search` 添加点时，`id_boundary` 即时由 `calculateRatioBoundary(ir)` 计算。
- 计算逻辑按 `curveSegments` 顺序分段：
  - `threshold`：`Id = idMin`
  - `slope`：`Id = startId + (Ir - irStart) * k`
  - `instant`：当 `Ir >= irStart` 时，`Id = idInst`
- 曲线定义确认后，`refreshRatioBoundaryPoints()` 会重算已有点的 `id_boundary`。

### 8.4 曲线/区域与参数的对应关系

- 比率类曲线（含误差带）由以下参数直接决定：`idMin`、`idInst`、`curveSegments`、`error`。
- 谐波类曲线（含误差带）由以下参数直接决定：`harmonicConstant`、`idMin`、`idInst`、`harmonicError`。
- 图中青色实心点（`value_update` 轨迹）来自运行时下行事件，不属于“添加测试点”的静态绘制结果。

### 8.5 视角自动重置（auto-fit）规则

- 触发时机：测试点数量变化、项目切换、首次挂载（含容器首帧可见）。
- 取值策略：综合“曲线范围 + 当前点集最大 x/y”，再统一乘 `1.2` 留白。
- 刻度步长：按 `1/2/5/10 × 10^n` 取 `nice step`，用于主刻度与网格计算。

---
