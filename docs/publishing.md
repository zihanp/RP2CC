# 发布清单

仓库根的 [`LICENSE`](../LICENSE) 是 MIT，适用于 RP2CC 自有的代码、Lean 源码、示例、脚本和文档。随包论文继续适用上游 [Apache-2.0](../references/papers/LICENSE)；SDK、固件中链接的第三方组件和赞助图片不因根许可证改授权。各部分的归属见[第三方资料与依赖](../references/third-party.md)。

## 发布前检查

在解压的根目录操作：

```sh
python tools/verify_release.py
python tools/verify_study.py
python -m unittest discover -s tests
git init -b main
git add -- .
git status --short
git diff --cached --stat
```

这组 Git 命令用于解压后的源码包；如果是从 GitHub 克隆的仓库，不需要再初始化。换 Markdown 或证据文件后，旧 `MANIFEST.sha256` 仍代表本次交付快照，不会自动更新。Git 属性保留文件字节，避免换行转换导致证书与收据哈希失配。

- `.deps/`、`.build/`、`work/` 是本地工作目录，不提交；构建的 map、日志和原始带调试信息的 ELF 也不应随手加入。提交新 ELF 前检查其调试节和构建路径。
- 公开包只保留与实验有关的观测、原始周期回传和可复核的镜像，不附主机路径、账户、采集时刻、设备枚举日志、工具缓存或本地 Git 配置。
- [赞助图](assets/wechat-sponsor.png)是维护者主动提供的微信收款码，用于本项目自愿赞助；图片仅保留原码及留白，未附付款页昵称。公开后它可被任何看到页面的人扫码。可以删掉 README 的图和报告末页后再公开。

## 网页反向核验

- **仓库许可**：根文件的正文对照 [GitHub 的 MIT 模板](https://choosealicense.com/licenses/mit/)，短标识为 [SPDX `MIT`](https://spdx.org/licenses/MIT.html)。[GitHub 的许可证检测说明](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository#detecting-a-license)建议将第三方许可范围另列，保持根文件正文简明。
- **芯片与烧录**：[Raspberry Pi 数据手册](https://datasheets.raspberrypi.com/rp2350/rp2350-datasheet.pdf)列出 150 MHz 的 Cortex-M33 / Hazard3、RP2354A 的 QFN60 封装和封装内 2 MB Flash；[芯片文档](https://www.raspberrypi.com/documentation/microcontrollers/microcontroller-chips.html#RP2350_features)列出 3 个 PIO 模块、每个 4 台状态机。[BOOTSEL 文档](https://www.raspberrypi.com/documentation/microcontrollers/pico-series.html#debug-using-a-pico-series-device)和 [picotool 文档](https://github.com/raspberrypi/picotool/blob/2.3.1/README.md#info)分别说明拖拽使用 UF2 和主机工具读取 ELF 的区别。
- **论文原件**：从 [BibTeX](../references/references.bib) 所指的固定提交重新读取六篇 PDF，并从同一提交读取 Apache-2.0 文本，逐字节对照均与随包文件一致。原稿引用位置与项目改写见[参考资料](../references/README.md)。

官方网页核对的是规格和来源。性能、证明和镜像核验分别依靠[实测记录](experiments.md)、[Lean 源码](../formal/)和[离线复核](../tools/verify_study.py)。RP2354A 的镜像已交叉编译和字节核验，尚无该型号的实板结果。
