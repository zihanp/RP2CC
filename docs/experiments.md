# 可复核的性能实验

研究问题是：**固定参数和严格的 32 位代数能否减少实际执行的工作？** 这里把三个量分开：Lean 检查任意输入的正确性；ELF 记录机器码与数据大小；实际板上的计数器给出运行周期。下面的基线也知道所有常量。

## 1. 板上时间：同一映像内比较

板为 BOOTSEL 报告的 QFN60、16 MiB Flash RP2350 系列实物。每次用 SDK `no_flash` 布局加载、校验 SRAM ELF，在同一块板上分别运行 Cortex‑M33 与 Hazard3；程序不会改写原 Flash。时钟均为 **150 MHz**。Cortex‑M33 读以处理器时钟计数的 SysTick，Hazard3 读 `mcycle`。三组实验（M33 通用、Hazard3 通用、Hazard3 专用）均经 USB CDC 传回采样，之后回到 ROM BOOTSEL。

每组的 11 个函数在**同一个最终映像**中：1 个恒等调用、2 种运算各 5 种实现（GCC `-O2/-O3/-Os` 普通源码、RP2CC 生成源码的 GCC `-O3`、同编译器的手工 `-O3` 解）。每个函数先预热一批；随后轮换函数顺序，完成 **41 轮 × 128 次**依赖调用。每轮各实现使用相同种子；前一次调用的输出决定下一次输入，最终结果传回主机校验。这里测的是有数据依赖的调用，不是独立调用的最高吞吐量。统计量是每批的周期数除以 128，再取中位数或 P95（排序后第 39 个值）。

自检和 1,024 个边界/生成测试向量在计时前完成。计时期间屏蔽中断、代码与数据在 SRAM，没有 USB 打印，也未启用持续 DMA；计时之后才发回结果。主机对每次回传的 **451 个批输出摘要**（41 × 11）独立重算，核对 nonce、记录长度、邮箱校验及错误数。原始数据不是凭串口打印的几个汇总数字。每批计数包含循环、间接调用、依赖链及计时边界，**不扣固定开销**。恒等调用的中位数仅作参照：M33 为 9.05，Hazard3 为 8.06 周期/调用。

### 通用代码生成的实测

周期/调用，保留两位小数；“相对”是该行 GCC `-O3` 中位数除以 RP2CC 中位数，两者都含相同测量框架。下面这两份通用实验的 P95 与各自中位数相同，完整 P95、极值及逐批计数均在 JSON。

| 处理器与运算 | 普通 -O2 | 普通 -O3 | 普通 -Os | RP2CC 通用 -O3 | 手工 -O3 | 相对 -O3 |
|---|---:|---:|---:|---:|---:|---:|
| M33：双道取整 | 17.05 | 17.05 | 18.05 | 13.05 | 13.05 | 1.31× |
| M33：257 轮仿射 | 1,038.05 | 1,038.05 | 1,038.05 | 12.05 | 12.05 | 86.11× |
| Hazard3：双道取整 | 21.06 | 21.06 | 21.06 | 16.06 | 12.06 | 1.31× |
| Hazard3：257 轮仿射 | 1,045.02 | 1,046.02 | 1,046.02 | 14.06 | 15.06 | 74.38× |

数据：[M33 原始结果](../dist/bench/arm/results.json) · [Hazard3 通用原始结果](../dist/bench/riscv/results.json)。两次均 `device_mismatches=0` 且 `io_pass_mask=15`：PIO0、PIO1、PIO2 分别回环 8 个字，DMA 在两段 SRAM 之间搬运并比对 32 个字。它不验证每个引脚、任意 PIO 工程，也不验证第二块 RP2354A 的电气性能。

**仿射为什么快？** 源码连续执行 257 次 `x ← 1664525·x + 1013904223 (mod 2³²)`。两次仿射函数的复合仍是仿射函数，于是编译期可以按幂的二进制分解合成最终系数，目标只剩一次乘加。手工对照用逐轮计算的 `f(0)` 和 `f(1)` 独立求最终系数。M33 上两者实测同速；不能把 86.11× 理解为相对该闭式手工算法的加速。Hazard3 上通用仿射比手工对照少 1 个含框架周期，不据此推广到其它布局。

**为什么双道取整还要单独看目标？** 两个 i16 同装在一个 u32，均做数学向下除以 16；负数不等于 C 有符号除法的向零截断。通用位运算在 M33 与手工同速，但在 Hazard3 比手工多 4.00 个含框架周期。可重放链接记录显示：M33 的通用 / 手工双道函数分别 16 / 14 字节；Hazard3 分别 24 / 14 字节。字节数并不决定周期数。

### Hazard3 专用指令选择

对匹配的完整双道取整函数，专用规则直接选 `sext.h`、两次算术右移、再 `pack`。RV32 目标需要 Zbb、Zbkb；其它架构不能直接照搬。双道函数从通用版 24 字节降到 **14 字节**（含压缩返回指令），而同一 GCC `-O3` 普通实现占 40 字节。仿射仍沿用通用的编译期数学变换。第三个独立固件使用相同的 11 函数测量框架，得到以下结果；数值为周期/调用，相对倍数按未四舍五入的中位数计算。

| 运算 | 普通 GCC -O3 中位数 | RP2CC 中位数 | RP2CC P95 | 手工 -O3 中位数 | 相对普通 -O3 |
|---|---:|---:|---:|---:|---:|
| Hazard3：双道取整 | 21.0625 | 12.0625 | 12.0625 | 13.0625 | 1.75× |
| Hazard3：257 轮仿射 | 1,046.0234375 | 15.0625 | 15.0625 | 14.0625 | 69.45× |

