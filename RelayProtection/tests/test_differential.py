"""
test_differential.py — 差动试验 API 模拟测试

验证:
1. compute_6ch / compute_2i 桥接函数输出正确
2. 通道映射正确 (API 6~11 = 电流通道，不与 0~5 电压通道冲突)
3. 6I / 2I 两种模式的 FSM 流程
4. √3 校正逻辑
"""

import sys
import os
import asyncio
import math
import cmath

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ══════════════════════════════════════════
# Part 1: 测试 compute_6ch
# ══════════════════════════════════════════

def test_compute_6ch():
    from calc.diff_bridge import compute_6ch

    print("=" * 60)
    print("Part 1: 测试 compute_6ch 桥接函数")
    print("=" * 60)

    payload = {
        "equationSettings": {
            "idEquation": "id_sum", "irEquation": "ir_diff_k",
            "kFactor": 2.0, "kp1": 1.0, "kp2": 1.0
        },
        "protectionSettings": {
            "vectorGroupLetter1": "y", "vectorGroupLetter2": "d",
            "vectorGroupClock": "11", "phaseCorrection": "none",
            "zeroSequenceCorrection": False
        },
        "projectSettings": {"testPhase": "A"}
    }

    channels = compute_6ch(1.0, 0.5, payload)

    labels = ["Ia", "Ib", "Ic", "Ix", "Iy", "Iz"]
    errors = []

    if abs(channels[0][0] - 1.25) > 0.01:
        errors.append(f"Ia 幅值错误: 期望 1.25, 实际 {channels[0][0]}")
    if abs(channels[3][0] - 0.75) > 0.01:
        errors.append(f"Ix 幅值错误: 期望 0.75, 实际 {channels[3][0]}")
    for i in [1, 2, 4, 5]:
        if channels[i][0] > 0.01:
            errors.append(f"{labels[i]} 幅值应为 0, 实际 {channels[i][0]}")

    for i, (amp, ang) in enumerate(channels):
        print(f"  {labels[i]}: {amp:8.4f} A ∠ {ang:8.2f}°")

    if errors:
        print(f"\n❌ {len(errors)} 个错误"); [print(f"  - {e}") for e in errors]
    else:
        print(f"\n✅ compute_6ch 校验通过!")
    return len(errors) == 0


# ══════════════════════════════════════════
# Part 2: 测试 compute_2i + √3
# ══════════════════════════════════════════

