"""Prepare the supplied offline SDK, pinned optional TinyUSB and host tools locally.

Windows: python tools/prepare_sdk.py --usb --windows-host-tools
No downloads unless a requested pinned component is absent. No device access.
"""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
SDKSHA="489f9b3efe7a9eefb66f839da124d4f75df9fc523d80065413840f3edd279633"
USBREF="86ad6e56c1700e85f1c5678607a762cfe3aa2f47"
USBSHA="3011c90c128988012b553e5d2f0a90bc0b64046591c964bc1f9f6659edcd7e4b"
PICOSHA="68730be0813f8f35be2cca147cf7f1572662d5dcf0f5ba468e02a6dd9e85db2b"

def required_archive(path,expected,url=None):
    if not path.is_file():
        if url is None: raise ValueError("missing supplied SDK ZIP: "+str(path))
        subprocess.run(["curl","-fsSL","--retry","2",url,"-o",str(path)],check=True)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
        raise ValueError("wrong archive SHA-256: "+str(path))

def unpack(archive,dest,strip=0,allowed=None):
    # The verified ZIPs also carry documentation symlinks irrelevant to builds;
    # extract regular files only. Original files in source ZIP remain unchanged.
    dest=dest.resolve()
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            parts=Path(info.filename).parts[strip:]
            if not parts or (allowed and parts[0] not in allowed) or info.is_dir(): continue
            if ((info.external_attr>>16)&0o170000)==0o120000: continue
            out=dest.joinpath(*parts).resolve()
            if not out.is_relative_to(dest): raise ValueError("bad ZIP path")
            out.parent.mkdir(parents=True,exist_ok=True)
            with z.open(info) as src, out.open("wb") as f: shutil.copyfileobj(src,f)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive",type=Path,default=ROOT/'vendor/pico-sdk.zip',
                   help="path to the pinned Pico SDK ZIP, defaults to vendor/pico-sdk.zip")
    p.add_argument("--deps",type=Path,default=ROOT/".deps")
    p.add_argument("--usb",action="store_true")
    p.add_argument("--windows-host-tools",action="store_true",
        help="official Windows picotool and locally compiled SDK pioasm (CMake/Ninja/host C++ required)")
    a=p.parse_args(); deps=a.deps.resolve(); deps.mkdir(exist_ok=True,parents=True)
    sdk=deps/"pico-sdk-master"
    if not (sdk/"pico_sdk_init.cmake").is_file():
        required_archive(a.archive,SDKSHA); unpack(a.archive,sdk,1)
    if a.usb and not (sdk/"lib/tinyusb/src/tusb.c").exists():
        shipped=ROOT/'vendor/tinyusb-sdk-pinned.zip'; z=shipped if shipped.is_file() else deps/"tinyusb-sdk-pinned.zip"; required_archive(z,USBSHA,"https://codeload.github.com/hathach/tinyusb/zip/"+USBREF)
        unpack(z,sdk/"lib/tinyusb",1)
    if a.windows_host_tools:
        if os.name!="nt": p.error("--windows-host-tools requires Windows")
        if not (deps/"picotool-host/picotool/picotool.exe").exists():
            name="picotool-2.3.1-x64-win.zip"; z=deps/name
            required_archive(z,PICOSHA,"https://github.com/raspberrypi/pico-sdk-tools/releases/download/v2.3.1-0/"+name)
            unpack(z,deps/"picotool-host")
        if not (deps/"pioasm-host/bin/pioasm.exe").exists():
            build=deps/"pioasm-build"
            subprocess.run(["cmake","-S",sdk/"tools/pioasm","-B",build,"-G","Ninja",
                "-DCMAKE_BUILD_TYPE=Release","-DPIOASM_VERSION_STRING=2.3.1",
                "-DCMAKE_INSTALL_PREFIX="+(deps/"pioasm-host").as_posix()],check=True)
            subprocess.run(["cmake","--build",build,"--target","install","--parallel","1"],check=True)
    print("SDK ready:",sdk)

if __name__=="__main__": main()
