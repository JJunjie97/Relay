# 差动保护核心计算与控制参数说明

本文档对差动保护解算管线在幅值计算（已算得 i1_prime 和 i2_prime）之后，进行**相位旋转、硬件通道展开、物理端子映射及故障负荷叠加**所需的所有核心控制变量进行完整定义与映射。

---

## 1. 核心控制参数映射表

| 变量路径 | 类型 | 说明及枚举值 |
|---|---|---|
| `params.payload.protectionSettings.vectorGroupLetter1` | string | **I1 侧（高压侧）联结字母**（"y" / "d"） |
| `params.payload.protectionSettings.vectorGroupLetter2` | string | **I2 侧（低压侧）联结字母**（"y" / "d"） |
| `params.payload.protectionSettings.vectorGroupClock` | string | **低压侧联结钟点数**（"0" ~ "11"） |
| `params.payload.protectionSettings.phaseCorrection` | string | **相位校正侧选项**（"none" / "y-side" / "delta-side"） |
| `params.payload.protectionSettings.zeroSequenceCorrection` | boolean | **零序电流校正**（true / false） |
| `params.payload.projectSettings.testPhase` | string | **测试相别**（"A" / "B" / "C" / "AB" / "BC" / "CA" / "ABC"） |
| `params.payload.connectionSettings.type` | string | **接线/通道模式**（"ext-differential" / "phase-differential"） |
| `params.payload.connectionSettings.i1Terminal` / `i2Terminal` | string | **物理端子路由映射** |
| `params.payload.testParams.diffMode` | number | **差动试验类型**（0 / 1 / 2） |
| `params.payload.testParams.loadCurrent` | number | **稳定负荷电流幅值** |
