"""AST node classes for Mort.

Named ``mort_ast`` (not ``ast``) so it never shadows Python's stdlib ``ast``.
Every expression node grows a ``.type`` attribute during type checking.
"""


class Node:
    def __init__(self):
        self.type = None      # resolved by the checker for expressions
        self.is_lit = False   # True for untyped integer-literal expressions
        self.const_val = None  # folded value for a constant integer expression


# ----- top level -----
class Program(Node):
    def __init__(self, funcs, structs, globals=None, externs=None, imports=None,
                 enums=None, tests=None, module_name=None):
        super().__init__()
        self.funcs = funcs
        self.structs = structs
        self.globals = globals or []  # top-level Let nodes
        self.externs = externs or []
        self.imports = imports or []
        self.enums = enums or []
        self.tests = tests or []
        self.module_name = module_name
        self.import_aliases = {}


class ImportDecl(Node):
    def __init__(self, parts, line, alias=None):
        super().__init__()
        self.parts = parts
        self.line = line
        self.alias = alias
        self.resolved_path = None


class Param:
    def __init__(self, name, typ):
        self.name = name
        self.typ = typ


class StructField:
    def __init__(self, name, typ):
        self.name = name
        self.typ = typ


class StructDecl(Node):
    def __init__(self, name, fields, line):
        super().__init__()
        self.name = name
        self.fields = fields  # list of StructField, in declared order
        self.line = line


class FnDecl(Node):
    def __init__(self, name, params, ret, body, line):
        super().__init__()
        self.name = name
        self.params = params
        self.ret = ret
        self.body = body
        self.line = line
        self.public = False
        self.module = None
        self.symbol_name = name
        self.import_aliases = {}


class EnumDecl(Node):
    def __init__(self, name, variants, line):
        super().__init__()
        self.name = name
        self.variants = variants
        self.line = line


class ExternFnDecl(Node):
    """A C-ABI function declaration with no Mort body."""

    def __init__(self, name, params, ret, line):
        super().__init__()
        self.name = name
        self.params = params
        self.ret = ret
        self.line = line


class TestDecl(Node):
    def __init__(self, name, body, line):
        super().__init__()
        self.name = name
        self.body = body
        self.line = line
        self.module = None
        self.import_aliases = {}


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
    def __init__(self, target, expr, line):
        super().__init__()
        self.target = target  # an lvalue expression (Var or unary '*' deref)
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


class For(Node):
    def __init__(self, var, decl_type, start, end, body, line):
        super().__init__()
        self.var = var
        self.decl_type = decl_type  # optional explicit loop-variable type
        self.start = start
        self.end = end        # exclusive upper bound
        self.body = body
        self.line = line
        self.var_type = None  # resolved by the checker


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


class Asm(Node):
    def __init__(self, text, line):
        super().__init__()
        self.text = text  # raw string body, emitted verbatim into __asm__
        self.line = line


class Break(Node):
    def __init__(self, line):
        super().__init__()
        self.line = line


class Continue(Node):
    def __init__(self, line):
        super().__init__()
        self.line = line


class MatchArm(Node):
    def __init__(self, pattern, body, line):
        super().__init__()
        self.pattern = pattern  # None is the wildcard arm
        self.body = body
        self.line = line


class Match(Node):
    def __init__(self, expr, arms, line):
        super().__init__()
        self.expr = expr
        self.arms = arms
        self.line = line
        self.exhaustive = False


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


class StrLit(Node):
    def __init__(self, value, line):
        super().__init__()
        self.value = value  # raw inner text; escapes preserved for C emission
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
        self.resolved_name = None


class Cast(Node):
    def __init__(self, expr, target_type, line):
        super().__init__()
        self.expr = expr
        self.target_type = target_type
        self.line = line


class StructLit(Node):
    def __init__(self, name, fields, line):
        super().__init__()
        self.name = name
        self.fields = fields  # list of (field_name, expr) in source order
        self.line = line


class FieldAccess(Node):
    def __init__(self, obj, field, line):
        super().__init__()
        self.obj = obj
        self.field = field
        self.line = line


class ArrayLit(Node):
    def __init__(self, elements, line):
        super().__init__()
        self.elements = elements  # list of expressions
        self.line = line


class ArrayRepeat(Node):
    def __init__(self, value, count, line):
        super().__init__()
        self.value = value  # expression
        self.count = count  # int literal count
        self.line = line


class Index(Node):
    def __init__(self, obj, index, line):
        super().__init__()
        self.obj = obj
        self.index = index
        self.line = line
