import math

def _solve_quadratic(a: float, b: float, c: float) -> float:
    """
    求解一元二次方程 a*x^2 + b*x + c = 0，并返回正实根。
    """
    if a == 0:
        if b == 0:
            return 0.0
        return -c / b
    discriminant = b**2 - 4 * a * c
    if discriminant < 0:
        return 0.0
    return (-b + math.sqrt(discriminant)) / (2 * a)

def _solve_sum_diff(id_val: float, ir_val: float, k: float) -> tuple[float, float]:
    """
    求解器 A：和差型
    ir = (i1' + i2') / K  => i1' + i2' = ir * K
    i1' - i2' = id
    """
    i1 = (id_val + ir_val * k) / 2.0
    i2 = (ir_val * k - id_val) / 2.0
    return i1, i2

def _solve_max(id_val: float, ir_val: float, k: float) -> tuple[float, float]:
    """
    求解器 B：最大值型
    max(i1', i2') = ir / K
    假设 i1' >= i2' => i1' = ir / K
    i1' - i2' = id  => i2' = ir / K - id
    """
    val_max = ir_val / k if k != 0 else 0.0
    i1 = val_max
    i2 = val_max - id_val
    return i1, i2

def _solve_const(id_val: float, ir_val: float, k: float) -> tuple[float, float]:
    """
    求解器 C：定值型 (合并 ir_i2_k 与 ir_id_abs_diff)
    i2' = ir / K
    i1' = id + i2'
    注：对于 ir_id_abs_diff，前端下发的 K 恒等于 2。两者公式完全一致。
    """
    i2 = ir_val / k if k != 0 else 0.0
    i1 = id_val + i2
    return i1, i2

def _solve_product(id_val: float, ir_val: float) -> tuple[float, float]:
    """
    求解器 D：乘积型
    i1' * i2' = ir^2
    i1' - i2' = id  => (id + i2') * i2' = ir^2  => i2'^2 + id*i2' - ir^2 = 0
    """
    i2 = _solve_quadratic(1.0, id_val, -(ir_val**2))
    i1 = id_val + i2
    return i1, i2

def _solve_product_sq(id_val: float, ir_val: float) -> tuple[float, float]:
    """
    路由 2 乘积型 (id_sum_sq / id_diff_sq)
    i1' * i2' = ir
    i1' - i2' = sqrt(id) = d  => (d + i2') * i2' = ir  => i2'^2 + d*i2' - ir = 0
    """
    d = math.sqrt(id_val) if id_val >= 0 else 0.0
    i2 = _solve_quadratic(1.0, d, -ir_val)
    i1 = d + i2
    return i1, i2

def solve(id_val: float, ir_val: float, k: float, kp1: float, kp2: float,
          id_eq: str, ir_eq: str) -> tuple[float, float, float]:
    """
    解算差动保护方程式，并返回物理输出电流幅值与侧 2 基准相位。
    
    参数:
        id_val: 动作电流 Id 试验值
        ir_val: 制动电流 Ir 试验值
        k: 制动系数 K
        kp1: 侧 1 变比/接线补偿系数
        kp2: 侧 2 变比/接线补偿系数
        id_eq: 动作方程 API 类型 (id_i1, id_sum, id_diff, id_sum_sq, id_diff_sq)
        ir_eq: 制动方程 API 类型
    
    返回:
        (i1_out, i2_out, theta2) -> 侧 1 物理幅值, 侧 2 物理幅值, 侧 2 最终相位偏角
    """
    # 1. 确定初始 theta2
    theta2 = 180.0 if id_eq in ("id_sum", "id_sum_sq") else 0.0

    # 2. 一级路由
    if id_eq == "id_i1":
        # 直通型
        i1, i2 = id_val, ir_val
    elif id_eq in ("id_sum_sq", "id_diff_sq"):
        # 路由 2 乘积型
        i1, i2 = _solve_product_sq(id_val, ir_val)
    else:
        # 路由 3 (id_sum / id_diff) -> 二级路由
        if ir_eq in ("ir_diff_k", "ir_sum_k", "ir_sum_div_k", "ir_imax_sum_k"):
            i1, i2 = _solve_sum_diff(id_val, ir_val, k)
        elif ir_eq == "ir_max_k":
            i1, i2 = _solve_max(id_val, ir_val, k)
        elif ir_eq in ("ir_i2_k", "ir_id_abs_diff"):
            i1, i2 = _solve_const(id_val, ir_val, k)
        elif ir_eq == "ir_sqrt_cos":
            i1, i2 = _solve_product(id_val, ir_val)
        else:
            # 默认后退到直通，以保证鲁棒性
            i1, i2 = id_val, ir_val

    # 3. 后处理：极性处理（负幅值修正）
    if i2 < 0:
        i2 = abs(i2)
        theta2 = 0.0 if theta2 == 180.0 else 180.0

    # 4. Kp 补偿除法
    i1_out = i1 / kp1 if kp1 != 0 else i1
    i2_out = i2 / kp2 if kp2 != 0 else i2

    return i1_out, i2_out, theta2

