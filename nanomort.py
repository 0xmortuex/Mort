#!/usr/bin/env python3
"""nanomort — a tiny statically-typed language that compiles to C, in one file.

Small enough to read in a sitting; real enough to build a bootable kernel. The
pipeline is the same one production compilers use, just miniature:

    source text  --lex-->  tokens  --parse-->  AST  --check-->  typed AST
                 --emit-->  C11  --(cc / zig cc)-->  native binary or kernel

Usage:
    python3 nanomort.py file.nm --emit-c     # print generated C to stdout
    python3 nanomort.py file.nm --run        # emit C, compile with cc, run it
    python3 nanomort.py file.nm --freestanding --emit-c   # kernel-mode C (no libc)

The language: fn, let/const, if/else, while, return; types int/bool and the
fixed-width ints i8..u64 plus pointers (*T); arithmetic, comparison, bitwise
and logical operators; casts (expr as T); string and char literals; globals;
the builtins print/println and the x86 port-I/O family (inb/outb/inw/outw),
plus asm("...") — enough to poke hardware and write a kernel.
"""
import sys
import subprocess
import tempfile
import os

# ------------------------------------------------------------------ errors
class NanoError(Exception):
    def __init__(self, msg, line):
        super().__init__(f"line {line}: {msg}")
        self.line = line

# ------------------------------------------------------------------ lexer
KEYWORDS = {
    "fn", "let", "const", "return", "if", "else", "while", "true", "false",
    "as", "asm", "int", "bool", "void",
}
# fixed-width integer type names, recognised as type keywords
INT_TYPES = {"i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64"}

# multi-char operators, longest first so <<= never lexes as << then =
_OPS3 = {"<<=", ">>="}
_OPS2 = {"->", "==", "!=", "<=", ">=", "&&", "||", "<<", ">>",
         "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^="}
_OPS1 = set("(){}[],;:.+-*/%<>=!&|^~")


class Tok:
    __slots__ = ("kind", "val", "line")

    def __init__(self, kind, val, line):
        self.kind, self.val, self.line = kind, val, line

    def __repr__(self):
        return f"Tok({self.kind},{self.val!r})"


def lex(src):
    toks, i, line, n = [], 0, 1, len(src)
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1; i += 1; continue
        if c in " \t\r":
            i += 1; continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":         # line comment
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":         # block comment
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                if src[i] == "\n":
                    line += 1
                i += 1
            i += 2
            continue
        if c == '"':                                             # string literal
            i += 1; buf = []
            while i < n and src[i] != '"':
                if src[i] == "\\":
                    i += 1
                    buf.append({"n": "\n", "t": "\t", "0": "\0", "\\": "\\",
                                '"': '"', "r": "\r"}.get(src[i], src[i]))
                else:
                    buf.append(src[i])
                i += 1
            if i >= n:
                raise NanoError("unterminated string", line)
            i += 1
            toks.append(Tok("str", "".join(buf), line))
            continue
        if c == "'":                                             # char literal
            i += 1
            if src[i] == "\\":
                i += 1
                ch = {"n": 10, "t": 9, "0": 0, "\\": 92, "'": 39, "r": 13}[src[i]]
                i += 1
            else:
                ch = ord(src[i]); i += 1
            if src[i] != "'":
                raise NanoError("bad char literal", line)
            i += 1
            toks.append(Tok("int", ch, line))
            continue
        if c.isdigit():                                          # number
            j = i
            if c == "0" and i + 1 < n and src[i + 1] in "xXbBoO":
                base = {"x": 16, "b": 2, "o": 8}[src[i + 1].lower()]
                i += 2; k = i
                digits = "0123456789abcdef"[:base] + "0123456789ABCDEF"[:base] + "_"
                while i < n and src[i] in digits:
                    i += 1
                toks.append(Tok("int", int(src[k:i].replace("_", ""), base), line))
            else:
                while i < n and (src[i].isdigit() or src[i] == "_"):
                    i += 1
                toks.append(Tok("int", int(src[j:i].replace("_", "")), line))
            continue
        if c.isalpha() or c == "_":                              # ident / keyword
            j = i
            while i < n and (src[i].isalnum() or src[i] == "_"):
                i += 1
            word = src[j:i]
            if word in KEYWORDS or word in INT_TYPES:
                toks.append(Tok(word, word, line))
            else:
                toks.append(Tok("ident", word, line))
            continue
        three = src[i:i + 3]
        if three in _OPS3:
            toks.append(Tok(three, three, line)); i += 3; continue
        two = src[i:i + 2]
        if two in _OPS2:
            toks.append(Tok(two, two, line)); i += 2; continue
        if c in _OPS1:
            toks.append(Tok(c, c, line)); i += 1; continue
        raise NanoError(f"unexpected character {c!r}", line)
    toks.append(Tok("eof", None, line))
    return toks

