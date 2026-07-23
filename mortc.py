#!/usr/bin/env python3
"""mortc — the Mort compiler driver.

Usage:
    python mortc.py program.mx              # compile to a native executable
    python mortc.py main.mx math.mx          # compile a multi-file program
    python mortc.py program.mx --run        # compile, then run it
    python mortc.py program.mx --emit-c     # print the generated C and stop
    python mortc.py program.mx -o out        # choose the output name

Compilation is a two-step pipeline: Mort source -> C (always), then C -> native
binary via a system C compiler (cc/gcc/clang). If no C compiler is found, the
generated C is written next to your source so you can build it yourself.
"""
import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mort.lexer import Lexer          # noqa: E402
from mort.parser import Parser        # noqa: E402
from mort.typechecker import Checker  # noqa: E402
from mort.codegen import CodeGen      # noqa: E402
from mort.errors import MortError     # noqa: E402
from mort.mort_ast import Node, Program  # noqa: E402
from mort import __language_version__, __version__  # noqa: E402
from mort.project import (           # noqa: E402
    ProjectError,
    add_git_dependency,
    add_path_dependency,
    add_registry_dependency,
    create_project,
    find_manifest,
    resolve_project,
    resolve_tests,
    write_lockfile,
)
from mort.formatter import format_file  # noqa: E402


_SOURCE_STDLIB_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "std")
_INSTALLED_STDLIB_DIR = os.path.join(sys.prefix, "mort-stdlib")
STDLIB_DIR = (
    _SOURCE_STDLIB_DIR
    if os.path.isdir(_SOURCE_STDLIB_DIR)
    else _INSTALLED_STDLIB_DIR
)


def compile_to_c(src, freestanding=False):
    """Run the full front-end and return generated C source (or raise MortError)."""
    return compile_sources_to_c([src], freestanding=freestanding)


def _parse_source(src, filename=None):
    try:
        program = Parser(Lexer(src).tokenize()).parse()
        if filename:
            _tag_source(program, filename)
    except MortError as error:
        error.filename = filename
        raise
    except RecursionError as error:
        raise MortError(
            "source nesting exceeds the compiler safety limit",
            filename=filename,
        ) from error
    return program


def _tag_source(node, filename, seen=None):
    """Attach a source filename to every AST node for checker diagnostics."""
    if seen is None:
        seen = set()
    if not isinstance(node, Node) or id(node) in seen:
        return
    seen.add(id(node))
    node.filename = filename
    for value in vars(node).values():
        if isinstance(value, Node):
            _tag_source(value, filename, seen)
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, Node):
                    _tag_source(item, filename, seen)
                elif isinstance(item, tuple):
                    for part in item:
                        if isinstance(part, Node):
                            _tag_source(part, filename, seen)


def compile_sources_to_c(sources, freestanding=False, filenames=None, warnings=None):
    """Compile one or more Mort source strings as a single program.

    Top-level declarations from every source participate in the same namespace,
    so functions, structs, and globals can be used across file boundaries.
    """
    sources = list(sources)
    if filenames is None:
        filenames = [None] * len(sources)
    else:
        filenames = list(filenames)
        if len(filenames) != len(sources):
            raise ValueError("filenames must have the same length as sources")
    programs = [_parse_source(src, filename)
                for src, filename in zip(sources, filenames)]
    for program in programs:
        if program.imports:
            node = program.imports[0]
            raise MortError(
                "imports require file-based compilation",
                node.line,
                filename=getattr(node, "filename", None),
            )
    try:
        return _compile_programs(programs, freestanding, warnings=warnings)
    except RecursionError as error:
        raise MortError(
            "source nesting exceeds the compiler safety limit") from error


