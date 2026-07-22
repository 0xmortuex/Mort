"""Static type checker.

Types are represented as strings:
  * integers: 'i8','i16','i32','i64','u8','u16','u32','u64'
  * 'bool', 'void'
  * pointers: '*' + inner, e.g. '*i32', '**u8'

Integer literals are "untyped" (``node.is_lit``) and coerce to any integer type
in a context that expects one (let annotations, assignments, returns, args, and
the other operand of a binary op). Everything else needs an explicit ``as`` cast.
"""
import copy

from .errors import MortError
from . import mort_ast as A

INT_TYPES = {
    "i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64",
    "c_char", "c_uchar", "c_short", "c_ushort", "c_int", "c_uint",
    "c_long", "c_ulong", "c_size",
}
ARITH_OPS = {"+", "-", "*", "/", "%"}
REL_OPS = {"<", ">", "<=", ">="}
BUILTIN_NAMES = {
    "print", "println", "assert", "alloc", "free", "len", "slice",
    "sizeof",
    "outb", "inb", "outw", "inw", "outl", "inl",
}
C_KEYWORDS = {
    "auto", "break", "case", "char", "const", "continue", "default", "do",
    "double", "else", "enum", "extern", "float", "for", "goto", "if",
    "inline", "int", "long", "register", "restrict", "return", "short",
    "signed", "sizeof", "static", "struct", "switch", "typedef", "union",
    "unsigned", "void", "volatile", "while", "_Alignas", "_Alignof",
    "_Atomic", "_Bool", "_Complex", "_Generic", "_Imaginary", "_Noreturn",
    "_Static_assert", "_Thread_local",
}

# Inclusive value range each integer type can hold.
INT_RANGES = {
    "i8": (-128, 127),
    "i16": (-32768, 32767),
    "i32": (-(2 ** 31), 2 ** 31 - 1),
    "i64": (-(2 ** 63), 2 ** 63 - 1),
    "u8": (0, 255),
    "u16": (0, 65535),
    "u32": (0, 2 ** 32 - 1),
    "u64": (0, 2 ** 64 - 1),
    "c_char": (-128, 127),
    "c_uchar": (0, 255),
    "c_short": (-(2 ** 15), 2 ** 15 - 1),
    "c_ushort": (0, 2 ** 16 - 1),
    "c_int": (-(2 ** 31), 2 ** 31 - 1),
    "c_uint": (0, 2 ** 32 - 1),
    # Conservative LLP64 ranges keep code portable between Windows and Unix.
    "c_long": (-(2 ** 31), 2 ** 31 - 1),
    "c_ulong": (0, 2 ** 32 - 1),
    "c_size": (0, 2 ** 64 - 1),
}


def _c_div(a, b):
    """Integer division with C semantics: truncate toward zero (pure integer,
    so no float rounding near i64/u64 limits)."""
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def _c_mod(a, b):
    """Remainder with C semantics: the result takes the sign of the dividend."""
    return a - _c_div(a, b) * b


def is_ptr(t):
    return isinstance(t, str) and t.startswith("*")


def pointee(t):
    return t[7:] if t.startswith("*const ") else t[1:]


def is_const_ptr(t):
    return isinstance(t, str) and t.startswith("*const ")


def is_array(t):
    return isinstance(t, str) and t.startswith("[") and not t.startswith("[]")


def is_slice(t):
    return isinstance(t, str) and t.startswith("[]")


def is_const_slice(t):
    return isinstance(t, str) and t.startswith("[]const ")


def slice_elem(t):
    return t[8:] if is_const_slice(t) else t[2:]


def array_parts(t):
    """'[i32;8]' -> ('i32', 8). Splits on the last ';' to allow nested arrays."""
    inner = t[1:-1]
    cut = inner.rfind(";")
    return inner[:cut], int(inner[cut + 1:])


def generic_parts(t):
    """Return (base, args) for a concrete generic type string, else None."""
    if not isinstance(t, str) or "<" not in t or not t.endswith(">"):
        return None
    cut = t.find("<")
    base = t[:cut]
    inner = t[cut + 1:-1]
    args = []
    start = depth = 0
    for index, char in enumerate(inner):
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        elif char == "," and depth == 0:
            args.append(inner[start:index])
            start = index + 1
    args.append(inner[start:])
    return base, args


def substitute_type(t, mapping):
    if t in mapping:
        return mapping[t]
    if is_ptr(t):
        prefix = "*const " if is_const_ptr(t) else "*"
        return prefix + substitute_type(pointee(t), mapping)
    if is_slice(t):
        prefix = "[]const " if is_const_slice(t) else "[]"
        return prefix + substitute_type(slice_elem(t), mapping)
    if is_array(t):
        elem, count = array_parts(t)
        return f"[{substitute_type(elem, mapping)};{count}]"
    parts = generic_parts(t)
    if parts:
        base, args = parts
        return base + "<" + ",".join(substitute_type(arg, mapping) for arg in args) + ">"
    return t


