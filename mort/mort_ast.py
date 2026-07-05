"""AST node classes for Mort.

Named ``mort_ast`` (not ``ast``) so it never shadows Python's stdlib ``ast``.
Every expression node grows a ``.type`` attribute during type checking.
"""


class Node:
    def __init__(self):
        self.type = None  # resolved by the checker for expressions


# ----- top level -----
class Program(Node):
    def __init__(self, funcs):
        super().__init__()
        self.funcs = funcs


class Param:
    def __init__(self, name, typ):
        self.name = name
        self.typ = typ


class FnDecl(Node):
    def __init__(self, name, params, ret, body, line):
        super().__init__()
        self.name = name
        self.params = params
        self.ret = ret
        self.body = body
        self.line = line


# ----- statements -----
class Let(Node):
    def __init__(self, name, decl_type, expr, line):
        super().__init__()
        self.name = name
        self.decl_type = decl_type  # explicit annotation or None
        self.expr = expr
        self.line = line
        self.var_type = None  # filled in by the checker


class Assign(Node):
    def __init__(self, name, expr, line):
        super().__init__()
        self.name = name
        self.expr = expr
        self.line = line


class Return(Node):
    def __init__(self, expr, line):
        super().__init__()
        self.expr = expr
        self.line = line


class If(Node):
    def __init__(self, cond, then, els, line):
        super().__init__()
        self.cond = cond
        self.then = then
        self.els = els  # Block, If, or None
        self.line = line


class While(Node):
    def __init__(self, cond, body, line):
        super().__init__()
        self.cond = cond
        self.body = body
        self.line = line


class Block(Node):
    def __init__(self, stmts, line):
        super().__init__()
        self.stmts = stmts
        self.line = line


class ExprStmt(Node):
    def __init__(self, expr, line):
        super().__init__()
        self.expr = expr
        self.line = line


# ----- expressions -----
class IntLit(Node):
    def __init__(self, value, line):
        super().__init__()
        self.value = value
        self.line = line


class BoolLit(Node):
    def __init__(self, value, line):
        super().__init__()
        self.value = value
        self.line = line


class Var(Node):
    def __init__(self, name, line):
        super().__init__()
        self.name = name
        self.line = line


class Unary(Node):
    def __init__(self, op, operand, line):
        super().__init__()
        self.op = op
        self.operand = operand
        self.line = line


class Binary(Node):
    def __init__(self, op, left, right, line):
        super().__init__()
        self.op = op
        self.left = left
        self.right = right
        self.line = line


class Call(Node):
    def __init__(self, name, args, line):
        super().__init__()
        self.name = name
        self.args = args
        self.line = line