def _compile_programs(programs, freestanding=False, test_mode=False, warnings=None):
    for source_program in programs:
        for function in source_program.funcs:
            if source_program.module_name and function.module is None:
                function.module = source_program.module_name
                function.symbol_name = f"{source_program.module_name}.{function.name}"
                function.import_aliases = dict(source_program.import_aliases)
        for test in source_program.tests:
            if source_program.module_name and test.module is None:
                test.module = source_program.module_name
                test.import_aliases = dict(source_program.import_aliases)
    program = Program(
        [f for p in programs for f in p.funcs],
        [s for p in programs for s in p.structs],
        [g for p in programs for g in p.globals],
        [e for p in programs for e in p.externs],
        enums=[e for p in programs for e in p.enums],
        tests=[t for p in programs for t in p.tests],
        aliases=[a for p in programs for a in p.aliases],
    )
    checker = Checker(program, freestanding=freestanding, test_mode=test_mode)
    checker.check()
    if warnings is not None:
        warnings.extend(checker.warnings)
    return CodeGen(program, freestanding=freestanding, test_mode=test_mode).generate()


def compile_files_to_c(
        paths, freestanding=False, test_mode=False, packages=None, warnings=None,
        source_overrides=None):
    """Resolve imports recursively and compile a set of root source files."""
    programs = []
    loaded = set()
    program_by_path = {}
    packages = packages or {}
    source_overrides = {
        os.path.normcase(os.path.realpath(path)): source
        for path, source in (source_overrides or {}).items()
    }

    def load(path):
        path = os.path.abspath(path)
        key = os.path.normcase(os.path.realpath(path))
        if key in loaded:
            return
        loaded.add(key)
        if key in source_overrides:
            source = source_overrides[key]
        else:
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    source = handle.read()
            except OSError as error:
                raise MortError(f"cannot read imported source: {error}", filename=path)
        program = _parse_source(source, path)
        programs.append(program)
        program_by_path[key] = program
        for declaration in program.imports:
            parts = declaration.parts
            if parts[0] in packages:
                entry = packages[parts[0]]
                if len(parts) == 1:
                    imported = entry
                else:
                    imported = os.path.join(os.path.dirname(entry), *parts[1:]) + ".mx"
            elif parts[0] == "std":
                if len(parts) != 2:
                    raise MortError(
                        "standard imports use 'import std.<module>;'",
                        declaration.line,
                        filename=path,
                    )
                imported = os.path.join(STDLIB_DIR, parts[1] + ".mx")
            else:
                imported = os.path.join(os.path.dirname(path), *parts) + ".mx"
            if not os.path.isfile(imported):
                raise MortError(
                    f"cannot find imported module {'.'.join(parts)!r}",
                    declaration.line,
                    filename=path,
                )
            declaration.resolved_path = os.path.abspath(imported)
            load(imported)

    try:
        for path in paths:
            load(path)
    except RecursionError as error:
        raise MortError(
            "source nesting or import depth exceeds the compiler safety limit"
        ) from error
    for program in programs:
        aliases = {}
        for declaration in program.imports:
            key = os.path.normcase(os.path.realpath(declaration.resolved_path))
            target = program_by_path[key]
            if target.module_name is not None:
                alias = declaration.alias or declaration.parts[-1]
                if alias in aliases and aliases[alias] != target.module_name:
                    raise MortError(
                        f"import alias {alias!r} is already used",
                        declaration.line,
                        filename=getattr(declaration, "filename", None),
                    )
                aliases[alias] = target.module_name
            elif declaration.alias is not None:
                raise MortError(
                    "an aliased import must target a file with a module declaration",
                    declaration.line,
                    filename=getattr(declaration, "filename", None),
                )
        program.import_aliases = aliases
        for function in program.funcs:
            function.module = program.module_name
            function.symbol_name = (
                f"{program.module_name}.{function.name}"
                if program.module_name else function.name
            )
            function.import_aliases = dict(aliases)
        for test in program.tests:
            test.module = program.module_name
            test.import_aliases = dict(aliases)
    try:
        return _compile_programs(
            programs, freestanding, test_mode=test_mode, warnings=warnings)
    except RecursionError as error:
        raise MortError(
            "source nesting exceeds the compiler safety limit") from error


def is_zig(cc):
    """True if the compiler argv is Zig's clang (supports easy cross-compiles).

    Matches only a real Zig invocation — the `zig` executable itself or the
    `ziglang` Python module — not any path that merely contains 'zig'
    (e.g. C:/tools/zigzag/cc).
    """
    if "ziglang" in cc:
        return True
    exe = os.path.splitext(os.path.basename(cc[0]))[0].lower() if cc else ""
    return exe == "zig"


