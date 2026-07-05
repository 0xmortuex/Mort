"""Lexer: turns Mort source text into a flat list of tokens."""
from .tokens import Token, T, KEYWORDS
from .errors import MortError

_TWO_CHAR = {
    "->": T.ARROW,
    "==": T.EQ,
    "!=": T.NE,
    "<=": T.LE,
    ">=": T.GE,
    "&&": T.AND,
    "||": T.OR,
}

_ONE_CHAR = {
    "(": T.LPAREN,
    ")": T.RPAREN,
    "{": T.LBRACE,
    "}": T.RBRACE,
    ",": T.COMMA,
    ";": T.SEMI,
    ":": T.COLON,
    ".": T.DOT,
    "&": T.AMP,
    "[": T.LBRACKET,
    "]": T.RBRACKET,
    "=": T.ASSIGN,
    "+": T.PLUS,
    "-": T.MINUS,
    "*": T.STAR,
    "/": T.SLASH,
    "%": T.PERCENT,
    "<": T.LT,
    ">": T.GT,
    "!": T.BANG,
}


class Lexer:
    def __init__(self, src):
        self.src = src
        self.i = 0
        self.line = 1
        self.col = 1
        self.tokens = []

    def _peek(self, offset=0):
        j = self.i + offset
        return self.src[j] if j < len(self.src) else "\0"

    def _advance(self):
        c = self.src[self.i]
        self.i += 1
        if c == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return c

    def _add(self, type, value, line, col):
        self.tokens.append(Token(type, value, line, col))

    def tokenize(self):
        while self.i < len(self.src):
            c = self._peek()
            if c in " \t\r\n":
                self._advance()
                continue
            # line comment
            if c == "/" and self._peek(1) == "/":
                while self.i < len(self.src) and self._peek() != "\n":
                    self._advance()
                continue
            line, col = self.line, self.col
            if c == '"':
                self._string(line, col)
            elif c.isdigit():
                self._number(line, col)
            elif c.isalpha() or c == "_":
                self._ident(line, col)
            else:
                self._symbol(line, col)
        self._add(T.EOF, None, self.line, self.col)
        return self.tokens

    def _number(self, line, col):
        # hex literal, e.g. 0xB8000
        if self._peek() == "0" and self._peek(1) in "xX":
            self._advance()
            self._advance()
            s = ""
            while self._peek() in "0123456789abcdefABCDEF":
                s += self._advance()
            if not s:
                raise MortError("invalid hex literal '0x'", line, col)
            if self._peek().isalnum() or self._peek() == "_":
                raise MortError("invalid hex literal", line, col)
            self._add(T.INT, int(s, 16), line, col)
            return
        s = ""
        while self._peek().isdigit():
            s += self._advance()
        # reject things like 12abc
        if self._peek().isalpha() or self._peek() == "_":
            raise MortError(f"invalid number literal near {s + self._peek()!r}", line, col)
        self._add(T.INT, int(s), line, col)

    def _string(self, line, col):
        # Capture the raw inner text (escape sequences kept verbatim) so it can
        # be re-emitted straight into a C string literal — handy for asm().
        self._advance()  # opening quote
        raw = ""
        while True:
            c = self._peek()
            if c == "\0" or c == "\n":
                raise MortError("unterminated string literal", line, col)
            if c == "\\":  # keep the escape pair as-is (e.g. \n, \", \\)
                raw += self._advance()
                raw += self._advance()
                continue
            if c == '"':
                self._advance()
                break
            raw += self._advance()
        self._add(T.STRING, raw, line, col)

    def _ident(self, line, col):
        s = ""
        while self._peek().isalnum() or self._peek() == "_":
            s += self._advance()
        self._add(KEYWORDS.get(s, T.IDENT), s, line, col)

    def _symbol(self, line, col):
        c = self._advance()
        two = c + self._peek()
        if two in _TWO_CHAR:
            self._advance()
            self._add(_TWO_CHAR[two], two, line, col)
            return
        if c in _ONE_CHAR:
            self._add(_ONE_CHAR[c], c, line, col)
            return
        raise MortError(f"unexpected character {c!r}", line, col)
