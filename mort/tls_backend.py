"""Build and cache Mort's checksum-pinned native TLS backend."""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MBEDTLS_VERSION = "3.6.6"
MBEDTLS_SHA256 = (
    "8fb65fae8dcae5840f793c0a334860a411f884cc537ea290ce1c52bb64ca007a"
)
CA_BUNDLE_VERSION = "certifi-2026.6.17"
CA_BUNDLE_SHA256 = (
    "bbc7e9c01d7551bb8a159b5dedd989b8ee3ce105aff522b68eb1b01bf854cab0"
)

_VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
_ARCHIVE = _VENDOR_DIR / f"mbedtls-{MBEDTLS_VERSION}.tar.bz2"
_CA_BUNDLE = _VENDOR_DIR / "cacert.pem"
_WRAPPER = _VENDOR_DIR / "mort_tls.c"
_ROOT_NAME = f"mbedtls-{MBEDTLS_VERSION}"
_THIRD_PARTY_SOURCES = (
    "3rdparty/everest/library/everest.c",
    "3rdparty/everest/library/x25519.c",
    "3rdparty/everest/library/Hacl_Curve25519_joined.c",
    "3rdparty/p256-m/p256-m_driver_entrypoints.c",
    "3rdparty/p256-m/p256-m/p256-m.c",
)
_EXTRACT_PREFIXES = (
    f"{_ROOT_NAME}/include/",
    f"{_ROOT_NAME}/library/",
    f"{_ROOT_NAME}/3rdparty/everest/include/",
    f"{_ROOT_NAME}/3rdparty/everest/library/",
    f"{_ROOT_NAME}/3rdparty/p256-m/p256-m/include/",
    f"{_ROOT_NAME}/3rdparty/p256-m/p256-m_driver_interface/",
)
_EXTRACT_FILES = {
    f"{_ROOT_NAME}/3rdparty/p256-m/p256-m_driver_entrypoints.c",
    f"{_ROOT_NAME}/3rdparty/p256-m/p256-m_driver_entrypoints.h",
    f"{_ROOT_NAME}/3rdparty/p256-m/p256-m/p256-m.c",
    f"{_ROOT_NAME}/3rdparty/p256-m/p256-m/p256-m.h",
}


class TlsBackendError(RuntimeError):
    """The verified TLS support library could not be prepared."""


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_tls_payloads():
    """Verify the shipped TLS sources and default roots by SHA-256."""
    expected = (
        (_ARCHIVE, MBEDTLS_SHA256),
        (_CA_BUNDLE, CA_BUNDLE_SHA256),
    )
    for path, digest in expected:
        if not path.is_file():
            raise TlsBackendError(f"required TLS payload is missing: {path}")
        actual = _sha256(path)
        if actual != digest:
            raise TlsBackendError(
                f"TLS payload integrity check failed for {path.name}: "
                f"expected {digest}, got {actual}"
            )
    if not _WRAPPER.is_file():
        raise TlsBackendError(f"required TLS integration source is missing: {_WRAPPER}")


def _cache_root():
    configured = os.environ.get("MORT_CACHE_DIR")
    if configured:
        return _writable_cache_root(Path(configured).expanduser().resolve(), explicit=True)
    candidates = []
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            candidates.append(Path(base).resolve() / "Mort" / "Cache")
    elif sys.platform == "darwin":
        candidates.append(Path.home() / "Library" / "Caches" / "Mort")
    else:
        base = os.environ.get("XDG_CACHE_HOME")
        if base:
            candidates.append(Path(base).expanduser().resolve() / "mort")
        candidates.append(Path.home() / ".cache" / "mort")
    candidates.append(Path(tempfile.gettempdir()).resolve() / "mort-cache")
    for candidate in candidates:
        try:
            return _writable_cache_root(candidate)
        except TlsBackendError:
            continue
    raise TlsBackendError("no writable cache directory is available")


def _writable_cache_root(path, explicit=False):
    probe = None
    try:
        path.mkdir(parents=True, exist_ok=True)
        descriptor, probe = tempfile.mkstemp(prefix=".mort-write-", dir=path)
        os.close(descriptor)
        os.unlink(probe)
        return path
    except OSError as error:
        if probe:
            try:
                os.unlink(probe)
            except OSError:
                pass
        label = "configured Mort cache" if explicit else "Mort cache"
        raise TlsBackendError(f"{label} is not writable: {path}: {error}") from error


