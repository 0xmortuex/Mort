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
from .tokens import T, Token
from .errors import MortError
from . import mort_ast as A

FIXED_INT_TYPES = {
    "i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64",
    "c_char", "c_uchar", "c_short", "c_ushort", "c_int", "c_uint",
    "c_long", "c_ulong", "c_size",
}
FLOAT_TYPES = {"f32", "f64"}

ASSIGNMENT_OPS = {
    T.ASSIGN: "=",
    T.PLUS_ASSIGN: "+=",
    T.MINUS_ASSIGN: "-=",
    T.STAR_ASSIGN: "*=",
    T.SLASH_ASSIGN: "/=",
    T.PERCENT_ASSIGN: "%=",
    T.AMP_ASSIGN: "&=",
    T.PIPE_ASSIGN: "|=",
    T.CARET_ASSIGN: "^=",
    T.SHL_ASSIGN: "<<=",
    T.SHR_ASSIGN: ">>=",
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

    def _expect_type_close(self):
        """Consume one generic-type closing angle.

        The lexer correctly treats ``>>`` as a shift in expressions. Inside a
        type, however, the same bytes may close two nested generic argument
        lists. Split that token lazily so expression shift parsing stays
        unchanged.
        """
        if self._at(T.SHR):
            tok = self._peek()
            tok.type = T.GT
            tok.value = ">"
            tok.col += 1
            return Token(T.GT, ">", tok.line, tok.col - 1)
        if self._at(T.GE):
            tok = self._peek()
            tok.type = T.ASSIGN
            tok.value = "="
            tok.col += 1
            return Token(T.GT, ">", tok.line, tok.col - 1)
        return self._expect(T.GT, "'>'")

    # ----- entry -----
    def parse(self):
        funcs = []
        structs = []
        globals_ = []
        externs = []
        imports = []
        enums = []
        tests = []
        aliases = []
        module_name = None
        while not self._at(T.EOF):
            if self._at(T.MODULE):
                if (module_name is not None or funcs or structs or globals_ or externs
                        or imports or enums or tests or aliases):
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
            elif self._at(T.RESOURCE):
                line = self._advance().line
                if not self._at(T.STRUCT):
                    raise MortError("'resource' must precede a struct declaration", line)
                structs.append(self._struct_decl(resource=True))
            elif self._at(T.STRUCT):
                structs.append(self._struct_decl())
            elif self._at(T.ENUM):
                enums.append(self._enum_decl())
            elif self._at(T.TYPE):
                aliases.append(self._type_alias_decl())
            elif self._at(T.TEST):
                tests.append(self._test_decl())
            elif self._at(T.LET):
                globals_.append(self._let_stmt())  # top-level let = global var
            elif self._at(T.CONST):
                globals_.append(self._let_stmt(mutable=False))
            elif self._at(T.EXTERN):
                externs.append(self._extern_fn_decl())
            else:
                funcs.append(self._fn_decl())
        return A.Program(
            funcs, structs, globals_, externs, imports, enums, tests, module_name,
            aliases=aliases)

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

    def _struct_decl(self, resource=False):
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
        return A.StructDecl(name, fields, line, generic_params, resource=resource)

    def _type_alias_decl(self):
        line = self._advance().line
        name = self._expect(T.IDENT, "type alias name").value
        self._expect(T.ASSIGN, "'='")
        target = self._type_name()
        self._expect(T.SEMI, "';'")
        return A.TypeAliasDecl(name, target, line)

    def _enum_decl(self):
        line = self._advance().line
        name = self._expect(T.IDENT, "enum name").value
        generic_params = []
        if self._at(T.LT):
            self._advance()
            generic_params.append(self._expect(T.IDENT, "generic parameter").value)
            while self._at(T.COMMA):
                self._advance()
                generic_params.append(self._expect(T.IDENT, "generic parameter").value)
            self._expect(T.GT, "'>'")
        self._expect(T.LBRACE, "'{'")
        variants = []
        while not self._at(T.RBRACE) and not self._at(T.EOF):
            variant_name = self._expect(T.IDENT, "variant name").value
            if self._at(T.LPAREN):
                self._advance()
                payload_types = [self._type_name()]
                while self._at(T.COMMA):
                    self._advance()
                    payload_types.append(self._type_name())
                self._expect(T.RPAREN, "')'")
                variants.append(A.EnumVariant(
                    variant_name, payload_types=payload_types))
            else:
                variants.append(A.EnumVariant(variant_name))
            if self._at(T.COMMA):
                self._advance()
            else:
                break
        self._expect(T.RBRACE, "'}'")
        return A.EnumDecl(name, variants, line, generic_params)

    def _type_name(self):
        # first-class function pointer: fn(T, U) -> R
        if self._at(T.FN):
            self._advance()
            self._expect(T.LPAREN, "'('")
            parameters = []
            if not self._at(T.RPAREN):
                parameters.append(self._type_name())
                while self._at(T.COMMA):
                    self._advance()
                    parameters.append(self._type_name())
            self._expect(T.RPAREN, "')'")
            self._expect(T.ARROW, "'->'")
            result = self._type_name()
            return f"fn({','.join(parameters)})->{result}"
        if self._at(T.LPAREN):
            line = self._advance().line
            elements = [self._type_name()]
            if not self._at(T.COMMA):
                raise MortError(
                    "tuple types require at least two elements", line)
            while self._at(T.COMMA):
                self._advance()
                if self._at(T.RPAREN):
                    break
                elements.append(self._type_name())
            self._expect(T.RPAREN, "')'")
            if len(elements) < 2:
                raise MortError(
                    "tuple types require at least two elements", line)
            return "(" + ",".join(elements) + ")"
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
        if tok.type == T.IDENT and tok.value in FIXED_INT_TYPES | FLOAT_TYPES:
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
                self._expect_type_close()
                name += "<" + ",".join(args) + ">"
            return name
        raise MortError(f"expected a type, got {tok.value!r}", tok.line)

    def _fn_decl(self):
        line = self._peek().line
        self._expect(T.FN, "'fn'")
        name = self._function_name()
        generic_params = []
        if self._at(T.LT):
            self._advance()
            generic_params.append(self._expect(T.IDENT, "generic parameter").value)
            while self._at(T.COMMA):
                self._advance()
                generic_params.append(self._expect(T.IDENT, "generic parameter").value)
            self._expect(T.GT, "'>'")
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
        return A.FnDecl(name, params, ret, body, line, generic_params)

    def _extern_fn_decl(self):
        line = self._advance().line  # 'extern'
        self._expect(T.FN, "'fn'")
        name = self._function_name()
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

    def _function_name(self):
        """Accept historical function names that became contextual literals."""
        token = self._peek()
        if token.type not in (T.IDENT, T.NULL):
            raise MortError(f"expected function name, got {token.value!r}", token.line)
        self._advance()
        return token.value

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
        if t == T.CONST:
            return self._let_stmt(mutable=False)
        if t == T.RETURN:
            return self._return_stmt()
        if t == T.IF:
            return self._if_stmt()
        if t == T.WHILE:
            return self._while_stmt()
        if t == T.LOOP:
            line = self._advance().line
            return A.While(A.BoolLit(True, line), self._block(), line)
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

    def _let_stmt(self, mutable=True):
        line = self._advance().line
        name = self._expect(T.IDENT, "variable name").value
        decl = None
        if self._at(T.COLON):
            self._advance()
            decl = self._type_name()
        self._expect(T.ASSIGN, "'='")
        expr = self._expression()
        self._expect(T.SEMI, "';'")
        return A.Let(name, decl, expr, line, mutable=mutable)

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
            if self._at(T.DOTDOTEQ):
                self._advance()
                inclusive = True
            else:
                self._expect(T.DOTDOT, "'..'")
                inclusive = False
            end = self._expression()
        finally:
            self._no_struct_lit = saved
        body = self._block()
        return A.For(var, decl_type, start, end, body, line, inclusive)

    def _expr_or_assign(self):
        line = self._peek().line
        expr = self._expression()
        if self._peek().type in ASSIGNMENT_OPS:
            op = ASSIGNMENT_OPS[self._advance().type]
            if not self._is_lvalue(expr):
                raise MortError("invalid assignment target", line)
            value = self._expression()
            self._expect(T.SEMI, "';'")
            return A.Assign(expr, value, line, op)
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
        if self._at(T.TRY):
            line = self._advance().line
            return A.Try(self._unary(), line)
        if self._at(T.MOVE):
            line = self._advance().line
            return A.Move(self._unary(), line)
        return self._postfix()

    def _postfix(self):
        expr = self._primary()
        while True:
            if self._at(T.LT) and self._looks_like_call_type_args():
                name = self._qualified_name(expr)
                if name is None:
                    raise MortError("only named functions can have type arguments", self._peek().line)
                self._advance()
                type_args = [self._type_name()]
                while self._at(T.COMMA):
                    self._advance()
                    type_args.append(self._type_name())
                self._expect_type_close()
                lp = self._expect(T.LPAREN, "'('")
                args = []
                if not self._at(T.RPAREN):
                    args.append(self._expression())
                    while self._at(T.COMMA):
                        self._advance()
                        args.append(self._expression())
                self._expect(T.RPAREN, "')'")
                expr = A.Call(name, args, lp.line, type_args)
            elif self._at(T.LPAREN):
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
                if self._at(T.INT):
                    field = str(self._advance().value)
                else:
                    field = self._expect(T.IDENT, "field name").value
                expr = A.FieldAccess(expr, field, dot.line)
            elif self._at(T.LBRACKET):
                lb = self._advance()
                index = self._expression()
                self._expect(T.RBRACKET, "']'")
                expr = A.Index(expr, index, lb.line)
            else:
                return expr

    def _looks_like_call_type_args(self):
        depth = 0
        offset = 0
        while self.i + offset < len(self.toks):
            token_type = self._peek(offset).type
            if token_type == T.LT:
                depth += 1
            elif token_type == T.GT:
                depth -= 1
                if depth == 0:
                    return self._peek(offset + 1).type == T.LPAREN
            elif token_type == T.SHR:
                depth -= 2
                if depth == 0:
                    return self._peek(offset + 1).type == T.LPAREN
                if depth < 0:
                    return False
            elif token_type in (T.SEMI, T.EOF):
                return False
            offset += 1
        return False

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
        if t.type == T.FLOAT:
            self._advance()
            return A.FloatLit(t.value, t.line)
        if t.type == T.CHAR:
            self._advance()
            return A.CharLit(t.value, t.line)
        if t.type == T.NULL:
            self._advance()
            # `null` is reserved as a value, but keeping it contextual in call
            # position preserves source compatibility with pre-0.20 functions.
            if self._at(T.LPAREN) or (
                    self._at(T.LT) and self._looks_like_call_type_args()):
                return A.Var(t.value, t.line)
            return A.NullLit(t.line)
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
            if self._looks_like_generic_qualifier():
                return A.Var(self._type_name(), t.line)
            self._advance()
            return A.Var(t.value, t.line)
        if t.type == T.LPAREN:
            line = self._advance().line
            # inside parentheses, struct literals are unambiguous again
            saved = self._no_struct_lit
            self._no_struct_lit = False
            try:
                first = self._expression()
                if self._at(T.COMMA):
                    elements = [first]
                    while self._at(T.COMMA):
                        self._advance()
                        if self._at(T.RPAREN):
                            break
                        elements.append(self._expression())
                    if len(elements) < 2:
                        raise MortError(
                            "tuple literals require at least two elements", line)
                    e = A.TupleLit(elements, line)
                else:
                    e = first
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

    def _looks_like_generic_qualifier(self):
        if self._peek().type != T.IDENT or self._peek(1).type != T.LT:
            return False
        depth = 0
        offset = 1
        while self.i + offset < len(self.toks):
            token_type = self._peek(offset).type
            if token_type == T.LT:
                depth += 1
            elif token_type == T.GT:
                depth -= 1
                if depth == 0:
                    return self._peek(offset + 1).type == T.DOT
            elif token_type == T.SHR:
                depth -= 2
                if depth == 0:
                    return self._peek(offset + 1).type == T.DOT
                if depth < 0:
                    return False
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
