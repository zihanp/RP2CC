# Lean 验证：编译器到底证明了什么

在项目根目录使用 Python 3.10+ 和 Lean **4.33.1**，无需 mathlib、第三方 Python 包或联网：

```powershell
python formal/check.py --self-test --kernel-instances --report work/lean-recheck.json
```

脚本先找工作区的便携 Lean，也可用 `--lean <可执行文件>` 或环境变量 `RP2CC_LEAN`。每次重建到新临时目录，检查关键定理的传递公理依赖，再分别回放基线和优化版本。`--kernel-instances` 另让 Lean 内核对每个实际表达式和每个常量对象分别化简一个闭合验收条件、检查带全称量词的具体定理；小项目适用，大对象可只运行通用证明加直接回放。报告含输入、证书和证明源码的 SHA-256。只验证自己的项目：

```powershell
python formal/check.py --source app.json --cert build/certificate.json
python -m rp2cc verify app.json --cert build/certificate.json
```

两条命令的工作不同。Lean 直接解析源 JSON 与证书，检查函数签名和变量绑定，独立归一化两侧表达式，并验证常量池的每个切片；它不采用证书中的改写步骤、源哈希或生成器给出的解释。Python 的 `verify` 另回放步骤、核对哈希并重新生成 C/H 逐字节比较。Lean 对新的、尚未建模但可能正确的改写会拒绝，拒绝不被当作不等价的证明。

| 源码 / 定理 | 主张 |
|---|---|
| `Proofs.lean`：`floor_correct`、`decoded_floors` | 任意 32 位输入。将它看作两个有符号 16 位数，结果的每道都恰为原整数除以 16 后向下取整。证明逐个推导 **32 个输出位** 对任意输入的函数，并非枚举抽样。举例：`−1` 得 `−1`、`−17` 得 `−2`。 |
| `Proofs.lean`：`affine_correct` | 对任意 `n : Nat`、任意 32 位 `x,a,b`，执行 `x ← a·x+b` 共 `n` 次，等于独立递推得出的 `A·x+B`，每步按 `2³²` 取模。输入语言把 `n` 限制在 `0..65535`。 |
| `Hazard3.lean`：`hazard3_correct` | 对任意 32 位输入，`sext.h tmp,x; srai tmp,tmp,4; srai x,x,20; pack x,tmp,x` 得到同样的双道有符号取整结果。`pack` 的第一个源提供低半字、第二个源提供高半字；论证只需临时寄存器与输入分开。逐位证明算值，不假设汇编器、寄存器分配或周期数正确；实际指令另对 ELF 反汇编。 |
| `Proofs.lean`：`slice_sound` | 验收某一安置后，对象按所需对齐，整个区间在池内，任意下标读取都与源对象一致；越过对象长度返回零，不允许观察对象地址。实际基址还依赖 C 的 `_Alignas` 和链接器。 |
| `Proofs.lean`：`dirty_restore` | 任意初始暂存值 `z` 可在该条纯线性更新序列中借用并还原。未把它冒充为已验证的寄存器分配器；实例中的 `restore` 只用更简单的加减抵消规则。 |
| `Checker.lean`：`subst_sound`、`norm_sound`、`accepts_sound` | 对这门纯字表达式语言，若独立检查器返回真，则对**所有动态输入环境**，候选表达式与代入已知参数的源表达式返回相同的机器字。模型中的程序都终止，没有 IO 或内存效果。 |
| `PoolExample.lean`：`demo_lower84`、`demo_attains84` | 对本例的六个固定对象，84 字节确实最小：仅三个对象已覆盖 83 个不同字节值，其最大占用末端必是 4 的倍数；所有六个对象又有一个 84 字节的显式解。这里的下界不是 ELF 或 Flash 镜像大小。 |

`Audit.lean` 审计上述关键定理。允许的标准公理仅为 `propext`、`Classical.choice`、`Quot.sound` 的子集；无 `sorryAx`、未经检查的 SAT 假设、由本项目添加的公理或原生计算公理。`Replay.lean` 是真实文件的独立语义入口，Python 驱动还会拒绝 JSON 重复键。`--self-test` 会实际接受一次 65,535 轮的等价程序，并拒绝错误的仿射常数、错误字节和违反对齐的布局。

RV32 取整的 ISA 依据为 RISC‑V International 的 [Unprivileged ISA，Bit-Manipulation 指令（Zbb、Zbkb）](https://docs.riscv.org/reference/isa/unpriv/b-st-ext.html) 及 RV32I 的算术右移。该证明只对应这条固定的 RV32 序列，需目标满足 Zbb 与 Zbkb；不横推到 Cortex-M33 或 RV64。

**信任边界。** 以上定理连接源 IR 与优化 IR，以及私有 ROM 的值读取；它们不等于对 Python 前端实现、C 编译器/ABI、汇编链接、启动代码、PIO 时序、DMA、设备寄存器或硅执行的逐层证明。实际 C 和固件另用可重放的构建、反汇编和相应测试验收。通用定理由 Lean 内核检查；普通 JSON 回放中的求值信任 Lean 运行时。`--kernel-instances` 还让内核检查具体产物的闭合验收条件。两条路径仍须正确解析输入文件；一条直接用 Lean 解析 JSON，另一条用独立的 Python 序列化器将已验数据写为 Lean 构造子。
