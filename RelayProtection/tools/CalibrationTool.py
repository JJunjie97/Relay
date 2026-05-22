import os
import sys
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import json
import asyncio
import gradio as gr
import threading
import math

from comms.HWGateway import HWGateway
from logic.FPGACodec import HWCodec, HWConfig
from logic.Calibration import calib

class CalibrationEditor:
    """
    A separate class specifically for updating and saving calibration parameters.
    This functionality is only needed during calibration and separated from the main runtime.
    """
    def __init__(self, calib_obj):
        self.calib = calib_obj
        
    def SaveCalib(self):
        try:
            os.makedirs(os.path.dirname(self.calib.calib_filepath), exist_ok=True)
            with open(self.calib.calib_filepath, 'w', encoding='utf-8') as f:
                json.dump(self.calib.calib_data, f, indent=4)
        except Exception as e:
            print(f"Failed to save calibration file: {e}")

    def UpdateCalib(self, ch_idx: int, factor: float, bias: float):
        ch_str = str(ch_idx)
        self.calib.calib_data[ch_str] = {
            "factor": round(factor, 4),
            "bias": round(bias, 4)
        }
        if 0 <= ch_idx < 16:
            self.calib._factors[ch_idx] = round(factor, 4)
            self.calib._biases[ch_idx] = round(bias, 4)
        self.SaveCalib()

calib_editor = CalibrationEditor(calib)

