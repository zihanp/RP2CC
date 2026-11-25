# RP2CC：RP2350 / RP2354A 数学内核编译器

RP2CC 把一小类可精确描述的整数运算编译成 C11，再由 Pico SDK 和 GCC 生成 ELF / UF2。它保留基线 C、优化 C 和可回放的证书；读者可以逐步检查优化做了什么。

在一块 150 MHz 开发板上，Cortex-M33 的 257 步固定递推从 **1,038.05 降到 12.05 周期/调用**（对比同样知道常量的 GCC `-O3` 直接循环）。Hazard3 的双道取整从 **21.06 降到 12.06 周期/调用**；生成的函数是 14 字节，与手工版本逐字节相同。这些是 SRAM、屏蔽中断下的单内核中位数。RP2354A 的 2 MiB 镜像做过交叉编译和字节核验，尚未在这一料号的实板上测试。

[最终研究报告（PPTX）](docs/RP2CC-研究报告.pptx) · [论文、原文和定位](references/README.md) · [实验方法与原始数据](docs/experiments.md)

## 解压后先试

在根目录运行。只需 Python 3.10+，不需插板、装 SDK 或安装 pip 包：

```sh
python tools/verify_release.py
python tools/verify_study.py
python -m rp2cc.ui
```

在浏览器打开终端显示的本地地址（默认 `http://127.0.0.1:8750`），选择示例，点击“编译并对比”。可逐项查看基线 C、优化 C、中间表示和优化证书。网页调用真正的 Python 编译器，不执行用户输入、不连接开发板。按 Ctrl+C 关闭服务。命令行也可以直接复现：

```sh
python tools/math_frontend.py examples/jump.rpm --out work/jump.json
python -m rp2cc emit work/jump.json --mode optimized --out work/kernel
python -m rp2cc verify work/jump.json --cert work/kernel/certificate.json
python -m rp2cc eval work/jump.json affine 0
```

最后一条应输出 `0xe89f545f`。阅读 `work/kernel/kernels.c` 看改写后的表达式。安装根目录 `lean-toolchain` 固定的 Lean **4.33.1** 后，可独立重编定理（不需 mathlib）：

```sh
python formal/check.py --self-test --kernel-instances --report work/lean-recheck.json
```

## 目录从哪里读起

| 路径 | 内容 |
|---|---|
| [rp2cc/](rp2cc/) | Python 的语义、候选变换、独立回放、C 生成和中文工作台 |
| [formal/](formal/) | Lean 4 定义、全输入定理、独立检查器和已有验证收据 |
| [examples/](examples/) · [firmware/](firmware/) | `.rpm` 数学输入、项目清单、板型、C/PIO 接口；[PIO 五组输入示例](examples/pio-math/README.md) |
| [bench/](bench/) · [dist/](dist/) | 实板测试源码、原始回传、链接证据及选板使用的 ELF / UF2 |
| [docs/](docs/) · [references/](references/) | 研究报告、语言与实验说明、六篇上游原始 PDF 和引用链 |

先读[一段程序怎么变成固件](docs/language.md)，再按需要看[板型、外设与工具链](docs/hardware.md)。C/PIO 外设通过 SDK 接入，Lean 的证明只覆盖建模的数学内核和只读值访问。量产还需要在最终硬件与发布固件上验证时限、外设负载、启动与恢复。

## 重建面向硬件的产物

包内附了固定版本的 Pico SDK 源码 ZIP，不附目标编译器。Windows 的首次准备需 CMake、Ninja、主机 C++ 和 curl。有现成 SDK/交叉工具链时也可以按[板型说明](docs/hardware.md)提供本地路径。

```sh
python tools/prepare_sdk.py --windows-host-tools
python tools/fetch_arm_windows.py
python project.py examples/math.project.json --board rp2354a --arch arm --out work/2354a
```

如果需要 USB CDC，准备 SDK 时加 `--usb`；如需 Hazard3，再运行 `python tools/fetch_riscv_windows.py`，构建时指定 `--arch riscv`。这些命令只生成电脑上的文件。选择并写入 UF2 前，请先核对目标板及 Flash 容量；首次观察结果可参考[烧录和 CDC 说明](docs/hardware.md)。

## 准备公开仓库

RP2CC 自有的编译器、Lean 证明、示例、脚本和文档采用 [MIT 许可证](LICENSE)。随包预印本、SDK 等保留各自的[第三方许可](references/third-party.md)；赞助图不在 MIT 授权范围内。

解压后可直接 `git init -b main`，不会带出原工作区的 Git 历史。提交前可按[发布清单](docs/publishing.md)复核文件和上游来源；引用本研究时，可从[六篇预印本到项目实验的对照](references/README.md)定位原文。

## 关于项目和赞助

我做这个项目，是想试试 GPT6.2A（我在项目里用的模型称呼）能在数学推导和嵌入式工程上帮到哪一步。我学嵌入式大约四五年，接触的东西比较杂，还有很多没学深。我会隔一段时间上线，看看问题，有空就回答。

如果这个项目对你有用，欢迎自愿赞助，帮我分担一点 AI API 费用。若你愿意提供较多支持，也可以联系我聊聊具体问题，我会看自己能不能给出解决思路。你也可以在仓库的 Issues 里给我出个小课题：一段算不过来的定点运算、一个 PIO 小实验，都可以。

<img src="docs/assets/wechat-sponsor.png" alt="微信赞助二维码（自愿赞助）" width="240">
