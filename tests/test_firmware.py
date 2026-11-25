"""File-only preflight: malformed payloads must never pass the delivery checker."""
import struct
import unittest

from tools.build_firmware import FAMILY, PROFILES, compare_elf_uf2, inspect_uf2

SIZE = 2 * 1024 * 1024
def block(addr, *, fam=FAMILY["arm"], flags=0x2000, n=256, num=0, count=1, marker=False):
    b = bytearray(512)
    struct.pack_into("<8I",b,0,0x0a324655,0x9e5d5157,flags,addr,n,num,count,fam)
    if marker:
        b[32:288]=b"\xef"*256; struct.pack_into("<I",b,288,0x9957e304)
    struct.pack_into("<I",b,508,0x0ab16f30)
    return bytes(b)

def e10():
    return block(0x10000000+SIZE-256,fam=0xe48bff57,flags=0xa000,count=2,marker=True)

class TestUF2(unittest.TestCase):
    def test_good_ram(self):
        img=block(0x20000000,count=2)+block(0x20081f00,num=1,count=2)
        self.assertEqual(inspect_uf2(img,"arm",True,SIZE)["end_exclusive"],"0x20082000")

    def test_good_flash(self):
        for arch in ("arm","riscv"):
            img=e10()+block(0x10000000,fam=FAMILY[arch])
            self.assertEqual(inspect_uf2(img,arch,False,SIZE)["e10_marker"],"0x101fff00")

    def test_invalid_ram(self):
        valid=block(0x20000000)
        for img in [b"",valid[:-1],valid[:-4]+bytes(4),valid[:2]+b"x"+valid[3:],
                    e10()+valid,block(0x10000000),block(0x20082000),block(0x20000001),
                    block(0x20000000,fam=FAMILY["riscv"]),block(0x20000000,n=512),
                    block(0x20000000,count=2),block(0x20000000,flags=0x2001),
                    block(0x20000000,count=2)+block(0x20000000,num=1,count=2),
                    block(0x20000000,count=2)+block(0x20000100,num=0,count=2)]:
            with self.subTest(img=img[:32].hex()):
                with self.assertRaises(ValueError): inspect_uf2(img,"arm",True,SIZE)

    def test_invalid_flash(self):
        page=block(0x10000000)
        for img in [page, e10(), page+e10(), e10()+e10()+page,
                    e10()[:40]+b"x"+e10()[41:]+page,
                    e10()+block(0x10000000,count=2)+block(0x101ff000,num=1,count=2),
                    e10()+block(0x20000000)]:
            with self.subTest(img=img[:32].hex()):
                with self.assertRaises(ValueError): inspect_uf2(img,"arm",False,SIZE)
        with self.assertRaises(ValueError): inspect_uf2(e10()+page,"arm",False,4*1024*1024)

    def test_profiles(self):
        self.assertEqual(PROFILES["rp2354a"][1],SIZE)
        self.assertEqual(PROFILES["hw973b"][1],16*1024*1024)

    def test_elf_payload_and_isa(self):
        ident=b"\x7fELF\x01\x01\x01"+bytes(9)
        base=0x20000000
        h=struct.pack("<16sHHIIIIIHHHHHH",ident,2,40,1,base+1,52,0,0,52,32,1,0,0,0)
        elf=h+struct.pack("<8I",1,84,base,base,4,12,5,4)+b"abcd"
        image=bytearray(block(base)); image[32:36]=b"abcd"
        self.assertEqual(compare_elf_uf2(elf,image,"arm",True,SIZE)["elf_load_bytes_compared"],4)
        with self.assertRaises(ValueError): compare_elf_uf2(elf,image,"riscv",True,SIZE)
        for off in [32,40]:
            bad=bytearray(image); bad[off]^=1
            with self.assertRaises(ValueError): compare_elf_uf2(elf,bad,"arm",True,SIZE)

if __name__ == "__main__": unittest.main()
