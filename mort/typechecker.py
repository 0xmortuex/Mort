"""Static type checker.

Validates types across the whole program and annotates every expression node
with a resolved ``.type`` ('int' or 'bool') that codegen relies on. Also records
each ``let``'s inferred type on ``.var_type``.
"""
from .errors import MortError
from . import mort_ast as A

# builtin name -> (param_types, return_type)
BUILTINS = {
    "print": (["int"], "void"),
}


class Checker:
    def __init__(self, program):
        self.program = program
        self.funcs = {}       # name -> (param_types, ret)
        self.scopes = []      # list of {name: type}
        self.current_ret = None

    def _error(self, msg, node):
        raise MortError(msg, getattr(node, "line", None))

    def check(self):
        # 1. collect signatures so functions can call each other in any order
        for f in self.program.funcs:
            if f.name in self.funcs or f.name in BUILTINS:
                self._error(f"function {f.name!r} is already defined", f)
            self.funcs[f.name] = ([p.typ for p in f.params], f.ret)

        # 2. entry-point rules
        if "main" not in self.funcs:
            raise MortError("no 'main' function defined")
        params, ret = self.funcs["main"]
        if params:
            raise MortError("'main' must take no parameters")
        if ret != "int":
            raise MortError("'main' must return int")

        # 3. check each body
        for f in self.program.funcs:
            self._check_fn(f)
        return self.program

    # ----- scope helpers -----
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
        # function body shares the parameter scope (no extra push)
        for s in f.body.stmts:
            self._check_stmt(s)

    def _check_block(self, block):
        self.scopes.append({})
        for s in block.stmts:
            self._check_stmt(s)
        self.scopes.pop()

    # ----- statements -----
    def _check_stmt(self, s):
        if isinstance(s, A.Let):
            t = self._check_expr(s.expr)
            if t == "void":
                self._error("cannot bind a void value to a variable", s)
            if s.decl_type and s.decl_type != t:
                self._error(
                    f"type mismatch: {s.name!r} is annotated {s.decl_type} "
                    f"but the value is {t}", s)
            s.var_type = t
            self._declare(s.name, t, s)

        elif isinstance(s, A.Assign):
            vt = self._lookup(s.name)
            if vt is None:
                self._error(f"assignment to undefined variable {s.name!r}", s)
            t = self._check_expr(s.expr)
            if t != vt:
                self._error(f"type mismatch: {s.name!r} is {vt} but assigned {t}", s)

        elif isinstance(s, A.Return):
            if s.expr is None:
                if self.current_ret != "void":
                    self._error(f"this function must return {self.current_ret}", s)
            else:
                t = self._check_expr(s.expr)
                if t != self.current_ret:
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

        else:  # pragma: no cover - defensive
            self._error("unknown statement kind", s)

    # ----- expressions -----
    def _check_expr(self, e):
        t = self._infer(e)
        e.type = t
        return t

    def _infer(self, e):
        if isinstance(e, A.IntLit):
            return "int"
        if isinstance(e, A.BoolLit):
            return "bool"
        if isinstance(e, A.Var):
            vt = self._lookup(e.name)
            if vt is None:
                self._error(f"undefined variable {e.name!r}", e)
            return vt
        if isinstance(e, A.Unary):
            ot = self._check_expr(e.operand)
            if e.op == "-":
                if ot != "int":
                    self._error("unary '-' requires an int", e)
                return "int"
            if e.op == "!":
                if ot != "bool":
                    self._error("unary '!' requires a bool", e)
                return "bool"
        if isinstance(e, A.Binary):
            lt = self._check_expr(e.left)
            rt = self._check_expr(e.right)
            op = e.op
            if op in ("+", "-", "*", "/", "%"):
                if lt != "int" or rt != "int":
                    self._error(f"operator '{op}' requires int operands", e)
                return "int"
            if op in ("<", ">", "<=", ">="):
                if lt != "int" or rt != "int":
                    self._error(f"operator '{op}' requires int operands", e)
                return "bool"
            if op in ("==", "!="):
                if lt != rt:
                    self._error(f"operator '{op}' needs operands of the same type", e)
                return "bool"
            if op in ("&&", "||"):
                if lt != "bool" or rt != "bool":
                    self._error(f"operator '{op}' requires bool operands", e)
                return "bool"
        if isinstance(e, A.Call):
            if e.name in BUILTINS:
                ptypes, ret = BUILTINS[e.name]
            elif e.name in self.funcs:
                ptypes, ret = self.funcs[e.name]
            else:
                self._error(f"call to undefined function {e.name!r}", e)
            if len(e.args) != len(ptypes):
                self._error(
                    f"function {e.name!r} expects {len(ptypes)} argument(s), "
                    f"got {len(e.args)}", e)
            for idx, (arg, pt) in enumerate(zip(e.args, ptypes), start=1):
                at = self._check_expr(arg)
                if at != pt:
                    self._error(
                        f"argument {idx} of {e.name!r} expects {pt}, got {at}", e)
            return ret
        self._error("cannot type this expression", e)  # pragma: no cover
