#!/bin/bash

# 检查是否以 root 权限运行
if [ "$EUID" -ne 0 ]; then
  echo "请使用 sudo 运行此脚本: sudo bash setup_pi5_config.sh"
  exit 1
fi

CONFIG_FILE="/boot/firmware/config.txt"

# 确保文件存在
if [ ! -f "$CONFIG_FILE" ]; then
    echo "未找到 $CONFIG_FILE，这不是标准的 Raspberry Pi OS Bookworm 环境。"
    exit 1
fi

# 备份原始配置
if [ ! -f "${CONFIG_FILE}.bak" ]; then
    cp "$CONFIG_FILE" "${CONFIG_FILE}.bak"
    echo "已备份原始配置文件到 ${CONFIG_FILE}.bak"
fi

echo "开始配置 DSI0 屏幕和串口..."

# 1. 确保开启图形加速
if ! grep -q "^dtoverlay=vc4-kms-v3d" "$CONFIG_FILE"; then
    echo "dtoverlay=vc4-kms-v3d" >> "$CONFIG_FILE"
fi

# 2. 添加 Waveshare 7.0 DSI0 屏幕配置
if ! grep -q "dtoverlay=vc4-kms-dsi-waveshare-panel,7_0_inchH,dsi0" "$CONFIG_FILE"; then
    echo "" >> "$CONFIG_FILE"
    echo "# 配置 Waveshare 7.0 inch H 屏幕使用 DSI0 接口" >> "$CONFIG_FILE"
    echo "dtoverlay=vc4-kms-dsi-waveshare-panel,7_0_inchH,dsi0" >> "$CONFIG_FILE"
    echo "=> 已添加屏幕配置。"
else
    echo "=> 屏幕配置已存在，跳过。"
fi

# 3. 添加串口配置 dtparam=uart0=on
if ! grep -q "^dtparam=uart0=on" "$CONFIG_FILE"; then
    # 如果有 enable_uart=1，将其注释掉，避免潜在的重复或冲突
    sed -i 's/^enable_uart=1/#enable_uart=1/' "$CONFIG_FILE"
    
    echo "" >> "$CONFIG_FILE"
    echo "# 启用硬件串口 uart0" >> "$CONFIG_FILE"
    echo "dtparam=uart0=on" >> "$CONFIG_FILE"
    echo "=> 已添加串口配置 dtparam=uart0=on。"
else
    echo "=> 串口配置 dtparam=uart0=on 已存在，跳过。"
fi

# 4. 关闭串口控制台 (释放给 FPGA 专用)
CMDLINE_FILE="/boot/firmware/cmdline.txt"
if [ -f "$CMDLINE_FILE" ]; then
    if grep -qE "console=(serial0|ttyAMA0),[0-9]+" "$CMDLINE_FILE"; then
        if [ ! -f "${CMDLINE_FILE}.bak" ]; then
            cp "$CMDLINE_FILE" "${CMDLINE_FILE}.bak"
            echo "已备份 ${CMDLINE_FILE} 到 ${CMDLINE_FILE}.bak"
        fi
        # 删除相关的 console 配置
        sed -i -E 's/console=(serial0|ttyAMA0),[0-9]+ *//g' "$CMDLINE_FILE"
        echo "=> 已从 cmdline.txt 中移除串口控制台输出，确保串口被纯粹释放给 FPGA。"
    else
        echo "=> 串口控制台输出已关闭，无需修改 cmdline.txt。"
    fi
fi

echo ""
echo "========================================="
echo "配置完成！"
echo "请运行以下命令重启树莓派使配置生效："
echo "sudo reboot"
echo "========================================="
