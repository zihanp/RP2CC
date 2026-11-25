# 参考资料：原文到项目证据

下列六篇是项目设计真正用到的本地稿件。它们来自 OpenAI Math Release，属于**预印本**；有些只提供研究构造或证书思路。随包 PDF 是未修改的原文，已与[固定的上游快照](https://github.com/openai/math/tree/3344da59991ed94f9ba711217b29c1d760badfa6)逐份核对 SHA-256；[Apache-2.0 许可证](papers/LICENSE)一并保留。表中页码从 PDF 第一页起算。[BibTeX](references.bib) · [更详细的工程迁移对照](../docs/research-map.md)

| 稿号与原文 | 实际参考的位置 | 在这里改成了什么、怎样核对 |
|---|---|---|
| 268.01 · [The periodic spin-one Haldane gap](papers/01-268.01-packed-lane-integer-floor.pdf) | 附录 A.3，PDF 21–22 页；附录 B.2，24–25 页 | 原稿用大整数分道处理；本项目把两个二补码 i16 放进一个 u32，重新推导掩码和符号位。[全输入证明](../formal/Proofs.lean)、[Hazard3 指令值语义](../formal/Hazard3.lean)和[板上对照](../docs/experiments.md)分别回答正确、编码和速度问题。 |
| 128.01 · [A Polynomial-Time 2-Approximation for Shortest Common Superstring](papers/02-128.01-shortest-common-superstring.pdf) | 定理 1.1、第 7 节，PDF 1、18–19 页 | 借来“让相同字节共享存放”的视角。实际实现有对齐和地址身份约束，**不采用**原稿的 2 近似算法或结论。[六个对象的最小性](../formal/PoolExample.lean)、[链接尺寸](../dist/footprint/comparison.json)。 |
| 130.02 · [Finite tensor savings and exact Fourier circuits](papers/03-130.02-dirty-coordinate-replay.pdf) | 引理 3.1，PDF 10–11 页 | 借用暂存坐标后恢复。项目在模 2³² 模型里另证 `dirty_restore`；成品只用简单抵消规则，不把它当成物理寄存器分配器。[证明](../formal/Proofs.lean)与[规则边界](../docs/language.md)。 |
| 380.01 · [Strongly polynomial linear programming](papers/04-380.01-exact-linear-certificates.pdf) | 定理 1、第 2.1 节，PDF 1–2、5 页 | 学的是候选和精确验收分开；没有把论文的 LP 求解器移植到 MCU。[候选器](../rp2cc/propose.py)、[Python 回放](../rp2cc/checker.py)。 |
| 187.01 · [Snaky in 21 Maker moves](papers/05-187.01-replayable-certificate-dag.pdf) | PDF 5–7、11–13 页 | 参考有限证据可独立回放的组织方式；不使用原博弈程序或证书格式。[独立 Lean 验收入口](../formal/Replay.lean)。 |
| 266.02 · [Exact Fourier certificates for complex Hadamard matrices of order six](papers/06-266.02-exact-rational-verifier.pdf) | 引理 7.1、第 7.4 节、附录 A，PDF 19–25 页 | 借“候选生成不必可信，但验收重算精确条件”的方法。项目重算位向量语义，不调用论文矩阵求解器。[Lean 验收定理](../formal/Checker.lean)。 |

**仿射递推不归给上述论文。** 本项目在模 2³² 上用仿射函数复合得到固定递推的闭式。[源程序](../examples/jump.rpm)、[生成的 C](../dist/generated/jump/kernels.c)、[Lean 定理](../formal/Proofs.lean)和[原始采样](../dist/bench/arm/results.json)各自回答算法、实现、正确性和速度问题。

硬件与工具依据：[RP2350 数据手册](https://datasheets.raspberrypi.com/rp2350/rp2350-datasheet.pdf)、[RISC-V 位操作扩展](https://docs.riscv.org/reference/isa/unpriv/b-st-ext.html)、[Pico SDK 固定版本及其他许可](third-party.md)。没有收入其他背景稿、论文附带程序、未获转载授权的板卡手册或原始工作区索引。
