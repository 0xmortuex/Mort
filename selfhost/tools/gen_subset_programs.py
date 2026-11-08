#!/usr/bin/env python3
"""Seeded generator of valid Mort programs with independently computed output.

    python selfhost/tools/gen_subset_programs.py --seed 7 --lines 400 --out DIR
    python selfhost/tools/gen_subset_programs.py --profile full --seed 7 --out DIR

Two profiles:

    subset  programs inside the bootstrap subset (selfhost/PLAN.md section 2),
            which count towards the corpus floor of section 5.1
    full    generics, payload enums, modules, tuples, slices and so on, which
            the parser and declaration checker must read; never counted

How expected results are obtained. Every construct is built as a pair: the Mort
text and a Python closure giving its meaning, written from the language
specification (docs/language-specification.md sections 4, 6 and 7) and using no
part of the compiler. `Program.expected_stdout`, `expected_stderr` and
`expected_exit` come from running those closures. A program is thrown away and
regenerated if the model reaches behaviour the specification leaves open.

Where a result is not independently checked:

  * `c_*` integers follow the target's C ABI, so the model only allows values
    and operations that stay in range (c_char: 0..127). Their wrap-around and
    target-specific widths are never exercised.
  * `u64` and `c_size` values are printed as two halves below 2^32, because
    `print` is wrong for a u64 above 2^63 (see BLOCKERS.md).
  * Bodies of `test` blocks (full profile) are parsed and checked, not run.
  * `asm("nop")` (full profile) has no modelled effect beyond executing.
  * `sizeof` of an enum is target layout and is used for allocation only, never
    printed. Pointer values are never printed. Struct `sizeof` is modelled by C
    layout on a 64-bit target.
  * Float output is modelled with Python's own `%.17g`, which is the format the
    runtime uses, so floats check arithmetic but not the choice of format.
  * Evaluation order of operands is unspecified, so an impure call appears only
    alone in a statement, where the order cannot matter. `&&` and `||` are
    specified and are tested with impure operands.

Determinism: the only randomness is `random.Random(<string seed>)`, and no output
depends on set order, so results are byte-identical across runs and
PYTHONHASHSEED values.
"""
import argparse
import random
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ----- the integer model (specification sections 4.1, 4.3, 7.1) -----

FIXED_BITS = {
    "u8": (8, False), "u16": (16, False), "u32": (32, False), "u64": (64, False),
    "i8": (8, True), "i16": (16, True), "i32": (32, True), "i64": (64, True),
    "int": (64, True),
}
# The c_* types whose width does not vary across supported targets. c_long and
# c_ulong differ between LLP64 and LP64, so they are limited to 32-bit values.
C_BITS = {
    "c_char": (8, True), "c_uchar": (8, False), "c_short": (16, True),
    "c_ushort": (16, False), "c_int": (32, True), "c_uint": (32, False),
    "c_long": (32, True), "c_ulong": (32, False), "c_size": (64, False),
}
SUBSET_INTS = ["u8", "u32", "u64", "i64", "int", "c_char", "c_int"]
FIXED_INTS = ["u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64", "int"]
FULL_INTS = FIXED_INTS + ["c_char", "c_uchar", "c_short", "c_ushort", "c_int",
                          "c_uint", "c_long", "c_ulong", "c_size"]
# Binary operators on two integers. && and || work on bool and are separate.
INT_BINARY_OPS = ["+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>",
                  "==", "!=", "<", ">", "<=", ">="]
COMPARISONS = {"==", "!=", "<", ">", "<=", ">="}
ARITHMETIC = {"+", "-", "*", "/", "%", "&", "|", "^"}
SHIFTS = {"<<", ">>"}


class Trap(Exception):
    """A controlled execution failure, which the specification defines."""


class Invalid(Exception):
    """Behaviour the specification leaves open; the program is regenerated."""


class Unfit(Exception):
    """The generator built a literal that its context cannot accept."""


def bits_of(ty):
    return FIXED_BITS.get(ty) or C_BITS[ty]


def int_range(ty):
    """The values a type may hold in the model."""
    if ty == "c_char":
        return (0, 127)  # signedness of `char` varies between targets
    bits, signed = bits_of(ty)
    if signed:
        return (-(1 << (bits - 1)), (1 << (bits - 1)) - 1)
    return (0, (1 << bits) - 1)


def wrap(ty, value):
    bits, signed = bits_of(ty)
    value &= (1 << bits) - 1
    if signed and value >> (bits - 1):
        value -= 1 << bits
    return value


def fits(ty, value):
    low, high = int_range(ty)
    return low <= value <= high


def finish(ty, value):
    """Reduce a fixed-width result; a c_* result must already be in range."""
    if ty in C_BITS:
        if not fits(ty, value):
            raise Invalid(f"{ty} result {value} depends on the target")
        return value
    return wrap(ty, value)


def truncated_div(left, right):
    quotient = abs(left) // abs(right)
    return quotient if (left < 0) == (right < 0) else -quotient


def int_binary(op, ty, left, right):
    if op in ("/", "%") and right == 0:
        raise Trap("division by zero")
    if ty in C_BITS and op in ("/", "%") and left == int_range(ty)[0] and right == -1:
        raise Invalid("C signed division overflow")
    if op == "+":
        value = left + right
    elif op == "-":
        value = left - right
    elif op == "*":
        value = left * right
    elif op == "/":
        if left == int_range(ty)[0] and right == -1 and ty not in C_BITS:
            return left  # section 7.1: minimum divided by -1 is the minimum
        value = truncated_div(left, right)
    elif op == "%":
        if left == int_range(ty)[0] and right == -1 and ty not in C_BITS:
            return 0
        value = left - truncated_div(left, right) * right
    elif op == "&":
        value = left & right
    elif op == "|":
        value = left | right
    elif op == "^":
        value = left ^ right
    else:
        raise AssertionError(op)
    return finish(ty, value)


def int_shift(op, ty, left, count):
    bits, signed = bits_of(ty)
    if count < 0:
        raise Trap("negative shift count")
    if ty in C_BITS:
        if count >= bits or left < 0:
            raise Invalid("C shift outside its defined range")
        value = left << count if op == "<<" else left >> count
        return finish(ty, value)
    if count >= bits:
        if op == "<<" or not signed:
            return 0
        return -1 if left < 0 else 0
    if op == "<<":
        return wrap(ty, left << count)
    return left >> count  # Python's >> is arithmetic, matching both signednesses


def compare(op, left, right):
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    if op == "<":
        return left < right
    if op == ">":
        return left > right
    if op == "<=":
        return left <= right
    if op == ">=":
        return left >= right
    raise AssertionError(op)


def fold_literals(op, left, right):
    """Arbitrary-precision evaluation of an all-literal expression (section 7.1)."""
    if op == "+":
        return left + right
    if op == "-":
        return left - right
    if op == "*":
        return left * right
    if op == "/":
        return truncated_div(left, right)
    if op == "%":
        return left - truncated_div(left, right) * right
    if op == "&":
        return left & right
    if op == "|":
        return left | right
    if op == "^":
        return left ^ right
    if op == "<<":
        return left << right
    if op == ">>":
        return left >> right
    raise AssertionError(op)


def cast_int(value, target):
    """Section 4.3: a fixed-width target takes the value modulo 2^N."""
    if target in C_BITS:
        if not fits(target, value):
            raise Invalid(f"cast to {target} of {value} depends on the target")
        return value
    return wrap(target, value)


# ----- model state -----

STEP_LIMIT = 400_000


class Heap(list):
    """A block from alloc(); `dead` is set when it is freed."""
    dead = False


class Ptr:
    """An address: a container (list, dict) and a key inside it."""
    __slots__ = ("obj", "key")

    def __init__(self, obj, key):
        self.obj = obj
        self.key = key

    def check(self):
        if self.obj is None:
            raise Invalid("null pointer dereference")
        if isinstance(self.obj, Heap) and self.obj.dead:
            raise Invalid("use after free")

    def load(self):
        self.check()
        try:
            value = self.obj[self.key]
        except (IndexError, KeyError) as error:
            raise Invalid("access outside an object") from error
        if value is None:
            raise Invalid("read of uninitialized memory")
        return value

    def store(self, value):
        self.check()
        if isinstance(self.obj, list) and not 0 <= self.key < len(self.obj):
            raise Invalid("write outside an object")
        self.obj[self.key] = value

    def offset(self, amount):
        if not amount:
            return self
        if not isinstance(self.obj, list):
            raise Invalid("indexing a pointer to a scalar")
        return Ptr(self.obj, self.key + amount)

    def same(self, other):
        return self.obj is other.obj and self.key == other.key


NULL = Ptr(None, 0)


def copy_value(value):
    if isinstance(value, dict):
        return {key: copy_value(item) for key, item in value.items()}
    if isinstance(value, Heap):
        return value
    if isinstance(value, list):
        return [copy_value(item) for item in value]
    return value


class Model:
    def __init__(self, args):
        self.out = bytearray()
        self.err = bytearray()
        self.globals = {}
        self.steps = 0
        self.args = args
        self.files = {}

    def tick(self):
        self.steps += 1
        if self.steps > STEP_LIMIT:
            raise Invalid("step limit")


class Break(Exception):
    pass


class Continue(Exception):
    pass


class Return(Exception):
    def __init__(self, value):
        self.value = value


class Exit(Exception):
    def __init__(self, code):
        self.code = code


# ----- the source-and-meaning combinators -----

P_BIN = {"||": 1, "&&": 2, "|": 3, "^": 4, "&": 5, "==": 6, "!=": 6, "<": 7, ">": 7,
         "<=": 7, ">=": 7, "<<": 8, ">>": 8, "+": 9, "-": 9, "*": 10, "/": 10, "%": 10}
P_CAST = 11
P_UNARY = 12
P_POSTFIX = 13
P_PRIMARY = 14


class E:
    """An expression: text, type, meaning.

    `ty` "lit" is an untyped integer literal expression, with `val` its value.
    `loc`, when present, gives the storage location of an lvalue.
    """
    __slots__ = ("src", "ty", "ev", "prec", "loc", "val")

    def __init__(self, src, ty, ev, prec=P_PRIMARY, loc=None, val=None):
        self.src = src
        self.ty = ty
        self.ev = ev
        self.prec = prec
        self.loc = loc
        self.val = val


class S:
    """A statement: source lines and meaning."""
    __slots__ = ("lines", "run")

    def __init__(self, lines, run):
        self.lines = lines
        self.run = run


def indented(lines):
    return ["    " + line if line else line for line in lines]


class Var:
    __slots__ = ("name", "ty", "key", "is_global", "frozen")

    def __init__(self, name, ty, key, is_global=False, frozen=False):
        self.name = name
        self.ty = ty
        self.key = key
        self.is_global = is_global
        self.frozen = frozen  # a loop counter: read it, never assign it


class Scope:
    def __init__(self, parent=None):
        self.parent = parent
        self.vars = []

    def add(self, var):
        self.vars.append(var)
        return var

    def visible(self):
        seen = []
        scope = self
        while scope is not None:
            seen.extend(scope.vars)
            scope = scope.parent
        return seen

    def of_type(self, ty):
        return [v for v in self.visible() if v.ty == ty]


class Fn:
    __slots__ = ("name", "params", "ret", "body", "pure")

    def __init__(self, name, params, ret, body, pure=True):
        self.name = name
        self.params = params
        self.ret = ret
        self.body = body
        self.pure = pure

    def call(self, model, args):
        model.tick()
        frame = {}
        for param, value in zip(self.params, args):
            frame[param.key] = value
        try:
            self.body(model, frame)
        except Return as result:
            return result.value
        return None


@dataclass(frozen=True)
class Spec:
    """Which program to build: the corpus is a list of these."""
    name: str
    seed: int
    lines: int
    profile: str = "subset"
    flavor: str = "random"
    focus: str = ""  # a section kind this program must contain


@dataclass
class Program:
    spec: Spec
    files: dict  # relative path -> bytes; the root is `root`
    root: str
    args: list
    expected_stdout: bytes
    expected_stderr: bytes
    expected_exit: int
    expect_trap: bool = False
    line_count: int = 0
    sections: list = field(default_factory=list)


class GeneratorError(Exception):
    pass


FULL_ONLY_SECTIONS = ("generics", "payload", "tuples", "fnptrs", "errors",
                      "resources", "modules", "floats", "misc", "widths")


# ----- the generator -----

