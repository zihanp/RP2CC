# 通过 USB CDC 收集 SRAM 基准结果

此通道用于把 RAM 基准的结果交给主机，在定时测量结束、重新允许中断之后才发送。`tools/usb_capture.py` 只处理串口与邮箱校验，不负责装载镜像、操作 Flash 或复位板卡。主机需安装 `pyserial`。

Pico SDK 2.3.1 的 `src/rp2_common/pico_stdio_usb/stdio_usb_descriptors.c` 指定 RP2350 默认 CDC 为 `2E8A:0009`；同一文件中的 `2E8A:000A` 对应 RP2040。SDK 的 `MODULE.bazel` 把 TinyUSB 固定到提交 `86ad6e56c1700e85f1c5678607a762cfe3aa2f47`，来源与 `.gitmodules` 均为 `hathach/tinyusb`。固件构建器的选项为 `--usb-stdio`，默认构建不会启用此通道。

Python 接口：在启动 **本次** RAM 固件前记录端口，启动后调用捕获函数。若明确知道目标板的应用串口序列号或路径，也可传 `serial_number=` 或 `port=`；即使指定路径，仍须匹配 VID/PID。若有多台候选板则中止，不会轮流打开未知串口。

```python
from tools.usb_capture import ports_snapshot, capture

before = ports_snapshot()           # 只列出操作系统端口，不打开任何串口
# 在此处由基准驱动装载并启动已验证的 SRAM 镜像
raw = capture(nonce, before=before, expected_length=3672, timeout=30)
```

发送方格式是 ASCII `RP2LAB1 <8位十六进制nonce> <原始邮箱逐字节十六进制>`，以换行结束。收集器允许 USB 分段、无关日志和旧 nonce，限定最大行长。只有以下检查全部通过，才回送一个 ASCII `K`：外层 nonce、邮箱小端 magic/version/nonce、内外长度、按 32 位字计算的 FNV 校验。校验的目的是检测误配或传输错误，不是加密认证。端口用 115200 打开并设置 DTR，不使用 1200-baud 复位。

离线测试不需要设备，也不会枚举或打开物理串口：

```powershell
python -m unittest discover -s tests -p 'test_usb_capture.py' -v
```
