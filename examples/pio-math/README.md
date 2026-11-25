# 数学内核 → PIO FIFO：五组输入的最小工程

本例不使用外部引脚。两段不同的程序通过片内 FIFO 串在一起：

1. [pairs.rpm](pairs.rpm) 让 CPU 将一个字里的两个有符号 i16 各自向下除以 16。编译器生成 `rp_scale_pair`。
2. [project.json](project.json) 的 `"pio": ["fifo_invert.pio"]` 让 SDK 调用 pioasm。[三条 PIO 指令](fifo_invert.pio)从 TX 取字、对 32 位逐位取反、将结果送回 RX。
3. [main.c](main.c) 申请一个空闲状态机，调用数学函数并将结果写入 TX，用与实现无关的手算常量分别核对 CPU 结果和 RX 回包。例如输入 `0x8000ffff` 表示 (−32768, −1)，CPU 应返回 `0xf800ffff`，PIO 应返回 `0x07ff0000`。

从项目根目录执行。以下只生成本地文件，不访问 USB：

```powershell
python project.py examples/pio-math/project.json --ram --kernel-instances --out work/pio-math
```

随附目标为 **RP2354A / Hazard3 的纯 SRAM ELF、UF2**；文件在 `dist/pio-math-project/firmware/`。换成 Cortex-M33 可追加 `--arch arm`，并使用新的输出目录。项目已通过数学核验与 SDK 交叉编译；**本示例没有上板运行**。

可选复核（需要 `pyelftools`）：`python examples/pio-math/check_image.py dist/pio-math-project` 直接读最终 ELF，核对 14 字节的数学函数、PIO 的三个机器字 `80a0 a0cf 8020`、其描述符未使用 GPIO，以及编译进程序的五组期望常量；不需要插板。

若以后在调试器中运行，查看 `pio_math_status`：`state=2, completed=5` 才表示全部通过；`state=1` 表示还在运行。错误码 3–8 依次代表无可用 PIO 资源、初始化失败、CPU 值错误、TX 满、RX 超时、RX 值错误。`input/cpu_word/rx_word` 记录最近进度。每次最多等待 100,000 次 RX 轮询（不是微秒）；无论成功或失败，已申请的状态机和程序均释放。PIO 自身可以停在 `pull block` 等待，但不会让 CPU 无期限等待。
