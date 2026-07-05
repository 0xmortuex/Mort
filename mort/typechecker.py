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


def is_ptr(t):
    return isinstance(t, str) and t.startswith("*")


def pointee(t):
    return t[1:]


class Checker:
    def __init__(self, program):
        self.program = program
        self.funcs = {}       # name -> (param_types, ret)
        self.scopes = []
        self.current_ret = None

    def _error(self, msg, node):
        raise MortError(msg, getattr(node, "line", None))

    def check(self):
        for f in self.program.funcs:
            if f.name in self.funcs or f.name == "print":
                self._error(f"function {f.name!r} is already defined", f)
            self.funcs[f.name] = ([p.typ for p in f.params], f.ret)

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
        return None

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
    def _coerce(self, expected, expr):
        """Return True if expr fits `expected`, retagging an untyped int literal."""
        if expr.type == expected:
            return True
        if expected in INT_TYPES and expr.is_lit:
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
        return isinstance(e, A.Var) or (isinstance(e, A.Unary) and e.op == "*")

    def _infer(self, e):
        if isinstance(e, A.IntLit):
            e.is_lit = True
            return "i64"
        if isinstance(e, A.BoolLit):
            return "bool"
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
                return "bool"
            if op in ("&&", "||"):
                if lt != "bool" or rt != "bool":
                    self._error(f"operator '{op}' requires bool operands", e)
                return "bool"

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
            if len(e.args) != 1:
                self._error("print expects exactly 1 argument", e)
            at = self._check_expr(e.args[0])
            if at not in INT_TYPES:
                self._error(f"print expects an integer, got {at}", e)
            return "void"
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
