# 第三方资料与依赖

根 [`LICENSE`](../LICENSE) 的 MIT 授权仅覆盖 RP2CC 自有部分。下表列出的材料保持原有归属，其中第三方组件即使被链接进随包固件，也不改为 MIT。

| 内容 | 上游与固定版本 | 随包内容 |
|---|---|---|
| OpenAI Math Release | [固定快照与上游许可](https://github.com/openai/math/blob/3344da59991ed94f9ba711217b29c1d760badfa6/LICENSE)，Apache-2.0 | [六篇原始 PDF](README.md)、[上游许可证](papers/LICENSE)与引用。不含论文验证脚本。 |
| Raspberry Pi Pico SDK | [2.3.1 源码提交](https://github.com/raspberrypi/pico-sdk/tree/079c6f39023649b154152db30f1d781e884879bc) | 原版源码 ZIP 随包。部分代码链接入示例固件；附 [SDK](licenses/pico-sdk.txt)、[pico_printf](licenses/pico-printf.txt) 和 [CMSIS 头文件](licenses/cmsis-stub.txt) 的上游通知。 |
| TinyUSB | [SDK 指定提交](https://github.com/hathach/tinyusb/tree/86ad6e56c1700e85f1c5678607a762cfe3aa2f47) | 仅用于启用 USB 的镜像；附 [上游许可证](licenses/tinyusb.txt)。 |
| 目标 GCC 与工具链运行库 | [Arm GNU 14.3.Rel1](https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads)；[Raspberry Pi Hazard3 工具包](https://github.com/raspberrypi/pico-sdk-tools/releases/tag/v2.3.1-0) | 发布包不含完整编译器、工具链或 SDK 缓存。构建时链接到的组件保留各自许可；版本与下载来源见 ``tools/fetch_arm_windows.py``、``tools/fetch_riscv_windows.py``。 |
| Lean 4 | [Lean 4.33.1](https://github.com/leanprover/lean4/releases/tag/v4.33.1) | 用于重编和核验项目证明，不打包 Lean 二进制。 |
| 微信赞助二维码 | 项目维护者提供 | 目的限于显示本项目的自愿赞助入口。不是软件许可证的一部分。 |

未公开厂商图纸、板卡手册图片、本地路径或个人工作目录。上游来源的核验方法见[发布清单](../docs/publishing.md)。
