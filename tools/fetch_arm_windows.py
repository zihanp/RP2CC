"""Fetch the pinned Windows GNU Arm bundle, verifying Arm's published SHA-256.

If your existing installation provides arm-none-eabi-gcc, this is unnecessary.
No PATH, registry, SDK or board state is modified. Python 3.10+ and curl needed.
"""
import argparse
import concurrent.futures
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

NAME = "arm-gnu-toolchain-14.3.rel1-mingw-w64-x86_64-arm-none-eabi.zip"
URL = "https://armkeil.blob.core.windows.net/developer/Files/downloads/gnu/14.3.rel1/binrel/" + NAME
SHA256 = "864c0c8815857d68a1bbba2e5e2782255bb922845c71c97636004a3d74f60986"
SIZE = 290006806
DEPS = Path(__file__).resolve().parents[1] / ".deps"


def install(name, url, sha256, size, label, default_deps, desc):
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--deps", type=Path, default=default_deps)
    ap.add_argument("--jobs", type=int, default=12, choices=range(1, 25), metavar="1..24")
    ap.add_argument("--archive", type=Path, help="Use a pre-downloaded ZIP with the pinned digest")
    args = ap.parse_args()
    deps = args.deps.resolve()
    deps.mkdir(parents=True, exist_ok=True)
    archive = args.archive or (deps / name)
    if not archive.exists():
        if args.archive:
            ap.error("specified archive does not exist")
        curl = shutil.which("curl")
        if curl is None:
            ap.error("curl is required to fetch the archive")
        if url.startswith("https://github.com/"):
            # Resolve once to avoid issuing one GitHub redirect per 4 MiB block.
            import os
            url = subprocess.check_output([curl, "-fsSL", "--retry", "2", "--max-time", "30",
                "--range", "0-0", "-o", os.devnull, "-w", "%{url_effective}", url], text=True)
        parts_dir = deps / (label+"-download-parts")
        parts_dir.mkdir(exist_ok=True)
        chunk = 4 * 1024 * 1024
        def part(i):
            start, stop = i * chunk, min(size, (i + 1) * chunk) - 1
            dest = parts_dir / f"{i:03d}.part"
            if not dest.is_file() or dest.stat().st_size != stop - start + 1:
                result = subprocess.run([curl, "--fail", "--silent", "--show-error", "--location",
                    "--retry", "3", "--connect-timeout", "12", "--max-time", "190",
                    "--range", f"{start}-{stop}", url, "--output", str(dest)])
                if result.returncode: raise RuntimeError(f"HTTP range {i} failed: curl {result.returncode}")
            if dest.stat().st_size != stop - start + 1:
                raise ValueError("invalid length of HTTP range " + str(i))
            return dest
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
            parts = list(ex.map(part, range((size + chunk - 1) // chunk)))
        with tempfile.NamedTemporaryFile(dir=deps, suffix=".zip", delete=False) as out:
            temp = Path(out.name)
            for p in parts:
                with p.open("rb") as src: shutil.copyfileobj(src, out, length=1024*1024)
        if digest(temp) != sha256:
            raise ValueError("download digest mismatch; retained diagnostic ZIP at " + str(temp))
        temp.replace(archive)
    if digest(archive) != sha256:
        raise ValueError("archive digest mismatch")
    with zipfile.ZipFile(archive) as z:
        # The pinned official archive uses a top-level versioned directory.
        for name in z.namelist():
            if not (deps / name).resolve().is_relative_to(deps):
                raise ValueError("invalid member path")
        z.extractall(deps)
    print("Verified and extracted:", archive)


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(2**20), b""): h.update(b)
    return h.hexdigest()


if __name__ == "__main__":
    install(NAME,URL,SHA256,SIZE,"arm",DEPS,__doc__)