# ------------------------------------------------------------------ AST
# Plain records — name, fields, line. No behaviour lives here; the passes
# below walk these and either annotate them (checker) or emit C (codegen).
class Node:
    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.type = None            # stamped by the checker

    def __repr__(self):
        return f"{self.kind}({self.__dict__})"


def N(kind, line, **kw):
    return Node(kind=kind, line=line, **kw)

# ------------------------------------------------------------------ parser
class Parser:
    def __init__(self, toks):
        self.toks = toks
        self.i = 0

    def peek(self, o=0):
        return self.toks[self.i + o]

    def at(self, kind):
        return self.peek().kind == kind

    def advance(self):
        t = self.toks[self.i]; self.i += 1; return t

    def expect(self, kind):
        t = self.peek()
        if t.kind != kind:
            raise NanoError(f"expected {kind!r}, got {t.val!r}", t.line)
        return self.advance()

    # program := (fn | global)*
    def parse(self):
        decls = []
        while not self.at("eof"):
            if self.at("fn"):
                decls.append(self.fn())
            elif self.at("let") or self.at("const"):
                decls.append(self.global_var())
            else:
                t = self.peek()
                raise NanoError(f"expected fn or global, got {t.val!r}", t.line)
        return decls

    def type(self):
        t = self.peek()
        if t.kind == "*":
            self.advance()
            return N("ptr", t.line, inner=self.type())
        if t.kind in ("int", "bool", "void") or t.kind in INT_TYPES:
            self.advance()
            return N("named", t.line, name=t.val)
        raise NanoError(f"expected a type, got {t.val!r}", t.line)

    def fn(self):
        line = self.expect("fn").line
        name = self.expect("ident").val
        self.expect("(")
        params = []
        while not self.at(")"):
            pname = self.expect("ident").val
            self.expect(":")
            params.append((pname, self.type()))
            if not self.at(")"):
                self.expect(",")
        self.expect(")")
        ret = N("named", line, name="void")
        if self.at("->"):
            self.advance()
            ret = self.type()
        body = self.block()
        return N("fn", line, name=name, params=params, ret=ret, body=body)

    def global_var(self):
        kw = self.advance()                     # let | const
        name = self.expect("ident").val
        vtype = None
        if self.at(":"):
            self.advance(); vtype = self.type()
        self.expect("=")
        value = self.expr()
        self.expect(";")
        return N("global", kw.line, name=name, vtype=vtype,
                 value=value, is_const=(kw.kind == "const"))

    def block(self):
        line = self.expect("{").line
        stmts = []
        while not self.at("}"):
            stmts.append(self.stmt())
        self.expect("}")
        return N("block", line, stmts=stmts)

    def stmt(self):
        t = self.peek()
        if t.kind in ("let", "const"):
            kw = self.advance()
            name = self.expect("ident").val
            vtype = None
            if self.at(":"):
                self.advance(); vtype = self.type()
            self.expect("=")
            value = self.expr()
            self.expect(";")
            return N("let", t.line, name=name, vtype=vtype, value=value,
                     is_const=(kw.kind == "const"))
        if t.kind == "return":
            self.advance()
            value = None if self.at(";") else self.expr()
            self.expect(";")
            return N("return", t.line, value=value)
        if t.kind == "if":
            return self.if_stmt()
        if t.kind == "while":
            self.advance()
            cond = self.expr()
            body = self.block()
            return N("while", t.line, cond=cond, body=body)
        if t.kind == "asm":
            self.advance(); self.expect("(")
            code = self.expect("str").val
            self.expect(")"); self.expect(";")
            return N("asm", t.line, code=code)
        if t.kind == "{":
            return self.block()
        # expression or assignment
        e = self.expr()
        if self.peek().kind in ("=", "+=", "-=", "*=", "/=", "%=",
                                 "&=", "|=", "^=", "<<=", ">>="):
            op = self.advance().kind
            rhs = self.expr()
            self.expect(";")
            return N("assign", t.line, op=op, target=e, value=rhs)
        self.expect(";")
        return N("exprstmt", t.line, expr=e)

    def if_stmt(self):
        line = self.expect("if").line
        cond = self.expr()
        then = self.block()
        els = None
        if self.at("else"):
            self.advance()
            els = self.if_stmt() if self.at("if") else self.block()
        return N("if", line, cond=cond, then=then, els=els)

    # ---- expressions: a precedence ladder, lowest first ----
    def expr(self):
        return self.binary(0)

    # precedence table: each row binds tighter than the one above
    LEVELS = [
        ("||",),
        ("&&",),
        ("==", "!="),
        ("<", ">", "<=", ">="),
        ("|",), ("^",), ("&",),
        ("<<", ">>"),
        ("+", "-"),
        ("*", "/", "%"),
    ]

    def binary(self, level):
        if level >= len(self.LEVELS):
            return self.unary()
        left = self.binary(level + 1)
        while self.peek().kind in self.LEVELS[level]:
            op = self.advance()
            right = self.binary(level + 1)
            left = N("binary", op.line, op=op.kind, left=left, right=right)
        return left

    def unary(self):
        t = self.peek()
        if t.kind in ("-", "!", "~"):
            self.advance()
            return N("unary", t.line, op=t.kind, operand=self.unary())
        if t.kind == "*":                       # dereference
            self.advance()
            return N("deref", t.line, operand=self.unary())
        if t.kind == "&":                       # address-of
            self.advance()
            return N("addr", t.line, operand=self.unary())
        return self.postfix()

    def postfix(self):
        e = self.primary()
        while True:
            t = self.peek()
            if t.kind == "(":                   # call
                self.advance()
                args = []
                while not self.at(")"):
                    args.append(self.expr())
                    if not self.at(")"):
                        self.expect(",")
                self.expect(")")
                e = N("call", t.line, callee=e, args=args)
            elif t.kind == "[":                 # index
                self.advance()
                idx = self.expr()
                self.expect("]")
                e = N("index", t.line, base=e, index=idx)
            elif t.kind == "as":                # cast
                self.advance()
                e = N("cast", t.line, inner=e, target=self.type())
            else:
                return e

    def primary(self):
        t = self.peek()
        if t.kind == "int":
            self.advance(); return N("int", t.line, value=t.val)
        if t.kind == "str":
            self.advance(); return N("str", t.line, value=t.val)
        if t.kind in ("true", "false"):
            self.advance(); return N("bool", t.line, value=(t.kind == "true"))
        if t.kind == "ident":
            self.advance(); return N("var", t.line, name=t.val)
        if t.kind == "(":
            self.advance(); e = self.expr(); self.expect(")"); return e
        raise NanoError(f"unexpected {t.val!r}", t.line)

