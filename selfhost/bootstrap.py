#!/usr/bin/env python3
"""The self-hosting proof: `python selfhost/bootstrap.py` (PLAN.md, section 3).

From a clean directory:

    C0  the Python compiler's C for selfhost/mortc_main.mx
    B1  zig cc -std=c11 -O2 C0        (stage-1)
    C1  B1 compiling mortc_main.mx
    B2  zig cc -std=c11 -O2 C1        (stage-2)
    C2  B2 compiling mortc_main.mx

and then requires C1 == C2 (the fixpoint) and C0 == C1, byte for byte. Every step
is a real process; a stage's C only ever comes from executing the stage before it.

Each process runs under a memory limit (Windows job objects): --memory-mb for
Python and the stage binaries, and --cc-memory-mb for the C compiler, which needs
more the first time it builds zig's libc into its cache. On POSIX only the stage
binaries are limited, because an address-space limit breaks the C compiler long
before it would use the memory.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
COMPILER_ROOT = HERE / "mortc_main.mx"
MARKER = ".mort-bootstrap"

C0_SCRIPT = """
import sys
sys.path.insert(0, sys.argv[1])
import mortc
text = mortc.compile_files_to_c([sys.argv[2]])
assert "\\r" not in text, "generated C holds a carriage return"
with open(sys.argv[3], "wb") as handle:
    handle.write(text.encode("utf-8"))
"""


class StageMismatch(Exception):
    """Two stages that must be byte-identical are not."""


class StageFailed(Exception):
    """A stage's process failed."""


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def assert_identical(path_a, path_b, name_a, name_b):
    """Raise StageMismatch naming the first differing byte, or return its size."""
    a = Path(path_a).read_bytes()
    b = Path(path_b).read_bytes()
    if a == b:
        return len(a)
    limit = min(len(a), len(b))
    offset = next((i for i in range(limit) if a[i] != b[i]), limit)
    line = a.count(b"\n", 0, offset) + 1
    def show(data):
        return f"0x{data[offset]:02x}" if offset < len(data) else "end of file"
    start = max(0, a.rfind(b"\n", 0, offset) + 1)
    context_a = a[start:start + 160].split(b"\n", 1)[0].decode("utf-8", "replace")
    context_b = b[start:start + 160].split(b"\n", 1)[0].decode("utf-8", "replace")
    raise StageMismatch(
        f"{name_a} and {name_b} differ: first differing byte {offset}, line {line}"
        f" ({name_a}: {show(a)}, {name_b}: {show(b)}; sizes {len(a)} and {len(b)})\n"
        f"  {name_a}: {context_a}\n  {name_b}: {context_b}")


