# 🤖 智能助手交接文档 (Agent Handover)

此文档由前一个 AI 助手生成，记录了当前项目的核心架构、关键重构决策以及接下来的任务目标。新的 AI 助手在开启对话后，请首先阅读本文档以快速了解当前状态。

## 🎯 当前任务目标与状态
**状态**：底层 FPGA 核心逻辑（通信、解析、校准、测试控制）的深度优化与代码重构已经全部完成，各项测试均已通过。
**下一步任务**：使用 `Nuitka` 将项目打包为无依赖的单体可执行文件 `sysengine`，并配置外置的 `calibration.json`，完成生产环境部署结构的搭建。

## 🏗️ 核心架构与重要约定

### 1. 通道映射体系（极简物理通道）
- **废弃**：过去在 `TestCtrl` 和 `API` 之间使用的抽象逻辑通道映射逻辑已经全面废除。
- **现行标准**：全系统严格使用 **真实物理通道索引 (hwCh)**。所有遍历和字典的 Key 都直接采用 `HWConfig.V_CHANNELS` (0,2,4,6,8,10) 和 `HWConfig.I_CHANNELS` (1,3,5,7,9,11)。
- `Calibration.py` 的接口已全部修改为仅接收 `chIdx` (即物理通道)，内部依靠 `chIdx in HWConfig.I_CHANNELS` 自动判定电流/电压。

### 2. 校准算法 (Calibration.py)
- **纯粹的数学建模**：校准公式已被精简为最标准的正向 `y = kx + b` (PhysToReg) 和逆向 `x = (y - b) / k` (RegToPhys)。
- **硬件反相解耦**：不再在代码里硬编码针对硬件放大器反相的负号（`-amplitude`）。如果通道是反相的，直接在 `calibration.json` 里将 `factor` 设为负数（例如 `-0.95`）。代码对符号做到完全透明。
- 采用 Python 单例模式 (`calib = Calibration()`) 保证全系统共享同一份配置，减少 I/O 开销。

### 3. 底层防抖与状态机 (USEEngine / TestCtrl)
- 彻底清理了 `TestCtrl.py` 中的过度防御逻辑，删除了不必要的中间变量。
- 在构建基准节点（Base Node 0x0000）时，防抖帧（`SYS_SET_DBNC`）始终位于整个预加载包的末尾（`baseFrame[-1]`），避免被覆盖。
- 使用 Python 3.10+ 的 `match-case` 语法替代了传统的 `if-elif` 链，变量命名严格遵循 **camelCase（驼峰命名法）**。

## 🚀 待执行任务 (Next Steps)

### 任务 1：Nuitka 独立编译发布
- 编写构建脚本（如 `build.sh`）。
- 使用 `Nuitka` 以 `--onefile` 或独立目录模式，把项目核心逻辑（包含所有的 `API`, `TestCtrl`, `USEEngine`, `FPGACodec` 等）编译成一个脱离 Python 环境即可运行的二进制文件，命名为 `sysengine`。

### 任务 2：独立配置文件与目录架构
- 规划目标生产环境结构，期望如下：
  ```text
  /opt/RelayProtection/        (或指定的部署根目录)
  ├── sysengine                (编译后的可执行文件)
  └── config/
      └── calibration.json     (完全独立的外置配置文件)
  ```
- **核心需求**：确保编译后的 `sysengine` 运行时，依然能够正确且动态地从外部的 `config/calibration.json` 读取校准系数，而不是将该 JSON 锁死在二进制内部。

---
**致新 AI 助手**：请确认已理解以上架构和目标，然后直接向用户询问是否开始编写 Nuitka 打包编译脚本。