def test_compute_2i():
    from calc.diff_bridge import compute_2i

    print("\n" + "=" * 60)
    print("Part 2: 测试 compute_2i (分相差动 + √3)")
    print("=" * 60)

    errors = []

    # Case A: 无校正，Y/Y
    payload_a = {
        "equationSettings": {
            "idEquation": "id_sum", "irEquation": "ir_diff_k",
            "kFactor": 2.0, "kp1": 1.0, "kp2": 1.0
        },
        "protectionSettings": {
            "vectorGroupLetter1": "y", "vectorGroupLetter2": "y",
            "phaseCorrection": "none"
        },
        "connectionSettings": {"i1Angle": 0.0, "i2Angle": 180.0}
    }
    sides_a = compute_2i(1.0, 0.5, payload_a)
    # solve: i1=1.25, i2=0.75, theta2=180
    # 无√3 → i1=1.25, i2=0.75
    # side2_angle = 180 + 180 = 360 → 归一化 = 0°
    print(f"\n  Case A (Y/Y, 无校正):")
    print(f"    Side1: {sides_a[0][0]:.4f} A ∠ {sides_a[0][1]:.2f}°")
    print(f"    Side2: {sides_a[1][0]:.4f} A ∠ {sides_a[1][1]:.2f}°")

    if abs(sides_a[0][0] - 1.25) > 0.01:
        errors.append(f"Case A: i1 应为 1.25, 实际 {sides_a[0][0]}")
    if abs(sides_a[1][0] - 0.75) > 0.01:
        errors.append(f"Case A: i2 应为 0.75, 实际 {sides_a[1][0]}")

    # Case B: Y侧校正, Y/Y → 两侧都乘√3
    payload_b = dict(payload_a)
    payload_b["protectionSettings"] = {
        "vectorGroupLetter1": "y", "vectorGroupLetter2": "y",
        "phaseCorrection": "y-side"
    }
    sides_b = compute_2i(1.0, 0.5, payload_b)
    expected_i1 = 1.25 * math.sqrt(3)
    expected_i2 = 0.75 * math.sqrt(3)
    print(f"\n  Case B (Y/Y, Y侧校正 → 两侧×√3):")
    print(f"    Side1: {sides_b[0][0]:.4f} A (期望 {expected_i1:.4f})")
    print(f"    Side2: {sides_b[1][0]:.4f} A (期望 {expected_i2:.4f})")

    if abs(sides_b[0][0] - expected_i1) > 0.01:
        errors.append(f"Case B: i1 应为 {expected_i1:.4f}, 实际 {sides_b[0][0]}")
    if abs(sides_b[1][0] - expected_i2) > 0.01:
        errors.append(f"Case B: i2 应为 {expected_i2:.4f}, 实际 {sides_b[1][0]}")

    # Case C: Δ侧校正, Y/D → 只有侧2乘√3
    payload_c = dict(payload_a)
    payload_c["protectionSettings"] = {
        "vectorGroupLetter1": "y", "vectorGroupLetter2": "d",
        "phaseCorrection": "delta-side"
    }
    sides_c = compute_2i(1.0, 0.5, payload_c)
    print(f"\n  Case C (Y/D, Δ侧校正 → 只有侧2×√3):")
    print(f"    Side1: {sides_c[0][0]:.4f} A (期望 1.2500, 不乘)")
    print(f"    Side2: {sides_c[1][0]:.4f} A (期望 {0.75*math.sqrt(3):.4f})")

    if abs(sides_c[0][0] - 1.25) > 0.01:
        errors.append(f"Case C: i1 不应乘√3, 期望 1.25, 实际 {sides_c[0][0]}")
    if abs(sides_c[1][0] - 0.75 * math.sqrt(3)) > 0.01:
        errors.append(f"Case C: i2 应乘√3, 期望 {0.75*math.sqrt(3):.4f}, 实际 {sides_c[1][0]}")

    if errors:
        print(f"\n❌ {len(errors)} 个错误"); [print(f"  - {e}") for e in errors]
    else:
        print(f"\n✅ compute_2i + √3 校验通过!")
    return len(errors) == 0


# ══════════════════════════════════════════
# Part 3: 通道映射校验
# ══════════════════════════════════════════

def test_channel_mapping():
    print("\n" + "=" * 60)
    print("Part 3: 通道映射校验")
    print("=" * 60)

    from api.ApiDifferentialTest import (
        ALL_CURRENT_CHANNELS, TERMINAL_6I, TERMINAL_2I
    )

    errors = []
    VOLTAGE_CHANNELS = {"0", "1", "2", "3", "4", "5"}
    CURRENT_CHANNELS = {"6", "7", "8", "9", "10", "11"}

    # 检查 ALL_CURRENT_CHANNELS
    all_set = set(ALL_CURRENT_CHANNELS)
    if all_set != CURRENT_CHANNELS:
        errors.append(f"ALL_CURRENT_CHANNELS={all_set} != 期望{CURRENT_CHANNELS}")
    print(f"  ALL_CURRENT_CHANNELS: {ALL_CURRENT_CHANNELS} ✓")

    # 检查 6I 映射没有电压通道
    for name, chs in TERMINAL_6I.items():
        for ch in chs:
            if ch in VOLTAGE_CHANNELS:
                errors.append(f"TERMINAL_6I['{name}'] 包含电压通道 {ch}!")
        print(f"  TERMINAL_6I['{name}']: {chs}")

    # 检查 2I 映射没有电压通道
    for name, chs in TERMINAL_2I.items():
        for ch in chs:
            if ch in VOLTAGE_CHANNELS:
                errors.append(f"TERMINAL_2I['{name}'] 包含电压通道 {ch}!")

    print(f"  TERMINAL_2I 共 {len(TERMINAL_2I)} 项，全部在电流通道范围内")

    if errors:
        print(f"\n❌ {len(errors)} 个错误"); [print(f"  - {e}") for e in errors]
    else:
        print(f"\n✅ 通道映射校验通过!")
    return len(errors) == 0


# ══════════════════════════════════════════
# Part 4: API 流程 (6I + 2I)
# ══════════════════════════════════════════

