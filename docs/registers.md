# RP2350 寄存器接口：覆盖了什么

**官方 Pico SDK 2.3.1 的 RP2350 寄存器结构目录共有 55 个头文件。** 用本项目 Cortex‑M33、Hazard3 的正式固件编译参数逐个编译，默认配置都通过 55/55。这个分母是 SDK 头文件数，不是芯片功能数，更不是“所有外设都已实测”。RP2CC 的数学语言不直接写寄存器；应用通过同一固件中的原生 C/C++、SDK 与 PIO 汇编使用硬件。

| 证据 | 这次实际检查了什么 | 可复核记录 |
|---|---|---|
| 头文件，可编译 | 50 个头直接声明 69 种结构；另 5 个是旧名称的转发头。Arm 与 RISC‑V 默认均为 55/55；RISC‑V 启用 SDK 的 Arm 专用头保护后为 48 个通过，7 个被正确拒绝。 | [逐文件结果、工具链、哈希](../dist/evidence/registers.json) |
| 接口，可链接 | 另一个固件探针在 Arm 和 Hazard3 各保留 27 个 SDK 函数引用、8 个寄存器块指针。链接器确实解析这些引用，但程序不调用该表去操作硬件。 | [引用清单](../firmware/sdk_contract.c)、[Arm 镜像](../dist/firmware/sdk-contract-arm/manifest.json)、[Hazard3 镜像](../dist/firmware/sdk-contract-riscv/manifest.json) |
| 行为，已上板 | 同一块 QFN60、16 MiB Flash 开发板上，两种架构均跑过 PIO0/1/2 各 8 个字的内部 FIFO 回环、DMA 复制并校验 32 个 32 位字；还实际用 USB CDC 回传过基准。三份原始记录的自检掩码均为 `0x0f`（3 个 PIO 位和 1 个 DMA 位，USB 不另占位）。 | [M33](../dist/bench/arm/results.json)、[Hazard3 通用](../dist/bench/riscv/results.json)、[Hazard3 专用](../dist/bench/riscv-tuned/results.json) |

## 为什么 RISC‑V 有两个数字

以 `systick.h` 为例：RISC‑V GCC 能理解 Arm SysTick 寄存器的 C 结构布局，并不意味着 Hazard3 具备同一个 SysTick。实际 RISC‑V 基准读的是 `mcycle`。默认 CMake 构建的 55/55 是语法和布局结论。第二轮把 SDK 的 `PICO_FORBID_ARM_HEADERS_ON_RISCV` 设为 `1`，再次编译全部头文件。以下 7 个均因头文件自己的明确诊断被拒，不能算作可用的 Hazard3 内核外设：

`m33.h`、`m33_eppb.h`、`mpu.h`、`nvic.h`、`sau.h`、`scb.h`、`systick.h`。

其余 48 个通过严格编译也不等于当前安全域、封装引脚或板级接线准许相应操作。5 个兼容头不重复算作硬件块：`bus_ctrl.h → busctrl.h`、`iobank0.h → io_bank0.h`、`ioqspi.h → io_qspi.h`、`padsbank0.h → pads_bank0.h`、`xip_ctrl.h → xip.h`。

## 如何得到这 165 项结果

[Python 脚本](../tools/audit_registers.py)递归枚举 SDK 的 `src/rp2350/hardware_structs/include/hardware/structs/`。每一项都单独生成 C11 翻译单元；不靠另一个被审计头先行包含来补齐依赖。脚本复用真实 CMake 编译参数，核对 GCC 记录的首层包含路径与枚举的 SDK 文件一致。没有通过时不能悄悄跳过。

对于直接声明的 69 种结构，脚本分别添加 `_Static_assert(sizeof(类型) > 0)`，并检查目标宏和 32 位指针宽度。SDK 自己在 49 个头中直接写有 53 条静态断言，其中既有精确的结构大小，也有成员偏移等约束。例如 `usb_dpram.h` 约束 USB 设备、主机两种 DPRAM 视图的大小及 `epx_data` 的偏移。另一个直接声明结构的头 `dma_debug.h` 没有自带大小断言，其两个类型只获得额外的完整性检查。5 个转发头没有自己的结构声明；验收的是它们能独立包含。

本次使用 Arm GCC 14.3.1、Raspberry Pi Hazard3 GCC 16.1.0，共 **3 × 55 = 165 次**：158 次得到正确架构的 32 位小端可重定位 ELF，7 次匹配预期的 SDK 拒绝。每个成功对象的可分配节都为零字节：测试本身没有操作寄存器，也没有可烧录程序。每项的头、测试源码、成功对象均有 SHA‑256。对象都是空检查单元，因此同一架构出现相同对象哈希是正常的，此时以头和测试源码的哈希区分。

在准备好工具链且两种 `rp2354a` 构建已有 CMake 编译数据库时，到项目根目录执行；不接触 USB 或开发板：

```powershell
python tools/audit_registers.py
# 如要保存独立复核报告：
python tools/audit_registers.py --out work/registers-recheck.json
```

干净环境可先按[硬件说明](hardware.md)准备 SDK、工具链，再在本机生成两份编译数据库：

```powershell
python tools/build_firmware.py --generated dist/generated/optimized --board rp2354a --arch arm --capability-smoke --out work/reg-link-arm
python tools/build_firmware.py --generated dist/generated/optimized --board rp2354a --arch riscv --capability-smoke --out work/reg-link-riscv
python tools/audit_registers.py --out work/registers-recheck.json
```

若数据库不在项目默认的 `.build/`，可用 `--arm-db`、`--riscv-db` 指定两份由可信工程生成的 `compile_commands.json`；SDK 路径不同可传 `--sdk`。以上仅构建本机文件。没有据此宣称 OTP 编程、安全域权限、USB 主机、HSTX 输出、所有 DMA 通道、外部引脚时序或第二块 RP2354A 已通过实板验证。
