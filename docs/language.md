# RP2CC 语言、编译与复核

RP2CC 是一个用 Python 标准库实现的专用编译器。输入是**已知位宽、已知常量、没有外部副作用**的计算内核；输出是带稳定 C 函数接口的 C11、逐步改写证书和统计文件。将内核与 Pico SDK 的外设工程链接，才得到可启动的 ELF／UF2。JSON 是可直接手写或由其他工具生成的输入语言，不会求值其中的 Python、C 或 shell 文本。

## 从例子开始

在 `rp2350-lab` 目录中运行，Python 3.10+ 即可，无需 `pip install`。

```powershell
python -m rp2cc emit examples/bench.json --out dist/generated/optimized
python -m rp2cc emit examples/bench.json --out dist/generated/baseline --mode baseline
python -m rp2cc verify examples/bench.json --cert dist/generated/optimized/certificate.json
python formal/check.py --source examples/bench.json --cert dist/generated/optimized/certificate.json
python -m rp2cc eval examples/bench.json floor2 0x8000ffff
python -m rp2cc eval examples/bench.json affine 0
python tests/test_compiler_report.py --out dist/evidence/compiler.json
```

最后两个 `eval` 的结果分别是 `0xf800ffff`、`0xe89f545f`。`emit` 写入 `kernels.c`、`kernels.h`、`certificate.json`、`stats.json`。证书绑定规范化源 JSON 的 SHA-256 和实际生成的 C/H 字节。`verify` 以同目录下的 C/H 作逐字节核对；仅核对证书文件的哈希**不能**代替这个命令。重用输出目录会替换这四个同名文件。`eval` 直接执行源语义，不读取候选结果。

## 更接近数学的写法：`.rpm`

可以不手写 JSON，也不必亲自把一次更新化成 `a*x+b`。以 `examples/jump.rpm` 的普通循环为例：

```python
STEPS = 257
SCALE = 1_664_525
BIAS = 1_013_904_223

def affine(x: u32) -> u32:
    for _ in range(STEPS):
        x = SCALE * x + BIAS
    return x
```

```powershell
python tools/math_frontend.py examples/jump.rpm --out dist/generated/jump.json --trace dist/generated/jump.frontend.json
python -m rp2cc emit dist/generated/jump.json --out dist/generated/jump
python -m rp2cc verify dist/generated/jump.json --cert dist/generated/jump/certificate.json
python formal/check.py --source dist/generated/jump.json --cert dist/generated/jump/certificate.json
```

`.rpm` 借用 Python 的书写习惯，**不是可执行的 Python 模块**。前端只用 `ast.parse` 读取语法树，从不 `eval`/`exec`。全局常量须写在函数前，可使用十六进制和数字分隔符。函数使用 `u32` 参数／返回类型，支持局部赋值、`+ - * & | ^ ~ << >>`、`lane_floor16(x)` 和最后一条 `return`。`>>` 是无符号右移；常量和每一步算术均按 u32 解释。没有导入、任意函数调用或寄存器访问。

`for _ in range(K)` 的 `K` 必须静态可算为 0…65535；循环体必须是对同一个已初始化变量的单条赋值。识别器把每个子式表示成 `(a,b)`，含义为 `a*x+b`，先处理子式再组合。加减逐项合并；乘法要求至少一边系数为零，然后乘以这个常数。例子中 `3*(y+4)+4*y-1` 自动化为 `7*y+11`。`x*x`、动态系数或对状态的任意位运算均拒绝；识别器不尝试猜测未覆盖的恒等式。循环的前置／后置纯赋值和多个依次进行的循环可以组合。

可选 `--trace` 留下源行号、循环次数、推导的系数、源文件和普通 JSON 的摘要。Lean 从**已生成的 v1 JSON**开始验证，不证明 Python 语法解析器与此识别器本身；`tests/test_compiler_math.py` 用手写逐轮参照、边界数值、随机系数和非线性拒绝分别测试它们。

## 如何写一个内核

```json
{
  "version": 1,
  "kernels": [
    {"name": "gain", "args": ["sample"], "known": {"scale": 17},
     "body": ["add", ["mul", "sample", "scale"], 32]}
  ],
  "blobs": [{"name": "signature", "align": 4, "hex": "a500ff5a"}]
}
```

