"""
ApiManualTest — Custom API for interactive manual testing and FPGA instruction validation.

Enables advanced real-time local emulation of FPGA's three-tier buffer system:
- Base: Bottom-tier persistent register storage.
- Shadow: Ready-to-commit staging pipeline.
- Active: Current real-time physical DAC output buffer.

Perfectly tracks standard and custom step overflows, unsigned ring phase calculations, and saturation rules.
Broadcasts full Base/Shadow/Active state grids to manual_test client in real-time.
"""

import logging
import asyncio
from typing import Dict, Any, List
from api.BaseApi import BaseApi, ApiNodeData
from logic.FPGACodec import HWCodec, HWConfig
from logic.Calibration import calib

logger = logging.getLogger("ApiManualTest")


class ApiManualTest(BaseApi):
    MODULE_KEY = "manual_test"

    def _onSetup(self, params: Dict[str, Any]):
        logger.info("Setting up manual test baseline states & emulated FPGA buffers...")
        
        # Initialize 3-tier FPGA emulated registers
        # key = hw_ch (0-15), val = Dict[hardware_layer (255, 0..63), [p1, p2]]
        self._sim_reset()
        
        # Zero calibration and system reset/start is preloaded in TestCtrl._preloadZeroCalibration.
        
        logger.info("Manual test baseline preloaded. Triggering initial buffer sync...")
        # Dispatch initial buffers to front-end asynchronously to ensure WS client is ready
        asyncio.create_task(self._delayed_sync())

    async def _delayed_sync(self):
        await asyncio.sleep(0.3)
        self._syncSimBuffers()

    def _onStop(self):
        logger.info("Manual test mode stopped.")

    def onWebCommand(self, msg: Dict[str, Any]):
        cmd = msg.get("cmd")
        if cmd == "sys_command":
            self._handleSysCommand(msg)
        elif cmd == "param_command":
            self._handleParamCommand(msg)
        elif cmd == "phase_gate":
            self._handlePhaseGate(msg)
        elif cmd == "pull_buffers":
            # Direct client request to sync buffers
            self._syncSimBuffers()

    def _handleSysCommand(self, msg: Dict[str, Any]):
        code = msg.get("code")
        p_u8 = msg.get("p_u8", 0)
        
        if code is None:
            logger.error("Missing command code in sys_command")
            return
            
        try:
            frame = HWCodec.BuildSystemFrame(code, p_u8)
            logger.info(f"Sending SYS command: code=0x{code:02X}, p_u8=0x{p_u8:02X}")
            self.ctrl.engine.flushNoAck([frame])
            
            # Local FPGA emulation transition
            if code == HWCodec.SYS_START:
                self.output_enabled = True
                self._syncSimBuffers()
                logger.info("Local FPGA Emulation: SYS_START processed (Output Enabled)")
            elif code == HWCodec.SYS_STOP:
                self.output_enabled = False
                self._syncSimBuffers()
                logger.info("Local FPGA Emulation: SYS_STOP processed (Output Disabled/Zeroed)")
            elif code == HWCodec.SYS_UPDATE:
                self._sim_update()
                self._syncSimBuffers()
                logger.info("Local FPGA Emulation: SYS_UPDATE processed (Shadow -> Active)")
            elif code == HWCodec.SYS_SYNC:
                self._sim_sync()
                self._syncSimBuffers()
                logger.info("Local FPGA Emulation: SYS_SYNC processed (Active -> Base + Shadow)")
            elif code == HWCodec.SYS_RESET:
                self._sim_reset()
                self._syncSimBuffers()
                logger.info("Local FPGA Emulation: SYS_RESET processed (All buffers reset & Output Disabled)")
                
        except Exception as e:
            logger.error(f"Failed to transmit SYS command: {e}")

    def _handleParamCommand(self, msg: Dict[str, Any]):
        code = msg.get("code")
        layer = msg.get("layer", 0) # Hardware layer: 255 (DC), 0..63 (AC harmonics)
        is_raw = msg.get("is_raw", False)
        
        if code is None:
            logger.error("Missing command code in param_command")
            return
            
        try:
            if is_raw:
                ch_mask = msg.get("ch_mask", 0)
                p1 = msg.get("p1", 0) & 0xFFFFFFFF
                p2 = msg.get("p2", 0) & 0xFFFFFFFF
                
                frame = HWCodec.BuildParamFrame(code, layer, ch_mask, p1, p2)
                logger.info(f"Sending RAW PARAM command: code=0x{code:02X}, layer=0x{layer:02X}, ch_mask=0x{ch_mask:04X}, p1=0x{p1:08X}, p2=0x{p2:08X}")
                self.ctrl.engine.flushNoAck([frame])
                
                # Perform local simulation write/step
                self._update_sim_buffers(code, layer, ch_mask, p1, p2)
                self._syncSimBuffers()
            else:
                phys_data = msg.get("phys", {})
                if not phys_data:
                    logger.warning("No physical channel data in param_command")
                    return
                
                # AC/DC calibration model: API layer 0 is DC (hardware index 255), layer 1..64 are AC
                api_layer = 0 if layer == 255 else (layer + 1)
                
                phys_dict = {}
                for ch_str, vals in phys_data.items():
                    phys_dict[ch_str] = {str(api_layer): [float(vals[0]), float(vals[1])]}
                
                # Check if it is a delta-step command
                is_delta = code in [
                    HWCodec.DDS_STEP_SHADOW, HWCodec.DDS_STEP_STAGE
                ]
                
                reg_dict = self.physDictToReg(phys_dict, is_delta=is_delta)
                
                # Flatten reg_dict (hw_ch -> [a_reg, p_reg]) for our local emulation matching
                phys_updates = {}
                for hw_ch, api_layers in reg_dict.items():
                    for api_l, regs in api_layers.items():
                        phys_updates[hw_ch] = regs
                
                frames = self.ctrl._compileDictToFrames(reg_dict, code)
                logger.info(f"Sending PHYSICAL PARAM command: code=0x{code:02X}, layer=0x{layer:02X}, compiled {len(frames)} frames")
                
                if frames:
                    self.ctrl.engine.flushNoAck(frames)
                    
                    # Update local simulation via individual registers calculated by calibration
                    self._update_sim_buffers(code, layer, 0, 0, 0, phys_updates=phys_updates)
                    self._syncSimBuffers()
                    
        except Exception as e:
            logger.error(f"Failed to transmit PARAM command: {e}")

    def _handlePhaseGate(self, msg: Dict[str, Any]):
        physical_ch = msg.get("ch")
        phase = msg.get("phase", 0)
        
        if physical_ch is None:
            logger.error("Missing channel in phase_gate command")
            return
            
        try:
            if not msg.get("is_raw", False):
                phase = HWConfig.ConvertPhaseToReg(float(phase)) & 0xFFFFFFFF
            
            frame = HWCodec.BuildPhaseGateFrame(physical_ch, phase)
            logger.info(f"Sending Phase Gate: channel={physical_ch}, phase=0x{phase:08X}")
            self.ctrl.engine.flushNoAck([frame])
        except Exception as e:
            logger.error(f"Failed to transmit Phase Gate: {e}")

    # =========================================================================
    # FPGA Emulation & Buffer Sync Methods
    # =========================================================================

    def _stringify_keys(self, d: dict) -> dict:
        """Convert all integer keys in nested emulated registers dict to strings for orjson compatibility."""
        return {
            str(ch): {str(layer): val for layer, val in layers.items()}
            for ch, layers in d.items()
        }

    def _syncSimBuffers(self):
        """Send complete three-tier simulated register state matrix to active client."""
        self.ctrl._send({
            "type": "sim_buffers",
            "base": self._stringify_keys(self.sim_base),
            "shadow": self._stringify_keys(self.sim_shadow),
            "active": self._stringify_keys(self.sim_active),
            "factors": calib._factors,
            "biases": calib._biases,
            "output_enabled": self.output_enabled
        })

    def _sim_reset(self):
        """Global hardware hard reset baseline (all registers 0, preloads zero-calibration V/I static state)."""
        self.output_enabled = False
        self.sim_base = {ch: {slot: [0, 0] for slot in range(64)} for ch in range(16)}
        self.sim_shadow = {ch: {slot: [0, 0] for slot in range(64)} for ch in range(16)}
        self.sim_active = {ch: {slot: [0, 0] for slot in range(64)} for ch in range(16)}
        
        # Reset matching hardware zero calibration preloads
        for ch_idx in range(12):
            hw_ch = HWConfig.MapChannel(ch_idx)
            dc_amp_reg, freq_reg = calib.PhysToReg(ch_idx, 0, 0.0, 50.0)
            self.sim_base[hw_ch][0] = [dc_amp_reg, freq_reg]
            self.sim_shadow[hw_ch][0] = [dc_amp_reg, freq_reg]
            self.sim_active[hw_ch][0] = [dc_amp_reg, freq_reg]

    def _sim_update(self):
        """Commit shadow registers into real-time DAC output active buffer (Ping-Pong buffer swap)."""
        for ch in range(16):
            self.sim_active[ch], self.sim_shadow[ch] = self.sim_shadow[ch], self.sim_active[ch]

    def _sim_sync(self):
        """Active commits back to both shadow and bottom-tier base registers."""
        for ch in range(16):
            self.sim_base[ch] = {slot: list(val) for slot, val in self.sim_active[ch].items()}
            self.sim_shadow[ch] = {slot: list(val) for slot, val in self.sim_active[ch].items()}

    def _calculate_step(self, base_p1: int, base_p2: int, step_p1: int, step_p2: int, sim_layer: int):
        """
        Emulate low-level FPGA arithmetic logic unit (ALU):
        - p1 (Amplitude, U16 raw upper bits):
          - DC (sim_layer 0): Signed I16 saturation addition (-32768 to 32767).
          - AC (sim_layer 1-63): Unsigned U16 ring phase addition (wraps around).
        - p2 (Freq/Phase, U32):
          - DC (sim_layer 0): Frequency. U32 saturation addition.
          - AC (sim_layer 1-63): Phase. U32 ring phase accumulation (wraps 0xFFFFFFFF).
        """
        b_p1_high = (base_p1 >> 16) & 0xFFFF
        s_p1_high = (step_p1 >> 16) & 0xFFFF
        
        b_p2 = base_p2 & 0xFFFFFFFF
        s_p2 = step_p2 & 0xFFFFFFFF
        
        if sim_layer == 0:
            # --- DC Layer: Saturation Addition ---
            # Cast p1 high 16-bit register to signed I16
            b_p1_signed = b_p1_high if b_p1_high < 32768 else b_p1_high - 65536
            s_p1_signed = s_p1_high if s_p1_high < 32768 else s_p1_high - 65536
            
            res_p1_signed = b_p1_signed + s_p1_signed
            if res_p1_signed > 32767:
                res_p1_signed = 32767
            elif res_p1_signed < -32768:
                res_p1_signed = -32768
            res_p1_high = res_p1_signed & 0xFFFF
            
            # Frequency (p2) saturation (0xFFFFFFFF max)
            res_p2 = b_p2 + s_p2
            if res_p2 > 0xFFFFFFFF:
                res_p2 = 0xFFFFFFFF
        else:
            # --- AC Harmonics: Unsigned Roll-over ---
            # Amplitude (U16 ring overflow)
            res_p1_high = (b_p1_high + s_p1_high) & 0xFFFF
            
            # Phase (U32 phase accumulator overflow)
            res_p2 = (b_p2 + s_p2) & 0xFFFFFFFF
            
        res_p1 = (res_p1_high << 16) & 0xFFFFFFFF
        return res_p1, res_p2

    def _update_sim_buffers(self, code: int, layer: int, ch_mask: int, p1: int, p2: int, phys_updates: dict = None):
        """
        Emulate register updates across 3-tier buffers triggered by PARAM write and step instructions.
        """
        # Resolve target channel indexes
        target_channels = []
        if phys_updates:
            target_channels = list(phys_updates.keys())
        else:
            for ch in range(16):
                if (ch_mask & (1 << ch)):
                    target_channels.append(ch)

        # Resolve physical slot index (0..63)
        sim_layer = 0 if layer == 255 else (layer + 1)

        for ch in target_channels:
            # Extract inputs (either physical compiled regs, or raw registers)
            val_p1 = phys_updates[ch][0] if phys_updates else p1
            val_p2 = phys_updates[ch][1] if phys_updates else p2

            # Acquire current channel register state
            b_val = self.sim_base[ch].get(sim_layer, [0, 0])
            s_val = self.sim_shadow[ch].get(sim_layer, [0, 0])

            if code == HWCodec.DDS_WR_SHADOW:
                self.sim_base[ch][sim_layer] = [val_p1, val_p2]
                self.sim_shadow[ch][sim_layer] = [val_p1, val_p2]
            elif code == HWCodec.DDS_WR_STAGE:
                self.sim_shadow[ch][sim_layer] = [val_p1, val_p2]
            elif code == HWCodec.DDS_STEP_SHADOW:
                res_p1, res_p2 = self._calculate_step(b_val[0], b_val[1], val_p1, val_p2, sim_layer)
                self.sim_base[ch][sim_layer] = [res_p1, res_p2]
                self.sim_shadow[ch][sim_layer] = [res_p1, res_p2]
            elif code == HWCodec.DDS_STEP_STAGE:
                res_p1, res_p2 = self._calculate_step(s_val[0], s_val[1], val_p1, val_p2, sim_layer)
                self.sim_shadow[ch][sim_layer] = [res_p1, res_p2]
