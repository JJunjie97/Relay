import asyncio
import os
import sys
import pickle
from typing import Any
from logic.USEEngine import USENode
from logic.FPGACodec import HWCodec
from utils.SysLogger import GetLogger

logger = GetLogger("EngineProxy")


class EngineProxy:
    def __init__(self, hwGateway=None, emitEvent=None):
        self._emit = emitEvent
        
        # 影子 nodes，以支持主进程对 Node 0x0000 进行零漂预校准等配置改写
        self.nodes = {
            0x0000: USENode(mode=1, baseFrame=[HWCodec.FRAME_SYS_RESET, HWCodec.BuildSystemFrame(HWCodec.SYS_SET_DBNC, 61), HWCodec.FRAME_SYS_START]),
            0xFFFF: USENode(mode=1, baseFrame=[HWCodec.FRAME_SYS_RESET])
        }
        self.nodeId = 0xFFFF
        self.errorReason = None
        
        self.proc = None
        self.reader = None
        self.writer = None
        self._recv_task = None
        self._connected = asyncio.Event()

    def start(self):
        """与原版 USEEngine 兼容的启动方法"""
        asyncio.create_task(self._start_backend())

    async def coreLoop(self):
        """与原版 USEEngine 兼容的 coreLoop，供测试用（如果测试中直接调用）"""
        while True:
            await asyncio.sleep(1)

    async def _start_backend(self):
        python_bin = sys.executable
        # 子进程 HwEngineProcess.py 位于 RelayProtection 目录下
        proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        hw_proc_path = os.path.join(proj_root, "HwEngineProcess.py")
        
        logger.info(f"Launching HwEngineProcess at {hw_proc_path} with working directory {proj_root}")
        
        env = os.environ.copy()
        # 确保子进程能正确导入 logic 目录下的模块
        env["PYTHONPATH"] = proj_root + os.pathsep + env.get("PYTHONPATH", "")
        
        try:
            self.proc = await asyncio.create_subprocess_exec(
                python_bin, hw_proc_path,
                env=env,
                cwd=proj_root,
                stdout=None,  # 继承父进程输出，便于日志查看
                stderr=None
            )
        except Exception as e:
            logger.error(f"Failed to start HwEngineProcess: {e}")
            self.errorReason = f"Subprocess start failed: {e}"
            return

        # 尝试连接本地 TCP 8081 服务端
        retries = 50
        connected = False
        while retries > 0:
            try:
                self.reader, self.writer = await asyncio.open_connection('127.0.0.1', 8081)
                connected = True
                break
            except ConnectionRefusedError:
                await asyncio.sleep(0.1)
                retries -= 1
        
        if not connected:
            logger.error("Failed to connect to HwEngineProcess IPC server on port 8081.")
            self.errorReason = "IPC Connection failed"
            return
            
        logger.info("Connected to HwEngineProcess IPC server.")
        self._connected.set()

        # 将本地在 TestCtrl._preloadZeroCalibration 中预配置好的 nodes 同步发送给子进程
        await self._send_cmd("upsert_nodes", self.nodes)
        
        # 启动接收循环
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def _send_cmd(self, cmd: str, args: Any):
        if not self.writer:
            try:
                await asyncio.wait_for(self._connected.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                logger.error(f"Timeout waiting for IPC connection while sending '{cmd}'")
                raise ConnectionError("IPC backend process is currently unavailable")
        try:
            data = pickle.dumps((cmd, args))
            header = len(data).to_bytes(4, 'big')
            self.writer.write(header + data)
            await self.writer.drain()
        except Exception as e:
            logger.error(f"Failed to send IPC command '{cmd}': {e}")
            self.errorReason = f"IPC send error: {e}"
            self.reader = None
            self.writer = None
            self._connected.clear()
            raise ConnectionError(f"IPC connection lost during send: {e}")

    def setDebounce(self, dbnc: int):
        self.nodes[0x0000].baseFrame[1] = HWCodec.BuildSystemFrame(HWCodec.SYS_SET_DBNC, dbnc)
        asyncio.create_task(self._send_cmd("set_debounce", dbnc))

    def manualTrig(self, targetId: int):
        self.nodeId = targetId  # 主进程同步更新影子 nodeId
        asyncio.create_task(self._send_cmd("manual_trig", targetId))

    def upsertNodes(self, newNodes: dict):
        self.nodes.update(newNodes)
        asyncio.create_task(self._send_cmd("upsert_nodes", newNodes))

    async def _recv_loop(self):
        try:
            while True:
                len_bytes = await self.reader.readexactly(4)
                length = int.from_bytes(len_bytes, 'big')
                payload_bytes = await self.reader.readexactly(length)
                
                try:
                    msg_type, val = pickle.loads(payload_bytes)
                except Exception as pe:
                    logger.error(f"Corrupted IPC payload received: {pe}")
                    continue

                if msg_type == "on_event":
                    # val 为 event 数据: [code, nodeId/diMask, tick/timestamp, timestamp]
                    if val[0] == 0:
                        # 影子更新当前 nodeId
                        self.nodeId = val[1]
                    if self._emit:
                        # 在事件循环中异步分发给 TestCtrl.onEvent
                        asyncio.create_task(self._emit(val))
                elif msg_type == "error":
                    logger.error(f"Backend engine reported error: {val}")
                    self.errorReason = val
        except asyncio.IncompleteReadError:
            logger.warning("HwEngineProcess IPC connection closed by remote.")
        except Exception as e:
            logger.error(f"Error in IPC receive loop: {e}")
            self.errorReason = f"IPC receive error: {e}"
        finally:
            self._connected.clear()
            if self.writer:
                self.writer.close()
                try:
                    await self.writer.wait_closed()
                except Exception:
                    pass
