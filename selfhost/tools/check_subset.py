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
from mort.errors import MortError  # noqa: E402
from mort.lexer import Lexer  # noqa: E402
from mort.parser import Parser  # noqa: E402
from mort.typechecker import BUILTIN_NAMES  # noqa: E402

ALLOWED_SCALARS = {"bool", "void", "u8", "u32", "u64", "i64", "int",
                   "c_char", "c_int"}
ALLOWED_NODES = {
    "Program", "ImportDecl", "StructDecl", "EnumDecl", "FnDecl", "ExternFnDecl",
    "Let", "Assign", "Return", "If", "While", "For", "Block", "ExprStmt",
    "Break", "Continue", "Match", "MatchArm",
    "IntLit", "CharLit", "StrLit", "BoolLit", "Var", "Unary", "Binary", "Call",
    "Cast", "StructLit", "FieldAccess", "Index", "ArrayLit",
}
ALLOWED_BUILTINS = {"alloc", "free", "sizeof", "println", "print",
                    "file_open", "file_close", "file_read", "file_write"}


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
    name = path.name
    try:
        program = Parser(Lexer(source).tokenize()).parse()
    except MortError as error:
        return [f"{name}:{error.line}: does not parse: {error.msg}"]
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

    # A file sees the whole flattened program, so collect names from all of it.
    enum_names = {e.name for e in program.enums}
    types = {s.name for s in program.structs} | enum_names
    callables = {f.name for f in program.funcs} | {f.name for f in program.externs}
    for sibling in path.parent.glob("*.mx"):
        other = Parser(Lexer(sibling.read_text(encoding="utf-8")).tokenize()).parse()
        enum_names |= {e.name for e in other.enums}
        types |= {s.name for s in other.structs} | {e.name for e in other.enums}
        callables |= {f.name for f in other.funcs} | {f.name for f in other.externs}

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
            if node.name == "sizeof":
                for type_argument in node.type_args:
                    check_type(type_argument, types, where, problems)
            if not node.name or "." in node.name:
                problems.append(f"{where}: qualified or payload call {node.name}")
        if isinstance(node, A.Cast):
            check_type(node.target_type, types, where, problems)
        if isinstance(node, A.For):
            check_type(node.decl_type, types, where, problems)
        if isinstance(node, A.StructLit) and node.name not in types:
            problems.append(f"{where}: struct literal {node.name!r}")
        if isinstance(node, A.FieldAccess) and not str(node.field).isidentifier():
            problems.append(f"{where}: tuple field .{node.field}")
        if isinstance(node, A.Match):
            check_match(node, enum_names, where, problems)
        if isinstance(node, A.ArrayLit) and id(node) not in global_nodes:
            problems.append(f"{where}: array literal outside a global")
        if isinstance(node, A.Let):
            if id(node) not in global_nodes:
                check_type(node.decl_type, types, where, problems)
    check_builtins(program, name, problems)
    check_function_values(program, callables | BUILTIN_NAMES, name, problems)
    return problems


def check_match(node, enum_names, where, problems):
    """Arms must name unit variants of a known enum; `_` alone is not enough."""
    variant_arms = 0
    for arm in node.arms:
        pattern = arm.pattern
        if pattern is None:
            continue
        variant_arms += 1
        if isinstance(pattern, A.Call):
            problems.append(f"{where}: payload pattern in match arm")
        elif not (isinstance(pattern, A.FieldAccess)
                  and isinstance(pattern.obj, A.Var)
                  and pattern.obj.name in enum_names):
            problems.append(f"{where}: match arm is not a unit enum variant")
    if variant_arms == 0:
        problems.append(f"{where}: match with no enum variant arm")


def check_function_values(program, callables, name, problems):
    """A callable's name may only appear as the target of a direct call."""
    def scan(root, local_names):
        for node in walk(root):
            if (isinstance(node, A.Var) and node.name in callables
                    and node.name not in local_names):
                problems.append(f"{name}:{node.line}: function value {node.name}")

    for function in program.funcs:
        local_names = {param.name for param in function.params}
        for node in walk(function.body):
            if isinstance(node, A.Let):
                local_names.add(node.name)
            elif isinstance(node, A.For):
                local_names.add(node.var)
        scan(function.body, local_names)
    for binding in program.globals:
        scan(binding, set())


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
