import itertools
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import usb_capture as c


def mailbox(nonce):
    words=[c.MAGIC,1,nonce]+[i*91 for i in range(3,18)]
    words[14]=len(words)
    h=2166136261
    for i,w in enumerate(words):
        if i not in (0,15): h=((h^w)*16777619)&0xffffffff
    words[15]=h
    return struct.pack('<18I',*words)


def frame(n, data):
    return b'RP2LAB1 %08x '%n+data.hex().upper().encode('ascii')+b'\r\n'


def device(s, vid=c.SDK_VID, pid=c.SDK_RP2350_PID, serial='abc'):
    return SimpleNamespace(device=s,vid=vid,pid=pid,serial_number=serial)


class Fake:
    def __init__(self,chunks): self.chunks=list(chunks); self.written=[]; self.closed=False
    @property
    def in_waiting(self): return len(self.chunks[0]) if self.chunks else 0
    def read(self,n):
        if not self.chunks: return b''
        v=self.chunks[0][:n]; self.chunks[0]=self.chunks[0][n:]
        if not self.chunks[0]: self.chunks.pop(0)
        return v
    def write(self,v): self.written.append(v); return len(v)
    def close(self): self.closed=True


class CaptureTests(unittest.TestCase):
    def test_frame_integrity(self):
        n=0x9efa20bd; raw=mailbox(n)
        self.assertEqual(c.decode_frame(frame(n,raw),'0x9efa20bd',len(raw)),raw)
        self.assertEqual(c.decode_frame(frame(n,raw),n^1),None)
        self.assertEqual(c.decode_frame(b'other CRT banner',n),None)
        for idx in (0,4,8,16,56,60,len(raw)-1):
            damaged=bytearray(raw); damaged[idx]^=1
            with self.assertRaises(ValueError): c.decode_frame(frame(n,damaged),n)
        with self.assertRaises(ValueError): c.decode_frame(frame(n,raw),n,len(raw)+4)
        with self.assertRaises(ValueError): c.decode_frame(b'RP2LAB1 9efa20bd '+b'ab c'*40,n)

    def test_new_port_only_and_explicit_identity(self):
        devs=[device('COM5'),device('COM6',pid=10),device('COM7',vid=0x1234),device('COM8',serial='new')]
        self.assertEqual(c._choose(devs,{'com5'},c.SDK_VID,9,None,None),'COM8')
        self.assertEqual(c._choose(devs,{'com5'},c.SDK_VID,9,'ABC','com5'),'COM5')
        self.assertEqual(c._choose(devs,set(),c.SDK_VID,9,None,'COM6'),None)
        with self.assertRaises(c.CaptureError): c._choose(devs,set(),c.SDK_VID,9,None,None)

    def test_fragmentation_noise_bad_frame_then_ack(self):
        n=31; raw=mailbox(n); corrupt=bytearray(raw); corrupt[-1]^=1
        whole=(b'a'*620+b'\nstray banner\n'+frame(n+1,raw)+frame(n,corrupt)+frame(n,raw))
        sizes=itertools.cycle((1,13,37,3)); chunks=[]
        while whole:
            k=next(sizes); chunks.append(whole[:k]); whole=whole[k:]
        link=Fake(chunks)
        with patch.object(c,'_list_ports',return_value=[device('COM8')]),patch.object(c,'_open_port',return_value=link) as opened:
            self.assertEqual(c.capture(n,timeout=.5,before={'COM5'},expected_length=len(raw)),raw)
        opened.assert_called_once_with('COM8'); self.assertEqual(link.written,[b'K']); self.assertTrue(link.closed)

    def test_broken_frame_is_never_acknowledged(self):
        n=0xaceb; raw=bytearray(mailbox(n)); raw[60]^=1; link=Fake([frame(n,raw)])
        with patch.object(c,'_list_ports',return_value=[device('COM8')]),patch.object(c,'_open_port',return_value=link):
            with self.assertRaisesRegex(TimeoutError,'checksum'): c.capture(n,timeout=.01,before=set())
        self.assertEqual(link.written,[]); self.assertTrue(link.closed)

    def test_old_port_not_opened_and_no_ack_mode(self):
        n=3; raw=mailbox(n); link=Fake([frame(n,raw)])
        with patch.object(c,'_list_ports',return_value=[device('COM8')]),patch.object(c,'_open_port',return_value=link) as op:
            with self.assertRaises(TimeoutError): c.capture(n,timeout=.005,before={'COM8'})
            op.assert_not_called()
            self.assertEqual(c.capture(n,timeout=.5,serial_number='AbC',acknowledge=False),raw)
        self.assertEqual(link.written,[])
