import unittest
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from calc.diff import solve

class TestDiffSolver(unittest.TestCase):
    def test_id_i1_direct(self):
        # 1. 直通模式测试 (id_i1 + ir_i2)
        # i1 = id = 5.0, i2 = ir = 3.0
        # kp1 = 1.0, kp2 = 1.0 -> 物理值也是 5.0, 3.0. theta2 = 0.0
        i1, i2, theta2 = solve(5.0, 3.0, 0.5, 1.0, 1.0, "id_i1", "ir_i2")
        self.assertAlmostEqual(i1, 5.0)
        self.assertAlmostEqual(i2, 3.0)
        self.assertAlmostEqual(theta2, 0.0)

    def test_id_sum_sq_product(self):
        # 2. 路由 2 乘积型 (id_sum_sq + ir_sum_sq_cos)
        # id = 4.0 -> d = sqrt(4.0) = 2.0
        # ir = 3.0
        # i2^2 + 2*i2 - 3 = 0 -> (i2 + 3)(i2 - 1) = 0 -> i2 = 1.0, i1 = d + i2 = 3.0
        # theta2 = 180.0
        # kp1 = 1.5, kp2 = 2.0 -> i1_out = 3.0 / 1.5 = 2.0, i2_out = 1.0 / 2.0 = 0.5
        i1, i2, theta2 = solve(4.0, 3.0, 0.5, 1.5, 2.0, "id_sum_sq", "ir_sum_sq_cos")
        self.assertAlmostEqual(i1, 2.0)
        self.assertAlmostEqual(i2, 0.5)
        self.assertAlmostEqual(theta2, 180.0)

    def test_solver_a_sum_diff(self):
        # 3. 求解器 A：和差型 (id_sum + ir_diff_k)
        # id = 2.0, ir = 8.0, k = 0.5
        # i1' + i2' = ir * k = 4.0
        # i1' - i2' = id = 2.0
        # -> i1' = 3.0, i2' = 1.0
        # kp1 = 1.0, kp2 = 1.0 -> i1_out = 3.0, i2_out = 1.0, theta2 = 180.0
        i1, i2, theta2 = solve(2.0, 8.0, 0.5, 1.0, 1.0, "id_sum", "ir_diff_k")
        self.assertAlmostEqual(i1, 3.0)
        self.assertAlmostEqual(i2, 1.0)
        self.assertAlmostEqual(theta2, 180.0)

        # 极性反转测试 (i2' < 0)
        # id = 6.0, ir = 4.0, k = 0.5
        # i1' + i2' = 2.0
        # i1' - i2' = 6.0
        # -> i1' = 4.0, i2' = -2.0
        # 极性修正后：i2' = 2.0, theta2 由 180.0 翻转为 0.0
        i1, i2, theta2 = solve(6.0, 4.0, 0.5, 1.0, 1.0, "id_sum", "ir_diff_k")
        self.assertAlmostEqual(i1, 4.0)
        self.assertAlmostEqual(i2, 2.0)
        self.assertAlmostEqual(theta2, 0.0)

    def test_solver_b_max(self):
        # 4. 求解器 B：最大值型 (id_diff + ir_max_k)
        # id = 1.0, ir = 3.0, k = 0.5
        # max(i1', i2') = ir / k = 6.0
        # 假设 i1' >= i2' -> i1' = 6.0
        # i1' - i2' = id = 1.0 -> i2' = 5.0
        # kp1 = 1.0, kp2 = 1.0 -> i1_out = 6.0, i2_out = 5.0, theta2 = 0.0
        i1, i2, theta2 = solve(1.0, 3.0, 0.5, 1.0, 1.0, "id_diff", "ir_max_k")
        self.assertAlmostEqual(i1, 6.0)
        self.assertAlmostEqual(i2, 5.0)
        self.assertAlmostEqual(theta2, 0.0)

    def test_solver_c_const_merged(self):
        # 5. 求解器 C：定值型 (ir_i2_k 与 ir_id_abs_diff 的合并)
        # 场景 A: ir_i2_k, id = 2.0, ir = 3.0, k = 0.5
        # i2' = ir / k = 6.0
        # i1' = id + i2' = 8.0
        # kp1 = 2.0, kp2 = 3.0 -> i1_out = 4.0, i2_out = 2.0, theta2 = 0.0
        i1_a, i2_a, theta2_a = solve(2.0, 3.0, 0.5, 2.0, 3.0, "id_diff", "ir_i2_k")
        self.assertAlmostEqual(i1_a, 4.0)
        self.assertAlmostEqual(i2_a, 2.0)
        self.assertAlmostEqual(theta2_a, 0.0)

        # 场景 B: ir_id_abs_diff, id = 2.0, ir = 3.0, k = 2.0 (前端下发为 2.0)
        # i2' = ir / 2.0 = 1.5
        # i1' = id + i2' = 3.5
        # kp1 = 2.0, kp2 = 3.0 -> i1_out = 1.75, i2_out = 0.5, theta2 = 0.0
        i1_b, i2_b, theta2_b = solve(2.0, 3.0, 2.0, 2.0, 3.0, "id_diff", "ir_id_abs_diff")
        self.assertAlmostEqual(i1_b, 1.75)
        self.assertAlmostEqual(i2_b, 0.5)
        self.assertAlmostEqual(theta2_b, 0.0)

        # 验证当 k=2.0 时，两者输出完全一致！
        i1_c, i2_c, theta2_c = solve(2.0, 3.0, 2.0, 2.0, 3.0, "id_diff", "ir_i2_k")
        self.assertEqual(i1_b, i1_c)
        self.assertEqual(i2_b, i2_c)
        self.assertEqual(theta2_b, theta2_c)

    def test_solver_d_product(self):
        # 6. 求解器 D：乘积型 (id_diff + ir_sqrt_cos)
        # id = 3.0, ir = 2.0
        # i2'^2 + 3*i2' - 4 = 0 -> (i2' + 4)(i2' - 1) = 0 -> i2' = 1.0, i1' = 4.0
        # kp1 = 1.0, kp2 = 1.0 -> i1_out = 4.0, i2_out = 1.0, theta2 = 0.0
        i1, i2, theta2 = solve(3.0, 2.0, 0.5, 1.0, 1.0, "id_diff", "ir_sqrt_cos")
        self.assertAlmostEqual(i1, 4.0)
        self.assertAlmostEqual(i2, 1.0)
        self.assertAlmostEqual(theta2, 0.0)

if __name__ == "__main__":
    unittest.main()
