"""
ApiRawTest — Minimal passthrough API for direct engine testing.

Accepts raw register-level ApiNodeData via params["nodes"], bypasses all
physical-to-register conversion. Used to isolate and verify USEEngine's
4 waveform modes independently of higher-level API logic.
"""

from api.BaseApi import BaseApi, ApiNodeData


class ApiRawTest(BaseApi):
    MODULE_KEY = "raw_test"

    def _onSetup(self, params):
        nodes = {}
        for nodeId_str, nodeDict in params.get("nodes", {}).items():
            nodeId = int(nodeId_str)
            node = ApiNodeData(mode=nodeDict["mode"])

            # Direct register-level assignment — no calibration, no conversion
            node.base       = nodeDict.get("base")        # {hw_ch: {layer: [amp_reg, phase_reg]}}
            node.reset      = nodeDict.get("reset")
            node.steps      = nodeDict.get("steps")       # [step_dict, ...]
            node.gate       = nodeDict.get("gate")
            node.interval   = nodeDict.get("interval")
            node.resetTime  = nodeDict.get("resetTime")
            node.resetDo    = nodeDict.get("resetDo")
            node.doActions  = nodeDict.get("doActions")
            node.countOverId  = nodeDict.get("countOverId")
            node.diMatchMask  = nodeDict.get("diMatchMask")
            node.diMatchId    = nodeDict.get("diMatchId")
            node.timeoutMs    = nodeDict.get("timeoutMs")
            node.timeoutId    = nodeDict.get("timeoutId")

            nodes[nodeId] = node

        self.ctrl.upsertNodes(nodes)

        # Store for external triggering after engine settles
        self._startNode = params.get("startNode", None)

    def _onStop(self):
        pass