class MockCtrl:
    def __init__(self):
        self.nodes = {}
        self.triggered = []
        self.reports = []
        self.value_updates = []
        self.stopped = False

    def upsertNodes(self, nodes_dict):
        self.nodes.update(nodes_dict)
        for nid, node in nodes_dict.items():
            info = f"    Node {nid}: mode={node.mode}"
            if node.timeoutMs is not None: info += f", timeout={node.timeoutMs}ms→{node.timeoutId}"
            if node.diMatchMask is not None: info += f", diMask=0x{node.diMatchMask:X}→{node.diMatchId}"
            if node.base: info += f", base={len(node.base)} ch"
            print(info)

    def trigNode(self, nodeId):
        self.triggered.append(nodeId)

    def sendReport(self, data):
        self.reports.append(data)
        print(f"    Report: {data}")

    def stopTest(self, reason=None):
        self.stopped = True

    def _send(self, data):
        if data.get("type") == "value_update":
            self.value_updates.append(data)


async def test_api_6i():
    from api.ApiDifferentialTest import ApiDifferentialTest

    print("\n" + "=" * 60)
    print("Part 4a: API 流程 (6I, ratio-fixed)")
    print("=" * 60)

    errors = []
    api = ApiDifferentialTest()
    mock = MockCtrl()
    api.ctrl = mock
    api.isActive = True
    api.physDictToReg = lambda d, **kw: {k: v for k, v in d.items()} if d else {}

    params = {
        "sys": {"logicMask": 7},
        "statics": {"0": {"1": [57.735, 0.0]}},  # 电压通道
        "payload": {
            "protectionSettings": {
                "vectorGroupLetter1": "y", "vectorGroupLetter2": "y",
                "vectorGroupClock": "0", "phaseCorrection": "none",
                "zeroSequenceCorrection": False
            },
            "equationSettings": {
                "idEquation": "id_sum", "irEquation": "ir_diff_k",
                "kFactor": 2.0, "kp1": 1.0, "kp2": 1.0
            },
            "connectionSettings": {
                "type": "ext-differential",
                "i1Terminal": "high-y", "i2Terminal": "low-y"
            },
            "projectSettings": {
                "activeProject": "ratio-fixed", "testPhase": "A",
                "points": [{"id": 1, "x": 0.5, "y": 1.0}]
            },
            "testParams": {
                "faultDuration": 1.0, "preFaultDuration": 0.5,
                "simulatePreFault": True
            }
        }
    }

    api._onSetup(params)
    await asyncio.sleep(0.7)

    # 检查通道映射：Node 2 的 base 应该包含通道 "6"~"11"，不含 "1"/"4"/"7"/"13"/"16"
    if 2 in mock.nodes:
        n2_channels = set(mock.nodes[2].base.keys()) if mock.nodes[2].base else set()
        for ch in ["6", "7", "8", "9", "10", "11"]:
            if ch not in n2_channels:
                errors.append(f"Node 2 缺少电流通道 {ch}")
        for bad_ch in ["1", "4", "13", "16"]:
            if bad_ch in n2_channels:
                errors.append(f"Node 2 含有错误通道 {bad_ch}")
        print(f"  Node 2 通道: {sorted(n2_channels)}")
    else:
        errors.append("Node 2 未创建")

    # 模拟 DI 触发
    api._current_node = 2
    api.onDi(0x01, 12345)
    api.onUpdate(3, 0, 12400)
    await asyncio.sleep(0.3)

    if mock.reports:
        rpt = mock.reports[0].get("points", [{}])[0]
        if "status" in rpt:
            errors.append("report 不应含 status")
        if "x" not in rpt:
            errors.append("report 缺少 x 字段")
    else:
        errors.append("未收到 report")

    api.isActive = False
    if errors:
        print(f"\n❌ {len(errors)} 个错误"); [print(f"  - {e}") for e in errors]
    else:
        print(f"\n✅ 6I API 流程 + 通道映射通过!")
    return len(errors) == 0