本次链接后，RP2CC 与手工的双道函数逐字节相同（各 14 字节），两份仿射函数也逐字节相同（各 22 字节）。两种放置地址不同：取整是编译版少 1 个周期，仿射是手工版少 1 个周期。因此不能说 RP2CC 算法比相同机器码的手写算法更快。也不要混合两个固件的最快数字：通用与专用的原始记录、链接地址、ELF 哈希均分别保留。

第三组同样为 0 次设备差分错误、451 个主机重算通过的批摘要、PIO/DMA 自检掩码 `0x0f`。证据：[专用版原始结果](../dist/bench/riscv-tuned/results.json) · [对应已链接地址、字节和反汇编](../dist/bench/riscv-tuned/linked_code.json) · [目标选择收据](../dist/generated/hazard3/hazard3.json) · [位级定理](../formal/Hazard3.lean)。下面的只读命令可列出三次运行的摘要：

```powershell
python -c "import json,pathlib; P=pathlib.Path('dist/bench'); print('run,clock_Hz,floor_p50,floor_p95,affine_p50,io_mask,verified_batches'); [print(p.parent.name,j['clock_hz'],s['op_floor2']['median_cycles_per_call'],s['op_floor2']['p95_cycles_per_call'],s['op_affine']['median_cycles_per_call'],j['io_pass_mask'],j['host_verified_sample_digests'],sep=',') for p in sorted(P.glob('*/results.json')) for j in [json.loads(p.read_bytes())] for s in [j['summary']]]"
```

## 2. 常量池：整份固件是不是也变小了？

实验用六个私有引用（包含长度为零的对象），调用方只能按“对象、索引”读取字节，无法观察地址身份。测试数据本身含窗口包含与尾首重叠。在相同的 RP2354A、Arm `-O3` 布局下，用同一个应用引用所有六个 getter，防止链接器删除未用数据：

| 单位：字节 | 分别存放 | 压缩后 | 差值 |
|---|---:|---:|---:|
| ELF 中的 `rp2cc_pool` | 148 | 84 | −64（−43.24%） |
| 应用在 Flash 的连续加载跨度 | 5,824 | 5,760 | −64（−1.10%） |

这 84 字节是**本例只读池**的严格最小值：其中三个对象已包含 83 种不同的字节值，三者占用的最远末端因对齐必为 4 的倍数，所以至少需要 84 字节。六个对象又确实能放入 84 字节。它不证明别的字节池或整个固件是最小的。这里比较 ELF 的 XIP 加载区间，不拿 UF2 封装大小估计 Flash 节省。数据：[链接前后地址、符号和 SHA-256](../dist/footprint/comparison.json)；具体下界与上界：[PoolExample.lean](../formal/PoolExample.lean)。

## 3. 复核步骤

公开版保留原始邮箱、全部周期和输出摘要。`elf_sha256` 指公开 ELF，`original_elf_sha256` 指采集时的原 ELF。两者的入口地址和所有加载段一致，UF2 也没有改动。公开版不含主机采集时刻、原始设备信息转储或构建日志。

以下从项目根目录执行，仅用本机文件，不触碰开发板：

快速验收全部已交付的二进制证据可运行 `python tools/verify_study.py`。如要重建**同一份**基准源配置，`tools/bench.py prepare --nonce <值> --out <目录>` 可使用对应 `results.json` 内的 `nonce`；专用版本还要加 `--tune hazard3`。在真实设备开始新采集时不指定 `--nonce`，产生新的随机标记。

```powershell
python tools/bench.py replay --results dist/bench/arm/results.json --mailbox dist/bench/arm/mailbox.bin
python tools/bench.py replay --results dist/bench/riscv/results.json --mailbox dist/bench/riscv/mailbox.bin
python tools/bench.py replay --results dist/bench/riscv-tuned/results.json --mailbox dist/bench/riscv-tuned/mailbox.bin
python tools/audit_firmware.py
python -m rp2cc verify examples/bench.json --cert dist/generated/optimized/certificate.json
python tools/tune_hazard3.py --source examples/bench.json --generated dist/generated/optimized --out dist/generated/hazard3 --verify
python formal/check.py --self-test --kernel-instances --report work/recheck.json
```

有关产物：[源基准](../bench/board_bench.c)、[独立手工对照](../bench/hand.c)、[M33 已链接代码](../dist/bench/arm/linked_code.json)、[Hazard3 通用已链接代码](../dist/bench/riscv/linked_code.json)、[主机差分测试收据](../dist/evidence/compiler.json)、[M33 编译变体审计](../dist/evidence/variants-arm.json)。符号字节数有时包含字面量池和对齐填充，不一概当作纯指令数。若需要重新编译基准，`tools/bench.py prepare` 生成新实验计划，`build` 只生成 SRAM 固件；显式 `run` 才是板操作，要求单独指定 BOOTSEL 设备序列和该 ELF 的 SHA-256，且会改变当前运行状态。[USB 采集说明](usb-capture.md)给出条件。

## 4. 没有由这些数据推出的结论

时间表只针对一块板、两个依次启动的 ISA、150 MHz、预热的 SRAM 和固定数据链。没有测 XIP 冷/热缓存、双核并发、持续 DMA 争用、开中断的抖动，也没有给出最坏执行时间或能耗。41 轮轮换也不构成所有位置等次数的大样本试验。RP2354A 镜像的链接/字节核验不代替第二块实板。USB、SDK 扩展和机器码验收不在数学语义证明范围内。最有价值的对照是保留普通实现、手工实现与原始周期，让下一项改进可以在相同问题上继续比较。