import cmath

def _apply_minimax_constraint(target: list[complex], eq_type: str) -> list[complex]:
    """
    状态空间求解器：利用最小化相电流峰值 (Minimax) 约束求解欠定方程。
    eq_type: "none", "zsc", "y-side", "delta-side"
    """
    if eq_type == "none":
        return list(target)
        
    # 判断是否为单相故障 (只有一个相电流非零)
    non_zeros = sum(1 for x in target if abs(x) > 1e-6)
    is_single_phase = (non_zeros == 1)
    
    if eq_type == "zsc":
        # 场景 2：Y侧零序校正。物理方程：I - I0 = I'
        if is_single_phase:
            # 采用双通道对消法最小化峰值：令非故障相为0，耦合相与动作相反相
            idx = next(i for i, x in enumerate(target) if abs(x) > 1e-6)
            res = [0j, 0j, 0j]
            res[idx] = target[idx]
            res[(idx+1)%3] = -target[idx]
            return res
        else:
            # 多相故障（默认已消除零序）
            return list(target)
            
    elif eq_type in ("y-side", "delta-side"):
        # 场景 3 & 4：相位校正。采用双通道对消法：
        # 对于单相目标，需要乘以 1.5 倍然后应用逆矩阵，以达成理论上完美的双通道对消峰值最小化。
        multiplier = 1.5 if is_single_phase else 1.0
        t = [x * multiplier for x in target]
        sqrt3 = math.sqrt(3)
        
        if eq_type == "y-side":
            # 物理方程：(Ia - Ib)/sqrt(3) = Ia' -> M_{+30}
            # 其满足 Minimax 约束的逆矩阵为 M_{-30}
            ia = (t[0] - t[2]) / sqrt3
            ib = (t[1] - t[0]) / sqrt3
            ic = (t[2] - t[1]) / sqrt3
            return [ia, ib, ic]
        else:
            # eq_type == "delta-side"
            # 物理方程：(Ix - Iz)/sqrt(3) = Ix' -> M_{-30}
            # 其满足 Minimax 约束的逆矩阵为 M_{+30}
            ia = (t[0] - t[1]) / sqrt3
            ib = (t[1] - t[2]) / sqrt3
            ic = (t[2] - t[0]) / sqrt3
            return [ia, ib, ic]
            
    return list(target)

def calculate_physical_currents(
    i1_prime: float, i2_prime: float, theta2: float,
    test_phase: str,
    phase_correction: str,
    zero_seq_correction: bool,
    clock: int,
    l1_type: str,
    l2_type: str
) -> tuple[list[complex], list[complex]]:
    """
    根据继电保护状态空间方程，将内部目标电流映射到物理发波电流。
    """
    # 1. 确立“内部目标向量”
    c1 = complex(i1_prime, 0)
    rad2 = math.radians(theta2)
    c2 = cmath.rect(i2_prime, rad2)
    
    t1 = [0j, 0j, 0j]
    t2 = [0j, 0j, 0j]
    
    if test_phase == "A":
        t1[0], t2[0] = c1, c2
    elif test_phase == "B":
        t1[1], t2[1] = c1, c2
    elif test_phase == "C":
        t1[2], t2[2] = c1, c2
    elif test_phase == "AB":
        t1[0], t1[1] = c1, -c1
        t2[0], t2[1] = c2, -c2
    elif test_phase == "BC":
        t1[1], t1[2] = c1, -c1
        t2[1], t2[2] = c2, -c2
    elif test_phase == "CA":
        t1[2], t1[0] = c1, -c1
        t2[2], t2[0] = c2, -c2
    elif test_phase == "ABC":
        rad_120 = math.radians(120)
        a1 = cmath.rect(1.0, rad_120)
        a2 = cmath.rect(1.0, -rad_120)
        t1 = [c1, c1 * a2, c1 * a1]
        t2 = [c2, c2 * a2, c2 * a1]

    # 2. 判断两侧方程组场景
    eq1 = "none"
    eq2 = "none"
    
    if phase_correction == "y-side":
        if l1_type == "y": eq1 = "y-side"
        if l2_type == "y": eq2 = "y-side"
    elif phase_correction == "delta-side":
        if l1_type == "d": eq1 = "delta-side"
        if l2_type == "d": eq2 = "delta-side"
        
    if zero_seq_correction:
        if eq1 == "none" and l1_type == "y": eq1 = "zsc"
        if eq2 == "none" and l2_type == "y": eq2 = "zsc"

    # 3. 如果侧 2 没有相位校正，需要施加变压器钟点数造成的物理角差
    if eq2 in ("none", "zsc"):
        delta_theta = (12 - clock) * 30.0
        rot = cmath.rect(1.0, math.radians(delta_theta))
        t2 = [x * rot for x in t2]

    # 4. 代入状态空间求解器，附加 Minimax 约束
    phys1 = _apply_minimax_constraint(t1, eq1)
    phys2 = _apply_minimax_constraint(t2, eq2)
    
    return phys1, phys2
