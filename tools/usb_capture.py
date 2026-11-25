"""Capture one nonce-bound RP2LAB1 mailbox from the SDK's RP2350 USB CDC.

Enumerate COM ports before starting a RAM run: before = ports_snapshot(). Then
capture(nonce, before=before, expected_length=nbytes). No reset/boot commands.
Only a verified frame receives ASCII K. pyserial is loaded lazily.
"""

import argparse
import math
from pathlib import Path
import re
import struct
import sys
import time

SDK_VID, SDK_RP2350_PID = 0x2e8a, 0x0009
MAX_MAILBOX = 128 * 1024
MAGIC, VERSION = 0x31423252, 1
HEX = re.compile(rb'[0-9A-Fa-f]+\Z')


class CaptureError(RuntimeError):
    pass


def _nonce(value):
    if isinstance(value, str):
        if not re.fullmatch(r'(?:0[xX])?[0-9A-Fa-f]{1,8}', value): raise ValueError('nonce must be 1..8 hex digits')
        value = int(value,16)
    if type(value) is not int or not 0 <= value <= 0xffffffff: raise ValueError('nonce must be uint32')
    return value


def _list_ports():
    try: from serial.tools import list_ports
    except ImportError as e: raise CaptureError('pyserial is required for physical USB CDC capture') from e
    return list(list_ports.comports())


def ports_snapshot():
    """Read only the OS port list, without opening any port. Take before SRAM launch."""
    return frozenset(p.device for p in _list_ports())


def _open_port(path):
    try: import serial
    except ImportError as e: raise CaptureError('pyserial is required for physical USB CDC capture') from e
    # 115200 avoids the conventional 1200-baud bootloader trigger. DTR is how the
    # SDK notices a CDC host; disable hardware flow control and do not assert RTS.
    link = serial.Serial(port=None, baudrate=115200, timeout=0.10, write_timeout=0.4, rtscts=False, dsrdtr=False)
    link.dtr, link.rts, link.port = True, False, path
    link.open()
    return link


def decode_frame(line, nonce, expected_length=None):
    """None = unrelated traffic. Invalid matching frames raise without acknowledgment."""
    nonce = _nonce(nonce)
    fields = line.rstrip(b'\r\n').split(b' ',2)
    if len(fields) != 3 or fields[0] != b'RP2LAB1' or fields[1].lower() != f'{nonce:08x}'.encode('ascii'):
        return None
    data_hex = fields[2]
    if len(data_hex) < 128 or len(data_hex) > 2*MAX_MAILBOX or len(data_hex) % 8:
        raise ValueError('bad mailbox frame length')
    if expected_length is not None and len(data_hex) != 2*expected_length: raise ValueError('unexpected mailbox byte count')
    if not HEX.fullmatch(data_hex): raise ValueError('invalid mailbox hex')
    raw = bytes.fromhex(data_hex.decode('ascii'))
    words = tuple(x[0] for x in struct.iter_unpack('<I',raw))
    if (words[0], words[1], words[2]) != (MAGIC,VERSION,nonce): raise ValueError('mailbox magic/version/nonce mismatch')
    if words[14] != len(words): raise ValueError('mailbox internal word count mismatch')
    h = 2166136261
    for i,w in enumerate(words[1:],1):
        if i != 15: h = ((h ^ w) * 16777619) & 0xffffffff
    if h != words[15]: raise ValueError('mailbox checksum mismatch')
    return raw


class _Lines:
    """Incrementally recover whole lines without buffering unlimited banners/noise."""
    def __init__(self, limit):
        self.buf, self.discard, self.limit = bytearray(), False, limit

    def feed(self, chunk):
        parts = chunk.split(b'\n')
        for i,part in enumerate(parts):
            if not self.discard:
                if len(self.buf)+len(part) > self.limit: self.buf.clear(); self.discard=True
                else: self.buf.extend(part)
            if i != len(parts)-1:
                if not self.discard: yield bytes(self.buf)
                self.buf.clear(); self.discard=False


