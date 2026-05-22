# 树莓派 5 专属 xc3sprog 编译与使用指南

为了让树莓派 5 (Debian Trixie) 能够正确通过物理 GPIO 的 JTAG 接口下载 `.bit` 字节流文件到 FPGA 芯片，我们对原版 `xc3sprog` 进行源码级别的定制修改并编译。

本目录下已存放了编译好的树莓派 5 专属可执行二进制文件：`FPGADownload/xc3sprog`。
本文档将详细阐述：
1. **如何从原版 `xc3sprog` 源码修改并编译出该文件**
2. **如何直接使用该预编译好的可执行文件进行固件下载与测试**

---

## 一、 如何从原始项目修改并编译出此文件

### 1.1 安装编译依赖环境
在树莓派终端执行以下命令，安装构建所需的编译工具及依赖库：
```bash
sudo apt-get update
sudo apt-get install build-essential cmake libusb-dev libftdi-dev git -y
```

### 1.2 获取官方源码
```bash
git clone https://github.com/matrix-io/xc3sprog.git
cd xc3sprog
```

### 1.3 核心源码修改与适配
树莓派 5 采用了全新的 **RP1 I/O 控制芯片**，传统的 GPIO 基础偏移量不再为 `0`，而是变成了动态偏移值（如 `571` 或 `569`）。我们必须实现动态检测逻辑并挂载自定义电缆。

请对克隆下来的源码作以下五处修改：

#### 📂 1. 新增：`sysfsrpi.h`
在 `xc3sprog/` 目录下创建文件 `sysfsrpi.h`，内容如下：
```cpp
#ifndef __IO_SYSFS_RPI__
#define __IO_SYSFS_RPI__

#include "sysfs.h"

class IOSysFsRPi : public IOSysFsGPIO
{
 public:
  IOSysFsRPi();
};

#endif
```

#### 📂 2. 新增：`sysfsrpi.cpp`
在 `xc3sprog/` 目录下创建文件 `sysfsrpi.cpp`，内容如下：
```cpp
#include "sysfsrpi.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>

// 动态获取树莓派 5 的 RP1 GPIO 基础偏移量
static int get_rp1_gpio_base() {
    DIR *dir = opendir("/sys/class/gpio");
    if (!dir) return 571; // 默认回退基准
    
    struct dirent *ent;
    int base = 571;
    while ((ent = readdir(dir)) != NULL) {
        if (strncmp(ent->d_name, "gpiochip", 8) == 0) {
            char path[256];
            snprintf(path, sizeof(path), "/sys/class/gpio/%s/label", ent->d_name);
            FILE *f = fopen(path, "r");
            if (f) {
                char label[256];
                if (fgets(label, sizeof(label), f)) {
                    if (strstr(label, "pinctrl-rp1") != NULL) {
                        base = atoi(ent->d_name + 8); // 解析实际基础地址
                        fclose(f);
                        break;
                    }
                }
                fclose(f);
            }
        }
    }
    closedir(dir);
    return base;
}

// 物理 JTAG 管脚映射: TMS=GPIO 24, TCK=GPIO 25, TDI=GPIO 23, TDO=GPIO 22
IOSysFsRPi::IOSysFsRPi()
 : IOSysFsGPIO(get_rp1_gpio_base() + 24, 
               get_rp1_gpio_base() + 25, 
               get_rp1_gpio_base() + 23, 
               get_rp1_gpio_base() + 22)
{}
```

#### 📂 3. 修改：`CMakeLists.txt`
修改第 140 行附近的 `add_library(xc3sproglib ...)`，将 `sysfsrpi.cpp` 加入构建：
```diff
-add_library(xc3sproglib  STATIC sysfs.cpp sysfscreator.cpp sysfsvoice.cpp ioftdi.cpp 
+add_library(xc3sproglib  STATIC sysfs.cpp sysfscreator.cpp sysfsvoice.cpp sysfsrpi.cpp ioftdi.cpp 
```

