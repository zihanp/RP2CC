# 靶机、板型与复现

固件构建同时生成 ELF 和 UF2。ELF 用于链接结果检查、调试和主机端 `picotool`；通过 BOOTSEL 磁盘拖拽烧录用的是 UF2，不是 ELF。板包负责链接布局、启动代码、芯片头文件和时钟前提。数学内核的证明边界见 `language.md`；实板周期和自检结果以 `dist/bench/` 的实测报告为准，某型号只有 ELF 和 UF2 不表示它已经在实板上运行。

## 板型与镜像选择

| `--board` | 封装和 Flash 链接上限 | 默认启动方式与适用条件 |
|---|---|---|
| `rp2350` / `hw973a` | RP2350A 类 QFN60，外置 4 MiB | 无默认 I/O 引脚；12 MHz 参考晶振，支持标准 NOR 03h 读取 |
| `hw973b` | RP2350A 类 QFN60，外置 16 MiB | 对应手册中的大容量版，只有确认容量才使用 |
| `rp2354a` | RP2354A QFN60，封装内 2 MiB | 仍使用 QMI 串行 Flash，绝不按 4/16 MiB 链接；12 MHz 板级参考 |
| `rp2350b` | RP2350B QFN80，外置 4 MiB | 48 个 GPIO 的 SDK 变体；默认示例不触碰这些引脚 |
| `pico2` | Raspberry Pi Pico 2 官方 RP2350A 板，4 MiB | 官方 SDK 板定义，适用其引脚和 Flash 的四线读取，不要套用到未知板 |

自定义板包保留 RP2350 A2 启动兼容，采用通用单线 03h 引导，不假定外置 Flash 的型号或四线使能位。这有利于跨板启动；依赖 XIP 取指的计算应在确认 Flash 型号后，选与板匹配的快速板包重新测量。除非显式启用 USB，样例不初始化 CDC/UART、不拉动 LED / WS2812、也不写 Flash、OTP 或外置引脚。固件仍进行正常 SDK 时钟与运行时初始化。

实验板以 BOOTSEL 的只读信息确认为 QFN60、16,384 KiB Flash。读到的容量符合 HW-973B 规格；通用 USB 标识不能单独证明商业板号，也不能替代第二块 RP2354A 实物。若需使用外接引脚，请按实际板卡原理图核实，不要把 Pico 2 的引脚定义套到 HW-973。

## 构建与复核

Python 标准库即可运行编译与打包脚本；另外需要 Ninja、CMake、SDK 和目标 GCC。以下在项目根目录执行。已安装对应工具时无需下载，可以直接传 `--sdk`、`--toolchain`、`--picotool-dir` 和 `--pioasm-dir`。生产环境应固定工具链。

```powershell
python tools/prepare_sdk.py --usb --windows-host-tools
python tools/fetch_arm_windows.py
# 仅在生成 Hazard3 固件时需要
python tools/fetch_riscv_windows.py
python -m rp2cc emit examples/bench.json --mode optimized --out dist/generated/optimized
python tools/build_firmware.py --generated dist/generated/optimized --board rp2354a --arch arm --out dist/my-2354
python tools/build_firmware.py --generated dist/generated/optimized --board hw973b --arch riscv --out dist/my-hazard3
python tools/build_firmware.py --generated dist/generated/optimized --board hw973b --arch arm --app firmware/cdc_main.c --usb-stdio --out dist/my-cdc
python tools/audit_firmware.py
```

工具与固定来源：本地 SDK ZIP 的版本为 2.3.1、SHA-256 为 `489f9b3efe7a9eefb66f839da124d4f75df9fc523d80065413840f3edd279633`。主机工具使用 Raspberry Pi picotool 2.3.1 和该 SDK 源码编译的 pioasm。SDK 自带的 `MODULE.bazel` 明确固定 TinyUSB 提交 `86ad6e56c1700e85f1c5678607a762cfe3aa2f47`，原始 SDK ZIP 不含这个子模块；只有 USB 功能需要补齐。目标编译器分别为 Arm GNU 14.3.Rel1 / GCC 14.3.1 与 Raspberry Pi 工具包的 Hazard3 GCC 16.1.0。两套 Windows 发行压缩包及可选依赖的完整 SHA-256 都写在准备脚本中。

SDK 路径有空格时，上游 RISC-V 自动探测会拆错编译器路径。本驱动使用参数数组真实编译 SDK 的 `riscv_zcmp_test.c` / `empty.c`，选择能通过的官方参数并传回 CMake；当前 GCC 接受 `-mcpu=hazard3-rp2350 -mstrict-align`。没有任何候选通过就报错。使用 Linux/macOS 或其它已安装工具时同样可以直接指定路径，未声称在所有主机系统上实测。

默认 demo 对随包的 `floor2` 和 `affine` 执行自检；自定义数学内核请通过 `--app 你的入口.c` 提供自己的 `main`。`--extra-source` 追加可信 C/C++/汇编，`--pio` 追加可重定位的原生 PIO 程序，`--sdk-lib` 追加 SDK 链接目标，各参数均可重复。自定义应用可直接使用官方头文件和并发、外设、IRQ 等 API；这是一条显式 SDK 扩展接口，不是数学 DSL 原生实现所有硬件功能。`--ram` 选用 SDK 的 `no_flash` 链接脚本，产出的所有 UF2 页必须在 `0x20000000..0x20081fff` 的 520 KiB SRAM 内。`--usb-stdio` 默认关闭；启用后允许程序调用 `stdio_init_all()` 使用 CDC，不接受 1200-baud 或厂商请求自动复位。CDC 样例会每秒发送一行结果。

## 如何写入，以及保护什么

