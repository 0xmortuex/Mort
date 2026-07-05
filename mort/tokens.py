"""Token definitions for the Mort lexer."""
from enum import Enum, auto


class T(Enum):
    # literals & identifiers
    INT = auto()
    STRING = auto()
    IDENT = auto()
    # keywords
    LET = auto()
    FN = auto()
    RETURN = auto()
    IF = auto()
    ELSE = auto()
    WHILE = auto()
    FOR = auto()
    IN = auto()
    TRUE = auto()
    FALSE = auto()
    KW_INT = auto()
    KW_BOOL = auto()
    AS = auto()
    STRUCT = auto()
    ASM = auto()
    # punctuation
    LPAREN = auto()
    RPAREN = auto()
    LBRACE = auto()
    RBRACE = auto()
    COMMA = auto()
    SEMI = auto()
    COLON = auto()
    ARROW = auto()
    DOT = auto()
    DOTDOT = auto()
    AMP = auto()
    LBRACKET = auto()
    RBRACKET = auto()
    # operators
    ASSIGN = auto()
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    SLASH = auto()
    PERCENT = auto()
    EQ = auto()
    NE = auto()
    LT = auto()
    GT = auto()
    LE = auto()
    GE = auto()
    AND = auto()
    OR = auto()
    BANG = auto()
    EOF = auto()


KEYWORDS = {
    "let": T.LET,
    "fn": T.FN,
    "return": T.RETURN,
    "if": T.IF,
    "else": T.ELSE,
    "while": T.WHILE,
    "for": T.FOR,
    "in": T.IN,
    "true": T.TRUE,
    "false": T.FALSE,
    "int": T.KW_INT,
    "bool": T.KW_BOOL,
    "as": T.AS,
    "struct": T.STRUCT,
    "asm": T.ASM,
}


class Token:
    __slots__ = ("type", "value", "line", "col")

    def __init__(self, type, value, line, col):
        self.type = type
        self.value = value
        self.line = line
        self.col = col

    def __repr__(self):
        return f"Token({self.type.name}, {self.value!r}, line={self.line})"
