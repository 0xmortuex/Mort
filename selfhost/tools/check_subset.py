#!/usr/bin/env python3
"""Verify selfhost/*.mx stays inside the bootstrap subset defined in PLAN.md.

Usage: python selfhost/tools/check_subset.py [--stats]

Parses each file with the Python front end and walks the AST. Exit status 1 on
any violation. --stats also prints the per-node-kind counts.
"""
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from mort import mort_ast as A  # noqa: E402
from mort.lexer import Lexer  # noqa: E402
from mort.parser import Parser  # noqa: E402
from mort.typechecker import BUILTIN_NAMES  # noqa: E402

ALLOWED_SCALARS = {"bool", "void", "u8", "u32", "u64", "i64", "int", "f64",
                   "c_char", "c_int"}
ALLOWED_NODES = {
    "Program", "ImportDecl", "StructDecl", "EnumDecl", "FnDecl", "ExternFnDecl",
    "Let", "Assign", "Return", "If", "While", "For", "Block", "ExprStmt",
    "Break", "Continue", "Match", "MatchArm",
    "IntLit", "CharLit", "StrLit", "BoolLit", "Var", "Unary", "Binary", "Call",
    "Cast", "StructLit", "FieldAccess", "Index", "ArrayLit",
}
ALLOWED_BUILTINS = {"alloc", "free", "sizeof", "println", "eprintln", "print",
                    "file_open", "file_close", "file_read", "file_write",
                    "arg_count", "arg_get"}


def nodes_of(value):
    if isinstance(value, A.Node):
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from nodes_of(item)


def base_type(type_text):
    """Strip pointer and const prefixes from a type string."""
    text = type_text.strip()
    while True:
        if text.startswith("*const "):
            text = text[len("*const "):].strip()
        elif text.startswith("*"):
            text = text[1:].strip()
        else:
            return text


def check_type(type_text, names, where, problems, global_array=False):
    if type_text is None:
        return
    base = base_type(type_text)
    if global_array and base.startswith("[") and ";" in base:
        check_type(base[1:base.rindex(";")], names, where, problems)
        return
    if base in ALLOWED_SCALARS or base in names:
        return
    problems.append(f"{where}: type {type_text!r} is outside the subset")


def check_file(path, stats):
    problems = []
    source = path.read_text(encoding="utf-8")
    program = Parser(Lexer(source).tokenize()).parse()
    name = path.name
    if program.module_name:
        problems.append(f"{name}: module declaration is outside the subset")
    if program.tests:
        problems.append(f"{name}: test declaration is outside the subset")
    if program.aliases:
        problems.append(f"{name}: type alias is outside the subset")
    for declaration in program.imports:
        if len(declaration.parts) != 1 or declaration.parts[0] == "std":
            problems.append(f"{name}: import must be one relative segment")
        if declaration.alias:
            problems.append(f"{name}: import alias is outside the subset")

    struct_names = {s.name for s in program.structs}
    enum_names = {e.name for e in program.enums}
    types = struct_names | enum_names
    # A file sees the whole flattened program; declare imported names too.
    for sibling in path.parent.glob("*.mx"):
        other = Parser(Lexer(sibling.read_text(encoding="utf-8")).tokenize()).parse()
        types |= {s.name for s in other.structs} | {e.name for e in other.enums}

    for declaration in program.structs:
        if declaration.generic_params:
            problems.append(f"{name}:{declaration.line}: generic struct")
        if declaration.resource:
            problems.append(f"{name}:{declaration.line}: resource struct")
        for field in declaration.fields:
            check_type(field.typ, types, f"{name}:{declaration.line}", problems)
    for declaration in program.enums:
        if declaration.generic_params:
            problems.append(f"{name}:{declaration.line}: generic enum")
        for variant in declaration.variants:
            if variant.payload_types:
                problems.append(f"{name}:{declaration.line}: enum payload")
    for function in list(program.funcs) + list(program.externs):
        where = f"{name}:{function.line}"
        if getattr(function, "generic_params", None):
            problems.append(f"{where}: generic function")
        if getattr(function, "public", False):
            problems.append(f"{where}: pub function")
        for param in function.params:
            check_type(param.typ, types, where, problems)
        check_type(function.ret, types, where, problems)
    for binding in program.globals:
        check_type(binding.decl_type, types, f"{name}:{binding.line}", problems,
                   global_array=True)

    global_nodes = {id(n) for g in program.globals for n in walk(g)}
    for node in walk(program):
        kind = type(node).__name__
        stats[kind] += 1
        where = f"{name}:{getattr(node, 'line', '?')}"
        if kind not in ALLOWED_NODES:
            problems.append(f"{where}: {kind} is outside the subset")
        if isinstance(node, A.Call):
            if node.type_args and node.name != "sizeof":
                problems.append(f"{where}: explicit type arguments on {node.name}")
        if isinstance(node, A.ArrayLit) and id(node) not in global_nodes:
            problems.append(f"{where}: array literal outside a global")
        if isinstance(node, A.Let):
            if id(node) not in global_nodes:
                check_type(node.decl_type, types, where, problems)
        if isinstance(node, A.MatchArm) and node.pattern is not None:
            pattern = node.pattern
            if isinstance(pattern, A.Call):
                problems.append(f"{where}: payload pattern in match arm")
    check_builtins(program, name, problems)
    return problems


def walk(root):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        for value in vars(node).values():
            for child in nodes_of(value):
                stack.append(child)


def check_builtins(program, name, problems):
    declared = {f.name for f in program.funcs} | {f.name for f in program.externs}
    for node in walk(program):
        if isinstance(node, A.Call) and not getattr(node, "indirect", False):
            callee = node.name
            if not callee or "." in callee:
                continue
            if callee in BUILTIN_NAMES and callee not in declared \
                    and callee not in ALLOWED_BUILTINS:
                problems.append(f"{name}:{node.line}: builtin {callee} is outside the subset")


def main(argv):
    paths = sorted((ROOT / "selfhost").glob("*.mx"))
    if not paths:
        print("no selfhost sources found", file=sys.stderr)
        return 1
    stats = collections.Counter()
    problems = []
    for path in paths:
        problems.extend(check_file(path, stats))
    for problem in problems:
        print(problem)
    print(f"checked {len(paths)} files, {len(problems)} violations")
    if "--stats" in argv:
        for kind, count in sorted(stats.items(), key=lambda item: -item[1]):
            print(f"{count:7}  {kind}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