正常烧录需用户**明确选择**：断开外部有风险的负载，确认芯片、板包、参考晶振、Flash 容量与已有内容备份，再进入 BOOTSEL，将所选 **Flash 版** `rp_lab.uf2` 复制到 RP2350 盘；ROM 完成写入后会重启。不要把 ELF、BIN、map 文件拖到 BOOTSEL 盘。RP2350 启用签名启动、OTP 限制或自定义分区时还需满足板方策略；这里交付的是未签名普通开发板镜像。普通程序烧录会替换原程序，SDK 的 A2/E10 兼容块位于所选容量最后一个 4 KiB 擦除扇区的最后一页，末扇区不要存放需保留的数据。审计工具要求该位置与板型完全一致。SRAM 测试使用专门 no_flash 镜像，切勿拿 Flash 版当作 SRAM 版试运行。

除非启用 `--usb-stdio` 并使用启动 USB 的应用，默认自检**不会亮灯或出现串口**。读取 MCU 调试符号 `rp2lab_status`：完成标记 `0x5250324c`、两项数学自检位均置位时 `checks == 3`。不希望使用调试器时可选 `dist/firmware/hw973b-cdc/rp_lab.uf2`（匹配的 16 MiB 板）在 CDC 串口看输出。

## 外设证据分级

| 部分 | 本次实现/证据 | 没有把什么当成已验证 |
|---|---|---|
| PIO0、PIO1、PIO2 | 同一个 3 指令 FIFO 回环程序：`pull block; mov isr, osr; push block`，机器字 `80a0 a0c7 8020`。每个 PIO 独立申请一台状态机，8 个不同 32 位字逐个写回校验；自检位 0/1/2 分别对应实例。 | 不驱动引脚；不是每块 PIO 的所有 4 台状态机同时满载，也未证明任意用户 PIO 协议。 |
| DMA | 申请空闲通道，搬运 32 个 32 位 SRAM 字，共 128 字节；加入内存屏障，逐字比对，检查 AHB 错误，自检位 3。 | 没有声称覆盖 DREQ 外设传输、所有 16 个通道或最坏争用。 |
| USB | 独立 CDC 镜像包含官方 TinyUSB、USB 设备栈、描述符和 SDK stdio。实际采集若使用 CDC，见测量报告。 | 并非 USB Host 和每种 USB 类的互操作认证。 |
| GPIO、UART、I²C、SPI、PWM、ADC、IRQ/异常、计时/看门狗、时钟/PLL/供电、缓存/Flash、插值器、SHA256、ROM、随机数、双核 | `--capability-smoke` 在 Arm 和 Hazard3 上各静态链接 27 个原型已检查的 SDK 函数引用，审计表符号 140 字节（含下列 8 个块指针）。具体 27 项在 `firmware/sdk_contract.c`。 | 审计镜像从不调用探针函数；没有接线的外设不称为实板功能测试。 |
| HSTX 控制与 FIFO、USB 寄存器、QMI、TRNG、OTP、ACCESSCTRL、TMDS | 同一不可丢弃的表各保留一个带 SDK 类型的寄存器块指针，共 8 项。 | 只验证 SDK 头文件/地址接口可用，没有写 OTP、改安全状态或验证协议/电气。 |

`dist/bench/arm/results.json` 和 `dist/bench/riscv/results.json` 在同一块 16 MiB 板上均记录 `io_pass_mask = 15`（十六进制 `0x0f`）：M33 和 Hazard3 两次 SRAM 执行均通过三个 PIO 实例的内部 FIFO 与一次 DMA SRAM 拷贝。两份报告分别读到 CPUID / mimpid，系统时钟均为 150 MHz。这并未实测第二块 RP2354A 板、GPIO 上的波形或其它未接线外设。

示例在单核启动阶段调用，轮询有次数上界；异常时不释放仍忙的 DMA 通道，静态源/目标在函数返回后仍然有效。其它并发应用不能把这个启动自检当作自己的外设仲裁器。

## 镜像审计

新构建可得到 ELF、UF2、BIN、链接 map、反汇编和构建日志。公开包保留前三项与每个镜像的 `manifest.json`。发布 ELF 保留符号、入口和所有加载字节，非加载调试数据已清零，原节表仅保留空项。按源码行调试请使用本地构建的 ELF。UF2 容器比程序大：每个 512 字节块通常只携带 256 字节应用数据，A2 Flash 版还有单独 E10 块。不要拿 ELF 文件大小（含符号表）或 UF2 大小当作 Flash 程序字节数。

独立 Python 检查器读取小端 ELF32 的机器号（Arm 40，RISC-V 243）、可执行入口、每个 PT_LOAD 的 VMA/LMA 和实际文件字节。它按 256 字节页独立重建数据及 SDK 扇区补零，再逐字节与 UF2 比较；还拒绝越界、错芯片族、页重叠和计数错配。E10 不是 ELF 的内容：另行检查它是 UF2 文件的首块、目标是容量末端减 256，核对绝对族 0xe48bff57、flags 0xa000、序号 0/2、256 字节 0xef、忽略标记 0x9957e304 和余区补零。SRAM 镜像完全禁止这种块。完整清单由 `python tools/audit_firmware.py --out dist/firmware/audit.json` 更新。该检查不替代 C 编译器、链接器、ROM 或所有驱动的形式化证明。

参考：本地《RP2350 Datasheet》的 Ordering information、Processor subsystem、Bootrom、DMA、PIO、Errata RP2350‑E10；SDK 2.3.1 的 `src/rp2350/hardware_regs/include/hardware/platform_defs.h`、`boot2_generic_03h.S`、`src/boards/include/boards/pico2.h`，以及 picotool 2.3.1 的 `elf2uf2/elf2uf2.cpp`。板级引脚以供应商 HW‑973AB 产品说明书和具体实物为准。