async def test_api_2i():
    from api.ApiDifferentialTest import ApiDifferentialTest

    print("\n" + "=" * 60)
    print("Part 4b: API 流程 (2I, phase-differential, iab+ixy)")
    print("=" * 60)

    errors = []
    api = ApiDifferentialTest()
    mock = MockCtrl()
    api.ctrl = mock
    api.isActive = True
    api.physDictToReg = lambda d, **kw: {k: v for k, v in d.items()} if d else {}

    params = {
        "sys": {"logicMask": 3},
        "statics": {"0": {"1": [57.735, 0.0]}},
        "payload": {
            "protectionSettings": {
                "vectorGroupLetter1": "y", "vectorGroupLetter2": "d",
                "phaseCorrection": "delta-side"
            },
            "equationSettings": {
                "idEquation": "id_sum", "irEquation": "ir_diff_k",
                "kFactor": 2.0, "kp1": 1.0, "kp2": 1.0
            },
            "connectionSettings": {
                "type": "phase-differential",
                "i1Terminal": "iab", "i1Angle": 0.0,
                "i2Terminal": "ixy", "i2Angle": 180.0
            },
            "projectSettings": {
                "activeProject": "ratio-fixed", "testPhase": "A",
                "points": [{"id": 1, "x": 1.0, "y": 0.5}]
            },
            "testParams": {
                "faultDuration": 1.0, "preFaultDuration": 0.5,
                "simulatePreFault": True
            }
        }
    }

    api._onSetup(params)
    await asyncio.sleep(0.7)

    if 2 in mock.nodes:
        n2_base = mock.nodes[2].base or {}
        n2_channels = set(n2_base.keys())
        print(f"  Node 2 通道: {sorted(n2_channels)}")

        # iab → 通道 6, 7 应该有相同的非零电流
        ch6 = n2_base.get("6", {}).get("1", [0, 0])
        ch7 = n2_base.get("7", {}).get("1", [0, 0])
        if ch6[0] < 0.01:
            errors.append(f"通道 6 (Ia) 应有电流, 实际 {ch6}")
        if ch6 != ch7:
            errors.append(f"iab 并联: 通道 6 和 7 应相同, 6={ch6}, 7={ch7}")
        else:
            print(f"  iab 并联验证: ch6={ch6}, ch7={ch7} ✓")

        # ixy → 通道 9, 10 应该有相同的非零电流（且乘了√3）
        ch9 = n2_base.get("9", {}).get("1", [0, 0])
        ch10 = n2_base.get("10", {}).get("1", [0, 0])
        if ch9[0] < 0.01:
            errors.append(f"通道 9 (Ix) 应有电流, 实际 {ch9}")
        if ch9 != ch10:
            errors.append(f"ixy 并联: 通道 9 和 10 应相同, 9={ch9}, 10={ch10}")
        else:
            print(f"  ixy 并联验证: ch9={ch9}, ch10={ch10} ✓")

        # Δ侧校正: side2 (d侧) 应该乘了√3
        # solve(0.5, 1.0, 2, 1, 1, id_sum, ir_diff_k) → i2=0.75
        expected_i2 = 0.75 * math.sqrt(3)
        if abs(ch9[0] - expected_i2) > 0.02:
            errors.append(f"Side2 应乘√3: 期望 {expected_i2:.4f}, 实际 {ch9[0]}")
        else:
            print(f"  √3 校正验证: i2={ch9[0]:.4f} ≈ 0.75×√3={expected_i2:.4f} ✓")

        # 通道 8 (Ic) 和 11 (Iz) 应为零（不在并联范围内）
        ch8 = n2_base.get("8", {}).get("1", [0, 0])
        ch11 = n2_base.get("11", {}).get("1", [0, 0])
        if ch8[0] > 0.001:
            errors.append(f"通道 8 (Ic) 不应有电流 (不在 iab 中), 实际 {ch8}")
        if ch11[0] > 0.001:
            errors.append(f"通道 11 (Iz) 不应有电流 (不在 ixy 中), 实际 {ch11}")
    else:
        errors.append("Node 2 未创建")

    # 模拟 DI
    api._current_node = 2
    api.onDi(0x01, 100)
    api.onUpdate(3, 0, 200)
    await asyncio.sleep(0.3)

    api.isActive = False
    if errors:
        print(f"\n❌ {len(errors)} 个错误"); [print(f"  - {e}") for e in errors]
    else:
        print(f"\n✅ 2I API 流程 + 并联 + √3 通过!")
    return len(errors) == 0


# ══════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════

async def main():
    print("差动试验 API 完整测试")
    print("=" * 60)

    ok1 = test_compute_6ch()
    ok2 = test_compute_2i()
    ok3 = test_channel_mapping()
    ok4 = await test_api_6i()
    ok5 = await test_api_2i()

    print("\n" + "=" * 60)
    results = [("compute_6ch", ok1), ("compute_2i+√3", ok2),
               ("通道映射", ok3), ("6I API", ok4), ("2I API", ok5)]
    for name, ok in results:
        print(f"  {name}: {'✅' if ok else '❌'}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
