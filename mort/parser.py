"""Recursive-descent parser: tokens -> AST.

Grammar (informal):
    program   := fn_decl*
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

FIXED_INT_TYPES = {"i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64"}


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
        while not self._at(T.EOF):
            if self._at(T.STRUCT):
                structs.append(self._struct_decl())
            else:
                funcs.append(self._fn_decl())
        return A.Program(funcs, structs)

    def _struct_decl(self):
        line = self._peek().line
        self._expect(T.STRUCT, "'struct'")
        name = self._expect(T.IDENT, "struct name").value
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
        return A.StructDecl(name, fields, line)

    def _type_name(self):
        # pointer type: *T
        if self._at(T.STAR):
            self._advance()
            return "*" + self._type_name()
        tok = self._peek()
        if tok.type == T.KW_INT:
            self._advance()
            return "i64"  # 'int' is a friendly alias for i64
        if tok.type == T.KW_BOOL:
            self._advance()
            return "bool"
        if tok.type == T.IDENT and tok.value in FIXED_INT_TYPES:
            self._advance()
            return tok.value
        if tok.type == T.IDENT:
            # a struct type; the checker validates it exists
            self._advance()
            return tok.value
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
        if t == T.ASM:
            return self._asm_stmt()
        if t == T.LBRACE:
            return self._block()
        return self._expr_or_assign()

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
        # a name, a pointer dereference (*p), or a field (s.x) — all locations
        return (
            isinstance(e, A.Var)
            or (isinstance(e, A.Unary) and e.op == "*")
            or isinstance(e, A.FieldAccess)
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
        left = self._equality()
        while self._at(T.AND):
            op = self._advance()
            left = A.Binary("&&", left, self._equality(), op.line)
        return left

    def _equality(self):
        left = self._comparison()
        while self._peek().type in (T.EQ, T.NE):
            op = self._advance()
            left = A.Binary(op.value, left, self._comparison(), op.line)
        return left

    def _comparison(self):
        left = self._term()
        while self._peek().type in (T.LT, T.GT, T.LE, T.GE):
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
        # '&' address-of and '*' dereference join '!' and '-' as prefix ops
        if self._peek().type in (T.BANG, T.MINUS, T.AMP, T.STAR):
            op = self._advance()
            # normalise '&' to op string '&'
            op_str = "&" if op.type == T.AMP else op.value
            return A.Unary(op_str, self._unary(), op.line)
        return self._postfix()

    def _postfix(self):
        expr = self._primary()
        while True:
            if self._at(T.LPAREN):
                if not isinstance(expr, A.Var):
                    raise MortError("only named functions can be called", self._peek().line)
                lp = self._advance()
                args = []
                if not self._at(T.RPAREN):
                    args.append(self._expression())
                    while self._at(T.COMMA):
                        self._advance()
                        args.append(self._expression())
                self._expect(T.RPAREN, "')'")
                expr = A.Call(expr.name, args, lp.line)
            elif self._at(T.DOT):
                dot = self._advance()
                field = self._expect(T.IDENT, "field name").value
                expr = A.FieldAccess(expr, field, dot.line)
            else:
                return expr

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
        if t.type == T.IDENT:
            # struct literal:  Name { field: expr, ... }
            if self._peek(1).type == T.LBRACE and not self._no_struct_lit:
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
        name_tok = self._advance()  # IDENT
        line = self._advance().line  # '{'
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
        return A.StructLit(name_tok.value, fields, line)