# ------------------------------------------------------------------ types
# A type is just a small tuple-ish object. Ints carry width + signedness.
class Ty:
    def __init__(self, kind, width=0, signed=False, inner=None):
        self.kind, self.width, self.signed, self.inner = kind, width, signed, inner

    def __eq__(self, o):
        return (isinstance(o, Ty) and self.kind == o.kind and self.width == o.width
                and self.signed == o.signed and self.inner == o.inner)

    def __repr__(self):
        if self.kind == "ptr":
            return f"*{self.inner}"
        if self.kind == "int":
            return ("i" if self.signed else "u") + str(self.width)
        return self.kind


BOOL = Ty("bool")
VOID = Ty("void")


def int_ty(name):
    return Ty("int", width=int(name[1:]), signed=name[0] == "i")


NAMED = {"int": Ty("int", 64, True), "bool": BOOL, "void": VOID}
for _n in INT_TYPES:
    NAMED[_n] = int_ty(_n)


def resolve(tnode):
    if tnode.kind == "ptr":
        return Ty("ptr", inner=resolve(tnode.inner))
    return NAMED[tnode.name]

# ------------------------------------------------------------------ checker
BUILTINS = {
    # name -> (param types, return type). None param means "any".
    "print":   ([Ty("int", 64, True)], VOID),
    "println": ([Ty("int", 64, True)], VOID),
    "inb": ([Ty("int", 16, False)], Ty("int", 8, False)),
    "inw": ([Ty("int", 16, False)], Ty("int", 16, False)),
    "outb": ([Ty("int", 16, False), Ty("int", 8, False)], VOID),
    "outw": ([Ty("int", 16, False), Ty("int", 16, False)], VOID),
}