#### 📂 4. 修改：`cabledb.h` 与 `cabledb.cpp`
- **`cabledb.h`**: 在 `CABLES_TYPES` 枚举体尾部加入新缆线枚举：
  ```cpp
  CABLE_SYSFS_GPIO_RPI,
  ```
- **`cabledb.cpp`**: 
  - 在 `CableDB::getCableType` 加入转换映射：
    ```cpp
    if (strcasecmp(given_name, "sysfsgpio_rpi") == 0)
      return CABLE_SYSFS_GPIO_RPI;
    ```
  - 在 `CableDB::getCableName` 中加入类型名称转换：
    ```cpp
    case CABLE_SYSFS_GPIO_RPI: return "sysfsgpio_rpi";
    ```

#### 📂 5. 修改：`cablelist.txt`
在文件最后一行追加注册信息：
```text
sysfsgpio_rpi  sysfsgpio_rpi 0     NULL
```

#### 📂 6. 修改：`utilities.cpp`
- 在头部引入新头文件：
  ```cpp
  #include "sysfsrpi.h"
  ```
- 在 `getIO` 函数里加入对 `CABLE_SYSFS_GPIO_RPI` 的实例化路由：
  ```cpp
  else if(cable->cabletype == CABLE_SYSFS_GPIO_RPI)  
  {
      io->reset(new IOSysFsRPi());
      io->get()->setVerbose(verbose);
      res = io->get()->Init(cable, serial, use_freq);
  }
  ```
- 在 `getCableName` 中追加其分支返回：
  ```cpp
  case CABLE_SYSFS_GPIO_RPI: return "sysfsgpio_rpi"; break;
  ```

---

### 1.4 编译项目
修改完成后，在 `xc3sprog/` 源码目录下执行编译：
```bash
mkdir build
cd build
cmake ..
make -j4
```
编译成功后，在 `build/` 目录下将直接生成可执行的二进制文件 `xc3sprog`。
您只需将其拷贝至 `FPGADownload/` 目录下：
```bash
cp xc3sprog /home/pi/Desktop/Relay/FPGADownload/xc3sprog
```

---

## 二、 如何使用此目录下编译好的文件

本目录 (`FPGADownload/`) 下的可执行文件 `xc3sprog` 是已为您编译好且可以直接执行的。您无需再次编译，按以下步骤配置并运行：

### 2.1 安装运行依赖 (解决 libftdi.so.1 缺失问题)
若在运行 `./xc3sprog` 时提示 `error while loading shared libraries: libftdi.so.1` 错误，请先执行：
```bash
sudo apt-get update && sudo apt-get install -y libftdi-dev
```

### 2.2 赋予执行权限
在使用前，请确保文件拥有可执行权限：
```bash
chmod +x /home/pi/Desktop/Relay/FPGADownload/xc3sprog
```

### 2.3 检测 JTAG 链路状态
确认树莓派物理 GPIO JTAG 与 FPGA 连线正确并共地，在 `/home/pi/Desktop/Relay/FPGADownload` 目录下执行：
```bash
sudo ./xc3sprog -c sysfsgpio_rpi
```
* **预期成功输出**：
  ```text
  JTAG loc.:   0  IDCODE: 0x24004093  Desc: XC6SLX25 Rev: C  IR length:  6
  ```

### 2.4 手动下载 FPGA `.bit` 文件
要将本目录下的 FPGA 固件文件下载到芯片中，可以运行以下命令（以 `0514/0514.bit` 为例）：
```bash
sudo ./xc3sprog -c sysfsgpio_rpi -v -p 0 0514/0514.bit
```

#### 参数解析：
* `./xc3sprog`：调用当前目录下的预编译下载器文件。
* `-c sysfsgpio_rpi`：指定下载线缆为我们适配树莓派 5 的自定义类型。
* `-v`：输出详细的烧录进度 and 日志。
* `-p 0`：指定操作第 0 号芯片（即 XC6SLX25）。
* `0512/0512.bit`：您想要烧录的目标固件文件的路径。
