"""Recursive-descent parser: tokens -> AST.

Grammar (informal):
    program   := (import_decl | struct_decl | enum_decl | global | extern_decl | fn_decl)*
    import_decl := 'import' IDENT ('.' IDENT)* ';'
    extern_decl := 'extern' 'fn' IDENT '(' params? ')' ('->' type)? ';'
    fn_decl   := 'fn' IDENT '(' params? ')' ('->' type)? block
    params    := param (',' param)*
    param     := IDENT ':' type
    type      := 'int' | 'bool'
    block     := '{' stmt* '}'
    stmt      := let | return | if | while | block | expr_or_assign
    let       := 'let' IDENT (':' type)? '=' expr ';'
    return    := 'return' expr? ';'
    if        := 'if' expr block ('else' (if | block))?
    while     := 'while' expr block
    expr_or_assign := expr ('=' expr)? ';'

Precedence (low -> high):
    || , && , == != , < > <= >= , + - , * / % , unary ! - , call , primary
"""
from .tokens import T
from .errors import MortError
from . import mort_ast as A

FIXED_INT_TYPES = {
    "i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64",
    "c_char", "c_uchar", "c_short", "c_ushort", "c_int", "c_uint",
    "c_long", "c_ulong", "c_size",
}


class Parser:
    def __init__(self, tokens):
        self.toks = tokens
        self.i = 0
        # True while parsing an if/while condition: suppresses struct literals
        # so `if p {` is a block, not `if (p{...})`. Reset inside parentheses.
        self._no_struct_lit = False

    # ----- token helpers -----
    def _peek(self, offset=0):
        return self.toks[self.i + offset]

    def _at(self, type):
        return self._peek().type == type

    def _advance(self):
        tok = self.toks[self.i]
        self.i += 1
        return tok

    def _expect(self, type, what=None):
        if not self._at(type):
            tok = self._peek()
            raise MortError(f"expected {what or type.name}, got {tok.value!r}", tok.line)
        return self._advance()

    # ----- entry -----
    def parse(self):
        funcs = []
        structs = []
        globals_ = []
        externs = []
        imports = []
        enums = []
        tests = []
        module_name = None
        while not self._at(T.EOF):
            if self._at(T.MODULE):
                if module_name is not None or funcs or structs or globals_ or externs or imports or enums or tests:
                    tok = self._peek()
                    raise MortError("module declaration must be the first declaration", tok.line)
                module_name = self._module_decl()
            elif self._at(T.IMPORT):
                imports.append(self._import_decl())
            elif self._at(T.PUB):
                line = self._advance().line
                if not self._at(T.FN):
                    raise MortError("pub currently applies to function declarations", line)
                function = self._fn_decl()
                function.public = True
                funcs.append(function)
            elif self._at(T.STRUCT):
                structs.append(self._struct_decl())
            elif self._at(T.ENUM):
                enums.append(self._enum_decl())
            elif self._at(T.TEST):
                tests.append(self._test_decl())
            elif self._at(T.LET):
                globals_.append(self._let_stmt())  # top-level let = global var
            elif self._at(T.EXTERN):
                externs.append(self._extern_fn_decl())
            else:
                funcs.append(self._fn_decl())
        return A.Program(
            funcs, structs, globals_, externs, imports, enums, tests, module_name)

    def _module_decl(self):
        self._advance()
        parts = [self._expect(T.IDENT, "module name").value]
        while self._at(T.DOT):
            self._advance()
            parts.append(self._expect(T.IDENT, "module name").value)
        self._expect(T.SEMI, "';'")
        return ".".join(parts)

    def _test_decl(self):
        line = self._advance().line
        name = self._expect(T.STRING, "test name").value
        body = self._block()
        return A.TestDecl(name, body, line)

    def _import_decl(self):
        line = self._advance().line
        parts = [self._expect(T.IDENT, "module name").value]
        while self._at(T.DOT):
            self._advance()
            parts.append(self._expect(T.IDENT, "module name").value)
        alias = None
        if self._at(T.AS):
            self._advance()
            alias = self._expect(T.IDENT, "import alias").value
        self._expect(T.SEMI, "';'")
        return A.ImportDecl(parts, line, alias)

    def _struct_decl(self):
        line = self._peek().line
        self._expect(T.STRUCT, "'struct'")
        name = self._expect(T.IDENT, "struct name").value
        generic_params = []
        if self._at(T.LT):
            self._advance()
            generic_params.append(self._expect(T.IDENT, "generic parameter").value)
            while self._at(T.COMMA):
                self._advance()
                generic_params.append(self._expect(T.IDENT, "generic parameter").value)
            self._expect(T.GT, "'>'")
        self._expect(T.LBRACE, "'{'")
        fields = []
        while not self._at(T.RBRACE) and not self._at(T.EOF):
            fname = self._expect(T.IDENT, "field name").value
            self._expect(T.COLON, "':'")
            fields.append(A.StructField(fname, self._type_name()))
            if self._at(T.COMMA):
                self._advance()
            else:
                break
        self._expect(T.RBRACE, "'}'")
        return A.StructDecl(name, fields, line, generic_params)

    def _enum_decl(self):
        line = self._advance().line
        name = self._expect(T.IDENT, "enum name").value
        self._expect(T.LBRACE, "'{'")
        variants = []
        while not self._at(T.RBRACE) and not self._at(T.EOF):
            variant_name = self._expect(T.IDENT, "variant name").value
            payload_type = None
            if self._at(T.LPAREN):
                self._advance()
                payload_type = self._type_name()
                self._expect(T.RPAREN, "')'")
            variants.append(A.EnumVariant(variant_name, payload_type))
            if self._at(T.COMMA):
                self._advance()
            else:
                break
        self._expect(T.RBRACE, "'}'")
        return A.EnumDecl(name, variants, line)

    def _type_name(self):
        # pointer type: *T
        if self._at(T.STAR):
            self._advance()
            if self._at(T.CONST):
                self._advance()
                return "*const " + self._type_name()
            return "*" + self._type_name()
        # array type: [T; N]
        if self._at(T.LBRACKET):
            self._advance()
            if self._at(T.RBRACKET):
                self._advance()
                if self._at(T.CONST):
                    self._advance()
                    return "[]const " + self._type_name()
                return "[]" + self._type_name()
            elem = self._type_name()
            self._expect(T.SEMI, "';'")
            n = self._expect(T.INT, "an array size").value
            self._expect(T.RBRACKET, "']'")
            return f"[{elem};{n}]"
        tok = self._peek()
        if tok.type == T.KW_INT:
            self._advance()
            return "i64"  # 'int' is a friendly alias for i64
        if tok.type == T.KW_BOOL:
            self._advance()
            return "bool"
        if tok.type == T.KW_VOID:
            self._advance()
            return "void"
        if tok.type == T.IDENT and tok.value in FIXED_INT_TYPES:
            self._advance()
            return tok.value
        if tok.type == T.IDENT:
            # A nominal or instantiated generic type; the checker validates it.
            self._advance()
            name = tok.value
            if self._at(T.LT):
                self._advance()
                args = [self._type_name()]
                while self._at(T.COMMA):
                    self._advance()
                    args.append(self._type_name())
                self._expect(T.GT, "'>'")
                name += "<" + ",".join(args) + ">"
            return name
        raise MortError(f"expected a type, got {tok.value!r}", tok.line)

    def _fn_decl(self):
        line = self._peek().line
        self._expect(T.FN, "'fn'")
        name = self._expect(T.IDENT, "function name").value
        self._expect(T.LPAREN, "'('")
        params = []
        if not self._at(T.RPAREN):
            params.append(self._param())
            while self._at(T.COMMA):
                self._advance()
                params.append(self._param())
        self._expect(T.RPAREN, "')'")
        ret = "void"
        if self._at(T.ARROW):
            self._advance()
            ret = self._type_name()
        body = self._block()
        return A.FnDecl(name, params, ret, body, line)

    def _extern_fn_decl(self):
        line = self._advance().line  # 'extern'
        self._expect(T.FN, "'fn'")
        name = self._expect(T.IDENT, "function name").value
        self._expect(T.LPAREN, "'('")
        params = []
        if not self._at(T.RPAREN):
            params.append(self._param())
            while self._at(T.COMMA):
                self._advance()
                params.append(self._param())
        self._expect(T.RPAREN, "')'")
        ret = "void"
        if self._at(T.ARROW):
            self._advance()
            ret = self._type_name()
        self._expect(T.SEMI, "';'")
        return A.ExternFnDecl(name, params, ret, line)

    def _param(self):
        name = self._expect(T.IDENT, "parameter name").value
        self._expect(T.COLON, "':'")
        return A.Param(name, self._type_name())

    def _block(self):
        line = self._peek().line
        self._expect(T.LBRACE, "'{'")
        stmts = []
        while not self._at(T.RBRACE) and not self._at(T.EOF):
            stmts.append(self._stmt())
        self._expect(T.RBRACE, "'}'")
        return A.Block(stmts, line)

    # ----- statements -----
    def _stmt(self):
        t = self._peek().type
        if t == T.LET:
            return self._let_stmt()
        if t == T.RETURN:
            return self._return_stmt()
        if t == T.IF:
            return self._if_stmt()
        if t == T.WHILE:
            return self._while_stmt()
        if t == T.FOR:
            return self._for_stmt()
        if t == T.ASM:
            return self._asm_stmt()
        if t == T.BREAK:
            line = self._advance().line
            self._expect(T.SEMI, "';'")
            return A.Break(line)
        if t == T.CONTINUE:
            line = self._advance().line
            self._expect(T.SEMI, "';'")
            return A.Continue(line)
        if t == T.MATCH:
            return self._match_stmt()
        if t == T.DEFER:
            line = self._advance().line
            expr = self._expression()
            self._expect(T.SEMI, "';'")
            return A.Defer(expr, line)
        if t == T.LBRACE:
            return self._block()
        return self._expr_or_assign()

    def _match_stmt(self):
        line = self._advance().line
        expr = self._condition()
        self._expect(T.LBRACE, "'{'")
        arms = []
        while not self._at(T.RBRACE) and not self._at(T.EOF):
            arm_line = self._peek().line
            pattern = None
            if self._at(T.IDENT) and self._peek().value == "_":
                self._advance()
            else:
                pattern = self._expression()
            self._expect(T.FAT_ARROW, "'=>'")
            body = self._block()
            arms.append(A.MatchArm(pattern, body, arm_line))
            if self._at(T.COMMA):
                self._advance()
        self._expect(T.RBRACE, "'}'")
        return A.Match(expr, arms, line)

    def _asm_stmt(self):
        line = self._advance().line
        self._expect(T.LPAREN, "'('")
        text = self._expect(T.STRING, "an assembly string").value
        self._expect(T.RPAREN, "')'")
        self._expect(T.SEMI, "';'")
        return A.Asm(text, line)

    def _let_stmt(self):
        line = self._advance().line
        name = self._expect(T.IDENT, "variable name").value
        decl = None
        if self._at(T.COLON):
            self._advance()
            decl = self._type_name()
        self._expect(T.ASSIGN, "'='")
        expr = self._expression()
        self._expect(T.SEMI, "';'")
        return A.Let(name, decl, expr, line)

    def _return_stmt(self):
        line = self._advance().line
        expr = None
        if not self._at(T.SEMI):
            expr = self._expression()
        self._expect(T.SEMI, "';'")
        return A.Return(expr, line)

    def _condition(self):
        # struct literals are ambiguous with the block that follows, so ban
        # them at the top level of a condition (parentheses re-enable them).
        saved = self._no_struct_lit
        self._no_struct_lit = True
        try:
            return self._expression()
        finally:
            self._no_struct_lit = saved

    def _if_stmt(self):
        line = self._advance().line
        cond = self._condition()
        then = self._block()
        els = None
        if self._at(T.ELSE):
            self._advance()
            els = self._if_stmt() if self._at(T.IF) else self._block()
        return A.If(cond, then, els, line)

    def _while_stmt(self):
        line = self._advance().line
        cond = self._condition()
        body = self._block()
        return A.While(cond, body, line)

    def _for_stmt(self):
        line = self._advance().line               # 'for'
        var = self._expect(T.IDENT, "a loop variable").value
        decl_type = None
        if self._at(T.COLON):
            self._advance()
            decl_type = self._type_name()
        self._expect(T.IN, "'in'")
        # struct literals are banned here (the range end is followed by '{')
        saved = self._no_struct_lit
        self._no_struct_lit = True
        try:
            start = self._expression()
            self._expect(T.DOTDOT, "'..'")
            end = self._expression()
        finally:
            self._no_struct_lit = saved
        body = self._block()
        return A.For(var, decl_type, start, end, body, line)

    def _expr_or_assign(self):
        line = self._peek().line
        expr = self._expression()
        if self._at(T.ASSIGN):
            self._advance()
            if not self._is_lvalue(expr):
                raise MortError("invalid assignment target", line)
            value = self._expression()
            self._expect(T.SEMI, "';'")
            return A.Assign(expr, value, line)
        self._expect(T.SEMI, "';'")
        return A.ExprStmt(expr, line)

    @staticmethod
    def _is_lvalue(e):
        # a name, deref (*p), field (s.x), or index (a[i]) — all locations
        return (
            isinstance(e, A.Var)
            or (isinstance(e, A.Unary) and e.op == "*")
            or isinstance(e, A.FieldAccess)
            or isinstance(e, A.Index)
        )

    # ----- expressions -----
    def _expression(self):
        return self._logic_or()

    def _logic_or(self):
        left = self._logic_and()
        while self._at(T.OR):
            op = self._advance()
            left = A.Binary("||", left, self._logic_and(), op.line)
        return left

    def _logic_and(self):
        left = self._bitor()
        while self._at(T.AND):
            op = self._advance()
            left = A.Binary("&&", left, self._bitor(), op.line)
        return left

    def _bitor(self):
        left = self._bitxor()
        while self._at(T.PIPE):
            op = self._advance()
            left = A.Binary("|", left, self._bitxor(), op.line)
        return left

    def _bitxor(self):
        left = self._bitand()
        while self._at(T.CARET):
            op = self._advance()
            left = A.Binary("^", left, self._bitand(), op.line)
        return left

    def _bitand(self):
        left = self._equality()
        while self._at(T.AMP):
            op = self._advance()
            left = A.Binary("&", left, self._equality(), op.line)
        return left

    def _equality(self):
        left = self._comparison()
        while self._peek().type in (T.EQ, T.NE):
            op = self._advance()
            left = A.Binary(op.value, left, self._comparison(), op.line)
        return left

    def _comparison(self):
        left = self._shift()
        while self._peek().type in (T.LT, T.GT, T.LE, T.GE):
            op = self._advance()
            left = A.Binary(op.value, left, self._shift(), op.line)
        return left

    def _shift(self):
        left = self._term()
        while self._peek().type in (T.SHL, T.SHR):
            op = self._advance()
            left = A.Binary(op.value, left, self._term(), op.line)
        return left

    def _term(self):
        left = self._factor()
        while self._peek().type in (T.PLUS, T.MINUS):
            op = self._advance()
            left = A.Binary(op.value, left, self._factor(), op.line)
        return left

    def _factor(self):
        left = self._cast()
        while self._peek().type in (T.STAR, T.SLASH, T.PERCENT):
            op = self._advance()
            left = A.Binary(op.value, left, self._cast(), op.line)
        return left

    def _cast(self):
        expr = self._unary()
        while self._at(T.AS):
            line = self._advance().line
            expr = A.Cast(expr, self._type_name(), line)
        return expr

    def _unary(self):
        # '&' address-of, '*' deref, '~' bitwise-not join '!' and '-' as prefixes
        if self._peek().type in (T.BANG, T.MINUS, T.AMP, T.STAR, T.TILDE):
            op = self._advance()
            op_str = "&" if op.type == T.AMP else op.value
            return A.Unary(op_str, self._unary(), op.line)
        return self._postfix()

    def _postfix(self):
        expr = self._primary()
        while True:
            if self._at(T.LPAREN):
                name = self._qualified_name(expr)
                if name is None:
                    raise MortError("only named functions can be called", self._peek().line)
                lp = self._advance()
                args = []
                if not self._at(T.RPAREN):
                    args.append(self._expression())
                    while self._at(T.COMMA):
                        self._advance()
                        args.append(self._expression())
                self._expect(T.RPAREN, "')'")
                expr = A.Call(name, args, lp.line)
            elif self._at(T.DOT):
                dot = self._advance()
                field = self._expect(T.IDENT, "field name").value
                expr = A.FieldAccess(expr, field, dot.line)
            elif self._at(T.LBRACKET):
                lb = self._advance()
                index = self._expression()
                self._expect(T.RBRACKET, "']'")
                expr = A.Index(expr, index, lb.line)
            else:
                return expr

    @staticmethod
    def _qualified_name(expr):
        if isinstance(expr, A.Var):
            return expr.name
        if isinstance(expr, A.FieldAccess):
            base = Parser._qualified_name(expr.obj)
            return None if base is None else base + "." + expr.field
        return None

    def _primary(self):
        t = self._peek()
        if t.type == T.INT:
            self._advance()
            return A.IntLit(t.value, t.line)
        if t.type == T.TRUE:
            self._advance()
            return A.BoolLit(True, t.line)
        if t.type == T.FALSE:
            self._advance()
            return A.BoolLit(False, t.line)
        if t.type == T.STRING:
            self._advance()
            return A.StrLit(t.value, t.line)
        if t.type == T.LBRACKET:
            return self._array_lit()
        if t.type == T.IDENT:
            # struct literal:  Name { field: expr, ... }
            if self._looks_like_struct_lit() and not self._no_struct_lit:
                return self._struct_lit()
            self._advance()
            return A.Var(t.value, t.line)
        if t.type == T.LPAREN:
            self._advance()
            # inside parentheses, struct literals are unambiguous again
            saved = self._no_struct_lit
            self._no_struct_lit = False
            try:
                e = self._expression()
            finally:
                self._no_struct_lit = saved
            self._expect(T.RPAREN, "')'")
            return e
        raise MortError(f"unexpected token {t.value!r}", t.line)

    def _struct_lit(self):
        name = self._type_name()
        line = self._expect(T.LBRACE, "'{'").line
        fields = []
        while not self._at(T.RBRACE) and not self._at(T.EOF):
            fname = self._expect(T.IDENT, "field name").value
            self._expect(T.COLON, "':'")
            fields.append((fname, self._expression()))
            if self._at(T.COMMA):
                self._advance()
            else:
                break
        self._expect(T.RBRACE, "'}'")
        return A.StructLit(name, fields, line)

    def _looks_like_struct_lit(self):
        if self._peek().type != T.IDENT:
            return False
        offset = 1
        if self._peek(offset).type != T.LT:
            return self._peek(offset).type == T.LBRACE
        depth = 0
        while self.i + offset < len(self.toks):
            token_type = self._peek(offset).type
            if token_type == T.LT:
                depth += 1
            elif token_type == T.GT:
                depth -= 1
                if depth == 0:
                    return self._peek(offset + 1).type == T.LBRACE
            elif token_type in (T.SEMI, T.EOF):
                return False
            offset += 1
        return False

    def _array_lit(self):
        line = self._advance().line  # '['
        if self._at(T.RBRACKET):
            raise MortError("empty array literal is not allowed", line)
        first = self._expression()
        if self._at(T.SEMI):                 # repeat form: [value; count]
            self._advance()
            count = self._expect(T.INT, "an array repeat count").value
            self._expect(T.RBRACKET, "']'")
            return A.ArrayRepeat(first, count, line)
        elements = [first]                   # list form: [a, b, c]
        while self._at(T.COMMA):
            self._advance()
            if self._at(T.RBRACKET):
                break                        # tolerate a trailing comma
            elements.append(self._expression())
        self._expect(T.RBRACKET, "']'")
        return A.ArrayLit(elements, line)