def _is_zig_compiler(compiler):
    if "ziglang" in compiler:
        return True
    executable = Path(compiler[0]).stem.lower() if compiler else ""
    return executable == "zig"


def compiler_environment(compiler):
    """Return an environment with writable, Mort-owned Zig caches."""
    environment = os.environ.copy()
    if not _is_zig_compiler(compiler):
        return environment
    root = _cache_root() / "zig"
    global_cache = root / "global"
    local_cache = root / "local"
    global_cache.mkdir(parents=True, exist_ok=True)
    local_cache.mkdir(parents=True, exist_ok=True)
    environment.setdefault("ZIG_GLOBAL_CACHE_DIR", str(global_cache))
    environment.setdefault("ZIG_LOCAL_CACHE_DIR", str(local_cache))
    return environment


def _compiler_identity(compiler):
    try:
        completed = subprocess.run(
            [*compiler, "--version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=20,
            env=compiler_environment(compiler),
        )
        version = completed.stdout
    except (OSError, subprocess.SubprocessError):
        version = "unknown"
    return json.dumps(
        {
            "argv": list(compiler),
            "version": version,
            "platform": sys.platform,
            "machine": platform.machine(),
        },
        sort_keys=True,
    )


def _archive_command(compiler):
    if "ziglang" in compiler:
        index = compiler.index("ziglang")
        return [*compiler[:index], "ziglang", "ar"]
    executable = Path(compiler[0]).stem.lower()
    if executable == "zig":
        return [compiler[0], "ar"]
    configured = os.environ.get("AR")
    if configured:
        found = shutil.which(configured)
        if found:
            return [found]
    for candidate in ("llvm-ar", "gcc-ar", "ar"):
        found = shutil.which(candidate)
        if found:
            return [found]
    raise TlsBackendError(
        "TLS compilation needs an ar-compatible static-library archiver"
    )


def _safe_extract(source_dir):
    with tarfile.open(_ARCHIVE, "r:bz2") as archive:
        selected = []
        for member in archive.getmembers():
            name = member.name.replace("\\", "/")
            if not (
                name in _EXTRACT_FILES
                or any(name.startswith(prefix) for prefix in _EXTRACT_PREFIXES)
            ):
                continue
            if member.issym() or member.islnk():
                raise TlsBackendError(
                    f"TLS source archive contains an unsupported link: {name}"
                )
            if not member.isdir() and not member.isfile():
                raise TlsBackendError(
                    f"TLS source archive contains an unsupported entry: {name}"
                )
            parts = Path(name).parts
            if Path(name).is_absolute() or ".." in parts:
                raise TlsBackendError(
                    f"TLS source archive contains an unsafe path: {name}"
                )
            selected.append(member)
        if sys.version_info >= (3, 12):
            archive.extractall(source_dir, members=selected, filter="data")
        else:
            archive.extractall(source_dir, members=selected)


def _write_ca_include(path):
    data = _CA_BUNDLE.read_bytes() + b"\0"
    with open(path, "w", encoding="ascii", newline="\n") as handle:
        handle.write("static const unsigned char mort_ca_bundle[] = {\n")
        for start in range(0, len(data), 16):
            chunk = data[start:start + 16]
            handle.write("    ")
            handle.write(", ".join(f"0x{byte:02x}" for byte in chunk))
            handle.write(",\n")
        handle.write("};\n")
        handle.write(
            "static const size_t mort_ca_bundle_len = "
            "sizeof(mort_ca_bundle);\n"
        )


def _compile_source(compiler, flags, includes, source, output, environment):
    command = [
        *compiler,
        "-std=c11",
        "-O2",
        "-D_FILE_OFFSET_BITS=64",
        *flags,
        *(f"-I{path}" for path in includes),
        "-c",
        str(source),
        "-o",
        str(output),
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise TlsBackendError(
            f"failed to compile TLS source {source.name}: {detail}"
        )


def _build_archive(compiler, flags, destination):
    environment = compiler_environment(compiler)
    with tempfile.TemporaryDirectory(prefix="mort-tls-build-") as temporary:
        root = Path(temporary)
        source_dir = root / "source"
        object_dir = root / "objects"
        source_dir.mkdir()
        object_dir.mkdir()
        _safe_extract(source_dir)
        mbedtls = source_dir / _ROOT_NAME
        wrapper = root / "mort_tls.c"
        shutil.copyfile(_WRAPPER, wrapper)
        _write_ca_include(root / "mort_ca_bundle.inc")

        sources = sorted((mbedtls / "library").glob("*.c"))
        sources.extend(mbedtls / item for item in _THIRD_PARTY_SOURCES)
        sources.append(wrapper)
        missing = [str(path) for path in sources if not path.is_file()]
        if missing:
            raise TlsBackendError(
                "TLS source archive is incomplete: " + ", ".join(missing)
            )
        includes = (
            root,
            mbedtls / "include",
            mbedtls / "library",
            mbedtls / "3rdparty/everest/include",
            mbedtls / "3rdparty/everest/include/everest",
            mbedtls / "3rdparty/everest/include/everest/kremlib",
            mbedtls / "3rdparty/p256-m/p256-m/include",
            mbedtls / "3rdparty/p256-m/p256-m/include/p256-m",
            mbedtls / "3rdparty/p256-m",
            mbedtls / "3rdparty/p256-m/p256-m_driver_interface",
        )
        objects = [
            object_dir / f"{index:03d}.o"
            for index in range(len(sources))
        ]
        workers = min(12, os.cpu_count() or 4)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(
                    _compile_source,
                    compiler,
                    flags,
                    includes,
                    source,
                    output,
                    environment,
                )
                for source, output in zip(sources, objects)
            ]
            for future in futures:
                future.result()

        archiver = _archive_command(compiler)
        staged_archive = root / "libmort_tls.a"
        for index in range(0, len(objects), 40):
            chunk = objects[index:index + 40]
            completed = subprocess.run(
                [*archiver, "rcs", str(staged_archive), *map(str, chunk)],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            if completed.returncode != 0:
                detail = completed.stderr.strip() or completed.stdout.strip()
                raise TlsBackendError(f"failed to archive TLS backend: {detail}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(staged_archive, destination)
        except OSError:
            if not destination.is_file():
                raise


def ensure_tls_backend(compiler, extra_cflags=()):
    """Return the cached static TLS library, building it after hash checks."""
    verify_tls_payloads()
    flags = tuple(extra_cflags)
    identity = hashlib.sha256(
        (
            _compiler_identity(compiler)
            + "\0"
            + "\0".join(flags)
            + "\0"
            + MBEDTLS_SHA256
            + "\0"
            + CA_BUNDLE_SHA256
            + "\0"
            + _sha256(_WRAPPER)
        ).encode("utf-8")
    ).hexdigest()[:24]
    destination = _cache_root() / "tls" / identity / "libmort_tls.a"
    stamp = destination.with_suffix(".a.sha256")

    def cache_is_valid():
        if not destination.is_file() or not stamp.is_file():
            return False
        try:
            recorded = stamp.read_text(encoding="ascii").strip()
        except OSError:
            return False
        return recorded == _sha256(destination)

    if cache_is_valid():
        return str(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock = destination.parent / "build.lock"
    deadline = time.monotonic() + 300
    descriptor = None
    while descriptor is None:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if cache_is_valid():
                return str(destination)
            if time.monotonic() >= deadline:
                raise TlsBackendError(
                    f"timed out waiting for another TLS build; "
                    f"remove stale lock {lock}"
                )
            time.sleep(0.2)
    try:
        if cache_is_valid():
            return str(destination)
        for stale in (destination, stamp):
            try:
                stale.unlink()
            except FileNotFoundError:
                pass
        print(
            "mortc: preparing the verified TLS backend (one-time build)",
            file=sys.stderr,
        )
        try:
            _build_archive(list(compiler), flags, destination)
        except (OSError, tarfile.TarError) as error:
            raise TlsBackendError(
                f"failed to prepare TLS backend: {error}") from error
        digest = _sha256(destination)
        with tempfile.NamedTemporaryFile(
                "w", encoding="ascii", dir=stamp.parent, delete=False) as handle:
            handle.write(digest + "\n")
            temporary_stamp = handle.name
        os.replace(temporary_stamp, stamp)
    finally:
        os.close(descriptor)
        try:
            lock.unlink()
        except FileNotFoundError:
            pass
    return str(destination)
