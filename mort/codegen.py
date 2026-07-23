"""Code generator: a type-checked AST -> portable C11 source.

Design choices:
  * Mort ``int`` maps to ``int64_t``; ``bool`` to C ``bool``.
  * Every user function is emitted as ``mort_<name>`` so a Mort program can
    never collide with a C library symbol.
  * The user's ``main`` becomes ``mort_main`` and a real C ``main`` calls it,
    which sidesteps the int64/int return-type clash.
"""
from . import mort_ast as A

_C_BASE = {
    "i8": "int8_t", "i16": "int16_t", "i32": "int32_t", "i64": "int64_t",
    "u8": "uint8_t", "u16": "uint16_t", "u32": "uint32_t", "u64": "uint64_t",
    "bool": "bool", "void": "void",
    "f32": "float", "f64": "double",
    "c_char": "char", "c_uchar": "unsigned char",
    "c_short": "short", "c_ushort": "unsigned short",
    "c_int": "int", "c_uint": "unsigned int",
    "c_long": "long", "c_ulong": "unsigned long", "c_size": "size_t",
}

_FIXED_INT_INFO = {
    "i8": ("int8_t", "uint8_t", 8, True),
    "i16": ("int16_t", "uint16_t", 16, True),
    "i32": ("int32_t", "uint32_t", 32, True),
    "i64": ("int64_t", "uint64_t", 64, True),
    "u8": ("uint8_t", "uint8_t", 8, False),
    "u16": ("uint16_t", "uint16_t", 16, False),
    "u32": ("uint32_t", "uint32_t", 32, False),
    "u64": ("uint64_t", "uint64_t", 64, False),
}

_FLOAT_TYPES = {"f32", "f64"}


def _is_array(t):
    return t.startswith("[") and not t.startswith("[]")


def _is_slice(t):
    return t.startswith("[]")


def _is_const_slice(t):
    return t.startswith("[]const ")


def _slice_elem(t):
    return t[8:] if _is_const_slice(t) else t[2:]


def _type_tag(t):
    return (t.replace("->", "_to_")
            .replace("*const ", "const_").replace("*", "ptr_")
            .replace("[]const ", "slice_const_").replace("[]", "slice_")
            .replace("[", "array_").replace("]", "").replace(";", "_")
            .replace("<", "_").replace(">", "").replace(",", "_")
            .replace("fn(", "function_").replace("(", "_").replace(")", "")
            .replace(" ", "_"))


def _slice_c_name(t):
    return "struct mort_" + _type_tag(t)


def _array_parts(t):
    inner = t[1:-1]
    cut = inner.rfind(";")
    return inner[:cut], int(inner[cut + 1:])


def _split_type_list(inner):
    if not inner:
        return []
    result = []
    start = 0
    angle = paren = bracket = 0
    for index, char in enumerate(inner):
        if char == "<":
            angle += 1
        elif char == ">":
            angle -= 1
        elif char == "(":
            paren += 1
        elif char == ")":
            paren -= 1
        elif char == "[":
            bracket += 1
        elif char == "]":
            bracket -= 1
        elif char == "," and angle == 0 and paren == 0 and bracket == 0:
            result.append(inner[start:index])
            start = index + 1
    result.append(inner[start:])
    return result


def _function_parts(t):
    if not isinstance(t, str) or not t.startswith("fn("):
        return None
    depth = 0
    close = None
    for index in range(2, len(t)):
        if t[index] == "(":
            depth += 1
        elif t[index] == ")":
            depth -= 1
            if depth == 0:
                close = index
                break
    if close is None or t[close + 1:close + 3] != "->":
        return None
    return _split_type_list(t[3:close]), t[close + 3:]


def _tuple_parts(t):
    if not isinstance(t, str) or not t.startswith("(") or not t.endswith(")"):
        return None
    elements = _split_type_list(t[1:-1])
    return elements if len(elements) >= 2 else None


def _tuple_c_name(t):
    # Keep structural tuple symbols in their own namespace so a legal user
    # struct name can never collide with a compiler-generated tuple layout.
    return "struct mort_tuple_" + _type_tag(t)


def _drop_c_name(t):
    return "mort_drop_" + _type_tag(t)


def _c_symbol(name):
    return _type_tag(name.replace(".", "__"))


def c_type(t, struct_names=frozenset(), enum_names=frozenset(),
           payload_enum_names=frozenset()):
    """Map a Mort type string to its C spelling.

    '*i32' -> 'int32_t*';  'Point' -> 'struct mort_Point'.
    """
    callable_type = _function_parts(t)
    if callable_type:
        parameters, result = callable_type
        result_type = c_type(
            result, struct_names, enum_names, payload_enum_names)
        parameter_types = ", ".join(
            c_type(item, struct_names, enum_names, payload_enum_names)
            for item in parameters
        ) or "void"
        return f"{result_type} (*)({parameter_types})"
    if _tuple_parts(t):
        return _tuple_c_name(t)
    if _is_slice(t):
        return _slice_c_name(t)
    if t.startswith("*const "):
        inner = c_type(t[7:], struct_names, enum_names, payload_enum_names)
        return (inner + " const*" if inner.endswith("*") else "const " + inner + "*")
    if t.startswith("*"):
        return c_type(t[1:], struct_names, enum_names, payload_enum_names) + "*"
    if t in struct_names:
        return "struct mort_" + _type_tag(t)
    if t in payload_enum_names:
        return "struct mort_" + _type_tag(t)
    if t in enum_names:
        return "enum mort_" + _type_tag(t)
    return _C_BASE[t]