def _choose(devices, previous, vid, pid, serial_number, port):
    found = []
    for p in devices:
        if (p.vid,p.pid) != (vid,pid): continue
        path = p.device.casefold()
        if port is not None and path != port.casefold(): continue
        if serial_number is not None and (p.serial_number or '').casefold() != serial_number.casefold(): continue
        if port is None and serial_number is None and path in previous: continue
        found.append(p.device)
    if len(found) > 1: raise CaptureError('multiple matching CDC ports; specify the board serial number or exact COM path')
    return found[0] if found else None


def capture(nonce, timeout=30, *, before=None, serial_number=None, port=None,
            pid=SDK_RP2350_PID, vid=SDK_VID, expected_length=None, acknowledge=True):
    """Return validated mailbox bytes. Never opens a nonmatching VID/PID.

    Without explicit port/serial only a device absent from `before` may be opened.
    When before=None, take a snapshot at call entry (pre-start the listener).
    """
    nonce = _nonce(nonce)
    if not math.isfinite(timeout) or timeout <= 0: raise ValueError('timeout must be positive')
    if expected_length is not None and (type(expected_length) is not int or not 64 <= expected_length <= MAX_MAILBOX or expected_length % 4):
        raise ValueError('expected_length must be a multiple of four in 64..131072')
    if before is None: before = ports_snapshot() if serial_number is None and port is None else ()
    previous = {str(n).casefold() for n in before}
    deadline = time.monotonic()+timeout
    status = 'no matching newly appeared SDK CDC port'
    while time.monotonic() < deadline:
        path = _choose(_list_ports(),previous,vid,pid,serial_number,port)
        if path:
            link = None
            try:
                link = _open_port(path)
                status = f'{path} open, no valid frame'
                lines = _Lines(2*(expected_length or MAX_MAILBOX)+40)
                while time.monotonic() < deadline:
                    chunk = link.read(max(1,min(link.in_waiting,16384)))
                    for line in lines.feed(chunk):
                        try: raw = decode_frame(line,nonce,expected_length)
                        except ValueError as e: status=f'{path}: {e}'; continue
                        if raw is None: continue
                        if acknowledge:
                            try:
                                if link.write(b'K') != 1: raise OSError('short ACK write')
                            except OSError as e:
                                print(f'usb_capture: verified frame; ACK unavailable: {e}', file=sys.stderr)
                        return raw
            except OSError as e: status=f'{path}: {e}'  # port can briefly exist before the driver is ready
            finally:
                if link is not None:
                    try: link.close()
                    except OSError: pass
        time.sleep(max(0,min(0.07,deadline-time.monotonic())))
    raise TimeoutError(f'CDC capture timed out ({status}); no unverified frame acknowledged')


def main(argv=None):
    ap = argparse.ArgumentParser(description='Capture only an RP2350 SDK CDC mailbox with the requested nonce.')
    ap.add_argument('--nonce', required=True, help='hex nonce; leading 0x is optional')
    ap.add_argument('--out',type=Path,required=True,help='new binary result file')
    ap.add_argument('--timeout',type=float,default=30); ap.add_argument('--expected-length',type=int)
    ap.add_argument('--port'); ap.add_argument('--serial-number'); ap.add_argument('--no-ack',action='store_true')
    ns=ap.parse_args(argv)
    try:
        if ns.out.exists(): raise ValueError('output already exists; choose a new capture filename')
        raw = capture(ns.nonce,ns.timeout,port=ns.port,serial_number=ns.serial_number,
                      expected_length=ns.expected_length,acknowledge=not ns.no_ack)
        ns.out.parent.mkdir(parents=True,exist_ok=True)
        with ns.out.open('xb') as f: f.write(raw)
        print(f'captured {len(raw)} bytes; mailbox checksum and nonce verified')
        return 0
    except (CaptureError,OSError,ValueError) as e:
        print(f'usb_capture: {e}',file=sys.stderr); return 2


if __name__ == '__main__': raise SystemExit(main())