class Checker:
    def __init__(self, decls, freestanding=False):
        self.decls = decls
        self.freestanding = freestanding
        self.fns = {}
        self.globals = {}
        self.scopes = []

    def error(self, msg, line):
        raise NanoError(msg, line)

    def lookup(self, name, line):
        for s in reversed(self.scopes):
            if name in s:
                return s[name]
        if name in self.globals:
            return self.globals[name]
        self.error(f"undefined name {name!r}", line)

    def check(self):
        for d in self.decls:                    # collect signatures first
            if d.kind == "fn":
                self.fns[d.name] = ([resolve(t) for _, t in d.params], resolve(d.ret))
        for d in self.decls:
            if d.kind == "global":
                vt = resolve(d.vtype) if d.vtype else self.expr(d.value)
                self.expr(d.value, expected=vt)
                d.type = vt
                self.globals[d.name] = vt
        entry = "kmain" if self.freestanding else "main"
        if entry not in self.fns:
            self.error(f"no '{entry}' function", 0)
        for d in self.decls:
            if d.kind == "fn":
                self.check_fn(d)
        return self.decls

    def check_fn(self, fn):
        self.cur_ret = resolve(fn.ret)
        self.scopes = [{}]
        for pname, t in fn.params:
            self.scopes[-1][pname] = resolve(t)
        self.block(fn.body)

    def block(self, blk):
        self.scopes.append({})
        for s in blk.stmts:
            self.stmt(s)
        self.scopes.pop()

    def stmt(self, s):
        if s.kind == "let":
            vt = resolve(s.vtype) if s.vtype else self.expr(s.value)
            self.expr(s.value, expected=vt)
            s.type = vt
            self.scopes[-1][s.name] = vt
        elif s.kind == "assign":
            tt = self.expr(s.target)
            self.expr(s.value, expected=tt)
        elif s.kind == "return":
            if s.value is not None:
                self.expr(s.value, expected=self.cur_ret)
            elif self.cur_ret != VOID:
                self.error("return needs a value", s.line)
        elif s.kind == "if":
            if self.expr(s.cond) != BOOL:
                self.error("if condition must be bool", s.line)
            self.block(s.then)
            if s.els:
                (self.stmt if s.els.kind == "if" else self.block)(s.els)
        elif s.kind == "while":
            if self.expr(s.cond) != BOOL:
                self.error("while condition must be bool", s.line)
            self.block(s.body)
        elif s.kind == "block":
            self.block(s)
        elif s.kind == "exprstmt":
            self.expr(s.expr)
        elif s.kind == "asm":
            pass
        else:
            self.error(f"cannot check statement {s.kind}", s.line)

    # returns the resolved type and stamps it on the node
    def expr(self, e, expected=None):
        t = self._expr(e, expected)
        e.type = t
        if expected is not None and t != expected:
            # allow untyped integer literals to adopt the expected int type
            if e.kind == "int" and expected.kind == "int":
                e.type = expected
                return expected
            self.error(f"type mismatch: expected {expected}, got {t}", e.line)
        return e.type

    def _expr(self, e, expected):
        k = e.kind
        if k == "int":
            return expected if (expected and expected.kind == "int") else NAMED["int"]
        if k == "bool":
            return BOOL
        if k == "str":
            return Ty("ptr", inner=NAMED["u8"])
        if k == "var":
            return self.lookup(e.name, e.line)
        if k == "unary":
            ot = self.expr(e.operand)
            if e.op == "!" and ot != BOOL:
                self.error("! needs a bool", e.line)
            return ot
        if k == "deref":
            ot = self.expr(e.operand)
            if ot.kind != "ptr":
                self.error("cannot dereference a non-pointer", e.line)
            return ot.inner
        if k == "addr":
            if e.operand.kind not in ("var", "deref", "index"):
                self.error("can only take the address of an lvalue", e.line)
            return Ty("ptr", inner=self.expr(e.operand))
        if k == "cast":
            src = self.expr(e.inner)
            dst = resolve(e.target)
            if src.kind not in ("int", "ptr") or dst.kind not in ("int", "ptr"):
                self.error(f"cannot cast {src} to {dst}", e.line)
            return dst
        if k == "index":
            bt = self.expr(e.base)
            if bt.kind != "ptr":
                self.error("cannot index a non-pointer", e.line)
            self.expr(e.index)
            return bt.inner
        if k == "binary":
            return self.binary(e)
        if k == "call":
            return self.call(e)
        self.error(f"cannot type expression {k}", e.line)

    def binary(self, e):
        lt = self.expr(e.left)
        rt = self.expr(e.right)
        if e.op in ("&&", "||"):
            if lt != BOOL or rt != BOOL:
                self.error("logical operator needs bools", e.line)
            return BOOL
        if e.op in ("==", "!=", "<", ">", "<=", ">="):
            # let an untyped literal side adopt the other side's int type
            if lt.kind == "int" and rt.kind == "int" and lt != rt:
                if e.left.kind == "int":
                    e.left.type = rt
                elif e.right.kind == "int":
                    e.right.type = lt
                else:
                    self.error(f"comparing {lt} with {rt}", e.line)
            return BOOL
        # arithmetic / bitwise
        if lt.kind == "ptr" and e.op in ("+", "-"):     # pointer arithmetic
            return lt
        if lt.kind != "int" or rt.kind != "int":
            self.error(f"operator {e.op} needs integers", e.line)
        if lt != rt:
            if e.left.kind == "int":
                e.left.type = rt; return rt
            if e.right.kind == "int":
                e.right.type = lt; return lt
            self.error(f"mixing {lt} and {rt} needs a cast", e.line)
        return lt

    def call(self, e):
        if e.callee.kind != "var":
            self.error("can only call a named function", e.line)
        name = e.callee.name
        if name in BUILTINS:
            params, ret = BUILTINS[name]
        elif name in self.fns:
            params, ret = self.fns[name]
        else:
            self.error(f"call to undefined function {name!r}", e.line)
        if name not in ("print", "println") and len(e.args) != len(params):
            self.error(f"{name} expects {len(params)} args, got {len(e.args)}", e.line)
        for a, pt in zip(e.args, params):
            self.expr(a, expected=pt)
        return ret

