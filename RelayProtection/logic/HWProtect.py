import asyncio
import time
from utils.SysLogger import GetLogger
from logic.FPGACodec import HWConfig
from gpiozero import DigitalOutputDevice, DigitalInputDevice

logger = GetLogger("HWProtect")

DISABLE_GPIO_HARDWARE = True
GPIO_AMP_ENABLE = 26
GPIO_HW_FAULT = 20

MAX_VIRTUAL_TEMP = 100.0
COEF_HEATING = 0.008 * (60.0 / 32767.0)**2
COEF_COOLING = 0.008

class HWProtect:
    def __init__(self):
        self.testCtrl = None
        self.pinAmpEnable = None
        self.ampEnabled = False
        self.monitorTask = asyncio.create_task(self._MonitorLoop())
        
        if not DISABLE_GPIO_HARDWARE:
            try:
                self.pinAmpEnable = DigitalOutputDevice(GPIO_AMP_ENABLE, active_high=False, initial_value=False)
                self.pinHwFault = DigitalInputDevice(GPIO_HW_FAULT, pull_up=True)
                self.pinHwFault.when_activated = lambda: self._TriggerProtection("HARDWARE_OVERHEAT_PROTECTION (GPIO)")
            except Exception as e:
                logger.warning(f"Failed to initialize GPIO: {e}")
        else:
            logger.warning("Hardware GPIO protection is BYPASSED (DISABLE_GPIO_HARDWARE = True).")

    def SetAmplifierEnable(self, active: bool):
        self.ampEnabled = active
        if self.pinAmpEnable:
            if active:
                self.pinAmpEnable.on()
                logger.info("Amplifier Enabled")
            else:
                self.pinAmpEnable.off()
                logger.info("Amplifier Disabled")

    def _TriggerProtection(self, reason: str):
        self.SetAmplifierEnable(False)
        logger.error(reason)
        if self.testCtrl:
            self.testCtrl.stopTest(reason)


    async def _MonitorLoop(self):
        I_CH = HWConfig.I_CHANNELS
        temps = {ch: 0.0 for ch in I_CH}
        monotonic = time.monotonic
        
        while not self.testCtrl:
            await asyncio.sleep(0.1)
        ctrl = self.testCtrl

        lastTs = monotonic()
        while False:
            await asyncio.sleep(0.2)
            now = monotonic()
            dt = now - lastTs
            lastTs = now
            coolMult = 1.0 - (COEF_COOLING * dt)
            maxT = 0.0

            if self.ampEnabled:
                state = ctrl.state
                heatFactor = COEF_HEATING * dt
                if state:
                    for ch in I_CH:
                        layers = state.get(ch)
                        power = 0.0
                        if layers:
                            for layer, vals in layers.items():
                                v = vals[0] >> 16
                                if v >= 32768:
                                    v -= 65536
                                power += v * v if layer == 0 else v * v * 0.5
                        T = temps[ch] * coolMult + power * heatFactor
                        temps[ch] = T
                        if T > maxT: maxT = T
                if maxT >= MAX_VIRTUAL_TEMP:
                    self._TriggerProtection("SOFTWARE_OVERHEAT_PROTECTION")
            else:
                for ch in I_CH:
                    T = temps[ch] * coolMult
                    temps[ch] = T
                    if T > maxT: maxT = T

            self.testCtrl.sendLoad(round(maxT, 2), self.ampEnabled)