def _windows_guarded(command, cwd, env, limit, timeout):
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("Basic", BasicLimits),
            ("Io", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise StageFailed(f"CreateJobObject failed: {ctypes.get_last_error()}")
    info = ExtendedLimits()
    info.Basic.LimitFlags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
    info.ProcessMemoryLimit = limit
    if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        raise StageFailed(f"SetInformationJobObject failed: {ctypes.get_last_error()}")
    process = subprocess.Popen(
        command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        if not kernel32.AssignProcessToJobObject(job, int(process._handle)):
            raise StageFailed(f"AssignProcessToJobObject failed: {ctypes.get_last_error()}")
        stdout, stderr = process.communicate(timeout=timeout)
        query = ExtendedLimits()
        if not kernel32.QueryInformationJobObject(
                job, 9, ctypes.byref(query), ctypes.sizeof(query), None):
            raise StageFailed(f"QueryInformationJobObject failed: {ctypes.get_last_error()}")
        peak = query.PeakProcessMemoryUsed
    finally:
        process.kill()
        kernel32.CloseHandle(job)
    return process.returncode, stdout, stderr, peak


def _posix_run(command, cwd, env, limit, timeout):
    import resource

    def apply_limit():
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

    before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    process = subprocess.Popen(
        command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        preexec_fn=apply_limit if limit else None)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    finally:
        process.kill()
    after = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    # ru_maxrss is a high-water mark across children, in KiB on Linux.
    peak = max(after, before) * 1024
    return process.returncode, stdout, stderr, peak


def run_step(name, command, cwd, env, limit, timeout):
    """Run one stage process; returns (stdout, peak bytes). Raises StageFailed."""
    if os.name == "nt":
        code, stdout, stderr, peak = _windows_guarded(command, cwd, env, limit, timeout)
    else:
        guarded = name in ("stage-1", "stage-2")
        code, stdout, stderr, peak = _posix_run(
            command, cwd, env, limit if guarded else None, timeout)
    if code != 0:
        raise StageFailed(
            f"{name} failed with exit code {code}\ncommand: {command}\n"
            f"stdout:\n{stdout.decode('utf-8', 'replace')}\n"
            f"stderr:\n{stderr.decode('utf-8', 'replace')}")
    return stdout, peak


def prepare_work_dir(work):
    """A clean directory, without ever removing something this script did not make."""
    if work.exists():
        if any(work.iterdir()) and not (work / MARKER).exists():
            raise StageFailed(
                f"{work} exists, is not empty and was not made by bootstrap.py; refusing to clear it")
        shutil.rmtree(work)
    work.mkdir(parents=True)
    (work / MARKER).write_text("made by selfhost/bootstrap.py\n", encoding="utf-8")


def exe_name(name):
    return name + ".exe" if os.name == "nt" else name


def cc_command(source, output, opt):
    return [sys.executable, "-m", "ziglang", "cc", "-std=c11", f"-O{opt}",
            str(source), "-o", str(output)]


def record(stages, name, path, built_by=None):
    data = Path(path).read_bytes()
    stages[name] = {
        "path": str(path), "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    if built_by is not None:
        stages[name]["built_by"] = str(built_by)
    print(f"{name}  {len(data):>9} bytes  sha256 {stages[name]['sha256']}"
          + (f"  built by {Path(built_by).name}" if built_by else ""), flush=True)


def scan_for_paths(path, name, forbidden):
    """No build paths and no clock macros in generated C (PLAN.md, section 4)."""
    data = Path(path).read_bytes()
    for text in (*forbidden, "__DATE__", "__TIME__"):
        needle = text.encode("utf-8")
        for variant in (needle, needle.replace(b"\\", b"/"), needle.replace(b"\\", b"\\\\")):
            if variant and variant in data:
                raise StageMismatch(f"{name} contains {variant!r}; generated C must not")


def bootstrap(work, opt, memory_mb, timeout, cc_memory_mb=1500):
    prepare_work_dir(work)
    limit = memory_mb * 1024 * 1024
    cc_limit = cc_memory_mb * 1024 * 1024
    env = dict(os.environ)
    env["SOURCE_DATE_EPOCH"] = "0"
    env["ZIG_LOCAL_CACHE_DIR"] = str(work / "zig-cache")
    root = str(COMPILER_ROOT)
    c0, c1, c2 = work / "c0.c", work / "c1.c", work / "c2.c"
    b1, b2 = work / exe_name("stage1"), work / exe_name("stage2")
    stages, peaks = {}, {}
    result = {"stages": stages, "fixpoint": None, "python_equal": None,
              "peak_memory_bytes": peaks}
    started = time.monotonic()

    def step(name, command):
        step_limit = cc_limit if name.startswith("cc-") else limit
        _, peak = run_step(name, command, cwd=work, env=env, limit=step_limit, timeout=timeout)
        peaks[name] = peak
        return peak

    def finish():
        result["seconds"] = round(time.monotonic() - started, 1)
        return result

    try:
        print(f"Work directory: {work}", flush=True)
        step("python", [sys.executable, "-c", C0_SCRIPT, str(ROOT), root, str(c0)])
        record(stages, "C0", c0)
        scan_for_paths(c0, "C0", (str(work), str(ROOT)))
        step("cc-1", cc_command(c0, b1, opt))
        record(stages, "B1", b1)
        step("stage-1", [str(b1), "-o", str(c1), root])
        record(stages, "C1", c1, built_by=b1)
        scan_for_paths(c1, "C1", (str(work), str(ROOT)))
        size = assert_identical(c0, c1, "C0", "C1")
        result["python_equal"] = "identical"
        print(f"C0 == C1: identical ({size} bytes)", flush=True)
        step("cc-2", cc_command(c1, b2, opt))
        record(stages, "B2", b2)
        step("stage-2", [str(b2), "-o", str(c2), root])
        record(stages, "C2", c2, built_by=b2)
        scan_for_paths(c2, "C2", (str(work), str(ROOT)))
        size = assert_identical(c1, c2, "C1", "C2")
        result["fixpoint"] = "identical"
        print(f"C1 == C2: identical ({size} bytes)", flush=True)
    finally:
        finish()
    return finish()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the Mort self-hosting proof.")
    parser.add_argument("--work-dir", type=Path, default=ROOT / "build" / "bootstrap",
                        help="directory for the stages; cleared first (default: build/bootstrap)")
    parser.add_argument("--json", type=Path, help="also write the result as JSON here")
    parser.add_argument("--opt", choices=("0", "1", "2", "3"), default="2",
                        help="C optimization level for B1 and B2 (the plan's value is 2)")
    parser.add_argument("--memory-mb", type=int, default=500,
                        help="memory limit per process (default: 500)")
    parser.add_argument("--cc-memory-mb", type=int, default=1500,
                        help="memory limit per C compiler process (default: 1500)")
    parser.add_argument("--timeout", type=int, default=300,
                        help="seconds before one step is abandoned (default: 300)")
    args = parser.parse_args(argv)
    work = args.work_dir.resolve()
    result = None
    try:
        result = bootstrap(work, args.opt, args.memory_mb, args.timeout, args.cc_memory_mb)
    except (StageMismatch, StageFailed) as error:
        print(f"BOOTSTRAP FAILED: {error}", file=sys.stderr, flush=True)
        return 1
    except subprocess.TimeoutExpired as error:
        print(f"BOOTSTRAP FAILED: {error}", file=sys.stderr, flush=True)
        return 1
    finally:
        if args.json is not None and result is not None:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    peak_mb = max(result["peak_memory_bytes"].values()) / (1024 * 1024)
    print(f"Peak memory of any step: {peak_mb:.0f} MB; time: {result['seconds']:.0f} s")
    print("Self-hosting holds: C1 == C2 and C0 == C1.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