class Checker:
    def __init__(self, program, freestanding=False, test_mode=False):
        self.program = program
        self.freestanding = freestanding
        self.test_mode = test_mode
        self.funcs = {}       # name -> (param_types, ret)
        self.func_decls = {}
        self.func_templates = {}
        self.structs = {}     # name -> {field: type}  (insertion-ordered)
        self.struct_templates = {}
        self.enums = {}       # name -> ordered {variant: optional payload type}
        self.enum_templates = {}
        self.globals = {}     # name -> type
        self.scopes = []
        self.current_ret = None
        self.extern_names = set()
        self.loop_depth = 0
        self.current_module = None
        self.current_import_aliases = {}
        self.block_depth = 0
        self.root_defer_open = True
        self.allowed_try_expr = None

    def _error(self, msg, node):
        raise MortError(
            msg,
            getattr(node, "line", None),
            filename=getattr(node, "filename", None),
        )

    def _valid_type(self, t):
        """A type is usable if its (possibly pointed-to / element) base is known."""
        if is_ptr(t):
            return pointee(t) == "void" or self._valid_type(pointee(t))
        if is_array(t):
            elem, n = array_parts(t)
            return n > 0 and self._valid_type(elem)
        if is_slice(t):
            return slice_elem(t) != "void" and self._valid_type(slice_elem(t))
        if generic_parts(t):
            return self._instantiate_struct(t) or self._instantiate_enum(t)
        return t in INT_TYPES or t == "bool" or t in self.structs or t in self.enums

    def _instantiate_struct(self, concrete_type):
        if concrete_type in self.structs:
            return True
        base, args = generic_parts(concrete_type)
        template = self.struct_templates.get(base)
        if template is None or len(args) != len(template.generic_params):
            return False
        if not all(self._valid_type(arg) for arg in args):
            return False
        mapping = dict(zip(template.generic_params, args))
        fields = [
            A.StructField(field.name, substitute_type(field.typ, mapping))
            for field in template.fields
        ]
        self.structs[concrete_type] = None
        resolved = {}
        for field in fields:
            if field.typ == concrete_type:
                self._error(
                    f"generic struct {concrete_type!r} cannot contain itself by value", template)
            if not self._valid_type(field.typ):
                self._error(
                    f"field {field.name!r} of {concrete_type!r} has unknown type "
                    f"{field.typ}", template)
            resolved[field.name] = field.typ
        self.structs[concrete_type] = resolved
        self.program.structs.append(
            A.StructDecl(concrete_type, fields, template.line))
        return True

    def _instantiate_enum(self, concrete_type):
        if concrete_type in self.enums:
            return True
        base, args = generic_parts(concrete_type)
        template = self.enum_templates.get(base)
        if template is None or len(args) != len(template.generic_params):
            return False
        if not all(self._valid_type(arg) for arg in args):
            return False
        mapping = dict(zip(template.generic_params, args))
        variants = [
            A.EnumVariant(
                variant.name,
                None if variant.payload_type is None
                else substitute_type(variant.payload_type, mapping),
            )
            for variant in template.variants
        ]
        self.enums[concrete_type] = None
        resolved = {}
        for variant in variants:
            payload_type = variant.payload_type
            if payload_type == concrete_type:
                self._error(
                    f"generic enum {concrete_type!r} cannot contain itself by value",
                    template,
                )
            if payload_type is not None and not self._valid_type(payload_type):
                self._error(
                    f"variant {concrete_type}.{variant.name} has unknown payload type "
                    f"{payload_type}",
                    template,
                )
            resolved[variant.name] = payload_type
        self.enums[concrete_type] = resolved
        concrete = A.EnumDecl(concrete_type, variants, template.line)
        if hasattr(template, "filename"):
            concrete.filename = template.filename
        self.program.enums.append(concrete)
        return True

    def _unify_generic_type(self, pattern, actual, generic_params, mapping):
        if pattern in generic_params:
            previous = mapping.get(pattern)
            if previous is None:
                mapping[pattern] = actual
                return True
            return previous == actual
        if is_ptr(pattern):
            if not is_ptr(actual):
                return False
            if is_const_ptr(actual) and not is_const_ptr(pattern):
                return False
            return self._unify_generic_type(
                pointee(pattern), pointee(actual), generic_params, mapping)
        if is_slice(pattern):
            if not is_slice(actual) or is_const_slice(pattern) != is_const_slice(actual):
                return False
            return self._unify_generic_type(
                slice_elem(pattern), slice_elem(actual), generic_params, mapping)
        if is_array(pattern):
            if not is_array(actual):
                return False
            pattern_elem, pattern_count = array_parts(pattern)
            actual_elem, actual_count = array_parts(actual)
            return (pattern_count == actual_count
                    and self._unify_generic_type(
                        pattern_elem, actual_elem, generic_params, mapping))
        pattern_parts = generic_parts(pattern)
        if pattern_parts:
            actual_parts = generic_parts(actual)
            if (not actual_parts or pattern_parts[0] != actual_parts[0]
                    or len(pattern_parts[1]) != len(actual_parts[1])):
                return False
            return all(
                self._unify_generic_type(expected, found, generic_params, mapping)
                for expected, found in zip(pattern_parts[1], actual_parts[1])
            )
        return True

    @staticmethod
    def _substitute_function_types(function, mapping):
        def visit(value):
            if value is None or isinstance(value, (str, int, bool)):
                return
            if isinstance(value, list):
                for item in value:
                    visit(item)
                return
            if isinstance(value, tuple):
                for item in value:
                    visit(item)
                return
            if isinstance(value, A.FnDecl):
                value.ret = substitute_type(value.ret, mapping)
            elif isinstance(value, A.Param):
                value.typ = substitute_type(value.typ, mapping)
            elif isinstance(value, A.Let) and value.decl_type is not None:
                value.decl_type = substitute_type(value.decl_type, mapping)
            elif isinstance(value, A.For) and value.decl_type is not None:
                value.decl_type = substitute_type(value.decl_type, mapping)
            elif isinstance(value, A.Cast):
                value.target_type = substitute_type(value.target_type, mapping)
            elif isinstance(value, A.StructLit):
                value.name = substitute_type(value.name, mapping)
            elif isinstance(value, A.Var) and generic_parts(value.name):
                value.name = substitute_type(value.name, mapping)
            elif isinstance(value, A.Call):
                value.type_args = [
                    substitute_type(argument, mapping) for argument in value.type_args
                ]
                if "." in value.name:
                    qualifier, member = value.name.rsplit(".", 1)
                    substituted = substitute_type(qualifier, mapping)
                    if substituted != qualifier:
                        value.name = f"{substituted}.{member}"
            if hasattr(value, "__dict__"):
                for child in vars(value).values():
                    visit(child)

        visit(function)

    def _instantiate_function(self, resolved, mapping, node):
        template = self.func_templates[resolved]
        missing = [name for name in template.generic_params if name not in mapping]
        if missing:
            self._error(
                f"cannot infer generic parameter(s) {', '.join(missing)} for "
                f"function {template.name!r}",
                node,
            )
        type_args = [mapping[name] for name in template.generic_params]
        concrete_symbol = resolved + "<" + ",".join(type_args) + ">"
        if concrete_symbol in self.funcs:
            return concrete_symbol
        instance = copy.deepcopy(template)
        self._substitute_function_types(instance, mapping)
        instance.generic_params = []
        instance.symbol_name = concrete_symbol
        for parameter in instance.params:
            if parameter.typ == "void" or not self._valid_type(parameter.typ):
                self._error(
                    f"generic function {template.name!r} produced invalid parameter "
                    f"type {parameter.typ}",
                    node,
                )
            if is_array(parameter.typ):
                self._error(
                    f"parameter {parameter.name!r} cannot be an array; pass a pointer "
                    "to its elements instead",
                    node,
                )
        if instance.ret != "void":
            if not self._valid_type(instance.ret):
                self._error(
                    f"generic function {template.name!r} produced invalid return type "
                    f"{instance.ret}",
                    node,
                )
            if is_array(instance.ret):
                self._error(f"function {template.name!r} cannot return an array", node)
        self.funcs[concrete_symbol] = (
            [parameter.typ for parameter in instance.params], instance.ret)
        self.func_decls[concrete_symbol] = instance
        self.program.funcs.append(instance)
        return concrete_symbol

    def check(self):
        # Enums are nominal integer-backed types with a closed variant set.
        for ed in list(self.program.enums):
            if not ed.variants:
                self._error(f"enum {ed.name!r} must have at least one variant", ed)
            variant_names = [variant.name for variant in ed.variants]
            if len(set(variant_names)) != len(variant_names):
                self._error(f"enum {ed.name!r} has a duplicate variant", ed)
            if ed.generic_params:
                if ed.name in self.enum_templates or ed.name in self.enums:
                    self._error(f"enum {ed.name!r} is already defined", ed)
                if len(set(ed.generic_params)) != len(ed.generic_params):
                    self._error(f"enum {ed.name!r} has a duplicate generic parameter", ed)
                self.enum_templates[ed.name] = ed
                continue
            if ed.name in self.enums:
                self._error(f"enum {ed.name!r} is already defined", ed)
            self.enums[ed.name] = {
                variant.name: variant.payload_type for variant in ed.variants
            }

        # 1. collect struct names first so fields may reference any struct
        #    (including recursively through pointers, e.g. `next: *Node`).
        for sd in self.program.structs:
            if sd.generic_params:
                if (sd.name in self.struct_templates or sd.name in self.structs
                        or sd.name in self.enum_templates or sd.name in self.enums):
                    self._error(f"struct {sd.name!r} is already defined", sd)
                if len(set(sd.generic_params)) != len(sd.generic_params):
                    self._error(f"struct {sd.name!r} has a duplicate generic parameter", sd)
                self.struct_templates[sd.name] = sd
                continue
            if sd.name in self.structs or sd.name in self.enums:
                self._error(f"struct {sd.name!r} is already defined", sd)
            self.structs[sd.name] = None  # placeholder until fields validated

        # 2. validate each struct's fields
        for sd in list(self.program.structs):
            if sd.generic_params:
                continue
            fields = {}
            for fld in sd.fields:
                if fld.name in fields:
                    self._error(
                        f"field {fld.name!r} declared twice in struct {sd.name!r}", sd)
                if not self._valid_type(fld.typ):
                    self._error(
                        f"field {fld.name!r} of {sd.name!r} has unknown type {fld.typ}", sd)
                fields[fld.name] = fld.typ
            self.structs[sd.name] = fields

        for ed in list(self.program.enums):
            if ed.generic_params:
                continue
            for variant in ed.variants:
                if variant.payload_type is not None and not self._valid_type(variant.payload_type):
                    self._error(
                        f"variant {ed.name}.{variant.name} has unknown payload type "
                        f"{variant.payload_type}", ed)

        # 3. collect and validate function signatures. Extern declarations use
        #    the platform C ABI and share the same call namespace.
        for f in [*self.program.externs, *self.program.funcs]:
            symbol_name = getattr(f, "symbol_name", f.name)
            if (symbol_name in self.funcs or symbol_name in self.func_templates
                    or f.name in BUILTIN_NAMES):
                self._error(f"function {f.name!r} is already defined", f)
            if getattr(f, "generic_params", None):
                if len(set(f.generic_params)) != len(f.generic_params):
                    self._error(
                        f"function {f.name!r} has a duplicate generic parameter", f)
                self.func_templates[symbol_name] = f
                continue
            if isinstance(f, A.ExternFnDecl) and f.name in C_KEYWORDS:
                self._error(f"external function name {f.name!r} is reserved by C", f)
            for p in f.params:
                if p.typ == "void":
                    self._error(f"parameter {p.name!r} cannot have type void", f)
                if not self._valid_type(p.typ):
                    self._error(f"parameter {p.name!r} has unknown type {p.typ}", f)
                if is_array(p.typ):
                    self._error(
                        f"parameter {p.name!r} cannot be an array; pass a pointer "
                        f"to its elements instead", f)
            if f.ret != "void":
                if not self._valid_type(f.ret):
                    self._error(f"function {f.name!r} has unknown return type {f.ret}", f)
                if is_array(f.ret):
                    self._error(f"function {f.name!r} cannot return an array", f)
            self.funcs[symbol_name] = ([p.typ for p in f.params], f.ret)
            self.func_decls[symbol_name] = f
            if isinstance(f, A.ExternFnDecl):
                self.extern_names.add(f.name)

        # 4. globals — initialised with a compile-time constant, usable anywhere
        self.scopes = []
        for g in self.program.globals:
            if (g.name in self.globals or g.name in self.funcs
                    or g.name in self.func_templates
                    or g.name in self.structs or g.name in self.enums
                    or g.name in BUILTIN_NAMES):
                self._error(f"global {g.name!r} conflicts with another name", g)
            if isinstance(g.expr, (A.ArrayLit, A.ArrayRepeat)):
                g.var_type = self._check_array_expr(g.expr, g.decl_type, g)
                if not self._is_const_init(g.expr):
                    self._error(f"global {g.name!r} must be initialised with constants", g)
                self.globals[g.name] = g.var_type
                continue
            t = self._check_expr(g.expr)
            if t == "void":
                self._error("cannot bind a void value to a global", g)
            if g.decl_type:
                if not self._valid_type(g.decl_type):
                    self._error(f"global {g.name!r} has unknown type {g.decl_type}", g)
                if not self._coerce(g.decl_type, g.expr):
                    self._error(
                        f"global {g.name!r} is {g.decl_type} but its value is {t}", g)
                g.var_type = g.decl_type
            else:
                g.var_type = t
            if not self._is_const_init(g.expr):
                self._error(
                    f"global {g.name!r} must be initialised with a constant "
                    f"(a literal or literal expression)", g)
            self.globals[g.name] = g.var_type

        # Hosted programs are launched through a C main; freestanding ones are
        # entered by a bootloader, so they have no 'main' requirement.
        if not self.freestanding and not self.test_mode:
            defined_names = {
                f.symbol_name for f in self.program.funcs if not f.generic_params
            }
            if "main" not in defined_names:
                raise MortError("no 'main' function defined")
            params, ret = self.funcs["main"]
            if params:
                raise MortError("'main' must take no parameters")
            if ret != "i64":
                raise MortError("'main' must return int")

        for f in self.program.funcs:
            if f.generic_params:
                continue
            self._check_fn(f)
        test_names = set()
        for test in self.program.tests:
            if test.name in test_names:
                self._error(f"test {test.name!r} is already defined", test)
            test_names.add(test.name)
            self.current_ret = "void"
            self.current_module = test.module
            self.current_import_aliases = test.import_aliases
            self.scopes = [{}]
            self.loop_depth = 0
            for statement in test.body.stmts:
                self._check_stmt(statement)
        return self.program

    # ----- scopes -----
    def _lookup(self, name):
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return self.globals.get(name)  # fall back to globals (locals shadow them)

    def _is_const_init(self, expr):
        """Globals need a compile-time-constant initialiser."""
        if isinstance(expr, (A.BoolLit, A.StrLit)):
            return True
        if (isinstance(expr, A.FieldAccess) and isinstance(expr.obj, A.Var)
                and expr.obj.name in self.enums):
            return True
        if isinstance(expr, A.ArrayRepeat):
            return self._is_const_init(expr.value)
        if isinstance(expr, A.ArrayLit):
            return all(self._is_const_init(el) for el in expr.elements)
        return bool(getattr(expr, "is_lit", False))  # int literal expression

    def _declare(self, name, typ, node):
        if name in self.scopes[-1]:
            self._error(f"variable {name!r} already declared in this scope", node)
        self.scopes[-1][name] = typ

    def _check_array_expr(self, expr, decl_type, node):
        """Check an array literal / repeat against an optional declared type.

        Returns the resolved '[elem;n]' type. Elements are coerced to the
        element type (with range checks); size must match a declared type.
        """
        elem = size = None
        if decl_type is not None:
            if not is_array(decl_type):
                self._error(f"type mismatch: array value assigned to {decl_type}", node)
            if not self._valid_type(decl_type):
                self._error(f"unknown type {decl_type}", node)
            elem, size = array_parts(decl_type)

        if isinstance(expr, A.ArrayRepeat):
            self._check_expr(expr.value)
            if elem is None:
                elem, size = expr.value.type, expr.count
            else:
                if expr.count != size:
                    self._error(f"array size mismatch: declared {size}, literal has {expr.count}", node)
                if not self._coerce(elem, expr.value):
                    self._error(f"array element expects {elem}, got {expr.value.type}", node)
        else:  # ArrayLit
            for el in expr.elements:
                self._check_expr(el)
            if elem is None:
                elem, size = expr.elements[0].type, len(expr.elements)
            elif len(expr.elements) != size:
                self._error(
                    f"array expects {size} elements, got {len(expr.elements)}", node)
            for el in expr.elements:
                if not self._coerce(elem, el):
                    self._error(f"array element expects {elem}, got {el.type}", node)

        expr.type = f"[{elem};{size}]"
        return expr.type

    def _check_fn(self, f):
        self.current_ret = f.ret
        self.current_module = f.module
        self.current_import_aliases = f.import_aliases
        self.scopes = [{}]
        self.loop_depth = 0
        self.block_depth = 0
        self.root_defer_open = True
        for p in f.params:
            self._declare(p.name, p.typ, f)
        for s in f.body.stmts:
            if isinstance(s, A.Defer) and not self.root_defer_open:
                self._error(
                    "function-scoped defer must appear before control-flow or "
                    "expression statements", s)
            self._check_stmt(s)
            if not isinstance(s, (A.Let, A.Defer)):
                self.root_defer_open = False
        if f.ret != "void" and not self._block_always_returns(f.body):
            self._error(f"function {f.name!r} may finish without returning {f.ret}", f)

    def _block_always_returns(self, block):
        """Conservative control-flow check for non-void function returns."""
        for statement in block.stmts:
            if isinstance(statement, A.Return):
                return True
            if isinstance(statement, A.Block) and self._block_always_returns(statement):
                return True
            if isinstance(statement, A.If) and statement.els is not None:
                then_returns = self._block_always_returns(statement.then)
                if isinstance(statement.els, A.If):
                    else_returns = self._if_always_returns(statement.els)
                else:
                    else_returns = self._block_always_returns(statement.els)
                if then_returns and else_returns:
                    return True
            if isinstance(statement, A.Match) and statement.exhaustive:
                if all(self._block_always_returns(arm.body) for arm in statement.arms):
                    return True
        return False

    def _if_always_returns(self, statement):
        if statement.els is None or not self._block_always_returns(statement.then):
            return False
        if isinstance(statement.els, A.If):
            return self._if_always_returns(statement.els)
        return self._block_always_returns(statement.els)

    def _check_block(self, block):
        self.scopes.append({})
        self.block_depth += 1
        try:
            for s in block.stmts:
                self._check_stmt(s)
        finally:
            self.block_depth -= 1
            self.scopes.pop()

    # ----- coercion -----
    def _const_value(self, e):
        """Evaluate a constant integer-literal expression, or None if not one.

        Values are arbitrary-precision, so an all-literal expression is range-
        checked against its target type — `1 << 8`, `200 << 4`, `~0` etc. are
        caught exactly like a bare out-of-range literal. (Runtime expressions
        wrap instead; see codegen's _narrow.)"""
        if isinstance(e, A.IntLit):
            return e.value
        if isinstance(e, A.Unary) and e.op == "-":
            v = self._const_value(e.operand)
            return None if v is None else -v
        if isinstance(e, A.Unary) and e.op == "~":
            v = self._const_value(e.operand)
            return None if v is None else ~v
        if isinstance(e, A.Binary):
            lv = self._const_value(e.left)
            rv = self._const_value(e.right)
            if lv is None or rv is None:
                return None
            op = e.op
            if op == "+":
                return lv + rv
            if op == "-":
                return lv - rv
            if op == "*":
                return lv * rv
            # Fold / and % with C semantics so the checked value matches the
            # value the generated C actually computes (esp. for negatives and
            # near the 64-bit limits).
            if op == "/" and rv != 0:
                return _c_div(lv, rv)
            if op == "%" and rv != 0:
                return _c_mod(lv, rv)
            if op == "&":
                return lv & rv
            if op == "|":
                return lv | rv
            if op == "^":
                return lv ^ rv
            if op == "<<" and rv >= 0:
                if lv == 0:
                    return 0
                if rv > 64:
                    return 1 << 65   # cap: already out of range for any Mort int
                return lv << rv
            if op == ">>" and rv >= 0:
                return lv >> rv
        return None

    def _coerce(self, expected, expr):
        """Return True if expr fits `expected`, retagging an untyped int literal.

        An untyped integer literal adopts the expected integer type — but its
        value must actually fit, so hardware writes like outb(0x12345, ...) or a
        `let x: u8 = 300;` are compile errors, not silent truncation.
        """
        if expr.type == expected:
            return True
        if is_const_ptr(expected) and is_ptr(expr.type):
            if pointee(expected) == pointee(expr.type):
                expr.type = expected
                return True
        if expected in INT_TYPES and expr.is_lit:
            value = self._const_value(expr)
            if value is not None:
                lo, hi = INT_RANGES[expected]
                if not (lo <= value <= hi):
                    self._error(
                        f"integer literal {self._value_str(value)} does not fit in "
                        f"{expected} (range {lo}..{hi})", expr)
            expr.type = expected  # untyped literal adopts the expected int type
            return True
        return False

    # ----- statements -----
    def _check_stmt(self, s):
        if isinstance(s, A.Let):
            if isinstance(s.expr, (A.ArrayLit, A.ArrayRepeat)):
                s.var_type = self._check_array_expr(s.expr, s.decl_type, s)
                self._declare(s.name, s.var_type, s)
                return
            previous_try_expr = self.allowed_try_expr
            self.allowed_try_expr = s.expr if isinstance(s.expr, A.Try) else None
            try:
                t = self._check_expr(s.expr)
            finally:
                self.allowed_try_expr = previous_try_expr
            if t == "void":
                self._error("cannot bind a void value to a variable", s)
            if s.decl_type:
                if not self._valid_type(s.decl_type):
                    self._error(f"variable {s.name!r} has unknown type {s.decl_type}", s)
                if is_array(s.decl_type):
                    self._error("an array must be initialised with an array literal "
                                "([..] or [value; n])", s)
                if not self._coerce(s.decl_type, s.expr):
                    self._error(
                        f"type mismatch: {s.name!r} is annotated {s.decl_type} "
                        f"but the value is {t}", s)
                s.var_type = s.decl_type
            else:
                s.var_type = t
            self._declare(s.name, s.var_type, s)

        elif isinstance(s, A.Assign):
            tt = self._check_expr(s.target)
            if (isinstance(s.target, A.Unary) and s.target.op == "*"
                    and is_const_ptr(s.target.operand.type)):
                self._error("cannot assign through a const pointer", s)
            if isinstance(s.target, A.Index) and is_const_ptr(s.target.obj.type):
                self._error("cannot assign through a const pointer", s)
            if isinstance(s.target, A.Index) and is_const_slice(s.target.obj.type):
                self._error("cannot assign through a const slice", s)
            if is_array(tt):
                self._error("cannot assign to a whole array; assign elements via a[i]", s)
            self._check_expr(s.expr)
            if not self._coerce(tt, s.expr):
                self._error(
                    f"type mismatch: target is {tt} but value is {s.expr.type}", s)

        elif isinstance(s, A.Return):
            if s.expr is None:
                if self.current_ret != "void":
                    self._error(f"this function must return {self.current_ret}", s)
            else:
                t = self._check_expr(s.expr)
                if not self._coerce(self.current_ret, s.expr):
                    self._error(
                        f"return type mismatch: expected {self.current_ret}, got {t}", s)

        elif isinstance(s, A.If):
            if self._check_expr(s.cond) != "bool":
                self._error("if condition must be a bool", s)
            self._check_block(s.then)
            if s.els is not None:
                if isinstance(s.els, A.If):
                    self._check_stmt(s.els)
                else:
                    self._check_block(s.els)

        elif isinstance(s, A.While):
            if self._check_expr(s.cond) != "bool":
                self._error("while condition must be a bool", s)
            self.loop_depth += 1
            try:
                self._check_block(s.body)
            finally:
                self.loop_depth -= 1

        elif isinstance(s, A.For):
            st = self._check_expr(s.start)
            et = self._check_expr(s.end)
            if st not in INT_TYPES or et not in INT_TYPES:
                self._error("'for' range bounds must be integers", s)
            if s.decl_type is not None:
                # explicit `for i: T in ...` — both bounds coerce to T
                if s.decl_type not in INT_TYPES:
                    self._error(f"'for' variable type must be an integer, got {s.decl_type}", s)
                if not self._coerce(s.decl_type, s.start) or not self._coerce(s.decl_type, s.end):
                    self._error(
                        f"range bounds must fit {s.decl_type}", s)
                s.var_type = s.decl_type
            elif st == et:
                s.var_type = st
            elif s.start.is_lit and not s.end.is_lit:
                s.start.type, s.var_type = et, et
            elif s.end.is_lit and not s.start.is_lit:
                s.end.type, s.var_type = st, st
            elif s.start.is_lit and s.end.is_lit:
                s.var_type = "i64"
            else:
                self._error(f"mismatched range types {st} and {et}; add an 'as' cast", s)
            # the loop variable is scoped to the loop (with the body)
            self.scopes.append({s.var: s.var_type})
            self.loop_depth += 1
            try:
                for st2 in s.body.stmts:
                    self._check_stmt(st2)
            finally:
                self.loop_depth -= 1
                self.scopes.pop()

        elif isinstance(s, A.Match):
            subject_type = self._check_expr(s.expr)
            if (subject_type in self.structs or is_array(subject_type)
                    or is_slice(subject_type) or is_ptr(subject_type)):
                self._error(f"cannot match on a value of type {subject_type}", s)
            wildcard_seen = False
            enum_variants = set()
            for index, arm in enumerate(s.arms):
                binding_name = None
                binding_type = None
                if arm.pattern is None:
                    if wildcard_seen:
                        self._error("match has more than one wildcard arm", arm)
                    if index != len(s.arms) - 1:
                        self._error("the wildcard match arm must be last", arm)
                    wildcard_seen = True
                else:
                    if (subject_type in self.enums and isinstance(arm.pattern, A.Call)
                            and arm.pattern.name.startswith(subject_type + ".")):
                        variant_name = arm.pattern.name.rsplit(".", 1)[1]
                        if variant_name not in self.enums[subject_type]:
                            self._error(
                                f"enum {subject_type!r} has no variant {variant_name!r}", arm)
                        payload_type = self.enums[subject_type][variant_name]
                        if payload_type is None:
                            self._error(
                                f"variant {subject_type}.{variant_name} has no payload", arm)
                        if (len(arm.pattern.args) != 1
                                or not isinstance(arm.pattern.args[0], A.Var)):
                            self._error(
                                "payload match patterns require one binding name", arm)
                        binding_name = arm.pattern.args[0].name
                        binding_type = payload_type
                        arm.variant_name = variant_name
                        arm.binding_name = binding_name
                        arm.binding_type = binding_type
                        enum_variants.add(variant_name)
                    else:
                        pattern_type = self._check_expr(arm.pattern)
                        if not self._coerce(subject_type, arm.pattern):
                            self._error(
                                f"match pattern expects {subject_type}, got {pattern_type}", arm)
                    if (isinstance(arm.pattern, A.FieldAccess)
                            and isinstance(arm.pattern.obj, A.Var)
                            and arm.pattern.obj.name == subject_type):
                        enum_variants.add(arm.pattern.field)
                        arm.variant_name = arm.pattern.field
                self.scopes.append({} if binding_name is None else {binding_name: binding_type})
                try:
                    for statement in arm.body.stmts:
                        self._check_stmt(statement)
                finally:
                    self.scopes.pop()
            if subject_type in self.enums:
                missing = set(self.enums[subject_type]) - enum_variants
                if missing and not wildcard_seen:
                    self._error(
                        f"non-exhaustive match on {subject_type}; missing: "
                        f"{', '.join(sorted(missing))}", s)
                s.exhaustive = True
            else:
                s.exhaustive = wildcard_seen

        elif isinstance(s, A.Block):
            self._check_block(s)

        elif isinstance(s, A.ExprStmt):
            self._check_expr(s.expr)

        elif isinstance(s, A.Asm):
            pass  # an opaque escape hatch; nothing to type-check

        elif isinstance(s, A.Defer):
            if self.block_depth != 0:
                self._error("defer is currently function-scoped and must be top-level", s)
            if self._check_expr(s.expr) != "void":
                self._error("defer expression must return void", s)

        elif isinstance(s, (A.Break, A.Continue)):
            if self.loop_depth == 0:
                word = "break" if isinstance(s, A.Break) else "continue"
                self._error(f"'{word}' is only allowed inside a loop", s)

        else:  # pragma: no cover
            self._error("unknown statement kind", s)

    # ----- expressions -----
    def _check_expr(self, e):
        e.is_lit = False
        t = self._infer(e)
        e.type = t
        # Record the folded value of a constant integer expression so codegen can
        # emit it directly (needed for shifts, whose C form is otherwise UB).
        if t in INT_TYPES and e.is_lit:
            e.const_val = self._const_value(e)
        return t

    @staticmethod
    def _is_lvalue(e):
        return (
            isinstance(e, A.Var)
            or (isinstance(e, A.Unary) and e.op == "*")
            or isinstance(e, A.FieldAccess)
            or isinstance(e, A.Index)
        )

    def _infer(self, e):
        if isinstance(e, A.IntLit):
            e.is_lit = True
            return "i64"
        if isinstance(e, A.BoolLit):
            return "bool"
        if isinstance(e, A.StrLit):
            return "*u8"  # a pointer to static, null-terminated bytes
        if isinstance(e, A.Var):
            vt = self._lookup(e.name)
            if vt is None:
                self._error(f"undefined variable {e.name!r}", e)
            return vt

        if isinstance(e, A.Cast):
            st = self._check_expr(e.expr)
            tgt = e.target_type
            payload_enums = {
                name for name, variants in self.enums.items()
                if any(payload is not None for payload in variants.values())
            }
            src_ok = st in INT_TYPES or (st in self.enums and st not in payload_enums) or is_ptr(st)
            tgt_ok = tgt in INT_TYPES or (tgt in self.enums and tgt not in payload_enums) or is_ptr(tgt)
            if not (src_ok and tgt_ok):
                self._error(f"cannot cast {st} to {tgt}", e)
            return tgt

        if isinstance(e, A.Try):
            if e is not self.allowed_try_expr:
                self._error("try is currently allowed only as a let initializer", e)
            result_type = self._check_expr(e.expr)
            result_parts = generic_parts(result_type)
            return_parts = generic_parts(self.current_ret)
            if (not result_parts or result_parts[0] != "Result"
                    or len(result_parts[1]) != 2):
                self._error("try expects a Result<Value, Error> expression", e)
            if (not return_parts or return_parts[0] != "Result"
                    or len(return_parts[1]) != 2):
                self._error("try requires the enclosing function to return Result", e)
            result_variants = self.enums.get(result_type, {})
            return_variants = self.enums.get(self.current_ret, {})
            if (result_variants.get("Ok") is None
                    or result_variants.get("Err") is None):
                self._error("try requires Result to define payload variants Ok and Err", e)
            if return_variants.get("Err") is None:
                self._error("the enclosing Result must define a payload Err variant", e)
            value_type, error_type = result_parts[1]
            _, return_error_type = return_parts[1]
            if error_type != return_error_type:
                self._error(
                    f"try error type {error_type} does not match return error type "
                    f"{return_error_type}", e)
            e.result_type = result_type
            e.error_type = error_type
            e.return_type = self.current_ret
            return value_type

        if isinstance(e, A.Unary):
            if e.op == "&":
                ot = self._check_expr(e.operand)
                if not self._is_lvalue(e.operand):
                    self._error("cannot take the address of this expression", e)
                return "*" + ot
            if e.op == "*":
                ot = self._check_expr(e.operand)
                if not is_ptr(ot):
                    self._error(f"cannot dereference a non-pointer value of type {ot}", e)
                return pointee(ot)
            if e.op == "-":
                ot = self._check_expr(e.operand)
                if ot not in INT_TYPES:
                    self._error("unary '-' requires an integer", e)
                e.is_lit = e.operand.is_lit
                return ot
            if e.op == "!":
                if self._check_expr(e.operand) != "bool":
                    self._error("unary '!' requires a bool", e)
                return "bool"
            if e.op == "~":
                ot = self._check_expr(e.operand)
                if ot not in INT_TYPES:
                    self._error("unary '~' requires an integer", e)
                e.is_lit = e.operand.is_lit
                return ot

        if isinstance(e, A.Binary):
            lt = self._check_expr(e.left)
            rt = self._check_expr(e.right)
            op = e.op
            if op in ARITH_OPS or op in REL_OPS:
                res = self._unify_ints(e, lt, rt, op)
                if op in ARITH_OPS:
                    e.is_lit = e.left.is_lit and e.right.is_lit
                    return res
                return "bool"
            if op in ("&", "|", "^"):
                res = self._unify_ints(e, lt, rt, op)
                e.is_lit = e.left.is_lit and e.right.is_lit
                return res
            if op in ("<<", ">>"):
                # the shift count needn't match the value's type; result is the
                # left operand's integer type.
                if lt not in INT_TYPES or rt not in INT_TYPES:
                    self._error(f"operator '{op}' requires int operands", e)
                count = self._const_value(e.right)
                if count is not None and count < 0:
                    self._error(f"shift count cannot be negative ({count})", e)
                e.is_lit = e.left.is_lit and e.right.is_lit
                return lt
            if op in ("==", "!="):
                if lt in INT_TYPES and rt in INT_TYPES:
                    self._unify_ints(e, lt, rt, op)
                elif lt != rt:
                    self._error(f"operator '{op}' needs operands of the same type", e)
                elif lt in self.structs or (
                        lt in self.enums
                        and any(value is not None for value in self.enums[lt].values())):
                    self._error(f"cannot compare aggregate values with '{op}'", e)
                return "bool"
            if op in ("&&", "||"):
                if lt != "bool" or rt != "bool":
                    self._error(f"operator '{op}' requires bool operands", e)
                return "bool"

        if isinstance(e, A.StructLit):
            self._valid_type(e.name)
            if e.name not in self.structs:
                self._error(f"unknown struct {e.name!r}", e)
            declared = self.structs[e.name]
            seen = set()
            for fname, fexpr in e.fields:
                if fname not in declared:
                    self._error(f"struct {e.name!r} has no field {fname!r}", e)
                if fname in seen:
                    self._error(f"field {fname!r} set twice", e)
                seen.add(fname)
                ftype = declared[fname]
                if is_array(ftype) and isinstance(fexpr, (A.ArrayLit, A.ArrayRepeat)):
                    self._check_array_expr(fexpr, ftype, e)
                else:
                    self._check_expr(fexpr)
                    if not self._coerce(ftype, fexpr):
                        self._error(
                            f"field {fname!r} expects {ftype}, got {fexpr.type}", e)
            missing = [f for f in declared if f not in seen]
            if missing:
                self._error(
                    f"struct {e.name!r} literal is missing field(s): "
                    f"{', '.join(missing)}", e)
            return e.name

        if isinstance(e, A.FieldAccess):
            if isinstance(e.obj, A.Var):
                self._valid_type(e.obj.name)
            if isinstance(e.obj, A.Var) and e.obj.name in self.enums:
                enum_name = e.obj.name
                if e.field not in self.enums[enum_name]:
                    self._error(f"enum {enum_name!r} has no variant {e.field!r}", e)
                if self.enums[enum_name][e.field] is not None:
                    self._error(
                        f"variant {enum_name}.{e.field} requires a payload", e)
                return enum_name
            ot = self._check_expr(e.obj)
            if is_slice(ot):
                if e.field == "len":
                    return "u64"
                if e.field == "data":
                    prefix = "*const " if is_const_slice(ot) else "*"
                    return prefix + slice_elem(ot)
                self._error(f"slice has no field {e.field!r}", e)
            if is_ptr(ot):
                self._error(
                    f"cannot access a field through a pointer; "
                    f"write (*expr).{e.field} to dereference first", e)
            if ot not in self.structs:
                self._error(f"type {ot} has no fields", e)
            if e.field not in self.structs[ot]:
                self._error(f"struct {ot!r} has no field {e.field!r}", e)
            return self.structs[ot][e.field]

        if isinstance(e, A.Index):
            ot = self._check_expr(e.obj)
            if not is_array(ot) and not is_slice(ot) and not is_ptr(ot):
                self._error(
                    f"cannot index a value of type {ot} (not an array, slice, or pointer)", e)
            it = self._check_expr(e.index)
            if it not in INT_TYPES:
                self._error(f"array index must be an integer, got {it}", e)
            if is_ptr(ot):
                elem = pointee(ot)
                if elem == "void":
                    self._error("cannot index a *void pointer; cast it to an element pointer first", e)
                return elem
            if is_slice(ot):
                return slice_elem(ot)
            elem, length = array_parts(ot)
            constant = self._const_value(e.index)
            if constant is not None and not (0 <= constant < length):
                self._error(
                    f"array index {constant} is out of bounds for length {length}", e)
            return elem

        if isinstance(e, (A.ArrayLit, A.ArrayRepeat)):
            self._error(
                "an array literal is only allowed as a variable's initialiser "
                "(let x: [T; N] = ...)", e)

        if isinstance(e, A.Call):
            return self._infer_call(e)

        self._error("cannot type this expression", e)  # pragma: no cover

    @staticmethod
    def _value_str(v):
        # Avoid str() on astronomically large ints (Python's 4300-digit cap).
        return str(v) if v.bit_length() < 128 else f"~2^{v.bit_length()}"

    def _retag_literal(self, node, target):
        """Adopt `target` for an int-literal operand, range-checking its value."""
        value = self._const_value(node)
        if value is not None:
            lo, hi = INT_RANGES[target]
            if not (lo <= value <= hi):
                self._error(
                    f"integer literal {self._value_str(value)} does not fit in "
                    f"{target} (range {lo}..{hi})", node)
        node.type = target

    def _unify_ints(self, e, lt, rt, op):
        """Both operands must end up the same integer type; literals adapt."""
        if lt not in INT_TYPES or rt not in INT_TYPES:
            self._error(f"operator '{op}' requires int operands", e)
        if lt == rt:
            return lt
        if e.left.is_lit and not e.right.is_lit:
            self._retag_literal(e.left, rt)
            return rt
        if e.right.is_lit and not e.left.is_lit:
            self._retag_literal(e.right, lt)
            return lt
        if e.left.is_lit and e.right.is_lit:
            return "i64"
        self._error(
            f"mismatched integer types {lt} and {rt}; add an 'as' cast", e)

    def _infer_call(self, e):
        if "." in e.name:
            enum_name, variant_name = e.name.rsplit(".", 1)
            self._valid_type(enum_name)
            if enum_name in self.enums and variant_name in self.enums[enum_name]:
                payload_type = self.enums[enum_name][variant_name]
                if payload_type is None:
                    self._error(
                        f"variant {enum_name}.{variant_name} has no payload", e)
                if len(e.args) != 1:
                    self._error(
                        f"variant {enum_name}.{variant_name} expects 1 payload", e)
                actual = self._check_expr(e.args[0])
                if not self._coerce(payload_type, e.args[0]):
                    self._error(
                        f"variant {enum_name}.{variant_name} expects {payload_type}, "
                        f"got {actual}", e)
                e.enum_name = enum_name
                e.enum_variant = variant_name
                return enum_name
        if e.name == "sizeof":
            if e.args or len(e.type_args) != 1:
                self._error("sizeof expects exactly one type argument and no values", e)
            target = e.type_args[0]
            if target == "void" or not self._valid_type(target):
                self._error(f"sizeof has invalid type argument {target}", e)
            return "u64"
        if e.name == "print":
            if self.freestanding:
                self._error(
                    "print is not available in freestanding mode; "
                    "write to hardware directly (e.g. the VGA buffer)", e)
            if len(e.args) != 1:
                self._error("print expects exactly 1 argument", e)
            at = self._check_expr(e.args[0])
            if at not in INT_TYPES:
                self._error(f"print expects an integer, got {at}", e)
            return "void"
        if e.name == "println":
            if self.freestanding:
                self._error("println is not available in freestanding mode", e)
            if len(e.args) != 1:
                self._error("println expects exactly 1 argument", e)
            at = self._check_expr(e.args[0])
            if at != "*u8":
                self._error(f"println expects a string (*u8), got {at}", e)
            return "void"
        if e.name == "assert":
            if self.freestanding:
                self._error("assert is not available in freestanding mode", e)
            if len(e.args) != 1:
                self._error("assert expects exactly 1 argument", e)
            if self._check_expr(e.args[0]) != "bool":
                self._error("assert expects a bool", e)
            return "void"
        if e.name == "alloc":
            if self.freestanding:
                self._error("alloc is not available in freestanding mode", e)
            if len(e.args) != 1:
                self._error("alloc expects exactly 1 argument", e)
            self._check_expr(e.args[0])
            if not self._coerce("u64", e.args[0]):
                self._error(f"alloc size must be u64, got {e.args[0].type}", e)
            return "*void"
        if e.name == "free":
            if self.freestanding:
                self._error("free is not available in freestanding mode", e)
            if len(e.args) != 1:
                self._error("free expects exactly 1 argument", e)
            at = self._check_expr(e.args[0])
            if not is_ptr(at):
                self._error(f"free expects a pointer, got {at}", e)
            return "void"
        if e.name == "len":
            if len(e.args) != 1:
                self._error("len expects exactly 1 argument", e)
            at = self._check_expr(e.args[0])
            if not is_array(at) and not is_slice(at) and at != "*u8":
                self._error(f"len expects an array, slice, or string, got {at}", e)
            return "u64"
        if e.name == "slice":
            if len(e.args) != 2:
                self._error("slice expects 2 arguments (pointer, length)", e)
            pointer_type = self._check_expr(e.args[0])
            if not is_ptr(pointer_type) or pointee(pointer_type) == "void":
                self._error("slice expects a typed pointer as its first argument", e)
            self._check_expr(e.args[1])
            if not self._coerce("u64", e.args[1]):
                self._error(f"slice length must be u64, got {e.args[1].type}", e)
            prefix = "[]const " if is_const_ptr(pointer_type) else "[]"
            return prefix + pointee(pointer_type)
        if e.name == "outb":
            # outb(port: u16, value: u8) — write a byte to an I/O port
            if len(e.args) != 2:
                self._error("outb expects 2 arguments (port, value)", e)
            self._check_expr(e.args[0])
            if not self._coerce("u16", e.args[0]):
                self._error(f"outb port must be u16, got {e.args[0].type}", e)
            self._check_expr(e.args[1])
            if not self._coerce("u8", e.args[1]):
                self._error(f"outb value must be u8, got {e.args[1].type}", e)
            return "void"
        if e.name == "inb":
            # inb(port: u16) -> u8 — read a byte from an I/O port
            if len(e.args) != 1:
                self._error("inb expects 1 argument (port)", e)
            self._check_expr(e.args[0])
            if not self._coerce("u16", e.args[0]):
                self._error(f"inb port must be u16, got {e.args[0].type}", e)
            return "u8"
        if e.name == "outw":
            # outw(port: u16, value: u16) — write a 16-bit word to an I/O port
            if len(e.args) != 2:
                self._error("outw expects 2 arguments (port, value)", e)
            self._check_expr(e.args[0])
            if not self._coerce("u16", e.args[0]):
                self._error(f"outw port must be u16, got {e.args[0].type}", e)
            self._check_expr(e.args[1])
            if not self._coerce("u16", e.args[1]):
                self._error(f"outw value must be u16, got {e.args[1].type}", e)
            return "void"
        if e.name == "inw":
            # inw(port: u16) -> u16 — read a 16-bit word from an I/O port
            if len(e.args) != 1:
                self._error("inw expects 1 argument (port)", e)
            self._check_expr(e.args[0])
            if not self._coerce("u16", e.args[0]):
                self._error(f"inw port must be u16, got {e.args[0].type}", e)
            return "u16"
        if e.name == "outl":
            # outl(port: u16, value: u32) — write a 32-bit dword to an I/O port
            if len(e.args) != 2:
                self._error("outl expects 2 arguments (port, value)", e)
            self._check_expr(e.args[0])
            if not self._coerce("u16", e.args[0]):
                self._error(f"outl port must be u16, got {e.args[0].type}", e)
            self._check_expr(e.args[1])
            if not self._coerce("u32", e.args[1]):
                self._error(f"outl value must be u32, got {e.args[1].type}", e)
            return "void"
        if e.name == "inl":
            # inl(port: u16) -> u32 — read a 32-bit dword from an I/O port
            if len(e.args) != 1:
                self._error("inl expects 1 argument (port)", e)
            self._check_expr(e.args[0])
            if not self._coerce("u16", e.args[0]):
                self._error(f"inl port must be u16, got {e.args[0].type}", e)
            return "u32"
        requested = e.name
        if "." in requested:
            alias, member = requested.split(".", 1)
            module = self.current_import_aliases.get(alias)
            if module is None:
                self._error(f"unknown module or import alias {alias!r}", e)
            resolved = f"{module}.{member}"
        else:
            local = f"{self.current_module}.{requested}" if self.current_module else None
            resolved = (
                local
                if local in self.funcs or local in self.func_templates
                else requested
            )
        if resolved not in self.funcs and resolved not in self.func_templates:
            self._error(f"call to undefined function {requested!r}", e)
        declaration = (
            self.func_templates[resolved]
            if resolved in self.func_templates
            else self.func_decls[resolved]
        )
        target_module = getattr(declaration, "module", None)
        if (target_module is not None and target_module != self.current_module
                and not getattr(declaration, "public", False)):
            self._error(f"function {requested!r} is private to module {target_module!r}", e)
        template = self.func_templates.get(resolved)
        if template is None and e.type_args:
            self._error(
                f"function {e.name!r} is not generic and cannot take type arguments", e)
        ptypes = (
            [parameter.typ for parameter in template.params]
            if template is not None else self.funcs[resolved][0]
        )
        if len(e.args) != len(ptypes):
            self._error(
                f"function {e.name!r} expects {len(ptypes)} argument(s), "
                f"got {len(e.args)}", e)
        actual_types = [self._check_expr(argument) for argument in e.args]
        if template is not None:
            if e.type_args and len(e.type_args) != len(template.generic_params):
                self._error(
                    f"generic function {e.name!r} expects "
                    f"{len(template.generic_params)} type argument(s), "
                    f"got {len(e.type_args)}",
                    e,
                )
            for type_argument in e.type_args:
                if type_argument == "void" or not self._valid_type(type_argument):
                    self._error(
                        f"generic function {e.name!r} has invalid type argument "
                        f"{type_argument}",
                        e,
                    )
            mapping = dict(zip(template.generic_params, e.type_args))
            generic_params = set(template.generic_params)
            if not e.type_args:
                for index, (pattern, actual) in enumerate(
                        zip(ptypes, actual_types), start=1):
                    if not self._unify_generic_type(
                            pattern, actual, generic_params, mapping):
                        self._error(
                            f"argument {index} of {e.name!r} cannot consistently infer "
                            f"generic type from {pattern} and {actual}",
                            e,
                        )
            resolved = self._instantiate_function(resolved, mapping, e)
        e.resolved_name = resolved
        ptypes, ret = self.funcs[resolved]
        for idx, (arg, pt) in enumerate(zip(e.args, ptypes), start=1):
            if not self._coerce(pt, arg):
                self._error(
                    f"argument {idx} of {e.name!r} expects {pt}, got {arg.type}", e)
        return ret
