"""Static type checker.

Types are represented as strings:
  * integers: 'i8','i16','i32','i64','u8','u16','u32','u64'
  * 'bool', 'void'
  * pointers: '*' + inner, e.g. '*i32', '**u8'

Integer literals are "untyped" (``node.is_lit``) and coerce to any integer type
in a context that expects one (let annotations, assignments, returns, args, and
the other operand of a binary op). Everything else needs an explicit ``as`` cast.
"""
from .errors import MortError
from . import mort_ast as A

INT_TYPES = {"i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64"}
ARITH_OPS = {"+", "-", "*", "/", "%"}
REL_OPS = {"<", ">", "<=", ">="}
BUILTIN_NAMES = {"print", "outb", "inb"}

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
    return t[1:]


class Checker:
    def __init__(self, program, freestanding=False):
        self.program = program
        self.freestanding = freestanding
        self.funcs = {}       # name -> (param_types, ret)
        self.structs = {}     # name -> {field: type}  (insertion-ordered)
        self.globals = {}     # name -> type
        self.scopes = []
        self.current_ret = None

    def _error(self, msg, node):
        raise MortError(msg, getattr(node, "line", None))

    def _base(self, t):
        while is_ptr(t):
            t = pointee(t)
        return t

    def _valid_type(self, t):
        """A type is usable as a value/field/param if its base is known."""
        return self._base(t) in INT_TYPES or self._base(t) in ("bool",) or self._base(t) in self.structs

    def check(self):
        # 1. collect struct names first so fields may reference any struct
        #    (including recursively through pointers, e.g. `next: *Node`).
        for sd in self.program.structs:
            if sd.name in self.structs:
                self._error(f"struct {sd.name!r} is already defined", sd)
            self.structs[sd.name] = None  # placeholder until fields validated

        # 2. validate each struct's fields
        for sd in self.program.structs:
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

        # 3. collect and validate function signatures
        for f in self.program.funcs:
            if f.name in self.funcs or f.name in BUILTIN_NAMES:
                self._error(f"function {f.name!r} is already defined", f)
            for p in f.params:
                if not self._valid_type(p.typ):
                    self._error(f"parameter {p.name!r} has unknown type {p.typ}", f)
            if f.ret != "void" and not self._valid_type(f.ret):
                self._error(f"function {f.name!r} has unknown return type {f.ret}", f)
            self.funcs[f.name] = ([p.typ for p in f.params], f.ret)

        # 4. globals — initialised with a compile-time constant, usable anywhere
        self.scopes = []
        for g in self.program.globals:
            if (g.name in self.globals or g.name in self.funcs
                    or g.name in self.structs or g.name in BUILTIN_NAMES):
                self._error(f"global {g.name!r} conflicts with another name", g)
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
        if not self.freestanding:
            if "main" not in self.funcs:
                raise MortError("no 'main' function defined")
            params, ret = self.funcs["main"]
            if params:
                raise MortError("'main' must take no parameters")
            if ret != "i64":
                raise MortError("'main' must return int")

        for f in self.program.funcs:
            self._check_fn(f)
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
        return bool(getattr(expr, "is_lit", False))  # int literal expression

    def _declare(self, name, typ, node):
        if name in self.scopes[-1]:
            self._error(f"variable {name!r} already declared in this scope", node)
        self.scopes[-1][name] = typ

    def _check_fn(self, f):
        self.current_ret = f.ret
        self.scopes = [{}]
        for p in f.params:
            self._declare(p.name, p.typ, f)
        for s in f.body.stmts:
            self._check_stmt(s)

    def _check_block(self, block):
        self.scopes.append({})
        for s in block.stmts:
            self._check_stmt(s)
        self.scopes.pop()

    # ----- coercion -----
    def _const_value(self, e):
        """Evaluate a constant integer-literal expression, or None if not one."""
        if isinstance(e, A.IntLit):
            return e.value
        if isinstance(e, A.Unary) and e.op == "-":
            v = self._const_value(e.operand)
            return None if v is None else -v
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
        return None

    def _coerce(self, expected, expr):
        """Return True if expr fits `expected`, retagging an untyped int literal.

        An untyped integer literal adopts the expected integer type — but its
        value must actually fit, so hardware writes like outb(0x12345, ...) or a
        `let x: u8 = 300;` are compile errors, not silent truncation.
        """
        if expr.type == expected:
            return True
        if expected in INT_TYPES and expr.is_lit:
            value = self._const_value(expr)
            if value is not None:
                lo, hi = INT_RANGES[expected]
                if not (lo <= value <= hi):
                    self._error(
                        f"integer literal {value} does not fit in {expected} "
                        f"(range {lo}..{hi})", expr)
            expr.type = expected  # untyped literal adopts the expected int type
            return True
        return False

    # ----- statements -----
    def _check_stmt(self, s):
        if isinstance(s, A.Let):
            t = self._check_expr(s.expr)
            if t == "void":
                self._error("cannot bind a void value to a variable", s)
            if s.decl_type:
                if not self._valid_type(s.decl_type):
                    self._error(f"variable {s.name!r} has unknown type {s.decl_type}", s)
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
            self._check_block(s.body)

        elif isinstance(s, A.Block):
            self._check_block(s)

        elif isinstance(s, A.ExprStmt):
            self._check_expr(s.expr)

        elif isinstance(s, A.Asm):
            pass  # an opaque escape hatch; nothing to type-check

        else:  # pragma: no cover
            self._error("unknown statement kind", s)

    # ----- expressions -----
    def _check_expr(self, e):
        e.is_lit = False
        t = self._infer(e)
        e.type = t
        return t

    @staticmethod
    def _is_lvalue(e):
        return (
            isinstance(e, A.Var)
            or (isinstance(e, A.Unary) and e.op == "*")
            or isinstance(e, A.FieldAccess)
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
            src_ok = st in INT_TYPES or is_ptr(st)
            tgt_ok = tgt in INT_TYPES or is_ptr(tgt)
            if not (src_ok and tgt_ok):
                self._error(f"cannot cast {st} to {tgt}", e)
            return tgt

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
            if op in ("==", "!="):
                if lt in INT_TYPES and rt in INT_TYPES:
                    self._unify_ints(e, lt, rt, op)
                elif lt != rt:
                    self._error(f"operator '{op}' needs operands of the same type", e)
                elif lt in self.structs:
                    self._error(f"cannot compare structs with '{op}'", e)
                return "bool"
            if op in ("&&", "||"):
                if lt != "bool" or rt != "bool":
                    self._error(f"operator '{op}' requires bool operands", e)
                return "bool"

        if isinstance(e, A.StructLit):
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
                self._check_expr(fexpr)
                if not self._coerce(declared[fname], fexpr):
                    self._error(
                        f"field {fname!r} expects {declared[fname]}, got {fexpr.type}", e)
            missing = [f for f in declared if f not in seen]
            if missing:
                self._error(
                    f"struct {e.name!r} literal is missing field(s): "
                    f"{', '.join(missing)}", e)
            return e.name

        if isinstance(e, A.FieldAccess):
            ot = self._check_expr(e.obj)
            if is_ptr(ot):
                self._error(
                    f"cannot access a field through a pointer; "
                    f"write (*expr).{e.field} to dereference first", e)
            if ot not in self.structs:
                self._error(f"type {ot} has no fields", e)
            if e.field not in self.structs[ot]:
                self._error(f"struct {ot!r} has no field {e.field!r}", e)
            return self.structs[ot][e.field]

        if isinstance(e, A.Call):
            return self._infer_call(e)

        self._error("cannot type this expression", e)  # pragma: no cover

    def _unify_ints(self, e, lt, rt, op):
        """Both operands must end up the same integer type; literals adapt."""
        if lt not in INT_TYPES or rt not in INT_TYPES:
            self._error(f"operator '{op}' requires int operands", e)
        if lt == rt:
            return lt
        if e.left.is_lit and not e.right.is_lit:
            e.left.type = rt
            return rt
        if e.right.is_lit and not e.left.is_lit:
            e.right.type = lt
            return lt
        if e.left.is_lit and e.right.is_lit:
            return "i64"
        self._error(
            f"mismatched integer types {lt} and {rt}; add an 'as' cast", e)

    def _infer_call(self, e):
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
        if e.name not in self.funcs:
            self._error(f"call to undefined function {e.name!r}", e)
        ptypes, ret = self.funcs[e.name]
        if len(e.args) != len(ptypes):
            self._error(
                f"function {e.name!r} expects {len(ptypes)} argument(s), "
                f"got {len(e.args)}", e)
        for idx, (arg, pt) in enumerate(zip(e.args, ptypes), start=1):
            at = self._check_expr(arg)
            if not self._coerce(pt, arg):
                self._error(
                    f"argument {idx} of {e.name!r} expects {pt}, got {at}", e)
        return ret