# ------------------------------------------------------------------ codegen
C_TYPE = {
    ("int", 8, True): "int8_t", ("int", 16, True): "int16_t",
    ("int", 32, True): "int32_t", ("int", 64, True): "int64_t",
    ("int", 8, False): "uint8_t", ("int", 16, False): "uint16_t",
    ("int", 32, False): "uint32_t", ("int", 64, False): "uint64_t",
}


def cty(t):
    if t.kind == "bool":
        return "bool"
    if t.kind == "void":
        return "void"
    if t.kind == "ptr":
        return cty(t.inner) + "*"
    return C_TYPE[(t.kind, t.width, t.signed)]


class CodeGen:
    def __init__(self, decls, freestanding=False):
        self.decls = decls
        self.freestanding = freestanding
        self.out = []
        self.strings = []

    def emit(self, s=""):
        self.out.append(s)

    def generate(self):
        self.emit("/* generated by nanomort — do not edit */")
        self.emit("#include <stdint.h>")
        self.emit("#include <stdbool.h>")
        if not self.freestanding:
            self.emit("#include <stdio.h>")
        self.emit()
        self.prelude()
        # forward declarations so functions can call in any order
        for d in self.decls:
            if d.kind == "fn":
                self.emit(self.signature(d) + ";")
        self.emit()
        for d in self.decls:
            if d.kind == "global":
                self.emit(f"{cty(d.type)} nm_{d.name} = {self.expr(d.value)};")
        self.emit()
        for d in self.decls:
            if d.kind == "fn":
                self.function(d)
        if not self.freestanding:
            self.emit("int main(void) { return (int)nm_main(); }")
        return "\n".join(self.out)

    def prelude(self):
        # port I/O and asm helpers — the escape hatch to hardware
        self.emit("static inline uint8_t nm_inb(uint16_t p){uint8_t v;"
                  '__asm__ volatile("inb %1,%0":"=a"(v):"Nd"(p));return v;}')
        self.emit("static inline uint16_t nm_inw(uint16_t p){uint16_t v;"
                  '__asm__ volatile("inw %1,%0":"=a"(v):"Nd"(p));return v;}')
        self.emit("static inline void nm_outb(uint16_t p,uint8_t v){"
                  '__asm__ volatile("outb %0,%1"::"a"(v),"Nd"(p));}')
        self.emit("static inline void nm_outw(uint16_t p,uint16_t v){"
                  '__asm__ volatile("outw %0,%1"::"a"(v),"Nd"(p));}')
        if not self.freestanding:
            self.emit("static void nm_print(int64_t v){printf(\"%lld\", (long long)v);}")
            self.emit("static void nm_println(int64_t v){printf(\"%lld\\n\",(long long)v);}")
        else:
            # no libc: print/println are no-ops in freestanding mode; a real
            # kernel prints via VGA (see the kernel example), not this builtin.
            self.emit("static void nm_print(int64_t v){(void)v;}")
            self.emit("static void nm_println(int64_t v){(void)v;}")
        self.emit()

    def signature(self, fn):
        ps = ", ".join(f"{cty(resolve(t))} nm_{n}" for n, t in fn.params) or "void"
        return f"{cty(resolve(fn.ret))} nm_{fn.name}({ps})"

    def function(self, fn):
        self.emit(self.signature(fn) + " {")
        self.block(fn.body, 1)
        self.emit("}")
        self.emit()

    def block(self, blk, depth):
        pad = "    " * depth
        for s in blk.stmts:
            self.stmt(s, depth, pad)

    def stmt(self, s, depth, pad):
        if s.kind == "let":
            self.emit(f"{pad}{cty(s.type)} nm_{s.name} = {self.expr(s.value)};")
        elif s.kind == "assign":
            self.emit(f"{pad}{self.expr(s.target)} {s.op} {self.expr(s.value)};")
        elif s.kind == "return":
            self.emit(f"{pad}return {self.expr(s.value)};" if s.value
                      else f"{pad}return;")
        elif s.kind == "exprstmt":
            self.emit(f"{pad}{self.expr(s.expr)};")
        elif s.kind == "asm":
            self.emit(f'{pad}__asm__ volatile({c_string(s.code)});')
        elif s.kind == "if":
            self.emit(f"{pad}if ({self.expr(s.cond)}) {{")
            self.block(s.then, depth + 1)
            if s.els and s.els.kind == "if":
                self.emit(f"{pad}}} else ")
                self.stmt(s.els, depth, pad)
            elif s.els:
                self.emit(f"{pad}}} else {{")
                self.block(s.els, depth + 1)
                self.emit(f"{pad}}}")
            else:
                self.emit(f"{pad}}}")
        elif s.kind == "while":
            self.emit(f"{pad}while ({self.expr(s.cond)}) {{")
            self.block(s.body, depth + 1)
            self.emit(f"{pad}}}")
        elif s.kind == "block":
            self.emit(f"{pad}{{")
            self.block(s, depth + 1)
            self.emit(f"{pad}}}")

    def expr(self, e):
        k = e.kind
        if k == "int":
            return str(e.value)
        if k == "bool":
            return "true" if e.value else "false"
        if k == "str":
            idx = len(self.strings)
            self.strings.append(e.value)
            # emit as a writable static array so a kernel can edit it in place
            body = ", ".join(str(b) for b in e.value.encode()) + ", 0"
            self.emit_top(f"static uint8_t nm_str_{idx}[] = {{{body}}};")
            return f"nm_str_{idx}"
        if k == "var":
            return f"nm_{e.name}"
        if k == "unary":
            return f"({e.op}{self.expr(e.operand)})"
        if k == "deref":
            return f"(*{self.expr(e.operand)})"
        if k == "addr":
            return f"(&{self.expr(e.operand)})"
        if k == "cast":
            return f"(({cty(e.type)})({self.expr(e.inner)}))"
        if k == "index":
            return f"({self.expr(e.base)})[{self.expr(e.index)}]"
        if k == "binary":
            return f"({self.expr(e.left)} {e.op} {self.expr(e.right)})"
        if k == "call":
            name = e.callee.name
            cname = f"nm_{name}"
            args = ", ".join(self.expr(a) for a in e.args)
            return f"{cname}({args})"
        raise NanoError(f"cannot emit {k}", e.line)

    # string literals need to appear before use; stash them at the top
    def emit_top(self, line):
        # insert after the includes/prelude (a simple marker approach)
        self.out.insert(self._top_mark(), line)

    def _top_mark(self):
        # place string arrays right after the prelude blank line
        return len(self.out)


def c_string(s):
    esc = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{esc}"'

# ------------------------------------------------------------------ driver
def compile_source(src, freestanding=False):
    toks = lex(src)
    decls = Parser(toks).parse()
    Checker(decls, freestanding=freestanding).check()
    return CodeGen(decls, freestanding=freestanding).generate()


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    path = argv[1]
    flags = set(argv[2:])
    freestanding = "--freestanding" in flags
    with open(path) as f:
        src = f.read()
    try:
        c = compile_source(src, freestanding=freestanding)
    except NanoError as e:
        print(f"{path}: {e}", file=sys.stderr)
        return 1

    if "--emit-c" in flags:
        print(c)
        return 0
    if "--run" in flags:
        with tempfile.TemporaryDirectory() as d:
            cf = os.path.join(d, "out.c")
            exe = os.path.join(d, "out")
            with open(cf, "w") as f:
                f.write(c)
            r = subprocess.run(["cc", cf, "-o", exe], capture_output=True, text=True)
            if r.returncode != 0:
                print(r.stderr, file=sys.stderr)
                return 1
            return subprocess.run([exe]).returncode
    # default: emit C
    print(c)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