在 C/C++ 中包含 `kernels.h`，调用 `rp_gain(sample)` 或 `rp_rom_signature(index)`。`args` 是运行时参数，`known` 是只在编译期存在的名称；两者不得重名。常量保留在普通对照版本里，保证比较双方知道同样的信息。JSON 文件不接受 `0x...` 写法，请用十进制；命令行 `eval` 接受十六进制。

| 表达式 | 精确定义 |
|---|---|
| `0`…`4294967295` / `"变量"` | 无符号 32 位字 / 动态或已知参数 |
| `["add",x,y]`, `["sub",x,y]`, `["mul",x,y]` | 加、减、乘，结果对 `2³²` 取模 |
| `["and",x,y]`, `["or",x,y]`, `["xor",x,y]`, `["not",x]` | 32 位逐位与、或、异或、取反 |
| `["shl",x,k]`, `["lshr",x,k]` | 左移／逻辑右移，`k` 必须是 0…31 的字面量；左移丢弃超出位宽的位 |
| `["floor2",x]` | 低、高半字分别解码为二补码 i16，各自数学向下除以 16，再截成两个半字装回原位 |
| `["affine_iter",x,n,a,b]` | 从 `x` 开始，准确重复 `x := a*x+b mod 2³²`；`n` 为 0…65535 字面量，`a,b` 为 u32 字面量 |

`kernels` 支持 1…128 项，函数最多 16 个动态参数、128 个已知参数。标识符是字母起头的 ASCII 字母、数字和下划线，最长 48 字符，不允许 `__`，函数名保留 `rom_` 前缀。源表达式总上限 8192 个结点、单条路径深度 80。没有指针、MMIO、隐式浮点、未定义的超宽移位或对负数的 C 有符号右移。

普通控制流、IRQ、DMA、双核、PIO 等**在独立 SDK 翻译单元里实现**。若多个寄存器的读取顺序重要，应先以独立 C 语句按规定顺序读出，接着把值传给 `rp_*`，最后写回；不要依赖同一个 C 调用中多个实参的求值顺序。编译器只处理已经取出的普通值，不会合并、改宽或删除外设事件。外设协议、总线时序、内存屏障、浮点精度和多核同步不属于这些纯计算定理的结论。

## 两项可复核的计算优化

**双道缩放。** 若一条 16 位输入道是 `0xffff`，它表示 −1，向下除以 16 仍是 −1，而 C 的有符号 `/16` 会得到 0。令 `v = (x >> 4) & 0x0fff0fff`。每道现在包含 12 位有符号结果的低 12 位。用下式同时扩展两个符号：

```text
result = (v + 0x78007800) XOR 0x78007800
```

单道中，`v <= 0x07ff` 时结果保留原值；`v >= 0x0800` 时高四位补 1。最大中间值是 `0x87ff`，不会从低道进位到高道。普通版分别提取两道并复制符号，避免负数右移的实现差异。

**仿射跳步。** 把一次更新记为 `(a,b): x ↦ a*x+b`。函数复合仍是这样的二元组。优化器用二进制幂计算 `n` 次迭代的 `(A,B)`；证书检查器故意不共用快速算法，而从 `(1,0)` 出发做 `n` 次原始递推。`n=257, a=1664525, b=1013904223` 的候选为：

```text
rp_affine(x) = x * 0x46a65a0d + 0xe89f545f     (mod 2^32)
```

原始 C 版本有完整的已知 257 次循环，后端可以自行处理它。`n=0` 返回原值，`a=0`、全 1、溢出等情况不另设例外。该变换只在**仅需最终状态**时成立。如果每步的输出会写 FIFO、触发中断或被逐步读取，就不能跳过这些步骤。

## Hazard3：查看最终机器码

同一份 IR 可用于 M33 和 Hazard3。上面的分道掩码/加法是可移植 C；若目标是支持 Zbb、Zbkb 的 RV32 Hazard3，还可以精确选择机器指令。这里选择的模式很窄：**整个函数有且只有一个动态参数 `x`，源函数体恰是 `floor2(x)`**。匹配依据是已验证的语法树，不是函数名。嵌在较大表达式中的 `floor2` 保留通用 C；仿射和其余函数不变。

