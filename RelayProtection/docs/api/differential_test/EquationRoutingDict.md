# 差动方程路由分发字典 (Equation Routing Dictionary)

后端算法 `equation_solver.py` 的终极路由清单。以 `idEquation` 为一级路由键，决定**侧 2 基准相位**和**求解器入口**；`irEquation` 为二级键，选择**标量代数方程**。

---

## 符号约定

| 符号 | 含义 |
|---|---|
| $I_1', I_2'$ | **矢量**（相量）—— 补偿后的两侧电流相量 |
| $i_1', i_2'$ | **标量** —— 对应矢量的幅值，$i_1' \geq 0$ |
| $i_d, i_r$ | 标量 —— 动作 / 制动电流值（试验点坐标） |
| $K$ | 制动系数（`equationSettings.kFactor`） |

> 大写 = 矢量，小写 = 标量。公式中不使用箭头。

---

## 一级路由：`idEquation` → 侧 2 基准相位

所有矢量公式在确定侧 2 基准相位后，动作方程**统一化简为同一标量形式**：

$$i_d = i_1' - i_2'$$

> **硬约束**：$i_1' \geq i_2' \geq 0$（侧 1 幅值始终 ≥ 侧 2）。所有求解器的输出必须满足此约束，不满足时由**后处理**修正（见「极性处理」节）。

| `idEquation` | 侧 2 基准相位 $\theta_2$ | 求解器路由 |
|---|---|---|
| `id_sum` | $180°$ | → **路由 3**（8 种 ir 方程 → 4 种标量求解器） |
| `id_diff` | $0°$ | → **路由 3**（同上，标量代数完全一致） |
| `id_sum_sq` | $180°$ | → **路由 2**（唯一标量求解器） |
| `id_diff_sq` | $0°$ | → **路由 2**（同上，标量代数完全一致） |
| `id_i1` | $0°$ | → **路由 1**（直通，无方程组） |

> **核心简化**：`id_sum` 与 `id_diff` 的唯一区别是 $\theta_2$（$180°$ vs $0°$），标量代数方程完全相同。`id_sum_sq` 与 `id_diff_sq` 同理。

---

## 路由 1：`id_i1`（直通型）

**唯一合法 ir**：`ir_i2`

**求解**（无方程组，直接赋值）：

$$i_1' = i_d, \quad i_2' = i_r, \quad \theta_2 = 0°$$

> 虽然此模式无矢量补偿概念，但前端仍下发 `kp1 = kp2 = 1`，后端统一走 Kp 除法（除以 1 不影响结果），保持管线一致性。

---

## 路由 2：`id_sum_sq` / `id_diff_sq`（乘积型，唯一求解器）

**唯一合法 ir**：`ir_sum_sq_cos`（配 `id_sum_sq`）或 `ir_diff_sq_cos`（配 `id_diff_sq`）

两者标量等价，仅 $\theta_2$ 不同。

**标量方程组**：

$$i_1' - i_2' = \sqrt{i_d}$$
$$i_1' \cdot i_2' = i_r$$

**求解**（令 $d = \sqrt{i_d}$）：

$$i_2' = \frac{-d + \sqrt{d^2 + 4 i_r}}{2}$$
$$i_1' = d + i_2'$$

**$\theta_2$ 选择**：

| `idEquation` | $\theta_2$ |
|---|---|
| `id_sum_sq` | $180°$ |
| `id_diff_sq` | $0°$ |

---

## 路由 3：`id_sum` / `id_diff`（8 种 ir → 4 种标量求解器）

两者标量代数完全一致，仅 $\theta_2$ 不同（`id_sum` → $180°$，`id_diff` → $0°$）。

公共前提：$i_d = i_1' - i_2'$

---

### 求解器 A：和差型（线性方程组）

**适用 ir**：`ir_diff_k`、`ir_sum_k`、`ir_sum_div_k`、`ir_imax_sum_k`

> 四个 API 值在标量层面完全等价：$i_r = (i_1' + i_2') / K$

**标量方程组**：

$$i_1' - i_2' = i_d$$
$$i_1' + i_2' = i_r \cdot K$$

**求解**：

$$i_1' = \frac{i_d + i_r \cdot K}{2}$$
$$i_2' = \frac{i_r \cdot K - i_d}{2}$$

---

### 求解器 B：最大值型（分支）

**适用 ir**：`ir_max_k`

**标量方程**：$\max(i_1', i_2') = i_r / K$

**求解**（$i_1' \geq i_2'$ 时）：

$$i_1' = \frac{i_r}{K}$$
$$i_2' = \frac{i_r}{K} - i_d$$

> 若 $i_2' > i_1'$，则 $i_{max} = i_2'$：$i_2' = i_r / K$，$i_1' = i_d + i_2'$

---

### 求解器 C：定值型

**适用 ir**：`ir_i2_k`、`ir_id_abs_diff`

> **合并说明**：当 `irEquation` 为 `ir_id_abs_diff` 时，前端下发的制动系数 $K$ 恒等于 2。因此两者在数学上完全一致，统一使用制动除法：

**标量方程**：

$$i_2' = \frac{i_r}{K}$$

**求解**：

$$i_2' = \frac{i_r}{K}$$
$$i_1' = i_d + i_2'$$

---

### 求解器 D：乘积型（二次方程）

**适用 ir**：`ir_sqrt_cos`

**标量方程**（$\cos\theta$ 抵消后）：