if __name__ == '__main__':
    class MockEngine:
        def HandleHwFeedback(self, soe, count):
            pass
            
    hwGateway = HWGateway()
    hwGateway.engine = MockEngine()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(hwGateway.Connect())
    
    def _sync_send_signal(ch_idx_str: str, mode: str, amp_reg_16: float):
        ch_idx = int(ch_idx_str)
        is_cur = ch_idx >= 6
        
        warn_msg = ""
        if is_cur:
            limit_reg_16 = int((2.0 * HWConfig.CURRENT) / 65536)
            if amp_reg_16 > limit_reg_16:
                amp_reg_16 = limit_reg_16
                warn_msg = "\n⚠️ 保护机制触发：为保护万用表，电流指令已被强行限制到最大 2A 安全上限！"
                
        hw_ch = HWConfig.MapChannel(ch_idx)
        mask = 1 << hw_ch
        amp_reg_32 = int(amp_reg_16) << 16
        
        async def send_task():
            hwGateway.SendBytes(HWCodec.FRAME_SYS_RESET)
            await asyncio.sleep(0.05)
            hwGateway.SendBytes(HWCodec.FRAME_SYS_START)
            await asyncio.sleep(0.05)
            
            if mode == "DC":
                frame_dc = HWCodec.BuildParamFrame(HWCodec.DDS_WR_SHADOW, 0xff, mask, amp_reg_32, 0)
                print(f"[{mode}] Sending Frame: {frame_dc.hex().upper()}")
                hwGateway.SendBytes(frame_dc)
            else:
                frame_ac = HWCodec.BuildParamFrame(HWCodec.DDS_WR_SHADOW, 0, mask, amp_reg_32, 0)
                print(f"[{mode}] Sending Frame: {frame_ac.hex().upper()}")
                hwGateway.SendBytes(frame_ac)
                
            await asyncio.sleep(0.01)
            hwGateway.SendBytes(HWCodec.FRAME_SYS_UPDATE)
                
        asyncio.run_coroutine_threadsafe(send_task(), loop).result()
        return f"✅ 已下发 Reg:{int(amp_reg_16)} ({mode}) 到通道 {ch_idx_str}{warn_msg}"

    def _sync_stop_signal():
        async def stop_task():
            hwGateway.SendBytes(HWCodec.FRAME_SYS_RESET)
        asyncio.run_coroutine_threadsafe(stop_task(), loop).result()
        return "🛑 已复位并停止输出"

    def calculate_and_save(ch_idx_str: str, raw_dc_reg_16: float, meas_dc: float, raw_ac_reg_16: float, meas_ac: float, invert_phase: bool):
        try:
            if meas_ac == 0:
                return "❌ 计算失败：交流实测值不能为 0！", None, None
            
            ch_idx = int(ch_idx_str)
            is_cur = ch_idx >= 6
            scale = HWConfig.CURRENT if is_cur else HWConfig.VOLTAGE
            
            raw_dc_phys = (int(raw_dc_reg_16) << 16) / scale
            raw_ac_phys = (int(raw_ac_reg_16) << 16) / scale
            
            meas_ac_peak = meas_ac * math.sqrt(2)
            
            factor = raw_ac_phys / meas_ac_peak
            if invert_phase:
                factor = -factor
            
            bias = meas_dc * factor - raw_dc_phys
            
            calib_editor.UpdateCalib(ch_idx, factor, bias)
            
            return f"✅ 保存成功！通道 {ch_idx_str} -> Factor: {factor:.6f}, Bias: {bias:.6f}", factor, bias
        except Exception as e:
            return f"❌ 异常: {e}", None, None

    def _on_target_change(ch_idx_str: str, reg_val_16: float, is_ac: bool = False):
        try:
            ch_idx = int(ch_idx_str)
            is_cur = ch_idx >= 6
            if is_cur:
                limit_reg_16 = int((2.0 * HWConfig.CURRENT) / 65536)
                if reg_val_16 > limit_reg_16:
                    reg_val_16 = float(limit_reg_16)
                    
            scale = HWConfig.CURRENT if is_cur else HWConfig.VOLTAGE
            phys_val = (int(reg_val_16) << 16) / scale
            if is_ac:
                phys_val = phys_val / math.sqrt(2)
                
            unit = 'A' if is_cur else 'V'
            mode_str = "RMS" if is_ac else "DC"
            return reg_val_16, f"≈ {phys_val:.3f} {unit} ({mode_str})", reg_val_16
        except:
            return reg_val_16, "计算错误", reg_val_16

    with gr.Blocks(title="硬件通道独立校准工具") as ui:
        gr.Markdown("## 硬件通道独立校准工具\n基于 FPGA 真实 Reg (16-bit) 值的纯净校准。")
        
        with gr.Row():
            ch_dropdown = gr.Dropdown(choices=[str(i) for i in range(12)], value="0", label="选择通道 (0-5: 电压, 6-11: 电流)")
            
        with gr.Row():
            dc_target_input = gr.Number(value=16383, label="DC 测试下发 Reg 值 (16-bit)")
            dc_phys_display = gr.Textbox(label="理论物理值", value="≈ 99.994 V (DC)", interactive=False)
            btn_dc_output = gr.Button("🚀 发送 DC 测试信号", variant="primary")
            
        with gr.Row():
            ac_target_input = gr.Number(value=16383, label="AC 测试下发 Reg 值 (16-bit)")
            ac_phys_display = gr.Textbox(label="理论物理值", value="≈ 70.706 V (RMS)", interactive=False)
            btn_ac_output = gr.Button("🚀 发送 AC 测试信号", variant="primary")
            
        btn_stop = gr.Button("🛑 停止输出并复位", variant="stop")
        output_status = gr.Textbox(label="状态", interactive=False)
        
        btn_dc_output.click(fn=lambda ch, reg: _sync_send_signal(ch, "DC", reg), inputs=[ch_dropdown, dc_target_input], outputs=output_status)
        btn_ac_output.click(fn=lambda ch, reg: _sync_send_signal(ch, "AC", reg), inputs=[ch_dropdown, ac_target_input], outputs=output_status)
        btn_stop.click(fn=_sync_stop_signal, inputs=[], outputs=output_status)
        
        gr.Markdown("### 计算校准参数")
        with gr.Row():
            r1 = gr.Number(value=0.0, label="DC Reg (16-bit) (点1)", interactive=True)
            m1 = gr.Number(value=0.0, label="Meas1 (点1 直流实测值)")
        with gr.Row():
            r2 = gr.Number(value=16383.0, label="AC Reg (16-bit) (点2)", interactive=True)
            m2 = gr.Number(value=0.0, label="Meas2 (点2 交流实测值 RMS)")
            
        dc_target_input.change(fn=lambda ch, reg: _on_target_change(ch, reg, False), inputs=[ch_dropdown, dc_target_input], outputs=[dc_target_input, dc_phys_display, r1])
        ac_target_input.change(fn=lambda ch, reg: _on_target_change(ch, reg, True), inputs=[ch_dropdown, ac_target_input], outputs=[ac_target_input, ac_phys_display, r2])
        
        def _on_ch_change(ch_str, dc_val, ac_val):
            dc_reg, dc_phys, dc_r = _on_target_change(ch_str, dc_val, False)
            ac_reg, ac_phys, ac_r = _on_target_change(ch_str, ac_val, True)
            return dc_reg, dc_phys, dc_r, ac_reg, ac_phys, ac_r
            
        ch_dropdown.change(fn=_on_ch_change, inputs=[ch_dropdown, dc_target_input, ac_target_input], outputs=[dc_target_input, dc_phys_display, r1, ac_target_input, ac_phys_display, r2])
            
        with gr.Row():
            invert_checkbox = gr.Checkbox(value=True, label="硬件输出反相 (如果输出和下发波形相反，请勾选此项)")
            
        btn_calc = gr.Button("💾 计算并保存至 JSON", variant="primary")
        calc_status = gr.Textbox(label="计算结果", interactive=False)
        
        btn_calc.click(fn=calculate_and_save, inputs=[ch_dropdown, r1, m1, r2, m2, invert_checkbox], outputs=[calc_status])

    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    print("UI 已启动在 0.0.0.0:8081 ...")
    ui.launch(server_name="0.0.0.0", server_port=8081, show_error=True)