| 指令（ABI：输入、返回都是 `a0`） | 完成的工作 |
|---|---|
| `sext.h tmp,a0` | 取低半字，符号扩展到 32 位 |
| `srai tmp,tmp,4` | 得低道的向下除以 16 结果 |
| `srai a0,a0,20` | 直接从原整字得高道的向下除以 16 结果 |
| `pack a0,tmp,a0` | 取两路各自的低 16 位，拼回原位置 |
| `ret` | 返回，无栈帧和额外内存访问 |

本次 GCC 用 `a5` 作临时寄存器，把两次移位和返回压成 16 位，其余两条是 32 位。实际基准 ELF、HW-973B 和 RP2354A 两个部署 ELF 的所选函数都是同样的 **14 字节**：`93 17 55 60 91 87 51 85 33 c5 a7 08 82 80`。这是具体二进制的事实；与运行周期是不同的量。周期对照见[实测报告](experiments.md)。

从同一份已验证输入复现，以下操作均不需要插板：

```powershell
python tools/tune_hazard3.py --source examples/bench.json --generated dist/generated/optimized --out dist/generated/hazard3
python tools/tune_hazard3.py --source examples/bench.json --generated dist/generated/optimized --out dist/generated/hazard3 --verify
python tools/check_hazard3_code.py --elf dist/bench/riscv-tuned/image/rp_lab.elf --receipt dist/generated/hazard3/hazard3.json --prefix op_ --out work/hazard3-code.json
```

第一、二条只需 Python 标准库；第三条读取 ELF 符号表，需要 `pyelftools`。部署 ELF 使用默认前缀 `rp_`，基准将同一接口重命名为 `op_`。要保留便携实现，在项目驱动上使用 `--portable`。

显式调用选择器且没有匹配项会报告错误；`project.py` 则会自动保留已核验的通用 C 并继续构建。一个最小反例是[只含仿射循环的工程](../examples/affine-only.project.json)：`python project.py examples/affine-only.project.json --out work/affine-only` 会在 RP2354A/Hazard3 上生成有效的 ELF 和 UF2，无需人为添加取整函数。它配套自己的原生 C 入口，未使用引脚，也不操作已连接的板。

三类证据有各自的职责：`portable/certificate.json` 对应原 IR 和未改动的通用 C/H；`hazard3.json` 记录并可重放目标选择和生成字节；Lean 的 [`hazard3_correct`](../formal/Hazard3.lean) 证明四步位运算对所有 u32 输入等于两道取整。最后，独立机器码检查器从 ELF 函数符号逐条解码，核对 ABI 寄存器、移位数、打包顺序和返回，不使用反汇编文本或函数名猜测指令。实际收据：[基准](../dist/evidence/hazard3-linked.json)、[HW-973B](../dist/evidence/hazard3-hw973b-deployment.json)、[RP2354A](../dist/evidence/hazard3-rp2354a-deployment.json)。这一小段做到了按最终机器码验收；并不据此宣称整个 SDK 或通用 GCC 已被形式化。

## 只读字节池

`blobs` 的每项包含名称、十六进制数据（两个字符一字节、不省略任何 `00`）和 1…256 的二次幂对齐值。允许空对象、重复内容和后缀／前缀重叠；最多 256 项、源字节总计不超过 1 MiB。对象只能用按值读取的函数访问；越过对象结尾返回 0，绝不读取邻居。地址、对象身份、可写对象和重定位不在接口内。

基线把各非空对象依次对齐存放。候选按三种顺序尝试已存在的子串和对齐尾首重合。检查器单独核对每一个偏移、对齐、范围和全部字节，实际 C 数组基址也按最大对齐声明。候选没有更短时保持原布局。搜索本身不保证最短，但若候选达到独立下界，就能证明该实例的字节池已经最小。

