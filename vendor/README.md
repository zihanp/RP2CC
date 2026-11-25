# 固定的 Pico SDK 源码

`pico-sdk.zip` 是未修改的 Raspberry Pi Pico SDK 2.3.1 源码归档（提交 `079c6f39023649b154152db30f1d781e884879bc`），归档内保留上游和第三方许可。

- SHA-256：`489f9b3efe7a9eefb66f839da124d4f75df9fc523d80065413840f3edd279633`。
- `python tools/prepare_sdk.py` 在校验后解压到 `.deps/`。带 USB 的构建才需要 `--usb`，它下载 SDK 固定的 TinyUSB 提交并验证哈希；离线可把同一压缩包放进 `.deps/tinyusb-sdk-pinned.zip`。

当前目录没有本地构建缓存，也不包含编译器、Lean 或 USB 记录。
