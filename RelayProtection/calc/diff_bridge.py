"""
diff_bridge.py — 差动试验桥接函数

将前端 payload JSON 参数映射到 diff.py 的 solve() + calculate_physical_currents()，
一次调用即可从 (ir, id) 坐标得到 6 路物理电流的 [幅值, 相位°]。
"""

import cmath
import math
from typing import Dict, Any, List

from calc.diff import solve, calculate_physical_currents


def compute_6ch(ir: float, id_val: float, payload: Dict[str, Any]) -> List[List[float]]:
    """
    核心桥接函数：输入 (ir, id) 坐标 + 前端 payload，输出 6 路电流参数。

    参数:
        ir:      制动电流坐标值
        id_val:  动作电流坐标值
        payload: 前端下发的 params.payload 完整字典

    返回:
        [[amp0, angle0], [amp1, angle1], ..., [amp5, angle5]]
        索引 0~2 = 侧1 (Ia, Ib, Ic)
        索引 3~5 = 侧2 (Ix, Iy, Iz)
    """
    # ── 1. 提取参数 ──
    eq = payload.get("equationSettings", {})
    prot = payload.get("protectionSettings", {})
    proj = payload.get("projectSettings", {})

    id_eq = eq.get("idEquation", "id_sum")
    ir_eq = eq.get("irEquation", "ir_diff_k")
    k = eq.get("kFactor", 2.0)
    kp1 = eq.get("kp1", 1.0)
    kp2 = eq.get("kp2", 1.0)

    test_phase = proj.get("testPhase", "A")

    l1_type = prot.get("vectorGroupLetter1", "y")
    l2_type = prot.get("vectorGroupLetter2", "y")
    clock = int(prot.get("vectorGroupClock", "0"))
    phase_correction = prot.get("phaseCorrection", "none")
    zero_seq_correction = prot.get("zeroSequenceCorrection", False)

    # ── 2. 方程求解：(ir, id) → (i1, i2, theta2) ──
    i1_out, i2_out, theta2 = solve(
        id_val=id_val,
        ir_val=ir,
        k=k,
        kp1=kp1,
        kp2=kp2,
        id_eq=id_eq,
        ir_eq=ir_eq
    )

    # ── 3. 状态空间方程：标量 → 6 路复数电流 ──
    phys1, phys2 = calculate_physical_currents(
        i1_prime=i1_out,
        i2_prime=i2_out,
        theta2=theta2,
        test_phase=test_phase,
        phase_correction=phase_correction,
        zero_seq_correction=zero_seq_correction,
        clock=clock,
        l1_type=l1_type,
        l2_type=l2_type
    )

    # ── 4. 复数 → [幅值, 相位°] ──
    result = []
    for c in phys1 + phys2:
        amp = abs(c)
        angle = math.degrees(cmath.phase(c)) if amp > 1e-9 else 0.0
        result.append([round(amp, 6), round(angle, 4)])

    return result


# ── 内置测试 ──
if __name__ == "__main__":
    # 典型用例：变压器 Y/d-11, id_sum, ir_diff_k, K=2, A相差动
    test_payload = {
        "equationSettings": {
            "idEquation": "id_sum",
            "irEquation": "ir_diff_k",
            "kFactor": 2.0,
            "kp1": 1.0,
            "kp2": 1.0
        },
        "protectionSettings": {
            "vectorGroupLetter1": "y",
            "vectorGroupLetter2": "d",
            "vectorGroupClock": "11",
            "phaseCorrection": "none",
            "zeroSequenceCorrection": False
        },
        "projectSettings": {
            "testPhase": "A"
        }
    }

    ir_test = 1.0
    id_test = 0.5

    channels = compute_6ch(ir_test, id_test, test_payload)

    print(f"输入: ir={ir_test}, id={id_test}")
    print(f"方程: id_sum + ir_diff_k, K=2, Y/d-11, 无校正, A相")
    print()

    labels = ["Ia", "Ib", "Ic", "Ix", "Iy", "Iz"]
    for i, (amp, ang) in enumerate(channels):
        print(f"  {labels[i]}: {amp:8.4f} A ∠ {ang:8.2f}°")

    # 手算验证:
    # solve: i1' = (0.5 + 1.0*2)/2 = 1.25, i2' = (1.0*2 - 0.5)/2 = 0.75, theta2=180°
    # kp1=kp2=1 → i1_out=1.25, i2_out=0.75
    # phaseCorrection=none → 侧2需叠加钟点角差 (12-11)*30=30°
    # A相单相: phys1=[1.25∠0°, 0, 0], phys2=[0.75∠(180+30)°, 0, 0]
    print()
    print("手算预期:")
    print(f"  Ia: 1.2500 A ∠ 0.00°")
    print(f"  Ix: 0.7500 A ∠ 210.00°")
    print(f"  其余: 0 A")
