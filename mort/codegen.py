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
    "c_char": "char", "c_uchar": "unsigned char",
    "c_short": "short", "c_ushort": "unsigned short",
    "c_int": "int", "c_uint": "unsigned int",
    "c_long": "long", "c_ulong": "unsigned long", "c_size": "size_t",
}


def _is_array(t):
    return t.startswith("[") and not t.startswith("[]")


def _is_slice(t):
    return t.startswith("[]")


def _is_const_slice(t):
    return t.startswith("[]const ")


def _slice_elem(t):
    return t[8:] if _is_const_slice(t) else t[2:]


def _type_tag(t):
    return (t.replace("*const ", "const_").replace("*", "ptr_")
            .replace("[]const ", "slice_const_").replace("[]", "slice_")
            .replace("[", "array_").replace("]", "").replace(";", "_")
            .replace("<", "_").replace(">", "").replace(",", "_"))


def _slice_c_name(t):
    return "struct mort_" + _type_tag(t)


def _array_parts(t):
    inner = t[1:-1]
    cut = inner.rfind(";")
    return inner[:cut], int(inner[cut + 1:])


def _c_symbol(name):
    return _type_tag(name.replace(".", "__"))


def c_type(t, struct_names=frozenset(), enum_names=frozenset(),
           payload_enum_names=frozenset()):
    """Map a Mort type string to its C spelling.

    '*i32' -> 'int32_t*';  'Point' -> 'struct mort_Point'.
    """
    if _is_slice(t):
        return _slice_c_name(t)
    if t.startswith("*const "):
        return "const " + c_type(
            t[7:], struct_names, enum_names, payload_enum_names) + "*"
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

    def _narrow(self, e, code):
        """Cast an arithmetic result back to a sub-int width (i8/u8/i16/u16), so
        Mort's fixed-width semantics survive C's promotion of narrow types to int.
        Wider types (i32/u32/i64/u64) already keep their width in C."""
        if getattr(e, "type", None) in ("i8", "u8", "i16", "u16"):
            return f"(({self._ct(e.type)}){code})"
        return code

    def _var_decl(self, var_type, cname, init=None):
        """A C declaration for a variable/field, handling arrays (whose size
        sits after the name): '[i32;8]' -> 'int32_t cname[8]'."""
        if _is_array(var_type):
            elem, n = _array_parts(var_type)
            decl = f"{self._ct(elem)} {cname}[{n}]"
        else:
            decl = f"{self._ct(var_type)} {cname}"
        return decl if init is None else f"{decl} = {init}"

    def _emit(self, s=""):
        self.lines.append(("    " * self.indent + s) if s else "")

    def generate(self):
        self.strings = []       # raw string-literal values, index = id
        self.used_inb = False   # set if a port-I/O builtin is generated, per helper
        self.used_outb = False
        self.used_inw = False
        self.used_outw = False
        self.used_inl = False
        self.used_outl = False
        self.used_println = False
        self.used_assert = False
        self.used_alloc = False
        self.used_free = False
        self.used_len = False
        self.used_bounds = False
        self.match_id = 0
        self.try_id = 0
        self.return_id = 0

        # Generate global initialisers and function bodies first, into side
        # buffers. This populates self.strings / used_* port-I/O flags (each
        # StrLit and port-I/O call registers itself), so the string table and
        # helpers can be emitted ahead of the code that references them.
        global_decls = [
            "static " + self._var_decl(g.var_type, "m_" + g.name, self._gen_expr(g.expr)) + ";"
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
            if self.used_assert or self.used_alloc or self.used_free or self.used_bounds:
                self._emit("#include <stdlib.h>")
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
        if self.program.structs:
            for s in self.program.structs:
                if not s.generic_params:
                    self._gen_struct(s)
                    self._emit()
        for enum in self.program.enums:
            if not enum.generic_params and enum.name in self.payload_enum_names:
                self._gen_enum(enum)
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
        # global variables (file-scope statics)
        if global_decls:
            for decl in global_decls:
                self._emit(decl)
            self._emit()
        if not self.freestanding:
            self._emit('static void mort_print(int64_t v) { printf("%lld\\n", (long long)v); }')
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
            self._emit()
        # prototypes first, so any call order works
        for f in self.program.externs:
            self._emit("extern " + self._extern_signature(f) + ";")
        for f in self.program.funcs:
            if not f.generic_params:
                self._emit(self._signature(f) + ";")
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
                    self._emit(
                        f"        {self._ct(variant.payload_type)} v_{variant.name};")
                self._emit("    } data;")
            self._emit("};")

    def _signature(self, f):
        ret = self._ct(f.ret)
        if f.params:
            params = ", ".join(f"{self._ct(p.typ)} m_{p.name}" for p in f.params)
        else:
            params = "void"
        return f"{ret} mort_{_c_symbol(f.symbol_name)}({params})"

    def _extern_signature(self, f):
        ret = self._ct(f.ret)
        if f.params:
            # Parameter names are not part of the C ABI. Omitting them also
            # prevents a harmless Mort name such as `register` from becoming
            # an invalid C declaration.
            params = ", ".join(self._ct(p.typ) for p in f.params)
        else:
            params = "void"
        return f"{ret} {f.name}({params})"

    def _gen_fn(self, f):
        saved_scopes = getattr(self, "defer_scopes", [])
        saved_loops = getattr(self, "loop_defer_bases", [])
        self.defer_scopes = [[]]
        self.loop_defer_bases = []
        self._emit(self._signature(f) + " {")
        self.indent += 1
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
            if isinstance(s.expr, A.Try):
                self._gen_try_let(s)
                return
            self._emit(self._var_decl(s.var_type, "m_" + s.name, self._gen_expr(s.expr)) + ";")
        elif isinstance(s, A.Assign):
            self._emit(f"{self._gen_expr(s.target)} = {self._gen_expr(s.expr)};")
        elif isinstance(s, A.Asm):
            self._emit(f'__asm__ volatile ("{s.text}");')
        elif isinstance(s, A.Return):
            if s.expr is None:
                self._emit_defer_scopes()
                self._emit("return;")
            else:
                temporary = f"mort_return_{self.return_id}"
                self.return_id += 1
                self._emit(self._var_decl(
                    s.expr.type, temporary, self._gen_expr(s.expr)) + ";")
                self._emit_defer_scopes()
                self._emit(f"return {temporary};")
        elif isinstance(s, A.If):
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
            self._emit(f"while ({self._gen_cond(s.cond)}) {{")
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
        elif isinstance(s, A.For):
            v = "m_" + s.var
            ct = self._ct(s.var_type)
            start = self._gen_expr(s.start)
            end = self._gen_expr(s.end)
            self._emit(f"for ({ct} {v} = {start}; {v} < {end}; {v} = {v} + 1) {{")
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
        elif isinstance(s, A.Block):
            self._emit("{")
            self.indent += 1
            self._gen_scoped_statements(s.stmts)
            self.indent -= 1
            self._emit("}")
        elif isinstance(s, A.ExprStmt):
            self._emit(self._gen_expr(s.expr) + ";")
        elif isinstance(s, A.Break):
            self._emit_defer_scopes(self.loop_defer_bases[-1])
            self._emit("break;")
        elif isinstance(s, A.Continue):
            self._emit_defer_scopes(self.loop_defer_bases[-1])
            self._emit("continue;")
        elif isinstance(s, A.Defer):
            self.defer_scopes[-1].append(s.expr)
        elif isinstance(s, A.Match):
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
                if arm.binding_name is not None:
                    self._emit(
                        f"{self._ct(arm.binding_type)} m_{arm.binding_name} = "
                        f"{temporary}.data.v_{arm.variant_name};")
                self._gen_scoped_statements(arm.body.stmts)
                self.indent -= 1
                self._emit("}")
            self.indent -= 1
            self._emit("}")

    def _gen_try_let(self, statement):
        expression = statement.expr
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
        self._emit(
            self._var_decl(
                statement.var_type,
                "m_" + statement.name,
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
        if isinstance(e, A.BoolLit):
            return "true" if e.value else "false"
        if isinstance(e, A.StrLit):
            # register the literal in mutable static storage and refer to it
            idx = len(self.strings)
            self.strings.append(e.value)
            return f"mort_str_{idx}"
        if isinstance(e, A.Var):
            return f"m_{e.name}"
        if isinstance(e, A.Cast):
            return f"(({self._ct(e.target_type)}){self._gen_expr(e.expr)})"
        if isinstance(e, A.StructLit):
            inits = ", ".join(
                f".f_{name} = {self._gen_expr(val)}" for name, val in e.fields)
            return f"(struct mort_{_type_tag(e.name)}){{ {inits} }}"
        if isinstance(e, A.FieldAccess):
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
            return f"({self._gen_expr(e.obj)}).f_{e.field}"
        if isinstance(e, A.Index):
            index = self._gen_expr(e.index)
            if _is_slice(e.obj.type):
                obj = self._gen_expr(e.obj)
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
            code = f"({e.op}{self._gen_expr(e.operand)})"
            # '-' and '~' can produce a value wider than the Mort type (C promotes
            # to int); narrow it back. '*'/'&' are lvalue/pointer — never wrap.
            if e.op in ("-", "~"):
                return self._narrow(e, code)
            return code
        if isinstance(e, A.Binary):
            # A fully-constant integer expression is emitted as its single folded
            # value. This is required for shifts (a C `1 << 63` is undefined — 1 is
            # a 32-bit int) and also keeps a nested inner shift like `(1 << 64) - 1`
            # from emitting an intermediate literal too large for any C type.
            if e.const_val is not None:
                return self._c_int_literal(e.const_val, e.type)
            if e.op in ("<<", ">>"):
                # Runtime shift: cast the left operand to the result type so it
                # executes at the right width (not C's promoted 32-bit int).
                code = (f"(({self._ct(e.type)})({self._gen_expr(e.left)}) "
                        f"{e.op} {self._gen_expr(e.right)})")
                return self._narrow(e, code)
            code = f"({self._gen_expr(e.left)} {e.op} {self._gen_expr(e.right)})"
            return self._narrow(e, code)
        if isinstance(e, A.Call):
            if e.enum_name is not None:
                enum_tag = _type_tag(e.enum_name)
                return (
                    f"(struct mort_{enum_tag}){{ .tag = "
                    f"MORT_{enum_tag}_{e.enum_variant}, "
                    f".data.v_{e.enum_variant} = {self._gen_expr(e.args[0])} }}"
                )
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
            elif e.name == "assert":
                self.used_assert = True
            elif e.name == "alloc":
                self.used_alloc = True
            elif e.name == "free":
                self.used_free = True
            elif e.name == "len" and e.args[0].type == "*u8":
                self.used_len = True
            args = ", ".join(self._gen_expr(a) for a in e.args)
            if e.name == "sizeof":
                return f"((uint64_t)sizeof({self._ct(e.type_args[0])}))"
            if e.name == "print":
                name = "mort_print"
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
            return f"{name}({args})"
        raise Exception("unreachable: cannot generate expression")  # pragma: no cover