| `bench.json` 中的对象 | 长度（B） | 顺序偏移 | 优化偏移 |
|---|---:|---:|---:|
| ramp | 64 | 0 | 0 |
| middle | 32 | 64 | 16 |
| tail | 16 | 96 | 48 |
| sweep | 32 | 112 | 48 |
| sync | 4 | 144 | 80 |
| empty | 0 | 0 | 0 |

示例故意包含窗口重叠，用来展示条件吻合时的净结果：148 B 变为 84 B，相差 64 B（43.24%）。此处还可以证明 **84 B 是当前接口与对齐约束下的字节池最小值**。令 `D` 为源数据中不同的字节值个数，`L` 为最长对象长度，`g` 为所有非空对象的对齐值和长度的最大公约数。每个对象的起点和长度都是 `g` 的倍数，所以最后一个已占用字节的末端 `E`（不含端点）也是。`[0,E)` 要容下全部 `D` 种字节（抽屉原理）和最长对象，故任何合法布局至少需要 `ceil(max(D,L)/g)*g` 字节；尾部额外填充不会减小它。本例 `D=83, L=64, g=4`，下界是 84 B，恰有已核对的 84 B 布局。全空时下界为 0，报告约定 `g=1`。

`stats.json` 的 `pool_lower_bound_bytes` 给出下界。`pool_optimal: true` 表示候选达到它；`false` 只表示这个界未证明最优，不能据此断言存在更小解。本例另有独立 Lean 实例证明：`formal/PoolExample.lean` 中的 `demo_lower84` 给出下界，`demo_attains84` 核验可达到的布局。这里没有证明整份固件最小；Flash、ELF、UF2 尺寸分别受段对齐、移除未用段和封装影响。

## 信任边界和实验入口

`propose.py` 只提议。`checker.py` 不导入提议器；从源表达式的已知参数替换结果开始，按 `(rule, path, after)` 逐步重建目标树，最后与声明目标比较。路径每一层是从 0 起的操作数编号，不包含操作符。已列出的规则还包括常量折叠、单位元、零化、同项、双取反和加减抵消。`restore(x,scratch)` 演示的是纯模加减的抵消；**没有实现借用物理寄存器的分配器**。

提议不合格时生成器保留原表达式／原布局并记入 `stats.json`，外部交来的错误证书由 `verify` 直接拒绝。证书和逐字节 C 回放都不能自己证明 C 到机器码的每一步。`formal/` 给出独立 Lean 模型与核验方式；C 降低、SDK、汇编、链接、启动、实际硬件均有各自的验证层级。二进制固件和实测结果按本项目的硬件／基准报告查阅。

`tests/test_compiler.py` 包括定值与组合表达式、坏证书、文件篡改、未知效果拒绝和回退注入；`tests/test_compiler_host.c` 是独立的本机 C 判定程序。两个 C 版本分别编译、无 LTO，再与 C 的数学定义交叉核对。双道测试遍历每道全部 65,536 个位型，但不声称已经用测试遍历全部 `2³²` 个输入。测试收据在 `dist/evidence/compiler.json`；运行命令见本页开头。

从项目根目录执行 `python -m unittest discover -s tests -v` 可独立运行编译器、项目驱动、UF2 和离线 USB 测试，既不会枚举板卡也不会改动 Flash。生成主机 C 差分的 PASS 收据还需要 PATH 上的主机 GCC/Clang（或 `RP2CC_HOST_CC`）；收据生成器若没有完成主机 C 的实际执行就拒绝写出 PASS。工程测试先走真实的前端、Python 证书与目标选择，再隔离 SDK/Lean 调用；上面另给了实际 Lean + SDK/UF2 构建命令，二者不要混为一项。

v1 JSON 源上限 4 MiB；Python 证书回放上限 64 MiB，独立 Lean 回放入口上限 12 MiB。大型模块应拆分，不能把资源上限导致的拒绝当作证明通过。工程清单也拒绝重复字段、未知目标、无效 SDK 库名，以及会覆盖工程输入的输出路径。

研究入口：[研究线索与实现对照](research-map.md) · [来源原文与页码](../references/README.md)。这里重新定义并检查了 32 位机器模型；没有把原稿中的任意精度、复数域或渐近界拿来当芯片实测结果。