class Gen:
    def __init__(self, spec):
        self.spec = spec
        self.full = spec.profile == "full"
        self.counter = 0
        self.items = {"main.mx": []}   # file name -> list of top-level items
        self.imports = {"main.mx": []}
        self.pure_fns = []             # (Fn, params) callable from expressions
        self.global_inits = []         # functions that set model globals
        self.sections = []
        self.r = random.Random(f"{spec.name}/{spec.seed}")
        self.paren_noise = 0.04
        self.structs = {}
        self.enums = {}
        self.args = ["alpha", "beta"]
        self.ints = FULL_INTS if self.full else SUBSET_INTS
        self.pure_mode = False  # inside a function callable from an expression

    # --- names and literals ---

    def fresh(self, prefix):
        self.counter += 1
        return f"{prefix}{self.counter}"

    def lit_text(self, value):
        """A spelling of a non-negative integer; the form is part of the test."""
        choice = self.r.random()
        if choice < 0.55 or value > (1 << 64):
            return str(value)
        if choice < 0.68:
            return hex(value)
        if choice < 0.76:
            return bin(value)
        if choice < 0.84:
            return "0o" + oct(value)[2:]
        if choice < 0.94 and value >= 1000:
            text = f"{value:_}"
            return text
        if 32 <= value < 127 and chr(value) not in "'\\":
            return f"'{chr(value)}'"
        if value in (9, 10, 13, 0):
            return {9: r"'\t'", 10: r"'\n'", 13: r"'\r'", 0: r"'\0'"}[value]
        if value < 256 and self.r.random() < 0.5:
            return rf"'\x{value:02x}'"
        return str(value)

    def lit(self, value):
        if value < 0:
            return E(f"-{self.lit_text(-value)}", "lit", lambda m, f: value, P_UNARY,
                     val=value)
        return E(self.lit_text(value), "lit", lambda m, f: value, val=value)

    def coerce(self, expr, ty):
        """Literal adoption (section 4.3)."""
        if expr.ty != "lit":
            return expr
        if ty not in FIXED_BITS and ty not in C_BITS:
            raise Unfit(f"literal into {ty}")
        if not fits(ty, expr.val):
            raise Unfit(f"{expr.val} does not fit {ty}")
        value = expr.val
        return E(expr.src, ty, lambda m, f: value, expr.prec, val=value)

    # --- operators ---

    def wrap_if(self, expr, need):
        if need or self.r.random() < self.paren_noise:
            return f"({expr.src})"
        return expr.src

    def bin_text(self, op, left, right):
        """Minimal parentheses, so that precedence itself is exercised."""
        prec = P_BIN[op]
        l_text = self.wrap_if(left, left.prec < prec)
        r_text = self.wrap_if(right, right.prec <= prec)
        # `<` is decided by a token scan for a closing `>` followed by `(`, so
        # `a < b && c > (d)` would parse as a generic call: keep `(` off the
        # right of `>` (see Parser._looks_like_call_type_args).
        if op == ">" and r_text.startswith("("):
            return f"!({l_text} <= {r_text})", P_UNARY
        if op == ">>" and r_text.startswith("("):
            r_text = f"0 + {r_text}"
        gap = " "
        if self.r.random() < 0.01:
            gap = "\n        "
        elif self.r.random() < 0.01:
            gap = " /* op */ "
        return f"{l_text} {op}{gap}{r_text}", prec

    def binary(self, op, left, right):
        text, prec = self.bin_text(op, left, right)
        if op in ("&&", "||"):
            if left.ty != "bool" or right.ty != "bool":
                raise AssertionError((op, left.ty, right.ty))
            if op == "&&":
                def ev(m, f):
                    return left.ev(m, f) and right.ev(m, f)
            else:
                def ev(m, f):
                    return left.ev(m, f) or right.ev(m, f)
            return E(text, "bool", ev, prec)
        if op in COMPARISONS and (left.ty not in self.all_int_like()
                                  or right.ty not in self.all_int_like()):
            return self.non_int_compare(text, prec, op, left, right)
        if left.val is not None and right.val is not None:
            # All-literal: the checker folds it exactly, whatever context the
            # literals were given; the result must then fit that context.
            a, b = left.val, right.val
            if op in COMPARISONS:
                # A literal outside i64 wraps at run time here instead of being
                # refused (BLOCKERS.md), so the comparison would be meaningless.
                if not (fits("i64", a) and fits("i64", b)):
                    raise Unfit("comparison of a literal outside i64")
                value = compare(op, a, b)
                return E(text, "bool", lambda m, f: value, prec)
            if op in ("/", "%") and b == 0:
                raise Unfit("literal zero divisor")
            if op in SHIFTS and (b < 0 or b > 64):
                raise Unfit("literal shift count")
            value = fold_literals(op, a, b)
            ty = left.ty if left.ty != "lit" else right.ty
            if op in SHIFTS:
                ty = left.ty
            if ty != "lit" and not fits(ty, value):
                raise Unfit(f"{value} does not fit {ty}")
            return E(text, ty, lambda m, f: value, prec, val=value)
        if op in SHIFTS:
            ty = left.ty
            if left.val is not None:
                # A literal left operand keeps the literal's own type, which
                # the context cannot name.
                raise Unfit("a shift of a literal by a variable has no context")
            count = self.coerce(right, right.ty if right.ty != "lit" else "u64")

            def ev(m, f):
                return int_shift(op, ty, left.ev(m, f), count.ev(m, f))
            return E(text, ty, ev, prec)
        ty = left.ty if left.ty != "lit" else right.ty
        left_c, right_c = self.coerce(left, ty), self.coerce(right, ty)
        if op in COMPARISONS:
            def ev(m, f):
                return compare(op, left_c.ev(m, f), right_c.ev(m, f))
            return E(text, "bool", ev, prec)

        def ev(m, f):
            return int_binary(op, ty, left_c.ev(m, f), right_c.ev(m, f))
        return E(text, ty, ev, prec)

    def all_int_like(self):
        return list(FIXED_BITS) + list(C_BITS) + ["lit"]

    def non_int_compare(self, text, prec, op, left, right):
        """== and != on bools, enums and pointers."""
        if left.ty == "ptr-null" or right.ty == "ptr-null":
            other = right if left.ty == "ptr-null" else left

            def ev(m, f):
                same = other.ev(m, f).same(NULL)
                return same if op == "==" else not same
            return E(text, "bool", ev, prec)

        def ev(m, f):
            a, b = left.ev(m, f), right.ev(m, f)
            same = a.same(b) if isinstance(a, Ptr) else a == b
            return same if op == "==" else not same
        return E(text, "bool", ev, prec)

    def unary(self, op, operand):
        need = operand.prec < P_UNARY
        inner = self.wrap_if(operand, need)
        if inner.startswith(op):  # `--a` and `&&a` must not fuse
            inner = f"({inner})"
        text = f"{op}{inner}"
        if op == "!":
            return E(text, "bool", lambda m, f: not operand.ev(m, f), P_UNARY)
        if operand.val is not None:
            value = -operand.val if op == "-" else ~operand.val
            if operand.ty != "lit" and not fits(operand.ty, value):
                raise Unfit(f"{value} does not fit {operand.ty}")
            return E(text, operand.ty, lambda m, f: value, P_UNARY, val=value)
        ty = operand.ty
        if op == "-":
            return E(text, ty, lambda m, f: finish(ty, -operand.ev(m, f)), P_UNARY)
        if op == "~":
            return E(text, ty, lambda m, f: finish(ty, ~operand.ev(m, f)), P_UNARY)
        raise AssertionError(op)

    def cast(self, operand, target):
        inner = self.wrap_if(operand, operand.prec < P_CAST)
        text = f"{inner} as {target}"
        if target.startswith("*"):
            return self.pointer_cast(text, operand, target)
        if operand.ty == "lit":
            value = cast_int(operand.val, target)
            return E(text, target, lambda m, f: value, P_CAST)
        if operand.ty in self.enums:
            return E(text, target, lambda m, f: cast_int(operand.ev(m, f)[0], target),
                     P_CAST)
        return E(text, target, lambda m, f: cast_int(operand.ev(m, f), target), P_CAST)

    def pointer_cast(self, text, operand, target):
        """Only alloc() to *T, a pointer to another pointer type, and 0 to null."""
        if operand.ty == "lit" and operand.val == 0:
            return E(text, target, lambda m, f: NULL, P_CAST)
        size = self.size_of(target[1:].replace("const ", "").strip())

        def ev(m, f):
            value = operand.ev(m, f)
            if isinstance(value, AllocToken):
                if size is None:
                    raise Invalid("allocation of an unsized element")
                return Ptr(Heap([None] * (value.bytes // size)), 0)
            return value
        return E(text, target, ev, P_CAST)

    def size_of(self, ty):
        """C layout on a 64-bit target; None when the model does not know it."""
        return self.layout(ty)[0] if self.layout(ty) else None

    def layout(self, ty):
        """(size, alignment), or None for a type with no modelled layout."""
        ty = ty.strip()
        if ty.startswith("*"):
            return (8, 8)
        if ty == "bool":
            return (1, 1)
        if ty in FIXED_BITS or ty in C_BITS:
            if ty in ("c_long", "c_ulong"):
                return None
            size = bits_of(ty)[0] // 8
            return (size, size)
        if ty in self.structs:
            offset = 0
            alignment = 1
            for _name, field_ty in self.structs[ty]:
                inner = self.layout(field_ty)
                if inner is None:
                    return None
                offset = (offset + inner[1] - 1) // inner[1] * inner[1]
                offset += inner[0]
                alignment = max(alignment, inner[1])
            return ((offset + alignment - 1) // alignment * alignment, alignment)
        return None

    # --- variables, fields, indexing ---

    def var(self, variable):
        key = variable.key
        if variable.is_global:
            return E(variable.name, variable.ty,
                     lambda m, f: copy_value(m.globals[key]),
                     loc=lambda m, f: Ptr(m.globals, key))
        return E(variable.name, variable.ty, lambda m, f: copy_value(f[key]),
                 loc=lambda m, f: Ptr(f, key))

    def field(self, base, name, ty):
        text = f"{self.wrap_if(base, base.prec < P_POSTFIX)}.{name}"
        if base.loc is not None:
            base_loc = base.loc

            def loc(m, f):
                return Ptr(base_loc(m, f).load(), name)
            return E(text, ty, lambda m, f: copy_value(loc(m, f).load()),
                     P_POSTFIX, loc=loc)
        return E(text, ty, lambda m, f: copy_value(base.ev(m, f)[name]), P_POSTFIX)

    def index(self, base, index_expr, ty):
        """`p[i]` for a pointer or `G[i]` for an array (global, or local in full)."""
        index_c = self.coerce(index_expr, "u64" if index_expr.ty == "lit"
                              else index_expr.ty)
        text = (f"{self.wrap_if(base, base.prec < P_POSTFIX)}"
                f"[{index_c.src}]")
        if base.ty.startswith("["):
            base_loc = base.loc

            def loc(m, f):
                array = base_loc(m, f).load()
                position = index_c.ev(m, f)
                if not 0 <= position < len(array):
                    raise Trap("index out of bounds")
                return Ptr(array, position)
        else:
            def loc(m, f):
                return base.ev(m, f).offset(index_c.ev(m, f))
        return E(text, ty, lambda m, f: copy_value(loc(m, f).load()), P_POSTFIX, loc=loc)

    def deref(self, pointer):
        ty = pointer.ty[1:].replace("const ", "").strip()
        inner = self.wrap_if(pointer, pointer.prec < P_UNARY)
        return E(f"*{inner}", ty, lambda m, f: copy_value(pointer.ev(m, f).load()),
                 P_UNARY, loc=lambda m, f: pointer.ev(m, f))

    def address_of(self, operand, pointer_ty):
        inner = self.wrap_if(operand, operand.prec < P_POSTFIX)
        return E(f"&{inner}", pointer_ty, operand.loc, P_UNARY)

    # --- statements ---

    def let(self, scope, name, expr, ty=None, const=False, annotate=None):
        declared = ty or ("int" if expr.ty == "lit" else expr.ty)
        value = self.coerce(expr, declared)
        if annotate is None:
            annotate = ty is not None and (self.r.random() < 0.7 or expr.val is not None)
        variable = scope.add(Var(name, declared, self.fresh("k"), frozen=const))
        key = variable.key
        keyword = "const" if const else "let"
        annotation = f": {declared}" if annotate else ""

        def run(m, f):
            f[key] = copy_value(value.ev(m, f))
        return variable, S([f"{keyword} {name}{annotation} = {value.src};"], run)

    def assign(self, target, expr, op="="):
        if op == "=":
            value = self.coerce(expr, target.ty)

            def run(m, f):
                loc = target.loc(m, f)
                loc.store(copy_value(value.ev(m, f)))
            return S([f"{target.src} = {value.src};"], run)
        base_op = op[:-1]
        ty = target.ty
        value = self.coerce(expr, ty) if base_op not in SHIFTS else (
            self.coerce(expr, "u64") if expr.ty == "lit" else expr)

        def run(m, f):
            loc = target.loc(m, f)
            current = loc.load()
            operand = value.ev(m, f)
            if base_op in SHIFTS:
                loc.store(int_shift(base_op, ty, current, operand))
            else:
                loc.store(int_binary(base_op, ty, current, operand))
        return S([f"{target.src} {op} {value.src};"], run)

    def ret(self, expr, ret_ty):
        value = self.coerce(expr, ret_ty) if expr is not None else None
        text = f"return {value.src};" if value is not None else "return;"

        def run(m, f):
            raise Return(None if value is None else copy_value(value.ev(m, f)))
        return S([text], run)

    def expr_stmt(self, expr):
        return S([f"{expr.src};"], lambda m, f: expr.ev(m, f))

    def stmt_print(self, expr):
        """print(x); values that print wrongly are split into two halves."""
        value = self.coerce(expr, "int") if expr.ty == "lit" else expr
        if value.ty in ("u64", "c_size"):
            name = self.fresh("wide")
            scope = Scope()
            variable, binding = self.let(scope, name, value, ty=value.ty, annotate=True)
            high = self.cast(self.binary(">>", self.var(variable), self.lit(32)), "i64")
            low = self.cast(self.binary("&", self.var(variable), self.lit(0xFFFFFFFF)),
                            "i64")
            return [binding, self.print_of(high), self.print_of(low)]
        return [self.print_of(value)]

    @staticmethod
    def print_of(expr):
        def run(m, f):
            m.out += f"{expr.ev(m, f)}\n".encode()
        return S([f"print({expr.src});"], run)

    def block_lines(self, statements):
        lines = []
        for statement in statements:
            noise = self.r.random()
            if noise < 0.03:
                lines.append(f"// note {self.fresh('n')}")
            elif noise < 0.05:
                lines.append(f"/* a /* nested */ {self.fresh('n')} */")
            elif noise < 0.07:
                lines.append("")
            lines.extend(statement.lines)
        return lines

    def block(self, statements):
        lines = ["{"] + indented(self.block_lines(statements)) + ["}"]

        def run(m, f):
            for statement in statements:
                statement.run(m, f)
        return S(lines, run)

    def body_run(self, statements):
        def run(m, f):
            for statement in statements:
                statement.run(m, f)
        return run

    def if_chain(self, arms, otherwise=None):
        """arms: [(cond E, body statements)]"""
        lines = []
        runs = []
        for index, (cond, body) in enumerate(arms):
            head = "if" if index == 0 else "} else if"
            lines.append(f"{head} {cond.src} {{")
            lines.extend(indented(self.block_lines(body)))
            runs.append((cond, self.body_run(body)))
        if otherwise is not None:
            lines.append("} else {")
            lines.extend(indented(self.block_lines(otherwise)))
        lines.append("}")
        other_run = None if otherwise is None else self.body_run(otherwise)

        def run(m, f):
            for cond, body_run in runs:
                if cond.ev(m, f):
                    body_run(m, f)
                    return
            if other_run is not None:
                other_run(m, f)
        return S(lines, run)

    def while_loop(self, cond, body):
        body_run = self.body_run(body)
        lines = [f"while {cond.src} {{"] + indented(self.block_lines(body)) + ["}"]

        def run(m, f):
            while True:
                m.tick()
                if not cond.ev(m, f):
                    return
                try:
                    body_run(m, f)
                except Break:
                    return
                except Continue:
                    continue
        return S(lines, run)

    def loop_stmt(self, body):
        body_run = self.body_run(body)
        lines = ["loop {"] + indented(self.block_lines(body)) + ["}"]

        def run(m, f):
            while True:
                m.tick()
                try:
                    body_run(m, f)
                except Break:
                    return
                except Continue:
                    continue
        return S(lines, run)

    def for_loop(self, var, start, end, inclusive, body, annotate_ty=None):
        if annotate_ty:
            start, end = self.coerce(start, annotate_ty), self.coerce(end, annotate_ty)
        dots = "..=" if inclusive else ".."
        annotation = f": {annotate_ty}" if annotate_ty else ""
        head = f"for {var.name}{annotation} in {start.src}{dots}{end.src} {{"
        lines = [head] + indented(self.block_lines(body)) + ["}"]
        body_run = self.body_run(body)
        key = var.key

        def run(m, f):
            low, high = start.ev(m, f), end.ev(m, f)
            for value in range(low, high + (1 if inclusive else 0)):
                m.tick()
                f[key] = value
                try:
                    body_run(m, f)
                except Break:
                    return
                except Continue:
                    continue
        return S(lines, run)

    def break_stmt(self):
        def run(m, f):
            raise Break()
        return S(["break;"], run)

    def continue_stmt(self):
        def run(m, f):
            raise Continue()
        return S(["continue;"], run)

    # --- declarations ---

    def add_item(self, lines, file="main.mx"):
        self.items.setdefault(file, []).append(lines)

    def define_fn(self, name, params, ret, statements, pure=True, file="main.mx",
                  public=False):
        run = self.body_run(statements)
        function = Fn(name, params, ret, run, pure)
        lines = [self.fn_head(name, params, ret, public) + " {"]
        lines.extend(indented(self.block_lines(statements)))
        lines.append("}")
        self.add_item(lines, file)
        return function

    def fn_head(self, name, params, ret, public=False):
        joined = ", ".join(f"{p.name}: {p.ty}" for p in params)
        arrow = f" -> {ret}" if ret else ""
        prefix = "pub " if public else ""
        return f"{prefix}fn {name}({joined}){arrow}"

    def call(self, function, args, ty=None):
        text = f"{function.name}({', '.join(a.src for a in args)})"

        def ev(m, f):
            values = [copy_value(a.ev(m, f)) for a in args]
            return function.call(m, values)
        return E(text, ty or function.ret, ev, P_POSTFIX)

    def add_global(self, name, ty, init_text, value, const=False):
        keyword = "const" if const else "let"
        self.add_item([f"{keyword} {name}: {ty} = {init_text};"])
        variable = Var(name, ty, self.fresh("g"), is_global=True)

        def init(m):
            m.globals[variable.key] = copy_value(value)
        self.global_inits.append(init)
        return variable

    # --- random expressions ---

    def edge(self, ty):
        low, high = int_range(ty)
        if ty in C_BITS:
            return self.r.choice([0, 1, 2, 7, 15, 63])
        return self.r.choice([low, low + 1, -1 if low < 0 else 0, 0, 1, 2, high - 1,
                              high, self.r.randint(low, high)])

    def int_expr(self, ty, depth, scope):
        """A pure expression of integer type `ty`, retrying what cannot fit."""
        for _ in range(8):
            try:
                return self.int_expr_once(ty, depth, scope)
            except Unfit:
                continue
        raise Unfit(f"no {ty} expression")

    def c_expr(self, ty, scope):
        """A c_* value, confined to small non-negative values (see the header)."""
        variables = scope.of_type(ty)
        if variables and self.r.random() < 0.6:
            return self.binary("&", self.var(self.r.choice(variables)),
                               self.lit(self.r.randint(0, 15)))
        others = [v for v in scope.visible()
                  if v.ty in self.ints and v.ty not in C_BITS and v.ty != ty]
        if others and self.r.random() < 0.5:
            masked = self.binary("&", self.var(self.r.choice(others)), self.lit(15))
            return self.cast(masked, ty)
        return self.coerce(self.lit(self.r.randint(0, 15)), ty)

    def int_expr_once(self, ty, depth, scope):
        if ty in C_BITS:
            return self.c_expr(ty, scope)
        kinds = ["lit", "edge"]
        variables = [v for v in scope.of_type(ty)]
        if variables:
            kinds += ["var"] * 4
        others = [v for v in scope.visible() if v.ty in self.ints and v.ty != ty]
        if depth > 0:
            kinds += ["arith"] * 4 + ["bit"] * 2 + ["shift", "div", "unary", "paren"]
            if others:
                kinds += ["cast"] * 2
            if any(fn.ret == ty for fn in self.pure_fns):
                kinds += ["call"]
        kind = self.r.choice(kinds)
        if kind == "var":
            return self.var(self.r.choice(variables))
        if kind == "lit":
            low, high = int_range(ty)
            return self.coerce(self.lit(self.r.randint(max(low, -9),
                                                       min(high, 300))), ty)
        if kind == "edge":
            return self.coerce(self.lit(self.edge(ty)), ty)
        if kind == "arith":
            return self.binary(self.r.choice(["+", "-", "*"]),
                               self.int_expr(ty, depth - 1, scope),
                               self.int_expr(ty, depth - 1, scope))
        if kind == "bit":
            return self.binary(self.r.choice(["&", "|", "^"]),
                               self.int_expr(ty, depth - 1, scope),
                               self.int_expr(ty, depth - 1, scope))
        if kind == "shift":
            count_ty = self.r.choice(self.ints)
            mask = min(63, int_range(count_ty)[1])
            count = self.binary("&", self.int_expr(count_ty, depth - 1, scope),
                                self.coerce(self.lit(mask), count_ty))
            return self.binary(self.r.choice(["<<", ">>"]),
                               self.int_expr(ty, depth - 1, scope), count)
        if kind == "div":
            divisor = self.binary("|", self.int_expr(ty, depth - 1, scope), self.lit(1))
            return self.binary(self.r.choice(["/", "%"]),
                               self.int_expr(ty, depth - 1, scope), divisor)
        if kind == "unary":
            return self.unary(self.r.choice(["-", "~"]),
                              self.int_expr(ty, depth - 1, scope))
        if kind == "paren":
            inner = self.int_expr(ty, depth - 1, scope)
            return E(f"({inner.src})", ty, inner.ev, val=inner.val)
        if kind == "cast":
            source = self.var(self.r.choice(others))
            if self.r.random() < 0.3:  # a chain: `x as A as B`
                source = self.cast(source, self.r.choice(
                    [t for t in self.ints if t in FIXED_BITS]))
            return self.cast(source, ty)
        if kind == "call":
            function = self.r.choice([fn for fn in self.pure_fns if fn.ret == ty])
            args = [self.int_expr(p.ty, 0, scope) for p in function.params]
            return self.call(function, args)
        raise AssertionError(kind)

    def bool_expr(self, depth, scope):
        ty = self.r.choice(self.ints)
        if depth <= 0 or self.r.random() < 0.35:
            return self.binary(self.r.choice(sorted(COMPARISONS)),
                               self.int_expr(ty, 1, scope),
                               self.int_expr(ty, 1, scope))
        choice = self.r.random()
        if choice < 0.45:
            return self.binary(self.r.choice(["&&", "||"]),
                               self.bool_expr(depth - 1, scope),
                               self.bool_expr(depth - 1, scope))
        if choice < 0.6:
            return self.unary("!", self.bool_expr(depth - 1, scope))
        if choice < 0.8:
            word = self.r.choice([True, False])
            return E("true" if word else "false", "bool", lambda m, f: word)
        return self.binary(self.r.choice(["==", "!="]),
                           self.bool_expr(depth - 1, scope),
                           self.bool_expr(depth - 1, scope))

    def random_lvalue(self, scope):
        """A mutable integer variable, not a loop counter."""
        candidates = [v for v in scope.visible()
                      if v.ty in self.ints and not v.frozen]
        return self.r.choice(candidates) if candidates else None

    def compound(self, variable, depth, scope):
        ty = variable.ty
        if ty in C_BITS:  # c_* arithmetic must stay inside the type (see the header)
            return self.assign(self.var(variable),
                               self.coerce(self.lit(self.r.randint(0, 15)), ty),
                               self.r.choice(["&=", "|=", "^="]))
        op = self.r.choice(["+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="])
        target = self.var(variable)
        if op in ("/=", "%="):
            right = self.binary("|", self.int_expr(ty, depth, scope), self.lit(1))
        elif op in ("<<=", ">>="):
            right = self.lit(self.r.randint(0, bits_of(ty)[0] - 1
                                            if ty not in C_BITS else 3))
        else:
            right = self.int_expr(ty, depth, scope)
        return self.assign(target, right, op)

    # --- random statements ---

    def statements(self, scope, depth, count, ret_ty, in_loop):
        out = []
        for _ in range(count):
            out.extend(self.statement(scope, depth, ret_ty, in_loop))
        return out

    def statement(self, scope, depth, ret_ty, in_loop):
        kinds = ["let"] * 3 + ["assign"] * 3 + ["compound"] * 2
        if not self.pure_mode:
            kinds += ["print"] * 2
        if depth > 0:
            kinds += ["if"] * 3 + ["while", "loop", "for", "for", "block"]
        if in_loop:
            kinds += ["exit_loop"]
        if ret_ty is not None:
            kinds += ["early_return"]
        kind = self.r.choice(kinds)
        if kind == "let":
            ty = self.r.choice(self.ints)
            _, statement = self.let(scope, self.fresh("v"),
                                    self.int_expr(ty, 2, scope), ty=ty)
            return [statement]
        if kind == "print":
            ty = self.r.choice(self.ints)
            return self.stmt_print(self.int_expr(ty, 2, scope))
        if kind in ("assign", "compound"):
            variable = self.random_lvalue(scope)
            if variable is None:
                return []
            if kind == "assign":
                return [self.assign(self.var(variable),
                                    self.int_expr(variable.ty, 2, scope))]
            return [self.compound(variable, 1, scope)]
        if kind == "if":
            cond = self.bool_expr(2, scope)
            then = self.statements(Scope(scope), depth - 1, self.r.randint(1, 3),
                                   ret_ty, in_loop)
            if self.r.random() < 0.5:
                other_cond = self.bool_expr(1, scope)
                other = self.statements(Scope(scope), depth - 1, 1, ret_ty, in_loop)
                last = (self.statements(Scope(scope), depth - 1, 1, ret_ty, in_loop)
                        if self.r.random() < 0.6 else None)
                return [self.if_chain([(cond, then), (other_cond, other)], last)]
            return [self.if_chain([(cond, then)])]
        if kind == "while":
            return self.bounded_while(scope, depth, ret_ty)
        if kind == "loop":
            return self.bounded_loop(scope, depth, ret_ty)
        if kind == "for":
            return [self.random_for(scope, depth, ret_ty)]
        if kind == "block":
            inner = self.statements(Scope(scope), depth - 1, self.r.randint(1, 3),
                                    ret_ty, in_loop)
            return [self.block(inner)]
        if kind == "exit_loop":
            statement = self.break_stmt() if self.r.random() < 0.5 else self.continue_stmt()
            cond = self.bool_expr(1, scope)
            return [self.if_chain([(cond, [statement])])]
        if kind == "early_return":
            cond = self.bool_expr(1, scope)
            return [self.if_chain([(cond, [self.ret(self.int_expr(ret_ty, 1, scope),
                                                    ret_ty)])])]
        raise AssertionError(kind)

    def bounded_while(self, scope, depth, ret_ty):
        inner = Scope(scope)
        counter_ty = self.r.choice(["u32", "int", "u64"])
        variable, binding = self.let(inner, self.fresh("c"), self.lit(0), ty=counter_ty)
        variable.frozen = True
        limit = self.lit(self.r.randint(1, 6))
        cond = self.binary("<", self.var(variable), limit)
        body_scope = Scope(inner)
        body = [self.assign(self.var(variable), self.lit(1), "+=")]
        body += self.statements(body_scope, depth - 1, self.r.randint(1, 3), ret_ty, True)
        return [binding, self.while_loop(cond, body)]

    def bounded_loop(self, scope, depth, ret_ty):
        inner = Scope(scope)
        variable, binding = self.let(inner, self.fresh("c"), self.lit(0), ty="u32")
        variable.frozen = True
        body_scope = Scope(inner)
        body = [self.assign(self.var(variable), self.lit(1), "+=")]
        limit_cond = self.binary(">=", self.var(variable),
                                 self.lit(self.r.randint(1, 5)))
        body.append(self.if_chain([(limit_cond, [self.break_stmt()])]))
        body += self.statements(body_scope, depth - 1, self.r.randint(1, 3), ret_ty, True)
        return [binding, self.loop_stmt(body)]

    def random_for(self, scope, depth, ret_ty):
        ty = self.r.choice([t for t in self.ints if t not in C_BITS])
        low, high = int_range(ty)
        if self.r.random() < 0.3:      # against the top of the type's range
            start = high - self.r.randint(0, 3)
            end = high
            inclusive = True
        else:
            start = max(low, self.r.randint(-2, 3))
            end = start + self.r.randint(0, 5)
            inclusive = self.r.random() < 0.3
            if not fits(ty, end):
                end, inclusive = high, True
        body_scope = Scope(scope)
        name = self.fresh("i")
        var = body_scope.add(Var(name, ty, self.fresh("k"), frozen=True))
        body = self.statements(body_scope, depth - 1, self.r.randint(1, 3), ret_ty, True)
        return self.for_loop(var, self.lit(start), self.lit(end), inclusive, body,
                             annotate_ty=ty)

    def random_fn(self, name, pure, file="main.mx"):
        """A function with random parameters and a body of random statements."""
        ret = self.r.choice(self.ints)
        scope = Scope()
        params = [scope.add(Var(self.fresh("p"), self.r.choice(self.ints),
                                self.fresh("k")))
                  for _ in range(self.r.randint(1, 4))]
        count = self.r.randint(2, 5) if pure else self.r.randint(4, 8)
        self.pure_mode = pure
        body = self.statements(Scope(scope), 2 if not pure else 1, count,
                               ret, False)
        self.pure_mode = False
        body.append(self.ret(self.int_expr(ret, 2, scope), ret))
        return self.define_fn(name, params, ret, body, pure, file=file)

    # --- assembly ---

    def build(self):
        """Add sections until the target size is reached, then run main."""
        table = self.section_table()
        index = 0
        focus = self.spec.flavor
        if focus == "matrix":
            self.attempt("matrix", self.sec_matrix)
        elif focus == "trap":
            self.add_section("random", index)
        else:
            if self.spec.focus:
                self.add_section(self.spec.focus, index)
                index += 1
            order = list(table)
            self.random_shuffle(order)
            while self.line_total() < self.spec.lines:
                self.add_section(order[index % len(order)], index)
                index += 1
        self.finish()

    def random_shuffle(self, order):
        self.r.shuffle(order)

    def attempt(self, kind, build):
        """Run one section's generator; retry on anything the model refuses."""
        base = (self.spec.name, self.spec.seed, kind, len(self.sections))
        snapshot = self.snapshot()
        for attempt in range(40):
            self.r = random.Random("/".join(map(str, base + (attempt,))))
            try:
                driver = build()
                self.probe(driver)
            except (Trap, Invalid, Unfit, Return, Break, Continue,
                    IndexError, ZeroDivisionError, OverflowError, ValueError):
                self.restore(snapshot)
                continue
            self.sections.append((kind, driver))
            return driver
        raise GeneratorError(f"section {kind} kept being rejected in {base}")

    def snapshot(self):
        return (self.counter, {k: list(v) for k, v in self.items.items()},
                {k: list(v) for k, v in self.imports.items()},
                list(self.pure_fns), list(self.global_inits),
                dict(self.structs), dict(self.enums))

    def restore(self, snapshot):
        counter, items, imports, pure, inits, structs, enums = snapshot
        self.counter = counter
        self.items = {k: list(v) for k, v in items.items()}
        self.imports = {k: list(v) for k, v in imports.items()}
        self.pure_fns = list(pure)
        self.global_inits = list(inits)
        self.structs = dict(structs)
        self.enums = dict(enums)

    def probe(self, driver):
        """Run the section on its own model; raises what `attempt` catches."""
        model = Model(self.args)
        for init in self.global_inits:
            init(model)
        try:
            driver.call(model, [])
        except Exit:
            pass

    def add_section(self, kind, index):
        # destroy() and Result are each one per program, so one of these sections.
        if kind in ("resources", "errors") and any(k == kind for k, _ in self.sections):
            kind = "random"
        table = self.section_table()
        return self.attempt(kind, table[kind])

    def section_table(self):
        table = {
            "random": self.sec_random,
            "arith": self.sec_arith,
            "control": self.sec_control,
            "literals": self.sec_literals,
            "structs": self.sec_structs,
            "enums": self.sec_enums,
            "pointers": self.sec_pointers,
            "globals": self.sec_globals,
            "calls": self.sec_calls,
            "strings": self.sec_strings,
            "scopes": self.sec_scopes,
            "builtins": self.sec_builtins,
            "imports": self.sec_imports,
            "shortcircuit": self.sec_shortcircuit,
        }
        if self.full:
            table.update({
                "generics": self.sec_generics,
                "payload": self.sec_payload,
                "tuples": self.sec_tuples,
                "fnptrs": self.sec_fnptrs,
                "errors": self.sec_errors,
                "resources": self.sec_resources,
                "modules": self.sec_modules,
                "floats": self.sec_floats,
                "misc": self.sec_misc,
                "widths": self.sec_widths,
            })
        return table

    def line_total(self):
        return sum(len(lines) + 1 for file_items in self.items.values()
                   for lines in file_items)

    def driver(self, statements):
        name = self.fresh("sec")
        return self.define_fn(name, [], None, statements, pure=False)

    def finish(self):
        drivers = [driver for _kind, driver in self.sections]
        calls = [self.expr_stmt(self.call(d, [], ty="void")) for d in drivers]
        exit_code = self.r.randint(0, 100)
        main_body = calls + [self.ret(self.lit(exit_code), "int")]
        if self.spec.flavor == "trap":
            main_body = calls + self.trap_statements() + main_body[-1:]
        self.main = self.define_fn("main", [], "int", main_body, pure=False)

    def trap_statements(self):
        """A runtime failure the specification defines (section 10)."""
        out = []
        scope = Scope()
        kind = self.r.choice(["divide", "remainder", "shift", "index"])
        out += self.stmt_print(self.lit(1234))
        if kind in ("divide", "remainder"):
            ty = self.r.choice(["u8", "u32", "u64", "i64", "int"])
            variable, binding = self.let(scope, self.fresh("t"), self.lit(7), ty=ty)
            zero, binding_zero = self.let(scope, self.fresh("t"), self.lit(0), ty=ty)
            op = "/" if kind == "divide" else "%"
            out += [binding, binding_zero]
            out += self.stmt_print(self.binary(op, self.var(variable), self.var(zero)))
        elif kind == "shift":
            count, binding = self.let(scope, self.fresh("t"), self.lit(-1), ty="i64")
            value, binding_value = self.let(scope, self.fresh("t"), self.lit(9), ty="i64")
            out += [binding, binding_value]
            out += self.stmt_print(self.binary("<<", self.var(value), self.var(count)))
        else:
            array = self.add_global(self.fresh("TA"), "[u8; 3]", "[1, 2, 3]", [1, 2, 3])
            index, binding = self.let(scope, self.fresh("t"), self.lit(3), ty="u64")
            out.append(binding)
            out += self.stmt_print(
                self.index(self.var(array), self.var(index), "u8"))
        return out

    def render_file(self, name):
        pieces = []
        for imported in self.imports.get(name, []):
            pieces.append(f"import {imported};")
        if pieces:
            pieces.append("")
        for item in self.items.get(name, []):
            pieces.extend(item)
            pieces.append("")
        return "\n".join(pieces) + "\n"

    def render(self):
        files = {}
        crlf = self.spec.profile == "subset" and self.spec.seed % 7 == 3
        for name in self.items:
            text = self.render_file(name)
            if crlf:
                text = text.replace("\n", "\r\n")
            files[name] = text.encode("ascii")
        return files

    def execute(self):
        model = Model(self.args)
        for init in self.global_inits:
            init(model)
        trapped = False
        code = 0
        try:
            code = self.main.call(model, [])
        except Exit as stop:
            code = stop.code
        except Trap:
            trapped = True
        return model, code, trapped

    # ===== subset sections =====

    def sec_random(self):
        count = self.r.randint(2, 4)
        pure = [self.random_fn(self.fresh("fp"), True) for _ in range(count)]
        self.pure_fns.extend(pure)
        impure = self.random_fn(self.fresh("fi"), False)
        out = []
        for function in pure + [impure]:
            for _ in range(self.r.randint(2, 4)):
                args = [self.coerce(self.lit(self.edge(p.ty)), p.ty)
                        for p in function.params]
                out += self.stmt_print(self.call(function, args))
        return self.driver(out)

    def sec_arith(self):
        scope = Scope()
        out = []
        for ty in self.r.sample(self.ints, min(6, len(self.ints))):
            _, statement = self.let(scope, self.fresh("v"),
                                    self.coerce(self.lit(self.edge(ty)), ty), ty=ty)
            out.append(statement)
        for _ in range(self.r.randint(10, 24)):
            ty = self.r.choice(self.ints)
            out += self.stmt_print(self.int_expr(ty, 3, scope))
        return self.driver(out)

    def sec_control(self):
        scope = Scope()
        function_scope = Scope(scope)
        params = [scope.add(Var(self.fresh("p"), "int", self.fresh("k")))
                  for _ in range(2)]
        total, binding = self.let(function_scope, self.fresh("v"), self.lit(0), ty="int")
        # Nested loops with labelled-by-shape exits, the classic control tangle.
        body_i = []
        var_i_scope = Scope(function_scope)
        i_name = self.fresh("i")
        var_i = var_i_scope.add(Var(i_name, "int", self.fresh("k"), frozen=True))
        var_j_scope = Scope(var_i_scope)
        j_name = self.fresh("i")
        var_j = var_j_scope.add(Var(j_name, "int", self.fresh("k"), frozen=True))
        add = self.assign(self.var(total),
                          self.binary("*", self.var(var_i), self.var(var_j)), "+=")
        skip = self.if_chain([(self.binary("==", self.binary("%", self.var(var_j),
                                                             self.lit(self.r.randint(2, 3))),
                                           self.lit(0)),
                               [self.continue_stmt()])])
        stop = self.if_chain([(self.binary(">", self.var(var_j), self.var(params[0])),
                               [self.break_stmt()])])
        early = self.if_chain([(self.binary(">", self.var(total), self.var(params[1])),
                                [self.ret(self.var(total), "int")])])
        inner = self.for_loop(var_j, self.lit(0), self.lit(self.r.randint(3, 7)),
                              self.r.random() < 0.5, [skip, stop, add, early],
                              annotate_ty="int")
        body_i.append(inner)
        outer = self.for_loop(var_i, self.lit(1), self.lit(self.r.randint(3, 6)),
                              self.r.random() < 0.5, body_i, annotate_ty="int")
        body = [binding, outer] + self.statements(function_scope, 2, 3, "int", False)
        body.append(self.ret(self.binary("-", self.var(total), self.lit(1)), "int"))
        name = self.fresh("fc")
        function = self.define_fn(name, params, "int", body, pure=False)
        out = []
        for _ in range(4):
            args = [self.lit(self.r.randint(-3, 9)), self.lit(self.r.randint(-20, 400))]
            out += self.stmt_print(self.call(function, args))
        return self.driver(out)

    def sec_literals(self):
        scope = Scope()
        out = []
        boundary = [("u8", 255), ("u32", (1 << 32) - 1), ("u64", (1 << 64) - 1),
                    ("i64", -(1 << 63)), ("i64", (1 << 63) - 1), ("int", -(1 << 63)),
                    ("c_int", (1 << 31) - 1), ("c_int", -(1 << 31)), ("c_char", 127)]
        for ty, value in self.r.sample(boundary, 4):
            variable, statement = self.let(scope, self.fresh("v"),
                                           self.lit(value), ty=ty, annotate=True)
            out.append(statement)
            out += self.stmt_print(self.var(variable))
            if ty not in C_BITS:  # one past the boundary wraps at run time
                step = 1 if value > 0 else -1
                out += self.stmt_print(self.binary("+", self.var(variable),
                                                   self.lit(step)))
        # All-literal expressions fold with arbitrary precision before the range
        # check, so these intermediate values may leave every type.
        folds = [
            ("u8", self.binary("-", self.lit(1 << 70), self.binary(
                "-", self.lit(1 << 70), self.lit(200)))),
            ("u64", self.binary("<<", self.lit(1), self.lit(63))),
            ("i64", self.unary("-", self.binary("<<", self.lit(1), self.lit(62)))),
            ("u8", self.binary("/", self.lit(-7 + 1000), self.lit(5))),
            ("i64", self.binary("/", self.lit(-7), self.lit(2))),
            ("i64", self.binary("%", self.lit(-7), self.lit(2))),
            ("int", self.binary("&", self.unary("~", self.lit(0)), self.lit(255))),
            ("u32", self.binary("^", self.lit(0xF0F0F0F0), self.lit(0x0F0F0F0F))),
            ("u8", self.binary(">>", self.lit(1 << 70), self.lit(63))),
            ("u8", self.binary("|", self.lit(ord("A")), self.lit(32))),
        ]
        self.r.shuffle(folds)
        for ty, expr in folds[:7]:
            _, statement = self.let(scope, self.fresh("v"), expr, ty=ty, annotate=True)
            out.append(statement)
            out += self.stmt_print(self.var(scope.vars[-1]))
        # Precedence over literals: every level, spelled without parentheses.
        chain = self.lit(self.r.randint(1, 9))
        for op in ["*", "+", "<<", "&", "^", "|"]:
            chain = self.binary(op, chain, self.lit(self.r.randint(1, 5)))
        _, statement = self.let(scope, self.fresh("v"), chain, ty="int", annotate=True)
        out.append(statement)
        out += self.stmt_print(self.var(scope.vars[-1]))
        return self.driver(out)

    def sec_structs(self):
        inner_name = self.fresh("In")
        outer_name = self.fresh("Out")
        inner_fields = [("a", self.r.choice(self.ints)), ("b", self.r.choice(self.ints))]
        self.structs[inner_name] = inner_fields
        self.structs[outer_name] = [("left", inner_name), ("right", inner_name),
                                    ("tag", self.r.choice(self.ints)),
                                    ("next", f"*{outer_name}")]
        self.add_item([f"struct {inner_name} {{",
                       f"    a: {inner_fields[0][1]},",
                       f"    b: {inner_fields[1][1]},",
                       "}"])
        self.add_item([f"struct {outer_name} {{ left: {inner_name}, right: {inner_name}, "
                       f"tag: {self.structs[outer_name][2][1]}, "
                       f"next: *{outer_name}{self.r.choice([',', ''])} }}"])
        scope = Scope()
        out = []
        make = [self.struct_lit(inner_name, scope) for _ in range(2)]
        variables = []
        for expr in make:
            variable, statement = self.let(scope, self.fresh("s"), expr, ty=inner_name)
            variables.append(variable)
            out.append(statement)
        tag_ty = self.structs[outer_name][2][1]
        tag = self.coerce(self.lit(self.edge(tag_ty)), tag_ty)
        outer_expr = self.make_struct(outer_name, [
            ("left", self.var(variables[0])), ("right", self.var(variables[1])),
            ("tag", tag), ("next", self.typed_null(f"*{outer_name}"))])
        outer, statement = self.let(scope, self.fresh("o"), outer_expr, ty=outer_name)
        out.append(statement)
        # Value semantics: mutating the copy must not touch the original.
        copy, statement = self.let(scope, self.fresh("o"), self.var(outer), ty=outer_name)
        out.append(statement)
        left_a = self.field(self.field(self.var(copy), "left", inner_name), "a",
                            inner_fields[0][1])
        out.append(self.assign(left_a, self.int_expr(inner_fields[0][1], 2, scope)))
        out += self.stmt_print(left_a)
        out += self.stmt_print(self.field(self.field(self.var(outer), "left", inner_name),
                                          "a", inner_fields[0][1]))
        # Through a pointer, and a function taking and returning a struct.
        pointer, statement = self.let(scope, self.fresh("q"),
                                      self.address_of(self.var(outer), f"*{outer_name}"),
                                      ty=f"*{outer_name}")
        out.append(statement)
        through = self.field(self.deref(self.var(pointer)), "tag", tag_ty)
        out.append(self.assign(through, self.int_expr(tag_ty, 1, scope)))
        out += self.stmt_print(self.field(self.var(outer), "tag", tag_ty))
        fn_scope = Scope()
        param = fn_scope.add(Var(self.fresh("p"), inner_name, self.fresh("k")))
        body = [self.assign(self.field(self.var(param), "a", inner_fields[0][1]),
                            self.int_expr(inner_fields[0][1], 1, Scope())),
                self.ret(self.var(param), inner_name)]
        function = self.define_fn(self.fresh("fs"), [param], inner_name, body)
        call = self.call(function, [self.var(variables[1])])
        returned, statement = self.let(scope, self.fresh("s"), call, ty=inner_name)
        out.append(statement)
        out += self.stmt_print(self.field(self.var(returned), "a", inner_fields[0][1]))
        out += self.stmt_print(self.field(self.var(variables[1]), "a", inner_fields[0][1]))
        for field_name, field_ty in inner_fields:
            out += self.stmt_print(self.field(self.var(returned), field_name, field_ty))
        out += self.stmt_print(E(f"sizeof<{outer_name}>()", "u64",
                                 lambda m, f, size=self.size_of(outer_name): size,
                                 P_POSTFIX))
        return self.driver(out)

    def make_struct(self, name, inits):
        fields = self.structs[name]
        pairs = []
        for field_name, field_ty in fields:
            expr = dict(inits)[field_name]
            pairs.append((field_name, self.coerce(expr, field_ty), field_ty))
        trailing = "," if self.r.random() < 0.3 else ""
        body = ", ".join(f"{n}: {e.src}" for n, e, _ in pairs)
        text = f"{name} {{ {body}{trailing} }}"

        def ev(m, f):
            return {n: copy_value(e.ev(m, f)) for n, e, _ in pairs}
        return E(text, name, ev)

    def struct_lit(self, name, scope):
        return self.make_struct(name, [(field_name, self.int_expr(ty, 2, scope))
                                       for field_name, ty in self.structs[name]])

    def typed_null(self, ty):
        return E(f"0 as {ty}", ty, lambda m, f: NULL, P_CAST)

    def declare_enum(self, name, variants):
        self.enums[name] = variants

    def variant(self, enum, name):
        index = self.enums[enum].index(name)
        value = (index,)
        return E(f"{enum}.{name}", enum, lambda m, f: value, P_POSTFIX)

    def match(self, subject, arms, default=None):
        """arms: [(variant index, pattern text, statements, bindings or None)]"""
        lines = [f"match {subject.src} {{"]
        runs = {}
        for variant_index, pattern, body, bind in arms:
            lines.append(f"    {pattern} => {{")
            lines.extend(indented(indented(self.block_lines(body))))
            lines.append("    }")
            runs[variant_index] = (self.body_run(body), bind)
        default_run = None
        if default is not None:
            lines.append("    _ => {")
            lines.extend(indented(indented(self.block_lines(default))))
            lines.append("    }")
            default_run = self.body_run(default)
        lines.append("}")

        def run(m, f):
            value = subject.ev(m, f)
            arm = runs.get(value[0])
            if arm is None:
                if default_run is None:
                    raise AssertionError("unmatched variant")
                default_run(m, f)
                return
            body_run, bind = arm
            if bind:
                bind(m, f, value)
            body_run(m, f)
        return S(lines, run)

    def sec_enums(self):
        name = self.fresh("En")
        variants = [f"V{i}" for i in range(self.r.randint(3, 6))]
        self.declare_enum(name, variants)
        self.add_item([f"enum {name} {{"] + [f"    {v}," for v in variants] + ["}"])
        scope = Scope()
        out = []
        param = scope.add(Var(self.fresh("p"), name, self.fresh("k")))
        total, binding = self.let(Scope(scope), self.fresh("v"), self.lit(0), ty="int")
        arms = []
        chosen = self.r.sample(range(len(variants)), self.r.randint(1, len(variants) - 1))
        for index in chosen:
            pattern = f"{name}.{variants[index]}"
            arms.append((index, pattern,
                         [self.assign(self.var(total), self.lit(index * 3 + 1), "+=")],
                         None))
        default = [self.assign(self.var(total), self.lit(100), "+=")]
        match_stmt = self.match(self.var(param), arms, default)
        body = [binding, match_stmt, self.ret(self.var(total), "int")]
        function = self.define_fn(self.fresh("fe"), [param], "int", body)
        for index in range(len(variants)):
            out += self.stmt_print(self.call(function, [self.variant(name, variants[index])]))
        # Enum comparison, and cast of a variant to an integer.
        first, statement = self.let(Scope(), self.fresh("x"), self.variant(name, variants[0]),
                                    ty=name)
        scope_main = Scope()
        scope_main.add(first)
        out.append(statement)
        value, statement = self.let(scope_main, self.fresh("v"),
                                    self.cast(self.var(first), "u32"), ty="u32")
        out.append(statement)
        out += self.stmt_print(self.var(value))
        cond = self.binary("==", self.var(first), self.variant(name, variants[0]))
        out.append(self.if_chain([(cond, self.stmt_print(self.lit(1)))]))
        cond = self.binary("!=", self.var(first), self.variant(name, variants[-1]))
        out.append(self.if_chain([(cond, self.stmt_print(self.lit(2)))]))
        # A match inside a loop, with an early exit from one arm.
        loop_scope = Scope(scope_main)
        i_var = loop_scope.add(Var(self.fresh("i"), "u32", self.fresh("k"), frozen=True))
        current, statement = self.let(loop_scope, self.fresh("e"),
                                      self.variant(name, variants[1]), ty=name)
        arm_body = self.stmt_print(self.var(i_var))
        arm_body.append(self.if_chain([(self.binary(">", self.var(i_var), self.lit(1)),
                                        [self.break_stmt()])]))
        looped = self.match(self.var(current),
                            [(1, f"{name}.{variants[1]}", arm_body, None)],
                            self.stmt_print(self.lit(9)))
        out.append(self.for_loop(i_var, self.lit(0), self.lit(4), False,
                                 [statement, looped], annotate_ty="u32"))
        return self.driver(out)

    def sec_pointers(self):
        scope = Scope()
        out = []
        ty = self.r.choice(["u8", "u32", "u64", "i64", "int"])
        element, statement = self.let(scope, self.fresh("v"),
                                      self.int_expr(ty, 1, scope), ty=ty)
        out.append(statement)
        pointer, statement = self.let(scope, self.fresh("q"),
                                      self.address_of(self.var(element), f"*{ty}"),
                                      ty=f"*{ty}")
        out.append(statement)
        out.append(self.assign(self.deref(self.var(pointer)),
                               self.int_expr(ty, 1, scope)))
        out += self.stmt_print(self.var(element))
        # Double indirection.
        twice, statement = self.let(scope, self.fresh("r"),
                                    self.address_of(self.var(pointer), f"**{ty}"),
                                    ty=f"**{ty}")
        out.append(statement)
        out.append(self.assign(self.deref(self.deref(self.var(twice))),
                               self.lit(self.r.randint(0, 100))))
        out += self.stmt_print(self.var(element))
        # A const view and null comparison.
        view, statement = self.let(scope, self.fresh("r"),
                                   self.cast(self.var(pointer), f"*const {ty}"),
                                   ty=f"*const {ty}")
        out.append(statement)
        out += self.stmt_print(self.deref(self.var(view)))
        null, statement = self.let(scope, self.fresh("r"),
                                   self.typed_null(f"*{ty}"), ty=f"*{ty}")
        out.append(statement)
        same = self.binary("==", self.var(null), self.typed_null(f"*{ty}"))
        out.append(self.if_chain([(same, self.stmt_print(self.lit(11)))]))
        other = self.binary("==", self.var(pointer), self.var(null))
        out.append(self.if_chain([(other, self.stmt_print(self.lit(12)))],
                                 self.stmt_print(self.lit(13))))
        # Heap: alloc, index, write, read, free.
        count = self.r.randint(2, 6)
        size = self.size_of(ty)
        allocated = E(f"alloc(sizeof<{ty}>() * {count})", "*void",
                      lambda m, f, bytes_=size * count: AllocToken(bytes_), P_POSTFIX)
        block, statement = self.let(scope, self.fresh("h"), self.cast(allocated, f"*{ty}"),
                                    ty=f"*{ty}")
        out.append(statement)
        i_scope = Scope(scope)
        i_var = i_scope.add(Var(self.fresh("i"), "u64", self.fresh("k"), frozen=True))
        element_expr = self.binary("+", self.cast(self.var(i_var), ty),
                                   self.coerce(self.lit(self.edge(ty)), ty))
        out.append(self.for_loop(i_var, self.lit(0), self.lit(count), False,
                                 [self.assign(self.index(self.var(block),
                                                         self.var(i_var), ty),
                                              element_expr)],
                                 annotate_ty="u64"))
        for position in range(count):
            out += self.stmt_print(self.index(self.var(block), self.lit(position), ty))
        def free_run(m, f, name=block.key):
            f[name].obj.dead = True
        out.append(S([f"free({block.name} as *void);"], free_run))
        return self.driver(out)

    def sec_globals(self):
        out = []
        scope = Scope()
        ty = self.r.choice(["u8", "u32", "u64", "i64", "int", "c_int"])
        value = self.edge(ty)
        counter = self.add_global(self.fresh("G"), ty, str(value), value)
        constant_value = self.r.randint(-5, 5)
        constant = self.add_global(self.fresh("C"), "i64", str(constant_value),
                                   constant_value, const=True)
        element_ty = self.r.choice(["u8", "u32", "u64", "i64"])
        values = [self.edge(element_ty) for _ in range(self.r.randint(2, 5))]
        text = "[" + ", ".join(str(v) for v in values) + self.r.choice(["", ","]) + "]"
        array = self.add_global(self.fresh("A"),
                                f"[{element_ty}; {len(values)}]", text, list(values))
        out += self.stmt_print(self.var(counter))
        out += self.stmt_print(self.var(constant))
        target = self.var(counter)
        out.append(self.assign(target, self.int_expr(ty, 2, scope)))
        out += self.stmt_print(self.var(counter))
        i_scope = Scope(scope)
        i_var = i_scope.add(Var(self.fresh("i"), "u64", self.fresh("k"), frozen=True))
        out.append(self.for_loop(i_var, self.lit(0), self.lit(len(values)), False,
                                 self.stmt_print(self.index(self.var(array), self.var(i_var),
                                                            element_ty)),
                                 annotate_ty="u64"))
        position = self.r.randrange(len(values))
        slot = self.index(self.var(array), self.lit(position), element_ty)
        out.append(self.assign(slot, self.lit(self.r.randint(0, 200))))
        pointer, statement = self.let(scope, self.fresh("q"),
                                      self.address_of(slot, f"*{element_ty}"),
                                      ty=f"*{element_ty}")
        out.append(statement)
        out.append(self.assign(self.deref(self.var(pointer)), self.lit(7), "+="))
        out += self.stmt_print(self.index(self.var(array), self.lit(position), element_ty))
        return self.driver(out)

    def sec_calls(self):
        # Direct recursion and mutual recursion, with bounded depth.
        fib = Scope()
        n = fib.add(Var(self.fresh("n"), "int", self.fresh("k")))
        name = self.fresh("fr")
        fib_fn = Fn(name, [n], "int", None, True)
        base = self.if_chain([(self.binary("<", self.var(n), self.lit(2)),
                               [self.ret(self.var(n), "int")])])
        step = self.binary("+", self.call(fib_fn, [self.binary("-", self.var(n), self.lit(1))]),
                           self.call(fib_fn, [self.binary("-", self.var(n), self.lit(2))]))
        body = [base, self.ret(step, "int")]
        fib_fn.body = self.body_run(body)
        self.add_item([self.fn_head(name, [n], "int") + " {"]
                      + indented(self.block_lines(body)) + ["}"])
        even_name, odd_name = self.fresh("fe"), self.fresh("fo")
        even_scope, odd_scope = Scope(), Scope()
        even_n = even_scope.add(Var(self.fresh("n"), "u32", self.fresh("k")))
        odd_n = odd_scope.add(Var(self.fresh("n"), "u32", self.fresh("k")))
        even_fn = Fn(even_name, [even_n], "bool", None, True)
        odd_fn = Fn(odd_name, [odd_n], "bool", None, True)

        def bool_lit(word):
            return E("true" if word else "false", "bool", lambda m, f: word)
        even_body = [self.if_chain([(self.binary("==", self.var(even_n), self.lit(0)),
                                     [self.ret(bool_lit(True), "bool")])]),
                     self.ret(self.call(odd_fn, [self.binary("-", self.var(even_n),
                                                             self.lit(1))]), "bool")]
        odd_body = [self.if_chain([(self.binary("==", self.var(odd_n), self.lit(0)),
                                    [self.ret(bool_lit(False), "bool")])]),
                    self.ret(self.call(even_fn, [self.binary("-", self.var(odd_n),
                                                             self.lit(1))]), "bool")]
        even_fn.body = self.body_run(even_body)
        odd_fn.body = self.body_run(odd_body)
        self.add_item([self.fn_head(even_name, [even_n], "bool") + " {"]
                      + indented(self.block_lines(even_body)) + ["}"])
        self.add_item([self.fn_head(odd_name, [odd_n], "bool") + " {"]
                      + indented(self.block_lines(odd_body)) + ["}"])
        # Many arguments, each a different type and position.
        many = Scope()
        types = [self.r.choice(self.ints) for _ in range(self.r.randint(5, 8))]
        params = [many.add(Var(self.fresh("p"), t, self.fresh("k"))) for t in types]
        pick = self.r.randrange(len(params))
        many_body = [self.ret(self.var(params[pick]), types[pick])]
        many_fn = self.define_fn(self.fresh("fm"), params, types[pick], many_body)
        out = self.stmt_print(self.call(fib_fn, [self.lit(self.r.randint(5, 12))]))
        word = self.r.randint(0, 9)
        call = self.call(even_fn, [self.lit(word)])
        out.append(self.if_chain([(call, self.stmt_print(self.lit(21)))],
                                 self.stmt_print(self.lit(22))))
        args = [self.coerce(self.lit(self.edge(t)), t) for t in types]
        out += self.stmt_print(self.call(many_fn, args))
        return self.driver(out)

    def sec_strings(self):
        scope = Scope()
        out = []
        samples = [
            ("plain text", b"plain text"),
            (r"tab\there", b"tab\there"),
            (r"quote \"x\"", b'quote "x"'),
            (r"back\\slash", b"back\\slash"),
            (r"hex \x41\x7a", b"hex Az"),
            (r"bell-free \r ok", b"bell-free \r ok"),
            ("{ not a block } // nor a comment", b"{ not a block } // nor a comment"),
        ]
        text, data = self.r.choice(samples)
        pool = Heap(list(data) + [0])
        literal = E(f'"{text}"', "*u8", lambda m, f: Ptr(pool, 0))
        pointer, statement = self.let(scope, self.fresh("t"), literal, ty="*u8")
        out.append(statement)

        def print_line(m, f, key=pointer.key):
            pointer_value = f[key]
            m.out += bytes(pointer_value.obj[:-1]) + b"\n"
        out.append(S([f"println({pointer.name});"], print_line))

        def print_err(m, f, key=pointer.key):
            pointer_value = f[key]
            m.err += bytes(pointer_value.obj[:-1]) + b"\n"
        out.append(S([f"eprintln({pointer.name});"], print_err))
        position = self.r.randrange(len(data))
        out += self.stmt_print(self.index(self.var(pointer), self.lit(position), "u8"))
        char_value = ord(self.r.choice("MNOPQ"))
        spelled = E(f"'{chr(char_value)}'", "lit", lambda m, f: char_value, val=char_value)
        out += self.stmt_print(self.binary("+", self.coerce(spelled, "u8"), self.lit(1)))
        return self.driver(out)

    def sec_scopes(self):
        scope = Scope()
        out = []
        name = self.fresh("sh")
        outer, statement = self.let(scope, name, self.lit(self.r.randint(0, 50)), ty="int")
        out.append(statement)
        inner_scope = Scope(scope)
        inner, statement = self.let(inner_scope, name, self.lit(self.r.randint(51, 99)),
                                    ty="int", annotate=True)
        inner_body = [statement]
        inner_body += self.stmt_print(self.var(inner))
        inner_body.append(self.assign(self.var(inner), self.lit(1), "+="))
        out.append(self.block(inner_body))
        out += self.stmt_print(self.var(outer))
        constant, statement = self.let(scope, self.fresh("cv"), self.lit(self.r.randint(0, 9)),
                                       ty="u8", const=True, annotate=True)
        out.append(statement)
        out += self.stmt_print(self.var(constant))
        return self.driver(out)

    def sec_builtins(self):
        scope = Scope()
        out = []
        for ty in self.r.sample(["u8", "u32", "u64", "i64", "int", "c_int", "c_char",
                                 "bool", "*u8"], 3):
            size = self.size_of(ty)
            out += self.stmt_print(E(f"sizeof<{ty}>()", "u64", lambda m, f, s=size: s,
                                     P_POSTFIX))
        out += self.stmt_print(E("arg_count()", "u64",
                                 lambda m, f: len(m.args) + 1, P_POSTFIX))
        which = self.r.randrange(len(self.args))

        def print_arg(m, f):
            m.out += m.args[which].encode() + b"\n"
        out.append(S([f"println(arg_get({which + 1}));"], print_arg))
        declaration = ["extern fn abs(x: c_int) -> c_int;"]
        if declaration not in self.items["main.mx"]:
            self.add_item(declaration)
        value = self.r.randint(-40, 40)
        abs_call = E(f"abs({value})", "c_int", lambda m, f: abs(value), P_POSTFIX)
        out += self.stmt_print(abs_call)
        # A file written and read back, in the program's working directory.
        path = f"gen_{self.fresh('f')}.tmp"
        message = bytes(self.r.randint(32, 126) for _ in range(self.r.randint(3, 12)))
        text = "".join(f"\\x{byte:02x}" for byte in message)
        out += self.file_round_trip(scope, path, text, message)
        return self.driver(out)

    def file_round_trip(self, scope, path, text, message):
        data_pool = Heap(list(message) + [0])
        writing, statement = self.let(
            scope, self.fresh("w"),
            E(f'file_open("{path}", "w")', "*void",
              lambda m, f: Ptr(m.files.setdefault(path, Heap()), 0), P_POSTFIX),
            ty="*void")
        body = [statement]
        data_text = f'"{text}"'
        length = len(message)

        def write(m, f):
            m.files[path][:] = data_pool[:length]
            return length
        wrote = E(f"file_write({writing.name}, {data_text}, {length})", "u64",
                  lambda m, f: write(m, f), P_POSTFIX)
        body += self.stmt_print(wrote)
        body.append(S([f"file_close({writing.name});"], lambda m, f: None))
        reading, statement = self.let(
            scope, self.fresh("w"),
            E(f'file_open("{path}", "r")', "*void",
              lambda m, f: Ptr(m.files.setdefault(path, Heap()), 0), P_POSTFIX),
            ty="*void")
        body.append(statement)
        buffer_size = len(message) + 8
        buffer_expr = self.cast(
            E(f"alloc({buffer_size})", "*void",
              lambda m, f: AllocToken(buffer_size), P_POSTFIX), "*u8")
        buffer, statement = self.let(scope, self.fresh("b"), buffer_expr, ty="*u8")
        body.append(statement)

        def read(m, f):
            pointer = f[buffer.key]
            stored = list(m.files[path])
            pointer.obj[:len(stored)] = stored
            return len(stored)
        got = E(f"file_read({reading.name}, {buffer.name}, {buffer_size})", "u64",
                lambda m, f: read(m, f), P_POSTFIX)
        body += self.stmt_print(got)
        body.append(S([f"file_close({reading.name});"], lambda m, f: None))
        body += self.stmt_print(self.index(self.var(buffer), self.lit(len(message) - 1), "u8"))
        return body

    def sec_imports(self):
        """A one-segment relative import, across a real file boundary."""
        file_name = f"mod{self.fresh('x')}.mx"
        self.imports["main.mx"].append(file_name[:-3])
        self.items.setdefault(file_name, [])
        self.imports.setdefault(file_name, [])
        count = self.r.randint(2, 3)
        made = [self.random_fn(self.fresh("fh"), True, file=file_name)
                for _ in range(count)]
        self.pure_fns.extend(made)
        global_name = self.fresh("HG")
        value = self.r.randint(0, 200)
        self.add_item([f"let {global_name}: u32 = {value};"], file_name)
        variable = Var(global_name, "u32", self.fresh("g"), is_global=True)

        def init(m, key=variable.key):
            m.globals[key] = value
        self.global_inits.append(init)
        out = self.stmt_print(self.var(variable))
        for function in made:
            args = [self.coerce(self.lit(self.edge(p.ty)), p.ty) for p in function.params]
            out += self.stmt_print(self.call(function, args))
        return self.driver(out)

    def sec_shortcircuit(self):
        """&& and || with impure operands: the one order the language fixes."""
        hits = self.add_global(self.fresh("G"), "u32", "0", 0)
        name = self.fresh("fb")
        scope = Scope()
        param = scope.add(Var(self.fresh("p"), "bool", self.fresh("k")))
        body = [self.assign(self.var(hits), self.lit(1), "+="),
                self.ret(self.var(param), "bool")]
        function = self.define_fn(name, [param], "bool", body, pure=False)

        def word(value):
            return E("true" if value else "false", "bool", lambda m, f: value)
        out = []
        for _ in range(4):
            left, right = self.r.choice([True, False]), self.r.choice([True, False])
            op = self.r.choice(["&&", "||"])
            expr = self.binary(op, self.call(function, [word(left)]),
                               self.call(function, [word(right)]))
            out.append(self.if_chain([(expr, self.stmt_print(self.lit(31)))],
                                     self.stmt_print(self.lit(32))))
            out += self.stmt_print(self.var(hits))
        return self.driver(out)

    # ===== the operator and type-pair matrix =====

    def sec_matrix(self):
        """Every binary operator on every ordered pair of integer types."""
        # Exact shapes are the point here, so no redundant parentheses: `x > (y)`
        # would be respelled by bin_text and lose the `>` it is meant to test.
        self.paren_noise = 0
        out = []
        for source in self.ints:
            for target in self.ints:
                out += self.matrix_pair(source, target)
        for ty in self.ints:
            out += self.matrix_literals(ty)
        return self.driver(out)

    def matrix_values(self, source, target):
        if source in C_BITS or target in C_BITS:
            return [(7, 2, 1), (5, 3, 2), (7, 7, 0)]  # never below zero: c_char
        low, high = int_range(target)
        source_low, source_high = int_range(source)
        count_high = source_high if source_high < 64 else 64
        rows = [(source_low, 1, 0), (source_high, -1 if low < 0 else high, 1),
                (0, high, count_high), (1, low if low < 0 else 3, count_high - 1)]
        if source == target and low < 0:
            rows.append((low, -1, 1))  # the one quotient that does not fit
        cleaned = []
        for a, b, c in rows:
            if b == 0:
                b = 1
            cleaned.append((a, b, c))
        return cleaned

    def matrix_pair(self, source, target):
        scope = Scope()
        a = scope.add(Var(self.fresh("a"), source, self.fresh("k")))
        b = scope.add(Var(self.fresh("b"), target, self.fresh("k")))
        c = scope.add(Var(self.fresh("c"), source, self.fresh("k")))
        body = []
        for op in INT_BINARY_OPS:
            if op in SHIFTS:
                expr = self.binary(op, self.var(b), self.var(c))
            else:
                expr = self.binary(op, self.cast(self.var(a), target), self.var(b))
            if op in COMPARISONS:
                body.append(self.if_chain([(expr, self.stmt_print(self.lit(1)))],
                                          self.stmt_print(self.lit(0))))
            else:
                body += self.stmt_print(expr)
        function = self.define_fn(self.fresh("mat"), [a, b, c], None, body)
        out = []
        for row in self.matrix_values(source, target):
            args = [self.coerce(self.lit(row[0]), source),
                    self.coerce(self.lit(row[1]), target),
                    self.coerce(self.lit(row[2]), source)]
            out.append(self.expr_stmt(self.call(function, args, ty="void")))
        return out

    def matrix_literals(self, ty):
        """Literal coercion on each side of every operator."""
        scope = Scope()
        x = scope.add(Var(self.fresh("x"), ty, self.fresh("k")))
        body = []
        _, high = int_range(ty)
        for op in INT_BINARY_OPS:
            operand = self.lit(min(high, 1 if ty in C_BITS else high))
            small = self.lit(7 if ty in C_BITS else min(high, 7))
            if op in SHIFTS:
                right = self.binary(op, self.var(x), self.lit(1))
                body += self.stmt_print(right)
                continue
            for left, right in ((self.var(x), operand), (small, self.var(x))):
                if op in ("/", "%") and right.ty == "lit" and right.val == 0:
                    continue
                expr = self.binary(op, left, right)
                if op in COMPARISONS:
                    body.append(self.if_chain([(expr, self.stmt_print(self.lit(1)))],
                                              self.stmt_print(self.lit(0))))
                else:
                    body += self.stmt_print(expr)
        function = self.define_fn(self.fresh("lit"), [x], None, body)
        out = []
        for value in ([1, 2, 3] if ty in C_BITS else [1, int_range(ty)[1],
                                                      max(int_range(ty)[0], 1)]):
            out.append(self.expr_stmt(self.call(function,
                                                [self.coerce(self.lit(value), ty)],
                                                ty="void")))
        return out

    # ===== full-language sections =====

    def sec_widths(self):
        """Every integer width, and the casts between them."""
        scope = Scope()
        out = []
        for ty in FIXED_INTS:
            variable, statement = self.let(scope, self.fresh("v"),
                                           self.coerce(self.lit(int_range(ty)[1]), ty),
                                           ty=ty, annotate=True)
            out.append(statement)
            out += self.stmt_print(self.binary("+", self.var(variable), self.lit(1)))
            for target in self.r.sample(FIXED_INTS, 3):
                out += self.stmt_print(self.cast(self.var(variable), target))
        return self.driver(out)

    def sec_generics(self):
        pair, box = self.fresh("Pair"), self.fresh("Box")
        self.add_item([f"struct {pair}<A, B> {{ first: A, second: B }}"])
        self.add_item([f"struct {box}<T> {{ inner: T }}"])
        maybe = self.fresh("Maybe")
        self.add_item([f"enum {maybe}<T> {{ Nothing, Just(T) }}"])
        swap, unbox, wrap_name = self.fresh("swap"), self.fresh("unbox"), self.fresh("wrap")
        self.add_item([
            f"fn {swap}<A, B>(value: {pair}<A, B>) -> {pair}<B, A> {{",
            f"    return {pair}<B, A> {{ first: value.second, second: value.first }};",
            "}"])
        self.add_item([f"fn {unbox}<T>(value: {box}<T>) -> T {{ return value.inner; }}"])
        self.add_item([f"fn {wrap_name}<T>(value: T) -> {box}<{box}<T>> {{",
                       # `> >`: a struct literal's lookahead misses `>>` (BLOCKERS.md)
                       f"    return {box}<{box}<T> > {{ inner: {box}<T> {{ inner: value }} }};",
                       "}"])
        scope = Scope()
        out = []
        first_ty, second_ty = self.r.sample(FIXED_INTS, 2)
        first_val = self.edge(first_ty)
        second_val = self.edge(second_ty)
        pair_ty = f"{pair}<{first_ty}, {second_ty}>"
        made = E(f"{pair}<{first_ty}, {second_ty}> {{ first: {first_val}, "
                 f"second: {second_val} }}",
                 pair_ty, lambda m, f: {"first": first_val, "second": second_val})
        value, statement = self.let(scope, self.fresh("p"), made, ty=pair_ty, annotate=True)
        out.append(statement)
        swapped_ty = f"{pair}<{second_ty}, {first_ty}>"

        def swap_run(m, f):
            v = copy_value(f[value.key])
            return {"first": v["second"], "second": v["first"]}
        call = E(f"{swap}<{first_ty}, {second_ty}>({value.name})", swapped_ty,
                 swap_run, P_POSTFIX)
        result, statement = self.let(scope, self.fresh("p"), call, ty=swapped_ty)
        out.append(statement)
        out += self.stmt_print(self.field(self.var(result), "first", second_ty))
        out += self.stmt_print(self.field(self.var(result), "second", first_ty))
        # Inferred type arguments, and `>>` closing two generic lists at once.
        inner_ty = self.r.choice(FIXED_INTS)
        inner_val = self.edge(inner_ty)
        outer_ty = f"{box}<{box}<{inner_ty}>>"
        nested = E(f"{wrap_name}({self.coerce(self.lit(inner_val), inner_ty).src} "
                   f"as {inner_ty})", outer_ty,
                   lambda m, f: {"inner": {"inner": inner_val}}, P_POSTFIX)
        boxed, statement = self.let(scope, self.fresh("b"), nested, ty=outer_ty, annotate=True)
        out.append(statement)
        out += self.stmt_print(E(f"{unbox}({unbox}({boxed.name}))", inner_ty,
                                 lambda m, f: f[boxed.key]["inner"]["inner"], P_POSTFIX))
        # A generic enum with a payload, matched.
        maybe_ty = f"{maybe}<u32>"
        number = self.r.randint(0, 900)
        just = E(f"{maybe}<u32>.Just({number})", maybe_ty, lambda m, f: (1, number), P_POSTFIX)
        held, statement = self.let(scope, self.fresh("m"), just, ty=maybe_ty, annotate=True)
        out.append(statement)
        arm_scope = Scope(scope)
        bound = arm_scope.add(Var(self.fresh("n"), "u32", self.fresh("k"), frozen=True))

        def bind(m, f, value):
            f[bound.key] = value[1]
        out.append(self.match(self.var(held), [
            (1, f"{maybe}<u32>.Just({bound.name})", self.stmt_print(self.var(bound)), bind),
            (0, f"{maybe}<u32>.Nothing", self.stmt_print(self.lit(0)), None),
        ]))
        return self.driver(out)

    def sec_payload(self):
        shape = self.fresh("Shape")
        kinds = [("Dot", []), ("Line", ["i32"]), ("Rect", ["i32", "u8"]),
                 ("Tag", ["u16", "bool", "i64"])]
        self.add_item([f"enum {shape} {{"] + [
            f"    {name}({', '.join(types)})," if types else f"    {name},"
            for name, types in kinds] + ["}"])
        self.enums[shape] = [name for name, _ in kinds]
        scope = Scope()
        param = scope.add(Var(self.fresh("p"), shape, self.fresh("k")))
        total, binding = self.let(Scope(scope), self.fresh("v"), self.lit(0), ty="i64")
        line_var = Var(self.fresh("l"), "i32", self.fresh("k"), frozen=True)
        a_var = Var(self.fresh("w"), "i32", self.fresh("k"), frozen=True)
        b_var = Var(self.fresh("h"), "u8", self.fresh("k"), frozen=True)
        tag_a = Var(self.fresh("t"), "u16", self.fresh("k"), frozen=True)

        def bind_line(m, f, value, k=line_var.key):
            f[k] = value[1]

        def bind_rect(m, f, value):
            f[a_var.key] = value[1]
            f[b_var.key] = value[2]

        def bind_tag(m, f, value):
            f[tag_a.key] = value[1]
        arms = [
            (0, f"{shape}.Dot", [self.assign(self.var(total), self.lit(1))], None),
            (1, f"{shape}.Line({line_var.name})",
             [self.assign(self.var(total), self.cast(self.var(line_var), "i64"))], bind_line),
            (2, f"{shape}.Rect({a_var.name}, {b_var.name})",
             [self.assign(self.var(total),
                          self.binary("*", self.cast(self.var(a_var), "i64"),
                                      self.cast(self.var(b_var), "i64")))], bind_rect),
            (3, f"{shape}.Tag({tag_a.name}, _, _)",
             [self.assign(self.var(total), self.cast(self.var(tag_a), "i64"))], bind_tag),
        ]
        body = [binding, self.match(self.var(param), arms),
                self.ret(self.var(total), "i64")]
        function = self.define_fn(self.fresh("area"), [param], "i64", body)
        out = []
        values = [
            (f"{shape}.Dot", (0,)),
            (f"{shape}.Line({self.r.randint(-50, 50)})", None),
            (f"{shape}.Rect({self.r.randint(-20, 20)}, {self.r.randint(0, 20)})", None),
            (f"{shape}.Tag({self.r.randint(0, 900)}, true, {self.r.randint(-9, 9)})", None),
        ]
        for text, value in values:
            if value is None:
                inner = text[text.index("(") + 1:-1].split(", ")
                name = text.split(".")[1].split("(")[0]
                index = self.enums[shape].index(name)
                parsed = [True if item == "true" else int(item) for item in inner]
                value = tuple([index] + parsed)
            literal = E(text, shape, lambda m, f, v=value: v, P_POSTFIX)
            out += self.stmt_print(self.call(function, [literal]))
        return self.driver(out)

    def sec_tuples(self):
        scope = Scope()
        out = []
        a, b, c = self.r.randint(-9, 9), self.r.randint(0, 9), self.r.randint(-9, 9)
        tuple_lit = E(f"({a}, {b}, {c},)", "(i64, u8, i32)",
                      lambda m, f: [a, b, c])
        value, statement = self.let(scope, self.fresh("t"), tuple_lit,
                                    ty="(i64, u8, i32)", annotate=True)
        out.append(statement)
        for position, ty in enumerate(["i64", "u8", "i32"]):
            out += self.stmt_print(E(f"{value.name}.{position}", ty,
                                     lambda m, f, p=position: f[value.key][p],
                                     P_POSTFIX))
        # Local arrays, the repeat form, a whole-array loop, and a slice over one.
        size = self.r.randint(2, 6)
        fill = self.r.randint(0, 200)
        repeated = E(f"[{fill}; {size}]", f"[u8; {size}]",
                     lambda m, f: [fill] * size)
        array, statement = self.let(scope, self.fresh("a"), repeated, ty=f"[u8; {size}]",
                                    annotate=True)
        out.append(statement)
        spot = self.r.randrange(size)
        out.append(self.assign(self.index(self.var(array), self.lit(spot), "u8"),
                               self.lit(self.r.randint(0, 250))))
        listed = E("[1, 2, 3]", "[i32; 3]", lambda m, f: [1, 2, 3])
        three, statement = self.let(scope, self.fresh("a"), listed, ty="[i32; 3]",
                                    annotate=True)
        out.append(statement)
        i_scope = Scope(scope)
        i_var = i_scope.add(Var(self.fresh("i"), "u64", self.fresh("k"), frozen=True))
        out.append(self.for_loop(i_var, self.lit(0), self.lit(3), False,
                                 self.stmt_print(self.index(self.var(three),
                                                            self.var(i_var), "i32")),
                                 annotate_ty="u64"))
        for position in range(size):
            out += self.stmt_print(self.index(self.var(array), self.lit(position), "u8"))
        slice_ty = "[]u8"

        def slice_ev(m, f):
            return Ptr(f[array.key], 0), size
        made = E(f"slice(&{array.name}[0], {size})", slice_ty, slice_ev, P_POSTFIX)
        view, statement = self.let(scope, self.fresh("s"), made, ty=slice_ty, annotate=True)
        out.append(statement)
        out += self.stmt_print(E(f"len({view.name})", "u64",
                                 lambda m, f: f[view.key][1], P_POSTFIX))
        element = self.r.randrange(size)
        out += self.stmt_print(E(f"{view.name}[{element}]", "u8",
                                 lambda m, f: f[view.key][0].offset(element).load(),
                                 P_POSTFIX))
        return self.driver(out)

    def sec_fnptrs(self):
        """Function values; the language has no closures."""
        double = self.fresh("dbl")
        negate = self.fresh("neg")
        apply = self.fresh("apply")
        self.add_item([f"fn {double}(x: i64) -> i64 {{ return x * 2; }}"])
        self.add_item([f"fn {negate}(x: i64) -> i64 {{ return -x; }}"])
        self.add_item([f"fn {apply}(g: fn(i64) -> i64, x: i64) -> i64 {{ return g(x); }}"])
        fns = {double: lambda x: wrap("i64", x * 2), negate: lambda x: wrap("i64", -x)}
        out = []
        scope = Scope()
        chosen = self.r.choice([double, negate])
        value = self.r.randint(-30, 30)
        stored, statement = self.let(scope, self.fresh("h"),
                                     E(chosen, "fn(i64) -> i64",
                                       lambda m, f: fns[chosen]),
                                     ty="fn(i64) -> i64", annotate=True)
        out.append(statement)
        call_text = f"{apply}({stored.name}, {value})"
        out += self.stmt_print(E(call_text, "i64",
                                 lambda m, f: f[stored.key](value), P_POSTFIX))
        null, statement = self.let(scope, self.fresh("h"),
                                   E("null", "fn(i64) -> i64", lambda m, f: NULL),
                                   ty="fn(i64) -> i64", annotate=True)
        out.append(statement)
        out.append(self.if_chain([(E(f"{null.name} == null", "bool", lambda m, f: True),
                                   self.stmt_print(self.lit(41)))]))
        return self.driver(out)

    def sec_errors(self):
        """try and defer, whose order the specification fixes."""
        # The checker recognises `try` only on an enum named Result (BLOCKERS.md).
        result = "Result"
        self.add_item(["enum Result<V, E> { Ok(V), Err(E) }"])
        check, wrapper = self.fresh("check"), self.fresh("wrapper")
        self.add_item([
            f"fn {check}(v: i64) -> {result}<i64, i64> {{",
            f"    if v < 0 {{ return {result}<i64, i64>.Err(v); }}",
            f"    return {result}<i64, i64>.Ok(v + 1);",
            "}"])
        self.add_item([
            f"fn {wrapper}(a: i64, b: i64) -> {result}<i64, i64> {{",
            "    defer print(900);",
            f"    let first: i64 = try {check}(a);",
            "    defer print(901);",
            f"    let second: i64 = try {check}(b);",
            f"    return {result}<i64, i64>.Ok(first + second);",
            "}"])
        out = []

        def run_check(v):
            return (1, v) if v < 0 else (0, v + 1)

        def run_wrapper(m, a, b):
            first = run_check(a)
            if first[0] == 1:
                m.out += b"900\n"
                return first
            second = run_check(b)
            if second[0] == 1:
                m.out += b"901\n900\n"
                return second
            m.out += b"901\n900\n"
            return (0, first[1] + second[1])
        result_ty = f"{result}<i64, i64>"
        for a, b in [(self.r.randint(-3, 5), self.r.randint(-3, 5)) for _ in range(4)]:
            bind_var = Var(self.fresh("n"), "i64", self.fresh("k"), frozen=True)
            text = f"{wrapper}({a}, {b})"
            call = E(text, result_ty, lambda m, f, a=a, b=b: run_wrapper(m, a, b), P_POSTFIX)

            def bind(m, f, value, key=bind_var.key):
                f[key] = value[1]
            match_stmt = self.match(call, [
                (0, f"{result}<i64, i64>.Ok({bind_var.name})", self.stmt_print(self.var(bind_var)),
                 bind),
                (1, f"{result}<i64, i64>.Err({bind_var.name})",
                 self.stmt_print(self.binary("-", self.var(bind_var), self.lit(1))), bind),
            ])
            out.append(match_stmt)
        return self.driver(out)

    def sec_resources(self):
        handle = self.fresh("Res")
        self.add_item([f"resource struct {handle} {{ id: i64 }}"])
        self.add_item([f"fn destroy(r: *{handle}) -> void {{ print((*r).id); }}"])
        scope = Scope()
        first, second = self.r.randint(1000, 1999), self.r.randint(2000, 2999)
        out = []

        def make(value):
            return E(f"{handle} {{ id: {value} }}", handle, lambda m, f: {"id": value})
        a, statement = self.let(scope, self.fresh("r"), make(first), ty=handle, annotate=True)
        out.append(statement)
        b_text = f"let moved{first}: {handle} = move {a.name};"
        moved_key = self.fresh("k")

        def run_move(m, f):
            f[moved_key] = f[a.key]
        out.append(S([b_text], run_move))
        c, statement = self.let(scope, self.fresh("r"), make(second), ty=handle, annotate=True)
        out.append(statement)

        def run_end(m, f):
            m.out += f"{f[c.key]['id']}\n".encode()
            m.out += f"{f[moved_key]['id']}\n".encode()
        out.append(S([f"// {c.name} and moved{first} are destroyed here, in reverse order"],
                     run_end))
        return self.driver(out)

    def sec_modules(self):
        unit = self.fresh("unit")
        alias = self.fresh("al")
        path = f"lib/{unit}.mx"
        self.items.setdefault(path, [])
        value = self.r.randint(1, 30)
        self.add_item([f"module lib.{unit};"], path)
        self.add_item([f"pub fn triple(x: i64) -> i64 {{ return helper(x) + {value}; }}"],
                      path)
        self.add_item(["fn helper(x: i64) -> i64 { return x * 3; }"], path)
        self.imports["main.mx"].append(f"lib.{unit} as {alias}")
        out = []
        argument = self.r.randint(-20, 20)
        result = wrap("i64", argument * 3 + value)
        out += self.stmt_print(E(f"{alias}.triple({argument})", "i64",
                                 lambda m, f: result, P_POSTFIX))
        alias_name = self.fresh("Word")
        self.add_item([f"type {alias_name} = u32;"])
        scope = Scope()
        word, statement = self.let(scope, self.fresh("w"),
                                   self.lit(self.r.randint(0, 100)), ty="u32")
        statement.lines = [statement.lines[0].replace(": u32", f": {alias_name}")]
        out.append(statement)
        out += self.stmt_print(self.var(word))
        return self.driver(out)

    def sec_floats(self):
        scope = Scope()
        out = []
        a, b = self.r.randint(-50, 50) + 0.5, self.r.randint(1, 9) + 0.25
        x, statement = self.let_float(scope, a, "f64")
        out.append(statement)
        y, statement = self.let_float(scope, b, "f64")
        out.append(statement)
        result = a * b + a / b

        def run_print(m, f):
            m.out += ("%.17g\n" % result).encode()
        out.append(S([f"print({x.name} * {y.name} + {x.name} / {y.name});"], run_print))
        as_int = int(result)
        out += self.stmt_print(E(f"({x.name} * {y.name} + {x.name} / {y.name}) as i64",
                                 "i64", lambda m, f: as_int, P_CAST))
        narrow = struct.unpack("<f", struct.pack("<f", abs(a)))[0]
        # Only a bare positive literal adopts f32; `-1.5` is a unary f64 expression.
        z, statement = self.let_float(scope, abs(a), "f32")
        out.append(statement)

        def run_narrow(m, f):
            m.out += ("%.17g\n" % narrow).encode()
        out.append(S([f"print({z.name});"], run_narrow))
        return self.driver(out)

    def let_float(self, scope, value, ty):
        variable = scope.add(Var(self.fresh("x"), ty, self.fresh("k")))

        def run(m, f):
            f[variable.key] = value
        return variable, S([f"let {variable.name}: {ty} = {value};"], run)

    def sec_misc(self):
        scope = Scope()
        out = []
        out.append(S(['asm("nop");'], lambda m, f: None))
        word = scope.add(Var(self.fresh("v"), "u32", self.fresh("k")))
        value = self.r.randint(0, 1 << 30)

        def run_let(m, f):
            f[word.key] = value
        out.append(S([f"let {word.name}: u32 = {value};"], run_let))
        written = self.r.randint(0, 500)

        def run_write(m, f):
            f[word.key] = written
        out.append(S([f"volatile_write<u32>(&{word.name}, {written});"], run_write))

        def run_read(m, f):
            m.out += f"{f[word.key]}\n".encode()
        out.append(S([f"print(volatile_read<u32>(&{word.name}));"], run_read))
        pointer, statement = self.let(scope, self.fresh("q"),
                                      E("null", "*u8", lambda m, f: NULL), ty="*u8",
                                      annotate=True)
        out.append(statement)
        out.append(self.if_chain([(self.binary("==", self.var(pointer),
                                               E("null", "ptr-null", lambda m, f: NULL)),
                                   self.stmt_print(self.lit(61)))]))
        name = self.fresh("sample")
        self.add_item([f'test "{name}" {{', "    let x: u8 = 1;", "    assert(x == 1);", "}"])
        return self.driver(out)


class AllocToken:
    """What alloc() returns until a cast gives it an element size."""

    def __init__(self, count):
        self.bytes = count


# ----- the public interface -----

def generate(spec):
    """Build one program; deterministic in `spec`."""
    gen = Gen(spec)
    gen.build()
    model, code, trapped = gen.execute()
    files = gen.render()
    total = sum(len(text.decode("ascii").splitlines()) for text in files.values())
    if trapped:
        code = None
    return Program(spec=spec, files=files, root="main.mx", args=gen.args,
                   expected_stdout=bytes(model.out), expected_stderr=bytes(model.err),
                   expected_exit=code, expect_trap=trapped, line_count=total,
                   sections=[kind for kind, _ in gen.sections])


def default_corpus(scale=1):
    """The fixed corpus. Size grows with `scale`; the seeds are not random."""
    specs = [Spec("matrix", 1, 0, flavor="matrix")]
    for index in range(2):
        specs.append(Spec(f"large{index}", 100 + index, 1600 * scale))
    for index in range(18 * scale):
        lines = 80 + (index * 37) % 180
        specs.append(Spec(f"small{index}", 200 + index, lines))
    for index in range(4):
        specs.append(Spec(f"trap{index}", 300 + index, 60, flavor="trap"))
    return specs


def full_corpus(scale=1):
    """Each full-only section kind is the focus of at least one program."""
    specs = []
    for index in range(len(FULL_ONLY_SECTIONS) * scale):
        specs.append(Spec(f"full{index}", 500 + index, 180, profile="full",
                          focus=FULL_ONLY_SECTIONS[index % len(FULL_ONLY_SECTIONS)]))
    return specs


def write_program(program, directory):
    directory = Path(directory)
    for relative, data in program.files.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return directory / program.root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--lines", type=int, default=300)
    parser.add_argument("--profile", choices=("subset", "full"), default="subset")
    parser.add_argument("--flavor", choices=("random", "matrix", "trap"), default="random")
    parser.add_argument("--out", required=True)
    options = parser.parse_args(argv)
    spec = Spec(f"cli{options.seed}", options.seed, options.lines, options.profile,
                options.flavor)
    program = generate(spec)
    root = write_program(program, options.out)
    (Path(options.out) / "expected_stdout.txt").write_bytes(program.expected_stdout)
    print(f"{root}: {program.line_count} lines, exit {program.expected_exit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