$$i_r^2 = i_1' \cdot i_2'$$

**标量方程组**：

$$i_1' - i_2' = i_d$$
$$i_1' \cdot i_2' = i_r^2$$

**求解**（代入 $i_1' = i_d + i_2'$）：

$$i_2'^2 + i_d \cdot i_2' - i_r^2 = 0$$

$$i_2' = \frac{-i_d + \sqrt{i_d^2 + 4 i_r^2}}{2}$$

$$i_1' = i_d + i_2'$$

---

## 后处理：极性处理（负幅值修正）

当求解器输出的 $i_2' < 0$ 时，取其绝对值，并翻转侧 2 的基准相位：

$$i_2' = |i_2'|$$

| 原 $\theta_2$ | 翻转后 |
|---|---|
| $180°$ | $0°$ |
| $0°$ | $180°$ |

> **数学合理性**：由于动作方程在标量层面统一为 $i_1' - i_2' = i_d$（其中 $i_d > 0$），在保证 $i_2' \geq 0$ 后，必然自动满足硬约束 $i_1' > i_2'$，因此无需进行额外的两侧幅值交换。

---

## 完整路由表

```
idEquation
├── id_i1 ──────────── θ₂=0° ──→ 直通型：i1=id, i2=ir
│
├── id_sum_sq ──────── θ₂=180° ─→ 乘积型（路由2）
├── id_diff_sq ─────── θ₂=0° ──→ 乘积型（路由2）  ← 同一求解器
│
├── id_sum ──────────── θ₂=180°
│   └── irEquation
│       ├── ir_diff_k / ir_sum_k / ir_sum_div_k / ir_imax_sum_k → 求解器 A（和差型）
│       ├── ir_max_k ───────────────────────────→ 求解器 B（最大值型）
│       ├── ir_i2_k / ir_id_abs_diff ───────────→ 求解器 C（定值型）
│       └── ir_sqrt_cos ────────────────────────→ 求解器 D（乘积型）
│
└── id_diff ─────────── θ₂=0°
    └── irEquation ← 与 id_sum 共用完全相同的求解器 A~D
```

---

## 求解器汇总（6 种标量方程）

| # | 求解器 | 标量方程 | 适用 ir API 值 |
|---|---|---|---|
| 1 | **直通** | $i_1' = i_d$, $i_2' = i_r$ | `ir_i2` |
| 2 | **平方乘积** | $i_1' - i_2' = \sqrt{i_d}$, $i_1' \cdot i_2' = i_r$ | `ir_sum_sq_cos`, `ir_diff_sq_cos` |
| 3 | **和差** | $i_1' - i_2' = i_d$, $i_1' + i_2' = i_r \cdot K$ | `ir_diff_k`, `ir_sum_k`, `ir_sum_div_k`, `ir_imax_sum_k` |
| 4 | **最大值** | $i_1' - i_2' = i_d$, $i_{max} = i_r / K$ | `ir_max_k` |
| 5 | **定值** | $i_2' = i_r / K$, $i_1' = i_d + i_r / K$ | `ir_i2_k`, `ir_id_abs_diff` |
| 6 | **乘积** | $i_1' - i_2' = i_d$, $i_1' \cdot i_2' = i_r^2$ | `ir_sqrt_cos` |

> - #1 仅服务 `id_i1`，#2 仅服务 `id_sum_sq` / `id_diff_sq`，#3~#6 服务 `id_sum` / `id_diff`。
> - 所有组合的 $\theta_2$ 由 `idEquation` 决定（sum 系 → 180°，其余 → 0°）。

---

## Kp 补偿除法（统一最终步骤）

所有求解器输出的 $i_1', i_2'$ 为**补偿域**幅值，最终物理输出需除以 Kp：

$$i_{1,out} = \frac{i_1'}{Kp_1}, \quad i_{2,out} = \frac{i_2'}{Kp_2}$$

| 场景 | $Kp_1$ | $Kp_2$ | 说明 |
|---|---|---|---|
| 正常补偿 | `equationSettings.kp1` | `equationSettings.kp2` | 变比/接线补偿系数 |
| `id_i1`（直通） | 1.0 | 1.0 | 前端固定下发 1，除法不影响结果 |

> 后端管线统一执行此步骤，无需按路由分支判断是否跳过。

---

## 后端实现接口建议

```python
def solve(id_val: float, ir_val: float, k: float, kp1: float, kp2: float,
          id_eq: str, ir_eq: str) -> tuple[float, float, float]:
    """
    Returns (i1_out, i2_out, theta2)
    """
    # 1. 确定 theta2
    theta2 = 180.0 if id_eq in ("id_sum", "id_sum_sq") else 0.0

    # 2. 一级路由
    if id_eq == "id_i1":
        i1, i2 = id_val, ir_val
    elif id_eq in ("id_sum_sq", "id_diff_sq"):
        i1, i2 = solve_product_sq(id_val, ir_val)
    else:
        # id_sum / id_diff → 二级路由
        solver = IR_SOLVER_MAP[ir_eq]  # ir_imax_sum_k 已归入和差型
        i1, i2 = solver(id_val, ir_val, k)

    # 3. 后处理：极性处理（负幅值修正）
    if i2 < 0:
        i2, theta2 = abs(i2), (0.0 if theta2 == 180.0 else 180.0)

    # 4. Kp 补偿除法
    return i1 / kp1, i2 / kp2, theta2
```