def find_c_compiler():
    """Return an argv prefix for a usable C compiler, or None.

    Accepts gcc/clang/cc directly, and also ``zig cc`` — Zig ships a full
    clang-based C compiler in one portable binary, the easiest option to
    install on Windows.
    """
    configured = os.environ.get("MORT_CC") or os.environ.get("CC")
    if configured:
        arguments = shlex.split(configured, posix=os.name != "nt")
        arguments = [
            item[1:-1] if len(item) >= 2 and item[0] == item[-1] == '"' else item
            for item in arguments
        ]
        if arguments and (
                shutil.which(arguments[0]) or os.path.isfile(arguments[0])):
            return arguments
    for cc in ("cc", "gcc", "clang"):
        found = shutil.which(cc)
        if found:
            return [found]
    return find_zig()


def find_zig():
    """Find a Zig C compiler specifically, or None.

    The kernel build needs Zig (not just any cc) because it cross-compiles to
    32-bit x86 bare metal, which a stock host gcc usually can't do. Prefers a
    `zig` on PATH, then the `pip install ziglang` module.
    """
    if shutil.which("zig"):
        return ["zig", "cc"]
    try:
        import ziglang  # noqa: F401
        return [sys.executable, "-m", "ziglang", "cc"]
    except ImportError:
        return None


