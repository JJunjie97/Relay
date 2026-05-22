from typing import List, Dict
from logic.FPGACodec import HWCodec, HWConfig
from logic.FlatCodec import TrigCond, FlatCodec
from logic.USEEngine import USENode


# 触发器条件字符串 → TrigCond 映射
_TRIG_COND_MAP = {
    "DI_MATCH": TrigCond.ConDiMatch,
    "TIMEOUT": TrigCond.ConTimeout,
    "COUNT_OVER": TrigCond.ConCountOver,
}


class NodeCompiler:

    @staticmethod
    def compile(raw: dict) -> USENode:
        node = USENode(mode=NodeCompiler._inferMode(raw))

        dyn = raw.get("dynamic", {})
        node.interval = dyn.get("interval")
        node.resetTime = raw.get("resetTime")
        node.resetDo = raw.get("resetDo")
        node.doActions = raw.get("doActions")

        static = raw.get("static")
        if static:
            node.baseFrame = HWCodec.CompileDdsWords(NodeCompiler._jsonToWords(static))

        resetStatic = raw.get("resetStatic")
        if resetStatic:
            node.resetFrame = HWCodec.CompileDdsWords(NodeCompiler._jsonToWords(resetStatic))

        steps = raw.get("steps")
        if steps:
            compiled = [HWCodec.CompileStepWords(NodeCompiler._jsonToWords(s)) for s in steps]
            count = dyn.get("count", len(compiled))
            # 用已有 steps 循环填充到 count 长度
            if count > len(compiled) and compiled:
                full = []
                for i in range(count):
                    full.append(compiled[i % len(compiled)])
                compiled = full
            node.stepFrames = compiled

        phaseGate = raw.get("phaseGate")
        if phaseGate and steps:
            node.gateFrames = [HWCodec.CompilePhaseGate(phaseGate) for _ in steps]

        for trig in raw.get("triggers", []):
            cond_str = trig.get("condition", "")
            nextId = trig.get("nextId", 0xFFFF)
            data = trig.get("data", 0)
            cond = _TRIG_COND_MAP.get(cond_str)
            if cond is None:
                continue
            if cond == TrigCond.ConDiMatch:
                node.diMatchMask = data
                node.diMatchId = nextId
            elif cond == TrigCond.ConTimeout:
                node.timeoutMs = data
                node.timeoutId = nextId
            elif cond == TrigCond.ConCountOver:
                node.countOverId = nextId

        return node

    @staticmethod
    def compileAll(rawNodes: List[dict]) -> Dict[int, USENode]:
        return {r["id"]: NodeCompiler.compile(r) for r in rawNodes}

    @staticmethod
    def _jsonToWords(d: dict) -> list:
        """JSON → packed int list.
        Supports two formats:
          static: {ch: {layer: [amp, phase/freq]}}
          step:   {ch: [damp, dphase/dfreq]}  (layer defaults to 1)
        """
        words = []
        for ch_str, val in d.items():
            api_ch = int(ch_str)
            phys_ch = HWConfig.MapChannel(api_ch)
            isCurrent = phys_ch in HWConfig.I_CHANNELS
            if isinstance(val, dict):
                # static format: {layer: [amp, phase/freq]}
                for layer_str, pair in val.items():
                    layer = int(layer_str)
                    amp = HWConfig.ConvertAmpToReg(pair[0], isCurrent)
                    pf = HWConfig.ConvertPhaseToReg(pair[1]) if layer == 0 else HWConfig.ConvertFreqToReg(pair[1])
                    words.append(FlatCodec.PackDdsWord(phys_ch, layer, amp, pf))
            elif isinstance(val, list):
                # step format: [damp, dfreq], layer=1
                amp = HWConfig.ConvertAmpToReg(val[0], isCurrent)
                pf = HWConfig.ConvertFreqToReg(val[1])
                words.append(FlatCodec.PackDdsWord(phys_ch, 1, amp, pf))
        return words

    @staticmethod
    def _inferMode(raw: dict) -> int:
        if raw.get("phaseGate"):
            return 4  # DcComp
        if raw.get("resetTime"):
            return 3  # Reset
        if raw.get("steps"):
            return 2  # Sweep
        return 1      # Static
