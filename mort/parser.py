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


class Parser:
    def __init__(self, tokens):
        self.toks = tokens
        self.i = 0

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
        while not self._at(T.EOF):
            funcs.append(self._fn_decl())
        return A.Program(funcs)

    def _type_name(self):
        tok = self._peek()
        if tok.type == T.KW_INT:
            self._advance()
            return "int"
        if tok.type == T.KW_BOOL:
            self._advance()
            return "bool"
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
        if t == T.LBRACE:
            return self._block()
        return self._expr_or_assign()

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

    def _if_stmt(self):
        line = self._advance().line
        cond = self._expression()
        then = self._block()
        els = None
        if self._at(T.ELSE):
            self._advance()
            els = self._if_stmt() if self._at(T.IF) else self._block()
        return A.If(cond, then, els, line)

    def _while_stmt(self):
        line = self._advance().line
        cond = self._expression()
        body = self._block()
        return A.While(cond, body, line)

    def _expr_or_assign(self):
        line = self._peek().line
        expr = self._expression()
        if self._at(T.ASSIGN):
            self._advance()
            if not isinstance(expr, A.Var):
                raise MortError("invalid assignment target", line)
            value = self._expression()
            self._expect(T.SEMI, "';'")
            return A.Assign(expr.name, value, line)
        self._expect(T.SEMI, "';'")
        return A.ExprStmt(expr, line)

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
        left = self._unary()
        while self._peek().type in (T.STAR, T.SLASH, T.PERCENT):
            op = self._advance()
            left = A.Binary(op.value, left, self._unary(), op.line)
        return left

    def _unary(self):
        if self._peek().type in (T.BANG, T.MINUS):
            op = self._advance()
            return A.Unary(op.value, self._unary(), op.line)
        return self._call()

    def _call(self):
        expr = self._primary()
        while self._at(T.LPAREN):
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
            self._advance()
            return A.Var(t.value, t.line)
        if t.type == T.LPAREN:
            self._advance()
            e = self._expression()
            self._expect(T.RPAREN, "')'")
            return e
        raise MortError(f"unexpected token {t.value!r}", t.line)