def _compile_main(argv=None, test_mode=False):
    ap = argparse.ArgumentParser(prog="mortc", description="The Mort compiler.")
    ap.add_argument("--version", action="version", version=f"Mort {__version__}")
    ap.add_argument(
        "--language-version",
        action="version",
        version=__language_version__,
        help="print the implemented Mort language version and exit",
    )
    ap.add_argument("files", nargs="+", metavar="file",
                    help="one or more .mx source files")
    ap.add_argument("-o", "--output", help="output file name")
    ap.add_argument("--emit-c", action="store_true", help="print generated C and exit")
    ap.add_argument("--check", action="store_true",
                    help="type-check successfully without invoking the C backend")
    ap.add_argument(
        "--diagnostic-format",
        choices=("human", "json"),
        default="human",
        help="compiler diagnostic output format (default: human)",
    )
    ap.add_argument("--warn-unused", action="store_true",
                    help="warn about unused local bindings and parameters")
    ap.add_argument("--deny-warnings", action="store_true",
                    help="report enabled warnings and fail the check/build")
    ap.add_argument("--run", action="store_true", help="run the program after building")
    ap.add_argument("-O", "--opt-level", choices=("0", "1", "2", "3", "s"),
                    default="2", help="C backend optimization level (default: 2)")
    ap.add_argument("-g", "--debug", action="store_true",
                    help="include backend debug information")
    ap.add_argument(
        "--sanitize", action="append",
        choices=("address", "undefined", "leak", "thread"),
        default=[],
        help="enable a hosted C-backend sanitizer (repeatable)",
    )
    ap.add_argument("--freestanding", action="store_true",
                    help="compile to a bare-metal object file (no libc, no main)")
    ap.add_argument("--link", action="append", default=[], metavar="FILE",
                    help="link an additional object or library file (repeatable)")
    ap.add_argument("-l", "--library", action="append", default=[], metavar="NAME",
                    help="link a system library by name (repeatable)")
    ap.add_argument("--std", action="append", default=[], metavar="MODULE",
                    help="include a bundled standard-library module (repeatable)")
    ap.add_argument("--package", action="append", default=[], metavar="NAME=ENTRY",
                    help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    packages = {}
    for specification in args.package:
        if "=" not in specification:
            print("mortc: --package expects NAME=ENTRY", file=sys.stderr)
            return 1
        name, entry = specification.split("=", 1)
        if not name or not os.path.isfile(entry):
            print(f"mortc: invalid package entry {specification!r}", file=sys.stderr)
            return 1
        packages[name] = os.path.abspath(entry)

    std_files = []
    for name in args.std:
        if not name or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789_" for ch in name):
            print(f"mortc: invalid standard-library module name {name!r}", file=sys.stderr)
            return 1
        path = os.path.join(STDLIB_DIR, name + ".mx")
        if not os.path.isfile(path):
            available = ", ".join(
                os.path.splitext(item)[0] for item in sorted(os.listdir(STDLIB_DIR))
                if item.endswith(".mx")
            )
            print(
                f"mortc: unknown standard-library module {name!r}"
                f" (available: {available or 'none'})",
                file=sys.stderr,
            )
            return 1
        std_files.append(path)

    source_files = [*std_files, *args.files]
    for path in [*source_files, *args.link]:
        if not os.path.exists(path):
            print(f"mortc: cannot find file {path!r}", file=sys.stderr)
            return 1
    if args.freestanding and args.run:
        print("mortc: --run cannot be used with --freestanding (nothing to run yet)",
              file=sys.stderr)
        return 1
    if args.check and (args.run or args.emit_c):
        print("mortc: --check cannot be combined with --run or --emit-c", file=sys.stderr)
        return 1
    if args.freestanding and (args.link or args.library):
        print("mortc: --link/-l cannot be used with --freestanding object compilation",
              file=sys.stderr)
        return 1
    if args.freestanding and args.sanitize:
        print("mortc: sanitizers are unavailable in freestanding mode", file=sys.stderr)
        return 1
    if "thread" in args.sanitize and any(
            item in args.sanitize for item in ("address", "leak")):
        print(
            "mortc: thread sanitizer cannot be combined with address or leak "
            "sanitizers",
            file=sys.stderr,
        )
        return 1

    compiler_warnings = []
    try:
        c_source = compile_files_to_c(
            source_files, freestanding=args.freestanding, test_mode=test_mode,
            packages=packages, warnings=compiler_warnings)
    except MortError as e:
        if args.diagnostic_format == "json":
            print(json.dumps(e.to_diagnostic(), ensure_ascii=False), file=sys.stderr)
        else:
            print(f"mortc: {e.render()}", file=sys.stderr)
        return 1

    if args.warn_unused or args.deny_warnings:
        for warning in compiler_warnings:
            if args.diagnostic_format == "json":
                print(json.dumps(warning.to_diagnostic(), ensure_ascii=False), file=sys.stderr)
            else:
                print(f"mortc: {warning.render()}", file=sys.stderr)
        if args.deny_warnings and compiler_warnings:
            return 1

    if args.check:
        print("mortc: check passed")
        return 0

    if args.emit_c:
        sys.stdout.write(c_source)
        return 0

    base = os.path.splitext(os.path.basename(args.files[0]))[0]

    # Freestanding output is explicitly x86-64 and may contain x86 assembly.
    # Always use Zig's cross compiler instead of a host compiler (notably the
    # ARM64 compiler on current macOS runners).
    cc = find_zig() if args.freestanding else find_c_compiler()
    if cc is None:
        out = args.output or (base + ".o" if args.freestanding else base)
        fallback = base + ".c"
        with open(fallback, "w", encoding="utf-8") as fh:
            fh.write(c_source)
        if args.freestanding:
            hint = f"zig cc -target x86_64-freestanding-none -c {fallback} -o {out}"
        else:
            hint = f"gcc {fallback} -o {out}"
        missing = (
            "no Zig compiler found for the x86-64 freestanding target"
            if args.freestanding
            else "no C compiler (cc/gcc/clang/zig) found"
        )
        print(
            f"mortc: {missing}.\n"
            f"       Wrote generated C to {fallback!r} — compile it with e.g. "
            f"`{hint}`.",
            file=sys.stderr,
        )
        return 2

    if args.freestanding:
        out = args.output or (base + ".o")
        # Cross-compile a real x86_64 bare-metal object when Zig is the backend;
        # otherwise just build a freestanding object for the host.
        cmd = list(cc)
        if is_zig(cc):
            cmd += ["-target", "x86_64-freestanding-none"]
        cmd += ["-ffreestanding", f"-O{args.opt_level}", "-std=c11", "-c"]
    else:
        out = args.output or (base + (".exe" if os.name == "nt" else ""))
        cmd = [*cc, f"-O{args.opt_level}", "-std=c11"]
        if os.name != "nt" and "MORT_REQUIRES_PTHREAD" in c_source:
            cmd.append("-pthread")
    if args.debug:
        cmd.append("-g")
    if args.sanitize:
        sanitizers = ",".join(dict.fromkeys(args.sanitize))
        cmd += [f"-fsanitize={sanitizers}", "-fno-omit-frame-pointer"]

    tmp = tempfile.NamedTemporaryFile("w", suffix=".c", delete=False, encoding="utf-8")
    try:
        tmp.write(c_source)
        tmp.close()
        try:
            link_args = [*args.link, *(f"-l{name}" for name in args.library)]
            if os.name == "nt" and "MORT_REQUIRES_WINSOCK" in c_source:
                link_args.append("-lws2_32")
            subprocess.run([*cmd, tmp.name, *link_args, "-o", out], check=True)
        except subprocess.CalledProcessError:
            print("mortc: the C backend failed to compile the generated code", file=sys.stderr)
            return 1
    finally:
        os.unlink(tmp.name)

    print(f"mortc: wrote {out}")
    sys.stdout.flush()  # keep our message ahead of the program's own output

    if args.run:
        exe = os.path.abspath(out)
        result = subprocess.run([exe])
        return result.returncode
    return 0


def _project_args(project, sources, output, run=False):
    argv = [*sources, "-o", output, "--opt-level", project["opt_level"]]
    if project["debug"]:
        argv.append("--debug")
    for module in project["std"]:
        argv += ["--std", module]
    for path in project["links"]:
        argv += ["--link", path]
    for library in project["libraries"]:
        argv += ["-l", library]
    for sanitizer in project["sanitizers"]:
        argv += ["--sanitize", sanitizer]
    for name, entry in project["packages"].items():
        argv += ["--package", f"{name}={entry}"]
    if run:
        argv.append("--run")
    return argv


def _load_project(start, offline=False):
    try:
        return resolve_project(find_manifest(start), offline=offline)
    except ProjectError as error:
        print(f"mortc: {error}", file=sys.stderr)
        return None


def _project_build(start, run=False):
    project = _load_project(start)
    if project is None:
        return 1
    os.makedirs(os.path.dirname(project["output"]), exist_ok=True)
    write_lockfile(project)
    fingerprint = _project_fingerprint(project)
    cache_path = os.path.join(project["root"], ".mort", "build-cache.json")
    cache = {}
    try:
        with open(cache_path, "r", encoding="utf-8") as handle:
            cache = json.load(handle)
    except (OSError, ValueError):
        pass
    if (cache.get("fingerprint") == fingerprint
            and cache.get("output") == project["output"]
            and os.path.isfile(project["output"])):
        print(f"mortc: build cache hit ({project['output']})")
        if run:
            return subprocess.run([project["output"]]).returncode
        return 0
    result = _compile_main(_project_args(
        project, project["sources"], project["output"], run=False))
    if result != 0:
        return result
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump({
            "fingerprint": fingerprint,
            "output": project["output"],
            "version": __version__,
        }, handle, indent=2, sort_keys=True)
        handle.write("\n")
    if run:
        return subprocess.run([project["output"]]).returncode
    return 0


def _project_fingerprint(project):
    digest = hashlib.sha256()
    digest.update(f"mort:{__version__}\0".encode("utf-8"))
    configuration = {
        key: project[key]
        for key in (
            "name", "output", "std", "links", "libraries", "packages",
            "sanitizers", "opt_level", "debug")
    }
    digest.update(json.dumps(
        configuration, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    files = set(project["sources"])
    files.update(project["links"])
    files.update(project["packages"].values())
    files.update(project["dependency_manifests"])
    files.update(
        os.path.join(STDLIB_DIR, name)
        for name in os.listdir(STDLIB_DIR) if name.endswith(".mx")
    )
    for filename in ("mort.toml", "mort.lock"):
        path = os.path.join(project["root"], filename)
        if os.path.isfile(path):
            files.add(path)
    roots = {project["root"]}
    roots.update(os.path.dirname(path) for path in project["dependency_manifests"])
    for root in roots:
        for directory, names, filenames in os.walk(root):
            names[:] = [
                name for name in names
                if name not in (".git", ".mort", "build", "__pycache__")
            ]
            files.update(
                os.path.join(directory, name)
                for name in filenames if name.endswith(".mx")
            )
    for path in sorted(os.path.abspath(item) for item in files):
        digest.update(path.encode("utf-8", errors="surrogatepass"))
        digest.update(b"\0")
        try:
            with open(path, "rb") as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
        except OSError as error:
            digest.update(f"missing:{error}".encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _project_test(start):
    project = _load_project(start)
    if project is None:
        return 1
    tests = resolve_tests(project)
    if not tests:
        print("mortc: no test sources found", file=sys.stderr)
        return 1
    passed = 0
    with tempfile.TemporaryDirectory(prefix="mort-tests-") as directory:
        for path in tests:
            name = os.path.splitext(os.path.basename(path))[0]
            output = os.path.join(directory, name + (".exe" if os.name == "nt" else ""))
            print(f"test {os.path.relpath(path, project['root'])} ... ", end="")
            sys.stdout.flush()
            result = _compile_main(
                _project_args(project, [*project["sources"], path], output, run=True),
                test_mode=True,
            )
            if result != 0:
                print("FAILED")
                return result
            passed += 1
            print("ok")
    print(f"mortc: {passed} test file(s) passed")
    return 0


def _format_command(paths, check=False):
    files = []
    if paths:
        for path in paths:
            if os.path.isfile(path):
                files.append(os.path.abspath(path))
            else:
                project = _load_project(path)
                if project is None:
                    return 1
                files.extend(project["sources"])
                files.extend(resolve_tests(project))
    else:
        project = _load_project(".")
        if project is None:
            return 1
        files.extend(project["sources"])
        files.extend(resolve_tests(project))
    files = sorted(dict.fromkeys(files))
    changed = []
    try:
        for path in files:
            if format_file(path, check=check):
                changed.append(path)
    except OSError as error:
        print(f"mortc: formatter failed: {error}", file=sys.stderr)
        return 1
    if check and changed:
        for path in changed:
            print(f"would format {path}")
        return 1
    print(f"mortc: formatted {len(changed)} of {len(files)} file(s)")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "std":
        ap = argparse.ArgumentParser(
            prog="mortc std", description="List bundled Mort standard modules.")
        ap.add_argument("--path", action="store_true",
                        help="also print the resolved standard-library directory")
        args = ap.parse_args(argv[1:])
        modules = sorted(
            os.path.splitext(name)[0]
            for name in os.listdir(STDLIB_DIR)
            if name.endswith(".mx")
        )
        if args.path:
            print(STDLIB_DIR)
        print("\n".join(modules))
        return 0
    if argv and argv[0] == "doctor":
        ap = argparse.ArgumentParser(
            prog="mortc doctor", description="Check the local Mort toolchain.")
        ap.parse_args(argv[1:])
        compiler = find_c_compiler()
        zig = find_zig()
        modules = (
            sorted(name for name in os.listdir(STDLIB_DIR) if name.endswith(".mx"))
            if os.path.isdir(STDLIB_DIR) else []
        )
        print(f"Mort {__version__}")
        print(f"Python: {sys.version.split()[0]} ({sys.executable})")
        print(f"Standard library: {STDLIB_DIR} ({len(modules)} modules)")
        print("C backend: " + (" ".join(compiler) if compiler else "not found"))
        print("Freestanding backend: " + (" ".join(zig) if zig else "not found"))
        if not modules:
            print("mortc: standard library is missing", file=sys.stderr)
            return 1
        if compiler is None:
            print(
                "mortc: front-end checks work, but native builds need "
                "cc, gcc, clang, or zig",
                file=sys.stderr,
            )
        return 0
    if argv and argv[0] == "fuzz":
        from mort.fuzz import run_fuzz
        ap = argparse.ArgumentParser(
            prog="mortc fuzz", description="Fuzz the Mort compiler front end.")
        ap.add_argument("--cases", type=int, default=1000, help="number of cases")
        ap.add_argument("--seed", type=int, default=0, help="deterministic random seed")
        args = ap.parse_args(argv[1:])
        if args.cases <= 0:
            print("mortc: --cases must be positive", file=sys.stderr)
            return 1
        result = run_fuzz(args.cases, args.seed)
        print(
            f"mortc: fuzzed {result['cases']} case(s) with seed {result['seed']} "
            f"({result['accepted']} accepted, {result['rejected']} rejected)"
        )
        return 0
    if argv and argv[0] == "lsp":
        from mort.lsp import run as run_lsp
        return run_lsp()
    if argv and argv[0] == "new":
        ap = argparse.ArgumentParser(prog="mortc new", description="Create a Mort project.")
        ap.add_argument("path", help="new project directory")
        args = ap.parse_args(argv[1:])
        try:
            target = create_project(args.path)
        except (OSError, ProjectError) as error:
            print(f"mortc: {error}", file=sys.stderr)
            return 1
        print(f"mortc: created project at {target}")
        return 0
    if argv and argv[0] == "add":
        ap = argparse.ArgumentParser(prog="mortc add", description="Add a project dependency.")
        ap.add_argument("name", help="dependency import name")
        source = ap.add_mutually_exclusive_group(required=True)
        source.add_argument("--path", help="local dependency project path")
        source.add_argument("--git", help="Git repository URL")
        source.add_argument(
            "--registry", metavar="CONSTRAINT",
            help="public registry semantic-version constraint")
        ap.add_argument("--ref", help="Git branch or tag (with --git)")
        ap.add_argument("--project", default=".", help="target project directory")
        args = ap.parse_args(argv[1:])
        try:
            manifest = find_manifest(args.project)
            if args.path:
                add_path_dependency(manifest, args.name, args.path)
            elif args.registry:
                add_registry_dependency(
                    manifest, args.name, args.registry)
            else:
                add_git_dependency(manifest, args.name, args.git, args.ref)
            project = resolve_project(manifest)
            lock = write_lockfile(project)
        except (OSError, ProjectError) as error:
            print(f"mortc: {error}", file=sys.stderr)
            return 1
        print(f"mortc: added {args.name}; updated {lock}")
        return 0
    if argv and argv[0] == "fetch":
        ap = argparse.ArgumentParser(prog="mortc fetch", description="Resolve project dependencies.")
        ap.add_argument("path", nargs="?", default=".", help="project directory")
        ap.add_argument("--locked", action="store_true",
                        help="fail instead of changing an out-of-date lockfile")
        ap.add_argument("--offline", action="store_true",
                        help="use only cached registry data and configured mirrors")
        args = ap.parse_args(argv[1:])
        project = _load_project(args.path, offline=args.offline)
        if project is None:
            return 1
        try:
            lock = write_lockfile(project, locked=args.locked)
        except (OSError, ProjectError) as error:
            print(f"mortc: {error}", file=sys.stderr)
            return 1
        print(f"mortc: dependencies locked in {lock}")
        return 0
    if argv and argv[0] in ("build", "run", "test"):
        command = argv[0]
        ap = argparse.ArgumentParser(
            prog=f"mortc {command}", description=f"{command.title()} a Mort project.")
        ap.add_argument("path", nargs="?", default=".",
                        help="project directory or mort.toml (default: current directory)")
        args = ap.parse_args(argv[1:])
        if command == "test":
            return _project_test(args.path)
        return _project_build(args.path, run=command == "run")
    if argv and argv[0] == "fmt":
        ap = argparse.ArgumentParser(prog="mortc fmt", description="Format Mort source files.")
        ap.add_argument("paths", nargs="*", help="files or project directories")
        ap.add_argument("--check", action="store_true", help="report changes without writing")
        args = ap.parse_args(argv[1:])
        return _format_command(args.paths, check=args.check)
    return _compile_main(argv)


if __name__ == "__main__":
    sys.exit(main())