class CodeGen:
    def __init__(self, program, freestanding=False, test_mode=False):
        self.program = program
        self.freestanding = freestanding
        self.test_mode = test_mode
        self.struct_names = {s.name for s in program.structs if not s.generic_params}
        self.enum_names = {e.name for e in program.enums if not e.generic_params}
        self.payload_enum_names = {
            enum.name for enum in program.enums
            if not enum.generic_params
            and any(variant.payload_type is not None for variant in enum.variants)
        }
        self.extern_names = {f.name for f in program.externs}
        self.lines = []
        self.indent = 0
        self.slice_types = self._collect_slice_types(program)
        self.tuple_types = self._collect_tuple_types(program)
        self.drop_destructors = getattr(program, "drop_destructors", {})

    @staticmethod
    def _collect_slice_types(program):
        found = set()
        seen = set()

        def visit(value):
            if isinstance(value, str):
                if _is_slice(value):
                    found.add(value)
                return
            if value is None or isinstance(value, (int, bool)):
                return
            if isinstance(value, dict):
                for key, item in value.items():
                    visit(key)
                    visit(item)
                return
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    visit(item)
                return
            if hasattr(value, "__dict__") and id(value) not in seen:
                seen.add(id(value))
                for item in vars(value).values():
                    visit(item)

        visit(program.globals)
        visit([item for item in program.structs if not item.generic_params])
        visit([item for item in program.enums if not item.generic_params])
        visit(program.externs)
        visit([item for item in program.funcs if not item.generic_params])
        visit(program.tests)
        return sorted(found)

    @staticmethod
    def _collect_tuple_types(program):
        found = set()
        seen = set()

        def visit_type(value):
            tuple_type = _tuple_parts(value)
            if tuple_type:
                found.add(value)
                for item in tuple_type:
                    visit_type(item)
                return
            callable_type = _function_parts(value)
            if callable_type:
                parameters, result = callable_type
                for item in parameters:
                    visit_type(item)
                visit_type(result)
                return
            if _is_array(value):
                item, _ = _array_parts(value)
                visit_type(item)
                return
            if _is_slice(value):
                visit_type(_slice_elem(value))
                return
            if value.startswith("*const "):
                visit_type(value[7:])
                return
            if value.startswith("*"):
                visit_type(value[1:])
                return
            angle = value.find("<")
            if angle != -1 and value.endswith(">"):
                for item in _split_type_list(value[angle + 1:-1]):
                    visit_type(item)

        def visit(value):
            if isinstance(value, str):
                visit_type(value)
                return
            if value is None or isinstance(value, (int, bool)):
                return
            if isinstance(value, dict):
                for key, item in value.items():
                    visit(key)
                    visit(item)
                return
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    visit(item)
                return
            if hasattr(value, "__dict__") and id(value) not in seen:
                seen.add(id(value))
                for item in vars(value).values():
                    visit(item)

        visit(program.globals)
        visit([item for item in program.structs if not item.generic_params])
        visit([item for item in program.enums if not item.generic_params])
        visit(program.externs)
        visit([item for item in program.funcs if not item.generic_params])
        visit(program.tests)
        return sorted(found, key=lambda item: (item.count("("), item))

    def _ct(self, t):
        return c_type(
            t, self.struct_names, self.enum_names, self.payload_enum_names)

    _U64_MAX = (1 << 64) - 1
    _I64_MAX = (1 << 63) - 1
    _I32_MAX = (1 << 31) - 1

    def _c_int_literal(self, v, t):
        """A C integer literal of value v, cast to Mort type t. Chooses a suffix
        so the magnitude is representable, and spells a value whose magnitude
        exceeds the signed max (e.g. INT64_MIN) via its unsigned bit pattern, so
        the result is clean under -Wall (no implicit-unsigned/overflow warnings)."""
        if v >= 0:
            if v > self._I64_MAX:
                lit = f"{v}ULL"       # needs unsigned long long
            elif v > self._I32_MAX:
                lit = f"{v}LL"
            else:
                lit = str(v)
        elif -v > self._I64_MAX:      # only INT64_MIN: -v isn't a signed literal
            lit = f"{v + (1 << 64)}ULL"   # its two's-complement pattern, as unsigned
        elif -v > self._I32_MAX:
            lit = f"{v}LL"
        else:
            lit = str(v)
        return f"(({self._ct(t)}){lit})"

    def _fixed_wrap(self, typ, code):
        """Convert an unsigned bit pattern to a fixed-width Mort integer."""
        ctype, unsigned, _, signed = _FIXED_INT_INFO[typ]
        code = f"(({unsigned})({code}))"
        if signed:
            self.used_int_helpers.add(("wrap", typ))
            return f"mort_wrap_{typ}({code})"
        return f"(({ctype}){code})"

    def _fixed_unary(self, typ, op, operand):
        _, unsigned, bits, _ = _FIXED_INT_INFO[typ]
        promoted = "uint32_t" if bits <= 32 else "uint64_t"
        if op == "-":
            code = f"(({promoted})0 - ({promoted})(({unsigned})({operand})))"
        else:
            code = f"(~({promoted})(({unsigned})({operand})))"
        return self._fixed_wrap(typ, code)

    def _fixed_binary(self, typ, op, left, right, line):
        _, unsigned, bits, _ = _FIXED_INT_INFO[typ]
        if op in ("+", "-", "*", "&", "|", "^"):
            promoted = "uint32_t" if bits <= 32 else "uint64_t"
            code = (
                f"(({promoted})(({unsigned})({left})) {op} "
                f"({promoted})(({unsigned})({right})))"
            )
            return self._fixed_wrap(typ, code)
        if op in ("/", "%"):
            helper = "div" if op == "/" else "rem"
            self.used_int_helpers.add((helper, typ))
            ctype = self._ct(typ)
            return (
                f"mort_{helper}_{typ}(({ctype})({left}), "
                f"({ctype})({right}), {line})"
            )
        raise AssertionError(f"unsupported fixed-width operator {op!r}")

    def _fixed_shift(self, typ, op, left, right, right_type, line):
        helper = "shl" if op == "<<" else "shr"
        self.used_int_helpers.add((helper, typ))
        signed_count = right_type in {
            "i8", "i16", "i32", "i64", "c_char", "c_short", "c_int", "c_long",
        }
        if signed_count:
            self.used_int_helpers.add(("shift_count", "signed"))
            count = f"mort_shift_count_signed((int64_t)({right}), {line})"
        else:
            count = f"((uint64_t)({right}))"
        ctype = self._ct(typ)
        return f"mort_{helper}_{typ}(({ctype})({left}), {count})"

    def _gen_cast(self, expression):
        source = self._gen_expr(expression.expr)
        target = expression.target_type
        if target not in _FIXED_INT_INFO:
            return f"(({self._ct(target)}){source})"
        if expression.expr.type in _FLOAT_TYPES:
            self.used_int_helpers.add(("float_cast", target))
            return (
                f"mort_float_to_{target}((double)({source}), "
                f"{expression.line})"
            )
        if expression.expr.type.startswith("*"):
            source = f"((uintptr_t)({source}))"
        return self._fixed_wrap(target, source)

    def _var_decl(self, var_type, cname, init=None, binding_const=False):
        """A C declaration for a variable/field, handling arrays (whose size
        sits after the name): '[i32;8]' -> 'int32_t cname[8]'."""
        callable_type = _function_parts(var_type)
        if callable_type:
            parameters, result = callable_type
            parameter_types = ", ".join(self._ct(item) for item in parameters) or "void"
            pointer_qualifier = " const" if binding_const else ""
            decl = (
                f"{self._ct(result)} (*{pointer_qualifier} {cname})"
                f"({parameter_types})"
            )
        elif _is_array(var_type):
            elem, n = _array_parts(var_type)
            qualifier = "const " if binding_const else ""
            if _function_parts(elem):
                decl = self._var_decl(
                    elem, f"{cname}[{n}]", binding_const=binding_const)
            else:
                decl = f"{qualifier}{self._ct(elem)} {cname}[{n}]"
        else:
            ctype = self._ct(var_type)
            if binding_const and ctype.endswith("*"):
                decl = f"{ctype} const {cname}"
            else:
                qualifier = "const " if binding_const else ""
                decl = f"{qualifier}{ctype} {cname}"
        return decl if init is None else f"{decl} = {init}"

    def _emit(self, s=""):
        self.lines.append(("    " * self.indent + s) if s else "")

    def _gen_integer_helpers(self):
        if not self.used_int_helpers:
            return

        needs_failure = any(
            operation in ("div", "rem", "shift_count", "float_cast")
            for operation, _ in self.used_int_helpers
        )
        if needs_failure:
            if self.freestanding:
                self._emit("static void mort_integer_failure(void) {")
                self._emit("    __builtin_trap();")
                self._emit("}")
            else:
                self._emit(
                    "static void mort_integer_failure("
                    "const char* reason, int64_t line) {")
                self._emit(
                    '    fprintf(stderr, "%s at Mort line %lld\\n", reason, '
                    "(long long)line);")
                self._emit("    exit(1);")
                self._emit("}")
            self._emit()

        # Shifts and signed arithmetic request their wrap helper while this
        # routine is emitting helpers, so close that dependency set first.
        for operation, typ in tuple(self.used_int_helpers):
            if operation in ("shl", "shr") and typ.startswith("i"):
                self.used_int_helpers.add(("wrap", typ))

        for typ in ("i8", "i16", "i32", "i64"):
            if ("wrap", typ) not in self.used_int_helpers:
                continue
            ctype, unsigned, bits, _ = _FIXED_INT_INFO[typ]
            self._emit(f"static inline {ctype} mort_wrap_{typ}({unsigned} value) {{")
            self._emit(f"    if (value <= ({unsigned})INT{bits}_MAX) {{")
            self._emit(f"        return ({ctype})value;")
            self._emit("    }")
            self._emit(
                f"    return ({ctype})(-1 - "
                f"({ctype})(UINT{bits}_MAX - value));")
            self._emit("}")
            self._emit()

        if ("shift_count", "signed") in self.used_int_helpers:
            self._emit(
                "static inline uint64_t mort_shift_count_signed("
                "int64_t count, int64_t line) {")
            if self.freestanding:
                self._emit("    (void)line;")
                self._emit("    if (count < 0) { mort_integer_failure(); }")
            else:
                self._emit(
                    '    if (count < 0) { mort_integer_failure('
                    '"negative integer shift count", line); }')
            self._emit("    return (uint64_t)count;")
            self._emit("}")
            self._emit()

        for typ, (ctype, _, bits, signed) in _FIXED_INT_INFO.items():
            if ("float_cast", typ) not in self.used_int_helpers:
                continue
            if signed:
                if bits == 64:
                    condition = (
                        "value >= -9223372036854775808.0 "
                        "&& value < 9223372036854775808.0")
                else:
                    lower = -(2 ** (bits - 1)) - 1
                    upper = 2 ** (bits - 1)
                    condition = f"value > {lower}.0 && value < {upper}.0"
            else:
                upper = 2 ** bits
                condition = f"value > -1.0 && value < {upper}.0"
            self._emit(
                f"static inline {ctype} mort_float_to_{typ}("
                "double value, int64_t line) {")
            if self.freestanding:
                self._emit(
                    f"    if (!({condition})) {{ mort_integer_failure(); }}")
                self._emit("    (void)line;")
            else:
                self._emit(f"    if (!({condition})) {{")
                self._emit(
                    '        mort_integer_failure('
                    '"floating-point to integer cast out of range", line);')
                self._emit("    }")
            self._emit(f"    return ({ctype})value;")
            self._emit("}")
            self._emit()

        for operation in ("shl", "shr", "div", "rem"):
            for typ, (ctype, unsigned, bits, signed) in _FIXED_INT_INFO.items():
                if (operation, typ) not in self.used_int_helpers:
                    continue
                if operation in ("shl", "shr"):
                    self._emit(
                        f"static inline {ctype} mort_{operation}_{typ}("
                        f"{ctype} value, uint64_t count) {{")
                    if operation == "shl":
                        self._emit(
                            f"    if (count >= {bits}U) {{ return ({ctype})0; }}")
                        promoted = "uint32_t" if bits <= 32 else "uint64_t"
                        expression = (
                            f"(({promoted})(({unsigned})value)) << count")
                    elif signed:
                        self._emit(
                            f"    if (count >= {bits}U) {{ "
                            f"return value < 0 ? ({ctype})-1 : ({ctype})0; }}")
                        self._emit("    if (count == 0U) { return value; }")
                        self._emit(
                            f"    {unsigned} shifted = "
                            f"(({unsigned})value) >> count;")
                        self._emit("    if (value < 0) {")
                        self._emit(
                            f"        shifted |= ({unsigned})"
                            f"(UINT{bits}_MAX << ({bits}U - count));")
                        self._emit("    }")
                        expression = "shifted"
                    else:
                        self._emit(
                            f"    if (count >= {bits}U) {{ return ({ctype})0; }}")
                        expression = "value >> count"
                    if signed:
                        self._emit(
                            f"    return mort_wrap_{typ}("
                            f"({unsigned})({expression}));")
                    else:
                        self._emit(f"    return ({ctype})({expression});")
                    self._emit("}")
                    self._emit()
                    continue

                self._emit(
                    f"static inline {ctype} mort_{operation}_{typ}("
                    f"{ctype} left, {ctype} right, int64_t line) {{")
                if self.freestanding:
                    self._emit("    (void)line;")
                    self._emit("    if (right == 0) { mort_integer_failure(); }")
                else:
                    reason = (
                        "integer division by zero"
                        if operation == "div"
                        else "integer remainder by zero"
                    )
                    self._emit(
                        f'    if (right == 0) {{ mort_integer_failure('
                        f'"{reason}", line); }}')
                if signed:
                    self._emit(
                        f"    if (left == INT{bits}_MIN "
                        f"&& right == ({ctype})-1) {{")
                    result = f"INT{bits}_MIN" if operation == "div" else "0"
                    self._emit(f"        return {result};")
                    self._emit("    }")
                symbol = "/" if operation == "div" else "%"
                self._emit(f"    return ({ctype})(left {symbol} right);")
                self._emit("}")
                self._emit()

    def generate(self):
        self.strings = []       # raw string-literal values, index = id
        self.used_inb = False   # set if a port-I/O builtin is generated, per helper
        self.used_outb = False
        self.used_inw = False
        self.used_outw = False
        self.used_inl = False
        self.used_outl = False
        self.used_println = False
        self.used_print = False
        self.used_print_float = False
        self.used_assert = False
        self.used_alloc = False
        self.used_free = False
        self.used_len = False
        self.used_bounds = False
        self.used_time = False
        self.used_file = False
        self.used_int_helpers = set()
        self.match_id = 0
        self.try_id = 0
        self.return_id = 0
        self.range_id = 0
        self.assign_id = 0
        self.index_id = 0

        # Generate global initialisers and function bodies first, into side
        # buffers. This populates self.strings / used_* port-I/O flags (each
        # StrLit and port-I/O call registers itself), so the string table and
        # helpers can be emitted ahead of the code that references them.
        global_decls = [
            "static " + self._var_decl(
                g.var_type, "m_" + g.name, self._gen_expr(g.expr),
                binding_const=not g.mutable) + ";"
            for g in self.program.globals
        ]
        saved = self.lines
        self.lines = []
        for f in self.program.funcs:
            if f.generic_params:
                continue
            self._gen_fn(f)
            self._emit()
        if self.test_mode:
            for index, test in enumerate(self.program.tests):
                self._gen_test(test, index)
                self._emit()
        body_lines = self.lines
        self.lines = saved

        self._emit("// Generated by the Mort compiler -- do not edit by hand.")
        # <stdint.h>/<stdbool.h> are freestanding-safe; <stdio.h> is not.
        if not self.freestanding:
            self._emit("#include <stdio.h>")
            if (self.used_assert or self.used_alloc or self.used_free
                    or self.used_bounds
                    or any(op in ("div", "rem", "shift_count", "float_cast")
                           for op, _ in self.used_int_helpers)):
                self._emit("#include <stdlib.h>")
            if self.used_time:
                self._emit("#include <time.h>")
        self._emit("#include <stdint.h>")
        self._emit("#include <stdbool.h>")
        self._emit("#include <stddef.h>")
        self._emit()
        for enum in self.program.enums:
            if not enum.generic_params and enum.name not in self.payload_enum_names:
                self._gen_enum(enum)
                self._emit()
        # Forward declarations let slice element pointers and structs refer to
        # each other before their full definitions.
        if self.program.structs:
            for s in self.program.structs:
                if not s.generic_params:
                    self._emit(f"struct mort_{_type_tag(s.name)};")
            self._emit()
        for enum in self.program.enums:
            if not enum.generic_params and enum.name in self.payload_enum_names:
                self._emit(f"struct mort_{_type_tag(enum.name)};")
        if self.payload_enum_names:
            self._emit()
        if self.tuple_types:
            for tuple_type in self.tuple_types:
                self._emit(f"{_tuple_c_name(tuple_type)};")
            self._emit()
        if self.slice_types:
            for slice_type in self.slice_types:
                elem = _slice_elem(slice_type)
                pointer = self._ct(elem) + "*"
                if _is_const_slice(slice_type):
                    pointer = "const " + pointer
                self._emit(f"{_slice_c_name(slice_type)} {{")
                self._emit(f"    {pointer} data;")
                self._emit("    uint64_t length;")
                self._emit("};")
                self._emit()
        # Aggregates that contain each other by value must be fully defined in
        # dependency order.  This supports both `struct S { pair: (i64,bool) }`
        # and the reverse `(S,bool)` without relying on declaration order.
        for kind, value in self._aggregate_definition_order():
            if kind == "tuple":
                self._gen_tuple(value)
            elif kind == "struct":
                self._gen_struct(value)
            else:
                self._gen_enum(value)
            self._emit()
        generated_drops = [
            typ for typ, symbol in self.drop_destructors.items()
            if symbol.startswith("$drop$")
        ]
        if generated_drops:
            direct_symbols = {
                symbol for symbol in self.drop_destructors.values()
                if not symbol.startswith("$drop$")
            }
            for function in self.program.funcs:
                if (not function.generic_params
                        and function.symbol_name in direct_symbols):
                    self._emit(self._signature(function) + ";")
            if direct_symbols:
                self._emit()
            for typ in generated_drops:
                self._emit(self._drop_signature(typ) + ";")
            self._emit()
            for typ in generated_drops:
                self._gen_drop_helper(typ)
                self._emit()
        # String literals live in mutable static storage, so the *u8 type they
        # carry is honest — writing through one is defined, not UB on a literal.
        if self.strings:
            for i, val in enumerate(self.strings):
                self._emit(f'static uint8_t mort_str_{i}[] = "{val}";')
            self._emit()
        # x86 port I/O helpers, each emitted only when its builtin is used.
        if self.used_outb:
            self._emit("static inline void mort_outb(uint16_t port, uint8_t val) {")
            self._emit('    __asm__ volatile ("outb %0, %1" : : "a"(val), "Nd"(port));')
            self._emit("}")
        if self.used_inb:
            self._emit("static inline uint8_t mort_inb(uint16_t port) {")
            self._emit("    uint8_t ret;")
            self._emit('    __asm__ volatile ("inb %1, %0" : "=a"(ret) : "Nd"(port));')
            self._emit("    return ret;")
            self._emit("}")
        if self.used_outw:
            self._emit("static inline void mort_outw(uint16_t port, uint16_t val) {")
            self._emit('    __asm__ volatile ("outw %0, %1" : : "a"(val), "Nd"(port));')
            self._emit("}")
        if self.used_inw:
            self._emit("static inline uint16_t mort_inw(uint16_t port) {")
            self._emit("    uint16_t ret;")
            self._emit('    __asm__ volatile ("inw %1, %0" : "=a"(ret) : "Nd"(port));')
            self._emit("    return ret;")
            self._emit("}")
        if self.used_outl:
            self._emit("static inline void mort_outl(uint16_t port, uint32_t val) {")
            self._emit('    __asm__ volatile ("outl %0, %1" : : "a"(val), "Nd"(port));')
            self._emit("}")
        if self.used_inl:
            self._emit("static inline uint32_t mort_inl(uint16_t port) {")
            self._emit("    uint32_t ret;")
            self._emit('    __asm__ volatile ("inl %1, %0" : "=a"(ret) : "Nd"(port));')
            self._emit("    return ret;")
            self._emit("}")
        if (self.used_inb or self.used_outb or self.used_inw or self.used_outw
                or self.used_inl or self.used_outl):
            self._emit()
        self._gen_integer_helpers()
        if not self.freestanding:
            if self.used_print:
                self._emit(
                    'static void mort_print(int64_t v) { '
                    'printf("%lld\\n", (long long)v); }')
            if self.used_print_float:
                self._emit(
                    'static void mort_print_float(double v) { printf("%.17g\\n", v); }')
            if self.used_println:
                self._emit("static void mort_println(uint8_t* text) { puts((char*)text); }")
            if self.used_assert:
                self._emit("static void mort_assert(bool condition, int64_t line) {")
                self._emit('    if (!condition) { fprintf(stderr, "assertion failed at Mort line %lld\\n", (long long)line); exit(1); }')
                self._emit("}")
            if self.used_alloc:
                self._emit("static void* mort_alloc(uint64_t size) { return malloc((size_t)size); }")
            if self.used_free:
                self._emit("static void mort_free(void* pointer) { free(pointer); }")
            if self.used_len:
                self._emit("static uint64_t mort_len(uint8_t* text) {")
                self._emit("    uint64_t length = 0;")
                self._emit("    while (text[length] != 0) { length++; }")
                self._emit("    return length;")
                self._emit("}")
            if self.used_bounds:
                self._emit("static uint64_t mort_bounds(uint64_t index, uint64_t length, int64_t line) {")
                self._emit('    if (index >= length) { fprintf(stderr, "index out of bounds at Mort line %lld\\n", (long long)line); exit(1); }')
                self._emit("    return index;")
                self._emit("}")
            if self.used_time:
                self._emit(
                    "static int64_t mort_unix_time(void) { return (int64_t)time(NULL); }")
                self._emit("static uint64_t mort_cpu_millis(void) {")
                self._emit(
                    "    return ((uint64_t)clock() * 1000ULL) / "
                    "(uint64_t)CLOCKS_PER_SEC;")
                self._emit("}")
            if self.used_file:
                self._emit(
                    "static void* mort_file_open(uint8_t* path, uint8_t* mode) { "
                    "return (void*)fopen((char*)path, (char*)mode); }")
                self._emit(
                    "static bool mort_file_close(void* handle) { "
                    "return handle != NULL && fclose((FILE*)handle) == 0; }")
                self._emit(
                    "static uint64_t mort_file_read(void* handle, uint8_t* buffer, "
                    "uint64_t length) { return (uint64_t)fread(buffer, 1, "
                    "(size_t)length, (FILE*)handle); }")
                self._emit(
                    "static uint64_t mort_file_write(void* handle, const uint8_t* buffer, "
                    "uint64_t length) { return (uint64_t)fwrite(buffer, 1, "
                    "(size_t)length, (FILE*)handle); }")
                self._emit(
                    "static bool mort_file_flush(void* handle) { "
                    "return handle != NULL && fflush((FILE*)handle) == 0; }")
            self._emit()
        # prototypes first, so any call order works
        for f in self.program.externs:
            self._emit("extern " + self._extern_signature(f) + ";")
        for f in self.program.funcs:
            if not f.generic_params:
                self._emit(self._signature(f) + ";")
        self._emit()
        # Function prototypes must precede globals because a function pointer
        # global may use a Mort function as its constant initializer.
        if global_decls:
            for decl in global_decls:
                self._emit(decl)
            self._emit()
        self.lines.extend(body_lines)
        # Hosted builds get a real C main that calls the user's main; a
        # freestanding object has no entry point (the bootloader supplies one).
        if not self.freestanding:
            if self.test_mode:
                self._emit("int main(void) {")
                for index, _ in enumerate(self.program.tests):
                    self._emit(f"    mort_test_{index}();")
                self._emit("    return 0;")
                self._emit("}")
            else:
                self._emit("int main(void) { return (int)mort_main(); }")
        return "\n".join(self.lines) + "\n"

    def _gen_struct(self, s):
        self._emit(f"struct mort_{_type_tag(s.name)} {{")
        self.indent += 1
        for fld in s.fields:
            self._emit(self._var_decl(fld.typ, "f_" + fld.name) + ";")
        self.indent -= 1
        self._emit("};")

    def _drop_signature(self, typ):
        if _is_array(typ):
            element, count = _array_parts(typ)
            return (
                f"static void {_drop_c_name(typ)}"
                f"({self._ct(element)} (*value)[{count}])"
            )
        return f"static void {_drop_c_name(typ)}({self._ct(typ)}* value)"

    def _drop_call(self, typ, address):
        symbol = self.drop_destructors[typ]
        name = (
            _drop_c_name(typ)
            if symbol.startswith("$drop$")
            else f"mort_{_c_symbol(symbol)}"
        )
        return f"{name}({address});"

    def _gen_drop_helper(self, typ):
        self._emit(self._drop_signature(typ) + " {")
        self.indent += 1
        if _is_array(typ):
            element, count = _array_parts(typ)
            self._emit(
                f"for (uint64_t i = {count}; i > 0; --i) {{")
            self.indent += 1
            self._emit(self._drop_call(element, "&(*value)[i - 1]"))
            self.indent -= 1
            self._emit("}")
        elif _tuple_parts(typ):
            for index, element in reversed(list(enumerate(_tuple_parts(typ)))):
                if element in self.drop_destructors:
                    self._emit(self._drop_call(element, f"&value->f_{index}"))
        elif typ in self.struct_names:
            declaration = next(
                item for item in self.program.structs
                if item.name == typ and not item.generic_params
            )
            for field in reversed(declaration.fields):
                if field.typ in self.drop_destructors:
                    self._emit(self._drop_call(
                        field.typ, f"&value->f_{field.name}"))
        elif typ in self.payload_enum_names:
            declaration = next(
                item for item in self.program.enums
                if item.name == typ and not item.generic_params
            )
            enum_tag = _type_tag(typ)
            self._emit("switch (value->tag) {")
            self.indent += 1
            for variant in declaration.variants:
                payload = variant.payload_type
                if payload in self.drop_destructors:
                    self._emit(f"case MORT_{enum_tag}_{variant.name}:")
                    self.indent += 1
                    self._emit(self._drop_call(
                        payload, f"&value->data.v_{variant.name}"))
                    self._emit("break;")
                    self.indent -= 1
            self._emit("default: break;")
            self.indent -= 1
            self._emit("}")
        self.indent -= 1
        self._emit("}")

    def _gen_tuple(self, tuple_type):
        self._emit(f"{_tuple_c_name(tuple_type)} {{")
        self.indent += 1
        for index, item_type in enumerate(_tuple_parts(tuple_type)):
            self._emit(self._var_decl(item_type, f"f_{index}") + ";")
        self.indent -= 1
        self._emit("};")

    def _aggregate_definition_order(self):
        nodes = {}
        for tuple_type in self.tuple_types:
            nodes[("tuple", tuple_type)] = tuple_type
        for struct in self.program.structs:
            if not struct.generic_params:
                nodes[("struct", struct.name)] = struct
        for enum in self.program.enums:
            if not enum.generic_params and enum.name in self.payload_enum_names:
                nodes[("enum", enum.name)] = enum

        def value_dependency(value_type):
            if _is_array(value_type):
                element, _ = _array_parts(value_type)
                return value_dependency(element)
            if _tuple_parts(value_type):
                return {("tuple", value_type)}
            if value_type in self.struct_names:
                return {("struct", value_type)}
            if value_type in self.payload_enum_names:
                return {("enum", value_type)}
            # Pointers, slices and callbacks only need forward declarations.
            return set()

        dependencies = {}
        for key, value in nodes.items():
            kind, _ = key
            if kind == "tuple":
                types = _tuple_parts(value)
            elif kind == "struct":
                types = [field.typ for field in value.fields]
            else:
                types = [
                    variant.payload_type for variant in value.variants
                    if variant.payload_type is not None
                ]
            dependencies[key] = set().union(
                *(value_dependency(item) for item in types)) if types else set()

        emitted = set()
        result = []
        remaining = list(nodes)
        while remaining:
            ready = [
                key for key in remaining
                if dependencies[key].issubset(emitted)
            ]
            if not ready:
                # The checker normally prevents impossible by-value cycles.
                # Preserve deterministic output if a malformed AST reaches
                # codegen, allowing the C compiler to diagnose it.
                ready = [remaining[0]]
            for key in ready:
                result.append((key[0], nodes[key]))
                emitted.add(key)
                remaining.remove(key)
        return result

    def _gen_enum(self, enum):
        type_tag = _type_tag(enum.name)
        tag_name = (f"mort_{type_tag}_tag" if enum.name in self.payload_enum_names
                    else f"mort_{type_tag}")
        self._emit(f"enum {tag_name} {{")
        self.indent += 1
        for index, variant in enumerate(enum.variants):
            comma = "," if index + 1 < len(enum.variants) else ""
            self._emit(f"MORT_{type_tag}_{variant.name} = {index}{comma}")
        self.indent -= 1
        self._emit("};")
        if enum.name in self.payload_enum_names:
            self._emit(f"struct mort_{type_tag} {{")
            self._emit(f"    enum {tag_name} tag;")
            payloads = [variant for variant in enum.variants
                        if variant.payload_type is not None]
            if payloads:
                self._emit("    union {")
                for variant in payloads:
                    self._emit("        " + self._var_decl(
                        variant.payload_type, "v_" + variant.name) + ";")
                self._emit("    } data;")
            self._emit("};")

    def _signature(self, f):
        if f.params:
            params = ", ".join(
                self._var_decl(p.typ, "m_" + p.name) for p in f.params)
        else:
            params = "void"
        return self._var_decl(
            f.ret, f"mort_{_c_symbol(f.symbol_name)}({params})")

    def _extern_signature(self, f):
        if f.params:
            # Parameter names are not part of the C ABI. Omitting them also
            # prevents a harmless Mort name such as `register` from becoming
            # an invalid C declaration.
            params = ", ".join(self._ct(p.typ) for p in f.params)
        else:
            params = "void"
        return self._var_decl(f.ret, f"{f.name}({params})")

    def _gen_fn(self, f):
        saved_scopes = getattr(self, "defer_scopes", [])
        saved_loops = getattr(self, "loop_defer_bases", [])
        self.defer_scopes = [[]]
        self.loop_defer_bases = []
        self._emit(self._signature(f) + " {")
        self.indent += 1
        for parameter in f.params:
            destructor = getattr(parameter, "destructor_symbol", None)
            if destructor is not None:
                live = f"mort_live_m_{parameter.name}"
                self._emit(f"bool {live} = true;")
                self.defer_scopes[0].append(
                    ("resource", parameter.name, destructor, live))
        for s in f.body.stmts:
            self._gen_stmt(s)
        if f.ret == "void":
            self._emit_defer_scopes()
        self.indent -= 1
        self._emit("}")
        self.defer_scopes = saved_scopes
        self.loop_defer_bases = saved_loops

    def _emit_defer_scopes(self, start=0):
        for scope in reversed(getattr(self, "defer_scopes", [])[start:]):
            for expression in reversed(scope):
                if isinstance(expression, tuple) and expression[0] == "resource":
                    _, name, destructor, live = expression
                    destructor_name = (
                        _drop_c_name(destructor[6:])
                        if destructor.startswith("$drop$")
                        else f"mort_{_c_symbol(destructor)}"
                    )
                    self._emit(
                        f"if ({live}) {{ {destructor_name}(&m_{name}); }}")
                else:
                    self._emit(self._gen_expr(expression) + ";")

    def _gen_scoped_statements(self, statements):
        self.defer_scopes.append([])
        for statement in statements:
            self._gen_stmt(statement)
        self._emit_defer_scopes(len(self.defer_scopes) - 1)
        self.defer_scopes.pop()

    def _gen_test(self, test, index):
        saved_scopes = getattr(self, "defer_scopes", [])
        saved_loops = getattr(self, "loop_defer_bases", [])
        self.defer_scopes = [[]]
        self.loop_defer_bases = []
        self._emit(f"static void mort_test_{index}(void) {{")
        self.indent += 1
        for statement in test.body.stmts:
            self._gen_stmt(statement)
        self._emit_defer_scopes()
        self.indent -= 1
        self._emit("}")
        self.defer_scopes = saved_scopes
        self.loop_defer_bases = saved_loops

    def _gen_stmt(self, s):
        if isinstance(s, A.Let):
            self._prepare_try_expr(s.expr)
            destructor = getattr(s, "destructor_symbol", None)
            self._emit(self._var_decl(
                s.var_type, "m_" + s.name, self._gen_expr(s.expr),
                binding_const=not s.mutable and destructor is None) + ";")
            if destructor is not None:
                live = f"mort_live_m_{s.name}"
                self._emit(f"bool {live} = true;")
                self.defer_scopes[-1].append(
                    ("resource", s.name, destructor, live))
        elif isinstance(s, A.Assign):
            self._prepare_try_expr(s.target)
            if s.op == "=":
                self._prepare_try_expr(s.expr)
                self._emit(
                    f"{self._gen_expr(s.target)} = {self._gen_expr(s.expr)};")
            else:
                target = self._gen_expr(s.target)
                temporary = f"mort_assign_{self.assign_id}"
                self.assign_id += 1
                self._emit(
                    f"{self._ct(s.target.type)}* {temporary} = &({target});")
                self._prepare_try_expr(s.expr)
                operation = s.op[:-1]
                right = self._gen_expr(s.expr)
                if s.target.type in _FIXED_INT_INFO:
                    if operation in ("<<", ">>"):
                        value = self._fixed_shift(
                            s.target.type, operation, f"*{temporary}",
                            right, s.expr.type, s.line)
                    else:
                        value = self._fixed_binary(
                            s.target.type, operation, f"*{temporary}",
                            right, s.line)
                else:
                    value = f"(*{temporary} {operation} {right})"
                self._emit(f"*{temporary} = {value};")
        elif isinstance(s, A.Asm):
            self._emit(f'__asm__ volatile ("{s.text}");')
        elif isinstance(s, A.Return):
            if s.expr is None:
                self._emit_defer_scopes()
                self._emit("return;")
            else:
                self._prepare_try_expr(s.expr)
                temporary = f"mort_return_{self.return_id}"
                self.return_id += 1
                self._emit(self._var_decl(
                    s.expr.type, temporary, self._gen_expr(s.expr)) + ";")
                self._emit_defer_scopes()
                self._emit(f"return {temporary};")
        elif isinstance(s, A.If):
            self._prepare_try_expr(s.cond)
            self._emit(f"if ({self._gen_cond(s.cond)}) {{")
            self.indent += 1
            self._gen_scoped_statements(s.then.stmts)
            self.indent -= 1
            if s.els is None:
                self._emit("}")
            else:
                self._emit("} else {")
                self.indent += 1
                if isinstance(s.els, A.If):
                    self._gen_stmt(s.els)
                else:
                    self._gen_scoped_statements(s.els.stmts)
                self.indent -= 1
                self._emit("}")
        elif isinstance(s, A.While):
            condition_has_try = self._contains_try(s.cond)
            self._emit(
                "while (true) {" if condition_has_try
                else f"while ({self._gen_cond(s.cond)}) {{")
            self.indent += 1
            self.defer_scopes.append([])
            self.loop_defer_bases.append(len(self.defer_scopes) - 1)
            if condition_has_try:
                self._prepare_try_expr(s.cond)
                self._emit(f"if (!({self._gen_cond(s.cond)})) {{ break; }}")
            for st in s.body.stmts:
                self._gen_stmt(st)
            self._emit_defer_scopes(len(self.defer_scopes) - 1)
            self.loop_defer_bases.pop()
            self.defer_scopes.pop()
            self.indent -= 1
            self._emit("}")
        elif isinstance(s, A.For):
            self._prepare_try_expr(s.start)
            self._prepare_try_expr(s.end)
            v = "m_" + s.var
            ct = self._ct(s.var_type)
            start = self._gen_expr(s.start)
            end = self._gen_expr(s.end)
            range_id = self.range_id
            self.range_id += 1
            cached_start = f"mort_range_start_{range_id}"
            cached_end = f"mort_range_end_{range_id}"
            self._emit("{")
            self.indent += 1
            self._emit(f"{ct} {cached_start} = {start};")
            self._emit(f"{ct} {cached_end} = {end};")
            if s.inclusive:
                has_next = f"mort_range_has_next_{range_id}"
                self._emit(f"{ct} {v} = {cached_start};")
                self._emit(f"bool {has_next} = {v} <= {cached_end};")
                self._emit(
                    f"for (; {has_next}; {has_next} = {v} != {cached_end}, "
                    f"{v} = {has_next} ? {v} + 1 : {v}) {{")
            else:
                self._emit(
                    f"for ({ct} {v} = {cached_start}; {v} < {cached_end}; "
                    f"{v} = {v} + 1) {{")
            self.indent += 1
            self.defer_scopes.append([])
            self.loop_defer_bases.append(len(self.defer_scopes) - 1)
            for st in s.body.stmts:
                self._gen_stmt(st)
            self._emit_defer_scopes(len(self.defer_scopes) - 1)
            self.loop_defer_bases.pop()
            self.defer_scopes.pop()
            self.indent -= 1
            self._emit("}")
            self.indent -= 1
            self._emit("}")
        elif isinstance(s, A.Block):
            self._emit("{")
            self.indent += 1
            self._gen_scoped_statements(s.stmts)
            self.indent -= 1
            self._emit("}")
        elif isinstance(s, A.ExprStmt):
            self._prepare_try_expr(s.expr)
            self._emit(self._gen_expr(s.expr) + ";")
        elif isinstance(s, A.Break):
            self._emit_defer_scopes(self.loop_defer_bases[-1])
            self._emit("break;")
        elif isinstance(s, A.Continue):
            self._emit_defer_scopes(self.loop_defer_bases[-1])
            self._emit("continue;")
        elif isinstance(s, A.Defer):
            binding = getattr(s.expr, "destroys_binding", None)
            if binding is not None:
                for scope in reversed(self.defer_scopes):
                    scope[:] = [
                        item for item in scope
                        if not (
                            isinstance(item, tuple)
                            and item[0] == "resource"
                            and item[1] == binding
                        )
                    ]
            self.defer_scopes[-1].append(s.expr)
        elif isinstance(s, A.Match):
            self._prepare_try_expr(s.expr)
            match_id = self.match_id
            self.match_id += 1
            temporary = f"mort_match_{match_id}"
            self._emit("{")
            self.indent += 1
            self._emit(f"{self._ct(s.expr.type)} {temporary} = {self._gen_expr(s.expr)};")
            emitted_condition = False
            for arm_index, arm in enumerate(s.arms):
                if arm.pattern is None:
                    self._emit("else {" if emitted_condition else "{")
                elif s.exhaustive and arm_index == len(s.arms) - 1:
                    # The checker proved every enum variant is covered. Making
                    # the final arm unconditional communicates that fact to C
                    # compilers as well, avoiding false missing-return warnings.
                    self._emit("else {" if emitted_condition else "{")
                else:
                    keyword = "else if" if emitted_condition else "if"
                    if s.expr.type in self.payload_enum_names:
                        comparison = (
                            f"{temporary}.tag == "
                            f"MORT_{_type_tag(s.expr.type)}_{arm.variant_name}")
                    else:
                        comparison = f"{temporary} == {self._gen_expr(arm.pattern)}"
                    self._emit(f"{keyword} ({comparison}) {{")
                    emitted_condition = True
                self.indent += 1
                self.defer_scopes.append([])
                for binding_index, binding_name, binding_type, destructor in zip(
                        arm.binding_indices, arm.binding_names, arm.binding_types,
                        arm.binding_destructors):
                    payload = f"{temporary}.data.v_{arm.variant_name}"
                    if arm.payload_arity > 1:
                        payload += f".f_{binding_index}"
                    self._emit(
                        f"{self._ct(binding_type)} m_{binding_name} = {payload};")
                    if destructor is not None:
                        live = f"mort_live_m_{binding_name}"
                        self._emit(f"bool {live} = true;")
                        self.defer_scopes[-1].append(
                            ("resource", binding_name, destructor, live))
                for statement in arm.body.stmts:
                    self._gen_stmt(statement)
                self._emit_defer_scopes(len(self.defer_scopes) - 1)
                self.defer_scopes.pop()
                self.indent -= 1
                self._emit("}")
            self.indent -= 1
            self._emit("}")

    @staticmethod
    def _contains_try(expression):
        if isinstance(expression, A.Try):
            return True
        if not isinstance(expression, A.Node):
            return False
        for value in vars(expression).values():
            if isinstance(value, A.Node) and CodeGen._contains_try(value):
                return True
            if isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, A.Node) and CodeGen._contains_try(item):
                        return True
                    if isinstance(item, tuple):
                        if any(
                                isinstance(part, A.Node)
                                and CodeGen._contains_try(part)
                                for part in item):
                            return True
        return False

    def _prepare_try_expr(self, expression):
        """Lower every eager ``try`` subexpression to checked temporaries."""
        if isinstance(expression, A.Try):
            self._prepare_try_expr(expression.expr)
            self._emit_try_unwrap(expression)
            return
        if not isinstance(expression, A.Node):
            return
        if isinstance(expression, A.Binary):
            self._prepare_try_expr(expression.left)
            if (expression.op in ("&&", "||")
                    and self._contains_try(expression.right)):
                short_id = self.try_id
                self.try_id += 1
                expression.lowered_name = f"mort_short_{short_id}"
                self._emit(
                    f"bool {expression.lowered_name} = "
                    f"{self._gen_expr(expression.left)};")
                condition = (
                    expression.lowered_name
                    if expression.op == "&&"
                    else "!" + expression.lowered_name
                )
                self._emit(f"if ({condition}) {{")
                self.indent += 1
                self._prepare_try_expr(expression.right)
                self._emit(
                    f"{expression.lowered_name} = "
                    f"{self._gen_expr(expression.right)};")
                self.indent -= 1
                self._emit("}")
                return
            self._prepare_try_expr(expression.right)
            return
        if isinstance(expression, A.Unary):
            self._prepare_try_expr(expression.operand)
            return
        if isinstance(expression, A.Cast):
            self._prepare_try_expr(expression.expr)
            return
        if isinstance(expression, A.Call):
            for argument in expression.args:
                self._prepare_try_expr(argument)
            return
        if isinstance(expression, A.StructLit):
            for _, value in expression.fields:
                self._prepare_try_expr(value)
            return
        if isinstance(expression, A.TupleLit):
            for value in expression.elements:
                self._prepare_try_expr(value)
            return
        if isinstance(expression, A.FieldAccess):
            self._prepare_try_expr(expression.obj)
            return
        if isinstance(expression, A.Index):
            self._prepare_try_expr(expression.obj)
            self._prepare_try_expr(expression.index)
            if _is_slice(expression.obj.type):
                expression.obj_temp_name = f"mort_index_obj_{self.index_id}"
                self.index_id += 1
                self._emit(self._var_decl(
                    expression.obj.type,
                    expression.obj_temp_name,
                    self._gen_expr(expression.obj),
                ) + ";")
            return
        if isinstance(expression, A.ArrayLit):
            for element in expression.elements:
                self._prepare_try_expr(element)
            return
        if isinstance(expression, A.ArrayRepeat):
            self._prepare_try_expr(expression.value)

    def _emit_try_unwrap(self, expression):
        try_id = self.try_id
        self.try_id += 1
        temporary = f"mort_try_{try_id}"
        result_tag = _type_tag(expression.result_type)
        return_tag = _type_tag(expression.return_type)
        self._emit(
            f"{self._ct(expression.result_type)} {temporary} = "
            f"{self._gen_expr(expression.expr)};")
        self._emit(f"if ({temporary}.tag == MORT_{result_tag}_Err) {{")
        self.indent += 1
        self._emit_defer_scopes()
        self._emit(
            f"return (struct mort_{return_tag}){{ .tag = MORT_{return_tag}_Err, "
            f".data.v_Err = {temporary}.data.v_Err }};")
        self.indent -= 1
        self._emit("}")
        expression.temp_name = f"mort_try_value_{try_id}"
        self._emit(self._var_decl(
            expression.type,
            expression.temp_name,
            f"{temporary}.data.v_Ok",
        ) + ";")

    def _gen_cond(self, e):
        """Generate a condition for if/while.

        Every Binary/Unary/Cast already parenthesises itself, and the enclosing
        `if (...)` / `while (...)` adds another pair — which clang flags as
        -Wparentheses-equality on `x == y`. Strip one matched outer pair so the
        result reads `if (a == b)`, not `if ((a == b))`.
        """
        s = self._gen_expr(e)
        if s.startswith("(") and s.endswith(")"):
            depth = 0
            for i, ch in enumerate(s):
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        # the first '(' closes only at the very end => one outer pair
                        return s[1:-1] if i == len(s) - 1 else s
        return s

    def _gen_expr(self, e):
        if isinstance(e, A.IntLit):
            return str(e.value)
        if isinstance(e, A.FloatLit):
            value = format(e.value, ".17g")
            if "." not in value and "e" not in value.lower():
                value += ".0"
            return value + ("f" if e.type == "f32" else "")
        if isinstance(e, A.CharLit):
            return str(e.value)
        if isinstance(e, A.NullLit):
            return "NULL"
        if isinstance(e, A.BoolLit):
            return "true" if e.value else "false"
        if isinstance(e, A.StrLit):
            # register the literal in mutable static storage and refer to it
            idx = len(self.strings)
            self.strings.append(e.value)
            return f"mort_str_{idx}"
        if isinstance(e, A.Var):
            if e.resolved_function is not None:
                if e.resolved_function in self.extern_names:
                    return e.resolved_function
                return f"mort_{_c_symbol(e.resolved_function)}"
            return f"m_{e.name}"
        if isinstance(e, A.Cast):
            return self._gen_cast(e)
        if isinstance(e, A.Try):
            if e.temp_name is None:  # pragma: no cover - checker/codegen invariant
                raise Exception("try expression was not prepared before generation")
            return e.temp_name
        if isinstance(e, A.Move):
            return (
                f"((mort_live_m_{e.binding_name} = false), "
                f"{self._gen_expr(e.expr)})"
            )
        if isinstance(e, A.StructLit):
            inits = ", ".join(
                f".f_{name} = {self._gen_expr(val)}" for name, val in e.fields)
            return f"(struct mort_{_type_tag(e.name)}){{ {inits} }}"
        if isinstance(e, A.TupleLit):
            inits = ", ".join(
                f".f_{index} = {self._gen_expr(value)}"
                for index, value in enumerate(e.elements))
            return f"({_tuple_c_name(e.type)}){{ {inits} }}"
        if isinstance(e, A.FieldAccess):
            if e.resolved_function is not None:
                if e.resolved_function in self.extern_names:
                    return e.resolved_function
                return f"mort_{_c_symbol(e.resolved_function)}"
            if isinstance(e.obj, A.Var) and e.obj.name in self.enum_names:
                if e.obj.name in self.payload_enum_names:
                    enum_tag = _type_tag(e.obj.name)
                    return (
                        f"(struct mort_{enum_tag}){{ .tag = "
                        f"MORT_{enum_tag}_{e.field} }}"
                    )
                return f"MORT_{_type_tag(e.obj.name)}_{e.field}"
            if _is_slice(e.obj.type):
                field = "length" if e.field == "len" else e.field
                return f"({self._gen_expr(e.obj)}).{field}"
            if _tuple_parts(e.obj.type):
                return f"({self._gen_expr(e.obj)}).f_{e.field}"
            return f"({self._gen_expr(e.obj)}).f_{e.field}"
        if isinstance(e, A.Index):
            index = self._gen_expr(e.index)
            if _is_slice(e.obj.type):
                obj = e.obj_temp_name or self._gen_expr(e.obj)
                if not self.freestanding:
                    self.used_bounds = True
                    index = f"mort_bounds((uint64_t)({index}), ({obj}).length, {e.line})"
                return f"({obj}).data[{index}]"
            if (_is_array(e.obj.type) and not self.freestanding
                    and getattr(e.index, "const_val", None) is None):
                self.used_bounds = True
                _, length = _array_parts(e.obj.type)
                index = f"mort_bounds((uint64_t)({index}), {length}, {e.line})"
            return f"{self._gen_expr(e.obj)}[{index}]"
        if isinstance(e, A.ArrayLit):
            return "{" + ", ".join(self._gen_expr(el) for el in e.elements) + "}"
        if isinstance(e, A.ArrayRepeat):
            v = self._gen_expr(e.value)
            return "{" + ", ".join([v] * e.count) + "}"
        if isinstance(e, A.Unary):
            # A constant '-'/'~' folds to its value (const_val is set only for
            # those two int unaries; '*'/'&'/'!' leave it None).
            if e.const_val is not None:
                return self._c_int_literal(e.const_val, e.type)
            operand = self._gen_expr(e.operand)
            code = f"({e.op}{operand})"
            # '-' and '~' can produce a value wider than the Mort type (C promotes
            # to int); narrow it back. '*'/'&' are lvalue/pointer — never wrap.
            if e.op in ("-", "~") and e.type in _FIXED_INT_INFO:
                return self._fixed_unary(e.type, e.op, operand)
            return code
        if isinstance(e, A.Binary):
            if e.lowered_name is not None:
                return e.lowered_name
            # A fully-constant integer expression is emitted as its single folded
            # value. This is required for shifts (a C `1 << 63` is undefined — 1 is
            # a 32-bit int) and also keeps a nested inner shift like `(1 << 64) - 1`
            # from emitting an intermediate literal too large for any C type.
            if e.const_val is not None:
                return self._c_int_literal(e.const_val, e.type)
            left = self._gen_expr(e.left)
            right = self._gen_expr(e.right)
            if e.type in _FIXED_INT_INFO and e.op in (
                    "+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>"):
                if e.op in ("<<", ">>"):
                    return self._fixed_shift(
                        e.type, e.op, left, right, e.right.type, e.line)
                return self._fixed_binary(e.type, e.op, left, right, e.line)
            return f"({left} {e.op} {right})"
        if isinstance(e, A.Call):
            if e.enum_name is not None:
                enum_tag = _type_tag(e.enum_name)
                if len(e.args) == 1:
                    payload = self._gen_expr(e.args[0])
                else:
                    tuple_values = ", ".join(
                        f".f_{index} = {self._gen_expr(argument)}"
                        for index, argument in enumerate(e.args)
                    )
                    payload = (
                        f"({_tuple_c_name(e.enum_payload_type)})"
                        f"{{ {tuple_values} }}"
                    )
                return (
                    f"(struct mort_{enum_tag}){{ .tag = "
                    f"MORT_{enum_tag}_{e.enum_variant}, "
                    f".data.v_{e.enum_variant} = {payload} }}"
                )
            if e.indirect:
                args = ", ".join(self._gen_expr(argument) for argument in e.args)
                return f"m_{e.name}({args})"
            if e.name == "inb":
                self.used_inb = True
            elif e.name == "outb":
                self.used_outb = True
            elif e.name == "inw":
                self.used_inw = True
            elif e.name == "outw":
                self.used_outw = True
            elif e.name == "inl":
                self.used_inl = True
            elif e.name == "outl":
                self.used_outl = True
            elif e.name == "println":
                self.used_println = True
            elif e.name == "print" and e.args[0].type in ("f32", "f64"):
                self.used_print_float = True
            elif e.name == "print":
                self.used_print = True
            elif e.name == "assert":
                self.used_assert = True
            elif e.name == "alloc":
                self.used_alloc = True
            elif e.name == "free":
                self.used_free = True
            elif e.name == "len" and e.args[0].type == "*u8":
                self.used_len = True
            elif e.name in ("unix_time", "cpu_millis"):
                self.used_time = True
            elif e.name in (
                    "file_open", "file_close", "file_read", "file_write", "file_flush"):
                self.used_file = True
            args = ", ".join(self._gen_expr(a) for a in e.args)
            if e.name == "sizeof":
                return f"((uint64_t)sizeof({self._ct(e.type_args[0])}))"
            if e.name == "print":
                name = (
                    "mort_print_float"
                    if e.args[0].type in ("f32", "f64") else "mort_print")
            elif e.name == "println":
                name = "mort_println"
            elif e.name == "assert":
                return f"mort_assert({args}, {e.line})"
            elif e.name == "alloc":
                name = "mort_alloc"
            elif e.name == "free":
                name = "mort_free"
            elif e.name == "len":
                if _is_array(e.args[0].type):
                    return str(_array_parts(e.args[0].type)[1])
                if _is_slice(e.args[0].type):
                    return f"({self._gen_expr(e.args[0])}).length"
                name = "mort_len"
            elif e.name == "slice":
                slice_type = e.type
                return (
                    f"({_slice_c_name(slice_type)}){{ .data = {self._gen_expr(e.args[0])}, "
                    f".length = {self._gen_expr(e.args[1])} }}"
                )
            elif (e.resolved_name or e.name) in self.extern_names:
                name = e.resolved_name or e.name
            else:
                name = f"mort_{_c_symbol(e.resolved_name or e.name)}"
            call = f"{name}({args})"
            destroyed = getattr(e, "destroys_binding", None)
            if destroyed is not None and not getattr(e, "deferred_destroy", False):
                return f"({call}, mort_live_m_{destroyed} = false)"
            return call
        raise Exception("unreachable: cannot generate expression")  # pragma: no cover
