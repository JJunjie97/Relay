# 树莓派5 初始配置 (Waveshare 7.0 DSI, 串口 & Python环境)

---

## 1. 配置 `/boot/firmware/config.txt`

编辑系统硬件配置文件：
```bash
sudo nano /boot/firmware/config.txt
```
在文件末尾添加以下内容（配置 Waveshare 7.0寸 H型屏 DSI0 接口 & 启用串口）：
```ini
# 适配 Waveshare 7.0寸 H型屏幕
dtoverlay=vc4-kms-v3d
dtoverlay=vc4-kms-dsi-waveshare-panel,7_0_inchH,dsi0
# 启用硬件串口
dtparam=uart0=on
```
*(注意：请确保注释掉其他屏幕 dtoverlay 配置)*

---

## 2. 关闭串口控制台占用 (防止干扰 FPGA)

编辑终端启动参数文件：
```bash
sudo nano /boot/firmware/cmdline.txt
```
**删除** 文件中类似 `console=serial0,115200` 或 `console=ttyAMA0,115200` 的字段。

---

## 3. Python 虚拟环境与依赖配置

在项目根目录 `Relay` 下直接运行：
```bash
# 1. 安装编译/运行期系统依赖 (防止 lgpio 等包安装失败)
sudo apt-get update && sudo apt-get install -y swig liblgpio-dev

# 2. 创建并配置虚拟环境
python3 -m venv .venv
source .venv/bin/activate
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
pip install -r requirements.txt
```

---

## 4. 重启生效
```bash
sudo reboot
```


