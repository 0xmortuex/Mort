"""The specification, executed.

``docs/language-specification.md`` is the single source of the grammar. This
file reads the EBNF out of the document, builds a recognizer and a sentence
generator from that text alone, and compares them with ``mort/lexer.py`` and
``mort/parser.py`` in both directions:

* every rule generates sentences the parser must accept (spec wider than parser);
* every rule has a generated negative sample that both sides reject;
* every ``.mx`` file in the repository and thousands of token mutants of the
  witness programs get the same verdict from the grammar and from the parser
  (spec narrower than parser, or parser widened alone);
* every keyword, punctuator and token kind in the lexer appears in the grammar,
  and the reverse.

``test_harness_*`` prove the point by changing one side at a time and asserting
that a problem is reported. Fenced ``mort`` examples in the document are
compiled and run with their asserted output.
"""
import hashlib
import os
import random
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mortc
from mort import lexer as lexer_module
from mort.errors import MortError
from mort.lexer import Lexer
from mort.parser import Parser
from mort.tokens import KEYWORDS, T

SPEC_PATH = ROOT / "docs" / "language-specification.md"
SPEC_TEXT = SPEC_PATH.read_text(encoding="utf-8")

TOKEN_RULES = ("IDENT", "INT", "FLOAT", "CHAR", "STRING")
CONSTRAINT_MESSAGES = ("invalid assignment target", "only named functions can")
PREDICATES = ("call-type-args", "struct-literal", "generic-qualifier")
MODES = ("no-struct", "allow-struct")
CHAR_CLASSES = {
    "any": lambda c: True,
    "alpha": str.isalpha,
    "alnum": str.isalnum,
    "latin1": lambda c: ord(c) <= 255,
    "LF": lambda c: c == "\n",
    "CR": lambda c: c == "\r",
    "TAB": lambda c: c == "\t",
    "NUL": lambda c: c == "\0",
}
CHAR_SAMPLES = {
    "any": "z", "alpha": "q", "alnum": "7", "latin1": "z",
    "LF": "\n", "CR": "\r", "TAB": "\t", "NUL": "\0",
}


# ----- EBNF ------------------------------------------------------------------

@dataclass(eq=False)
class Seq:
    items: list


@dataclass(eq=False)
class Alt:
    options: list


@dataclass(eq=False)
class Opt:
    inner: object


@dataclass(eq=False)
class Rep:
    inner: object


@dataclass(eq=False)
class Lit:
    text: str


@dataclass(eq=False)
class Ref:
    name: str


@dataclass(eq=False)
class Special:
    name: str


@dataclass(eq=False)
class Except:
    base: object
    excluded: object


_EBNF_TOKEN = re.compile(
    r"""\s+|\(\*.*?\*\)
    |(?P<name>[A-Za-z][A-Za-z0-9_-]*)
    |(?P<str>"[^"]*"|'[^']*')
    |(?P<special>\?[^?]*\?)
    |(?P<punct>[=;,|()\[\]{}-])""",
    re.VERBOSE | re.DOTALL,
)


class EbnfError(Exception):
    pass


def parse_ebnf(text):
    """Parse ISO-style EBNF into {rule name: node}, keeping source order."""
    tokens = []
    position = 0
    while position < len(text):
        match = _EBNF_TOKEN.match(text, position)
        if match is None:
            raise EbnfError(f"bad EBNF near {text[position:position + 30]!r}")
        position = match.end()
        if match.lastgroup is not None:
            tokens.append((match.lastgroup, match.group()))
    index = 0

    def peek():
        return tokens[index] if index < len(tokens) else ("eof", "")

    def take(value):
        nonlocal index
        if peek()[1] != value:
            raise EbnfError(f"expected {value!r}, got {peek()[1]!r}")
        index += 1

    def alternatives():
        options = [sequence()]
        while peek()[1] == "|":
            take("|")
            options.append(sequence())
        return options[0] if len(options) == 1 else Alt(options)

    def sequence():
        items = [term()]
        while peek()[1] == ",":
            take(",")
            items.append(term())
        return items[0] if len(items) == 1 else Seq(items)

    def term():
        base = factor()
        if peek()[1] == "-":
            take("-")
            return Except(base, factor())
        return base

    def factor():
        nonlocal index
        kind, value = peek()
        if kind == "name":
            index += 1
            return Ref(value)
        if kind == "str":
            index += 1
            return Lit(value[1:-1])
        if kind == "special":
            index += 1
            return Special(value[1:-1].strip())
        if value in ("(", "[", "{"):
            take(value)
            inner = alternatives()
            take({"(": ")", "[": "]", "{": "}"}[value])
            return {"(": lambda n: n, "[": Opt, "{": Rep}[value](inner)
        raise EbnfError(f"unexpected {value!r} in EBNF")

    rules = {}
    while index < len(tokens):
        kind, name = peek()
        if kind != "name":
            raise EbnfError(f"expected a rule name, got {name!r}")
        index += 1
        take("=")
        if name in rules:
            raise EbnfError(f"rule {name!r} defined twice")
        rules[name] = alternatives()
        take(";")
    return rules


def fenced_blocks(text):
    """Yield (info string, body, 1-based line) for each fenced block."""
    blocks = []
    lines = text.split("\n")
    index = 0
    while index < len(lines):
        if lines[index].startswith("```"):
            info = lines[index][3:].strip()
            start = index + 1
            index += 1
            while index < len(lines) and lines[index] != "```":
                index += 1
            blocks.append((info, "\n".join(lines[start:index]) + "\n", start + 1))
        index += 1
    return blocks


def grammar_text(spec, kind):
    bodies = [body for info, body, _ in fenced_blocks(spec) if info == f"ebnf {kind}"]
    if not bodies:
        raise EbnfError(f"the specification has no ```ebnf {kind} block")
    return "\n".join(bodies)


def walk(node):
    yield node
    if isinstance(node, Seq):
        for item in node.items:
            yield from walk(item)
    elif isinstance(node, Alt):
        for item in node.options:
            yield from walk(item)
    elif isinstance(node, (Opt, Rep)):
        yield from walk(node.inner)
    elif isinstance(node, Except):
        yield from walk(node.base)
        yield from walk(node.excluded)


def referenced(rules):
    names = set()
    for body in rules.values():
        names.update(n.name for n in walk(body) if isinstance(n, Ref))
    return names


def terminals(rules):
    found = set()
    for body in rules.values():
        found.update(n.text for n in walk(body) if isinstance(n, Lit))
    return found


# ----- the generator, shared by both grammars ---------------------------------

class Generator:
    """Expand rules into token lists, covering each choice point in a rule."""

    def __init__(self, rules, leaf):
        self.rules = rules
        self.leaf = leaf  # function(Ref|Special|Lit) -> list of strings, or None
        self.cost = {name: 10 ** 9 for name in rules}
        changed = True
        while changed:
            changed = False
            for name, body in rules.items():
                value = self._node_cost(body)
                if value < self.cost[name]:
                    self.cost[name] = value
                    changed = True

    def _node_cost(self, node):
        if isinstance(node, Seq):
            return sum(self._node_cost(item) for item in node.items)
        if isinstance(node, Alt):
            return min(self._node_cost(item) for item in node.options)
        if isinstance(node, (Opt, Rep)):
            return 0
        if isinstance(node, Except):
            return self._node_cost(node.base)
        if isinstance(node, Ref) and node.name in self.rules:
            return self.cost[node.name]
        return 1

    def _prefer(self, option):
        """Among equally short options, prefer one that starts with a name."""
        if isinstance(option, Ref) and option.name == "IDENT":
            return 0
        return 1 if self._starts_with_name(option, set()) else 2

    def _starts_with_name(self, node, seen):
        if isinstance(node, Seq):
            return self._starts_with_name(node.items[0], seen)
        if isinstance(node, Alt):
            return any(self._starts_with_name(item, seen) for item in node.options)
        if isinstance(node, Ref):
            if node.name == "IDENT":
                return True
            if node.name in self.rules and node.name not in seen:
                seen.add(node.name)
                return self._starts_with_name(self.rules[node.name], seen)
        return False

    def expand(self, node, forced=None):
        forced = forced or {}
        if isinstance(node, Seq):
            result = []
            for item in node.items:
                result += self.expand(item, forced)
            return result
        if isinstance(node, Alt):
            if id(node) in forced:
                return self.expand(node.options[forced[id(node)]], forced)
            order = sorted(
                range(len(node.options)),
                key=lambda i: (self._node_cost(node.options[i]),
                               self._prefer(node.options[i]), i))
            return self.expand(node.options[order[0]], forced)
        if isinstance(node, Opt):
            return self.expand(node.inner, forced) if forced.get(id(node)) else []
        if isinstance(node, Rep):
            result = []
            for _ in range(forced.get(id(node), 0)):
                result += self.expand(node.inner, forced)
            return result
        if isinstance(node, Except):
            return self.expand(node.base, forced)
        if isinstance(node, Ref) and node.name in self.rules:
            return self.expand(self.rules[node.name])
        return self.leaf(node)

    def sentences(self, name):
        """One expansion per choice in the rule's own body, plus the default."""
        body = self.rules[name]
        variants = [self.expand(body)]
        maximal = {id(n): 1 for n in walk(body) if isinstance(n, (Opt, Rep))}
        full = self.expand(body, maximal)
        if full not in variants:
            variants.append(full)
        for node in walk(body):
            if isinstance(node, Alt):
                choices = range(len(node.options))
            elif isinstance(node, Opt):
                choices = (1,)
            elif isinstance(node, Rep):
                choices = (1, 2)
            else:
                continue
            for choice in choices:
                sentence = self.expand(body, {id(node): choice})
                if sentence not in variants:
                    variants.append(sentence)
        return variants

    def _contains(self, node, target):
        return any(isinstance(n, Ref) and n.name == target for n in walk(node))

    def expand_through(self, node, target, inner):
        """Expand ``node`` with one Ref to ``target`` replaced by ``inner``."""
        if isinstance(node, Ref):
            return list(inner) if node.name == target else self.expand(node)
        if isinstance(node, Seq):
            result = []
            done = False
            for item in node.items:
                if not done and self._contains(item, target):
                    result += self.expand_through(item, target, inner)
                    done = True
                else:
                    result += self.expand(item)
            return result
        if isinstance(node, Alt):
            for option in node.options:
                if self._contains(option, target):
                    return self.expand_through(option, target, inner)
        if isinstance(node, (Opt, Rep)):
            return self.expand_through(node.inner, target, inner)
        return self.expand(node)

    def host(self, start, name, inner):
        """Embed ``inner`` in a shortest whole program reaching rule ``name``."""
        parents = {start: None}
        frontier = [start]
        while frontier and name not in parents:
            next_frontier = []
            for rule in frontier:
                for node in walk(self.rules[rule]):
                    if (isinstance(node, Ref) and node.name in self.rules
                            and node.name not in parents):
                        parents[node.name] = rule
                        next_frontier.append(node.name)
            frontier = next_frontier
        if name not in parents:
            return None
        sentence = list(inner)
        current = name
        while parents[current] is not None:
            parent = parents[current]
            sentence = self.expand_through(self.rules[parent], current, sentence)
            current = parent
        return sentence


# ----- syntactic grammar ------------------------------------------------------

class Reject(Exception):
    pass


def classify(text):
    """The (type, value) a grammar terminal lexes to, value only for IDENT."""
    tokens = Lexer(text).tokenize()
    if len(tokens) != 2:
        raise EbnfError(f"terminal {text!r} is not one token")
    token = tokens[0]
    return (token.type, token.value if token.type == T.IDENT else None)


class SyntaxGrammar:
    def __init__(self, spec):
        self.rules = parse_ebnf(grammar_text(spec, "syntax"))
        self.terminal_of = {}
        for text in terminals(self.rules):
            self.terminal_of[text] = classify(text)
        self.null = {name: False for name in self.rules}
        self.first = {name: set() for name in self.rules}
        self.gated_rules = set()
        changed = True
        while changed:
            changed = False
            for name, body in self.rules.items():
                nullable = self._nullable(body)
                first = self._first(body)
                gated = self._gated(body)
                if nullable and not self.null[name]:
                    self.null[name] = True
                    changed = True
                if not first <= self.first[name]:
                    self.first[name] |= first
                    changed = True
                if gated and name not in self.gated_rules:
                    self.gated_rules.add(name)
                    changed = True
        self.kept = []
        self.node_first = {}
        self.node_null = {}
        self.node_gated = {}
        for body in self.rules.values():
            for node in walk(body):
                self.node_first[id(node)] = self._first(node)
                self.node_null[id(node)] = self._nullable(node)
                self.node_gated[id(node)] = self._gated(node)

    def _nullable(self, node):
        if isinstance(node, Seq):
            return all(self._nullable(item) for item in node.items)
        if isinstance(node, Alt):
            return any(self._nullable(item) for item in node.options)
        if isinstance(node, (Opt, Rep)):
            return True
        if isinstance(node, Ref):
            return self.null.get(node.name, False)
        if isinstance(node, Special):
            return node.name != "angle-close"
        return False

    def _first(self, node):
        if isinstance(node, Seq):
            result = set()
            for item in node.items:
                result |= self._first(item)
                if not self._nullable(item):
                    break
            return result
        if isinstance(node, Alt):
            return set().union(*(self._first(item) for item in node.options))
        if isinstance(node, (Opt, Rep)):
            return self._first(node.inner)
        if isinstance(node, Lit):
            return {self.terminal_of[node.text]}
        if isinstance(node, Ref):
            if node.name in TOKEN_RULES:
                return {(T[node.name], None)}
            return self.first.get(node.name, set())
        if isinstance(node, Special) and node.name == "angle-close":
            return {(T.GT, None), (T.SHR, None), (T.GE, None)}
        return set()

    def _gated(self, node):
        if isinstance(node, Special):
            return node.name in PREDICATES
        if isinstance(node, Seq):
            for item in node.items:
                if self._gated(item):
                    return True
                if not self._nullable(item):
                    return False
            return False
        if isinstance(node, Alt):
            return any(self._gated(item) for item in node.options)
        if isinstance(node, (Opt, Rep)):
            return self._gated(node.inner)
        if isinstance(node, Ref):
            return node.name in self.gated_rules
        return False

    def structural_problems(self):
        problems = []
        known = set(self.rules) | set(TOKEN_RULES)
        for name in sorted(referenced(self.rules) - known):
            problems.append(f"undefined rule {name!r}")
        for name in sorted(set(self.rules) - set(_rules_under(self.rules, "program")) - {"program"}):
            problems.append(f"unreachable rule {name!r}")
        for name, body in self.rules.items():
            for node in walk(body):
                if isinstance(node, Special) and node.name not in (
                        *PREDICATES, *MODES, "angle-close"):
                    problems.append(f"rule {name!r}: unknown special {node.name!r}")
                if isinstance(node, Alt):
                    problems += self._ll1_problems(name, node)
                if isinstance(node, (Opt, Rep)) and self._nullable(node.inner):
                    problems.append(f"rule {name!r}: nullable optional body")
        return problems

    def _ll1_problems(self, name, node):
        """Alternatives must be decidable on one token unless a predicate gates."""
        problems = []
        seen = set()
        for option in node.options:
            first = self._first(option)
            clash = {
                item for item in first if item in seen
                or (item[1] is not None and (item[0], None) in seen)
            }
            if self._gated(option):
                continue
            if clash:
                problems.append(f"rule {name!r}: alternatives are not LL(1) on {clash}")
            seen |= first
        return problems

    def generator(self):
        def leaf(node):
            if isinstance(node, Lit):
                return [node.text]
            if isinstance(node, Ref):
                return [{"IDENT": "a", "INT": "1", "FLOAT": "1.5", "CHAR": "'c'",
                         "STRING": '"s"'}[node.name]]
            return [">"] if node.name == "angle-close" else []
        return Generator(self.rules, leaf)

    def accepts(self, tokens, overrides=None, site=None, visited=None, nesting=None):
        return Recognizer(self, tokens, overrides, site, visited, nesting).run()

    def index(self, body):
        """Make a node built after construction usable by the recognizer."""
        self.kept.append(body)
        for node in walk(body):
            self.node_first[id(node)] = self._first(node)
            self.node_null[id(node)] = self._nullable(node)
            self.node_gated[id(node)] = self._gated(node)


class Recognizer:
    """LL(1) reading of the grammar, with the predicates §3.3 defines."""

    def __init__(self, grammar, tokens, overrides=None, site=None, visited=None, nesting=None):
        self.g = grammar
        self.nesting = nesting      # (rule, deepest allowed) to reject deeper recursion
        self.depth = 0
        self.toks = [[t.type, t.value] for t in tokens]
        self.i = 0
        self.no_struct = False
        self.overrides = overrides or {}
        self.site = site            # (Ref node id, {rule: body}) applied inside that Ref
        self.visited = visited      # collects rule names and Ref node ids reached

    def run(self):
        try:
            self.rule("program")
        except Reject:
            return False
        return self.toks[self.i][0] == T.EOF

    def body(self, name):
        return self.overrides.get(name, self.g.rules[name])

    def rule(self, name):
        saved = self.no_struct
        if self.visited is not None:
            self.visited.add(name)
        limited = self.nesting is not None and self.nesting[0] == name
        if limited:
            self.depth += 1
            if self.depth > self.nesting[1]:
                raise Reject
        self.match(self.body(name))
        if limited:
            self.depth -= 1
        self.no_struct = saved

    def _has(self, node):
        token_type, value = self.toks[self.i]
        first = self.g.node_first[id(node)]
        return (token_type, None) in first or (token_type, value) in first

    def can_start(self, node):
        if not self.g.node_gated[id(node)]:
            return self._has(node)
        if isinstance(node, Special):
            return self.predicate(node.name)
        if isinstance(node, Seq):
            for item in node.items:
                if isinstance(item, Special):
                    if item.name in PREDICATES and not self.predicate(item.name):
                        return False
                    continue
                if self.can_start(item):
                    return True
                if not self.g.node_null[id(item)]:
                    return False
            return True
        if isinstance(node, Alt):
            return any(self.can_start(option) for option in node.options)
        if isinstance(node, (Opt, Rep)):
            return self.can_start(node.inner)
        if isinstance(node, Ref):
            return self.can_start(self.body(node.name))
        return False

    def predicate(self, name):
        kind = self.toks[self.i][0]
        if name == "call-type-args":
            return kind == T.LT and self.angle_followed_by(self.i, T.LPAREN)
        next_kind = self.toks[self.i + 1][0] if kind != T.EOF else T.EOF
        if name == "struct-literal":
            if kind != T.IDENT or self.no_struct:
                return False
            if next_kind != T.LT:
                return next_kind == T.LBRACE
            return self.angle_followed_by(self.i + 1, T.LBRACE)
        if name == "generic-qualifier":
            return (kind == T.IDENT and next_kind == T.LT
                    and self.angle_followed_by(self.i + 1, T.DOT))
        raise AssertionError(name)

    def angle_followed_by(self, start, follower):
        depth = 0
        index = start
        while index < len(self.toks):
            kind = self.toks[index][0]
            if kind == T.LT:
                depth += 1
            elif kind in (T.GT, T.SHR):
                depth -= 1 if kind == T.GT else 2
                if depth == 0:
                    return self.toks[index + 1][0] == follower
                if depth < 0:
                    return False
            elif kind in (T.SEMI, T.EOF, T.LBRACE, T.RBRACE):
                return False
            index += 1
        return False

    def match(self, node):
        if isinstance(node, Lit):
            wanted = self.g.terminal_of[node.text]
            token = self.toks[self.i]
            if token[0] != wanted[0] or (wanted[1] is not None and token[1] != wanted[1]):
                raise Reject
            self.i += 1
        elif isinstance(node, Ref):
            if node.name in TOKEN_RULES:
                if self.toks[self.i][0] != T[node.name]:
                    raise Reject
                self.i += 1
            else:
                if self.visited is not None:
                    self.visited.add(id(node))
                if self.site is not None and self.site[0] == id(node):
                    saved = self.overrides
                    self.overrides = {**saved, **self.site[1]}
                    self.rule(node.name)
                    self.overrides = saved
                else:
                    self.rule(node.name)
        elif isinstance(node, Seq):
            for item in node.items:
                self.match(item)
        elif isinstance(node, Alt):
            for option in node.options:
                if self.can_start(option):
                    self.match(option)
                    return
            raise Reject
        elif isinstance(node, Opt):
            if self.can_start(node.inner):
                self.match(node.inner)
        elif isinstance(node, Rep):
            while self.can_start(node.inner):
                self.match(node.inner)
        elif isinstance(node, Special):
            self.special(node.name)
        else:
            raise TypeError(node)

    def special(self, name):
        if name == "no-struct":
            self.no_struct = True
        elif name == "allow-struct":
            self.no_struct = False
        elif name == "angle-close":
            token = self.toks[self.i]
            if token[0] == T.GT:
                self.i += 1
            elif token[0] == T.SHR:
                token[0], token[1] = T.GT, ">"
            elif token[0] == T.GE:
                token[0], token[1] = T.ASSIGN, "="
            else:
                raise Reject
        elif not self.predicate(name):
            raise Reject


def lex(source):
    try:
        return Lexer(source).tokenize()
    except MortError:
        return None


def parser_verdict(source, parser_cls=Parser, lexer_cls=Lexer):
    """accept, reject, constraint, or crash for one source text."""
    tokens = lexer_cls(source).tokenize()
    try:
        parser_cls(tokens).parse()
    except MortError as error:
        if any(text in str(error) for text in CONSTRAINT_MESSAGES):
            return "constraint"
        return "reject"
    except Exception as error:  # noqa: BLE001 - reported as a harness problem
        return f"crash: {error!r}"
    return "accept"


def render(tokens):
    def text(token):
        if token.type == T.STRING:
            return f'"{token.value}"'
        if token.type == T.CHAR:
            return f"'\\x{token.value:02x}'"
        return str(token.value)
    return " ".join(text(token) for token in tokens if token.type != T.EOF)


# Valid forms the generator's minimal expansions cannot reach by construction.
WITNESSES = [
    "module a.b; import std.vec; import x.y as z; pub fn f() {}",
    "resource struct R { a: i64, b: u8, } fn destroy(r: *R) -> void {}",
    "struct E {} struct G<T, U> { a: T, b: *const U } enum C { A, B, }",
    "enum P<T> { S(T), N, Q(i64, *u8) } type A = fn(i64, *u8) -> void;",
    "let g: [i64; 3] = [1, 2, 3,]; const h: []const u8 = v; let t: (i64, u8,) = (1, 2,);",
    "extern fn null(a: c_int) -> c_size; fn null() {} test \"t\" { let a = 1; }",
    "fn f<T>(a: T, b: []u8) -> Vec<Vec<T>> { let x: A<B<i64>> = y; let z: A<i64>= w; }",
    "fn f() { let a = B<B<i64>> { v: 1 }; let c = E<i64>.A(1); let d = E<*u8>.B; }",
    "fn f() { g<i64>(1); g<A<i64>, *u8>(); (g)(1); (a.b).c(1); a.b<T>(2); null(); }",
    "fn f() { if a < b { if c > (d) {} } while (P { x: 1 }).x > 0 {} }",
    "fn f() { for i: u8 in 0..=3 {} for j in a..b {} loop { break; continue; } }",
    "fn f() { match a { E.A(x, _) => {} 1 => {}, _ => {} } match move v { _ => {} } }",
    "fn f() { asm(\"nop\"); defer g(); { let a = 1; } return; }",
    "fn f() { a[0].b.0 = 1; *p += 2; (x) <<= 3; a >>= 1; a |= 1; a &= 1; a ^= 1; a %= 2; }",
    "fn f() { let x = !a && -b || ~c | d ^ e & f == g != h < i > j <= k >= l << m >> n; }",
    "fn f() { let x = a + b - c * d / e % f as i64 as *const u8; y = try move z; w = &*p; }",
    "fn f() { let x = 0xFF + 0b1 + 0o7 + 1_000 + 1.5e3 + 'a' + \"s\" + true + false + null; }",
    "fn f() -> i64 { let a = [0; 4]; let t = (a, (b, c)); return t.1.0; }",
    "fn f() { let v = P { a: 1, b: Q<T> { c: 2 }, }; let w = i64 { }; }",
    "fn f(a: fn() -> fn(i64) -> i64, b: (i64, (u8, bool)), c: [[u8; 2]; 3]) {}",
]


def repository_sources():
    found = []
    for pattern in ("std/*.mx", "examples/**/*.mx", "conformance/cases/**/*.mx",
                    "selfhost/*.mx", "kernel/*.mx"):
        found += sorted(ROOT.glob(pattern))
    return found


def check_syntax(spec, parser_cls=Parser, mutants=4000):
    """Every disagreement between the spec's grammar and the parser."""
    try:
        grammar = SyntaxGrammar(spec)
    except EbnfError as error:
        return [f"grammar does not load: {error}"]
    problems = grammar.structural_problems()
    if problems:
        return problems
    generator = grammar.generator()
    samples = []

    def compare(source, label):
        tokens = lex(source)
        if tokens is None:
            problems.append(f"{label}: generated text does not lex: {source!r}")
            return None
        verdict = parser_verdict(source, parser_cls)
        if verdict.startswith("crash"):
            problems.append(f"{label}: parser {verdict} on {source!r}")
            return None
        try:
            grammar_says = grammar.accepts(tokens)
        except RecursionError:
            return None  # a data table too long for the right-recursive list rules
        if verdict == "constraint":
            return "constraint"
        if grammar_says != (verdict == "accept"):
            problems.append(
                f"{label}: grammar {'accepts' if grammar_says else 'rejects'} but "
                f"parser says {verdict}: {source!r}")
        return verdict

    for name in grammar.rules:
        strict = 0
        sentences = generator.sentences(name)
        for sentence in sentences:
            program = generator.host("program", name, sentence)
            source = " ".join(program)
            samples.append(source)
            verdict = compare(source, f"rule {name}")
            if verdict == "accept":
                strict += 1
            elif verdict == "reject":
                problems.append(f"rule {name}: parser rejects sentence: {source!r}")
        if not strict:
            problems.append(f"rule {name}: no generated sentence was accepted")
        problems += _negative_problems(grammar, generator, name, sentences, parser_cls)

    for source in WITNESSES:
        samples.append(source)
        if compare(source, "witness") != "accept":
            problems.append(f"witness not accepted: {source!r}")
    for path in repository_sources():
        text = path.read_text(encoding="utf-8")
        if lex(text) is None:
            continue
        compare(text, f"corpus {path.name}")
        tokens = lex(text)
        samples.append(render(tokens[:50]))

    rng = random.Random(20261108)
    pool = [",", ";", "(", ")", "{", "}", "[", "]", "<", ">", ">>", "=", ":", ".",
            "..", "->", "=>", "*", "&", "1", "a", "_", "let", "if", "fn", "as",
            "move", "try", "const", "null", "<=", ">=", "+", "else", "type"]
    for _ in range(mutants):
        words = rng.choice(samples).split(" ")
        position = rng.randrange(len(words))
        action = rng.randrange(4)
        if action == 0:
            del words[position]
        elif action == 1:
            words.insert(position, words[position])
        elif action == 2:
            words[position] = rng.choice(pool)
        elif position + 1 < len(words):
            words[position], words[position + 1] = words[position + 1], words[position]
        source = " ".join(words)
        if lex(source) is not None:
            compare(source, "mutant")
    return problems


def _negative_problems(grammar, generator, name, sentences, parser_cls):
    """A rule has a negative sample when corrupting its text is rejected by both."""
    pool = [",", ";", ")", "{", "let", "1"]
    for sentence in sentences:
        for position in range(len(sentence)):
            variants = [sentence[:position] + sentence[position + 1:]]
            variants += [sentence[:position] + [t] + sentence[position:] for t in pool]
            variants += [sentence[:position] + [t] + sentence[position + 1:] for t in pool]
            for variant in variants:
                program = generator.host("program", name, variant)
                source = " ".join(program)
                tokens = lex(source)
                if tokens is None:
                    continue
                if (not grammar.accepts(tokens)
                        and parser_verdict(source, parser_cls) == "reject"):
                    return []
    return [f"rule {name}: no corrupted sentence is rejected by both sides"]


# ----- lexical grammar --------------------------------------------------------

class LexicalGrammar:
    def __init__(self, spec):
        self.rules = parse_ebnf(grammar_text(spec, "lexical"))

    def generator(self):
        def leaf(node):
            if isinstance(node, Lit):
                return [node.text]
            return [CHAR_SAMPLES[node.name]]
        return Generator(self.rules, leaf)

    def ends(self, node, text, position):
        """Yield every end position of ``node`` matched at ``position``."""
        if isinstance(node, Lit):
            if text.startswith(node.text, position):
                yield position + len(node.text)
        elif isinstance(node, Special):
            if position < len(text) and CHAR_CLASSES[node.name](text[position]):
                yield position + 1
        elif isinstance(node, Ref):
            yield from self.ends(self.rules[node.name], text, position)
        elif isinstance(node, Seq):
            yield from self._sequence(node.items, text, position)
        elif isinstance(node, Alt):
            for option in node.options:
                yield from self.ends(option, text, position)
        elif isinstance(node, Opt):
            yield from self.ends(node.inner, text, position)
            yield position
        elif isinstance(node, Rep):
            for end in self.ends(node.inner, text, position):
                if end > position:
                    yield from self.ends(node, text, end)
            yield position
        elif (isinstance(node, Except)
                and not any(True for _ in self.ends(node.excluded, text, position))):
            yield from self.ends(node.base, text, position)

    def _sequence(self, items, text, position):
        if not items:
            yield position
            return
        for end in self.ends(items[0], text, position):
            yield from self._sequence(items[1:], text, end)

    def full(self, name, text):
        return any(end == len(text) for end in self.ends(self.rules[name], text, 0))


def single_token(text, lexer_cls=Lexer):
    """The token type when ``text`` lexes to exactly one token, else None."""
    if text != text.strip():
        return None
    try:
        tokens = lexer_cls(text).tokenize()
    except MortError:
        return None
    return tokens[0].type if len(tokens) == 2 else None


def is_trivia(text, lexer_cls=Lexer):
    try:
        return len(lexer_cls(text).tokenize()) == 1
    except MortError:
        return False


def is_tuple_index(text, lexer_cls=Lexer):
    try:
        tokens = lexer_cls("a." + text).tokenize()
    except MortError:
        return False
    return (len(tokens) == 4 and tokens[1].type == T.DOT
            and tokens[2].type == T.INT and text == text.strip())


def check_lexical(spec, lexer_cls=Lexer):
    try:
        grammar = LexicalGrammar(spec)
    except EbnfError as error:
        return [f"lexical grammar does not load: {error}"]
    problems = []
    for name in sorted(referenced(grammar.rules) - set(grammar.rules)):
        problems.append(f"undefined lexical rule {name!r}")
    if problems:
        return problems
    generator = grammar.generator()
    verdicts = {name: (lambda s, n=name: single_token(s, lexer_cls) == T[n])
                for name in TOKEN_RULES}
    verdicts["tuple-index"] = lambda s: is_tuple_index(s, lexer_cls)
    verdicts["trivia"] = lambda s: is_trivia(s, lexer_cls)
    for name in verdicts:
        if name not in grammar.rules:
            problems.append(f"lexical rule {name!r} is missing")
    if problems:
        return problems
    pool = "_.xeE09'\"\\+-/*\na$@#`?"
    for name, verdict in verdicts.items():
        negatives = 0
        samples = list(generator.sentences(name))
        for part in _rules_under(grammar.rules, name):
            for sentence in generator.sentences(part):
                if grammar.full(name, "".join(sentence)):
                    samples.append(sentence)
        for sentence in samples:
            text = "".join(sentence)
            if not grammar.full(name, text):
                problems.append(f"lexical {name}: generated {text!r} does not match")
            if not verdict(text):
                problems.append(f"lexical {name}: lexer rejects {text!r}")
            variants = [text[:i] + text[i + 1:] for i in range(len(text))]
            for i in range(len(text) + 1):
                variants += [text[:i] + c + text[i:] for c in pool]
            for variant in variants:
                says = grammar.full(name, variant)
                if says != verdict(variant) and not _out_of_range(variant):
                    problems.append(
                        f"lexical {name}: grammar {'accepts' if says else 'rejects'} "
                        f"{variant!r} but the lexer disagrees")
                elif not says:
                    negatives += 1
        if not negatives:
            problems.append(f"lexical {name}: no negative sample")
    return problems


def _rules_under(rules, start):
    """Every rule reachable from ``start``, ``start`` excluded."""
    seen = set()
    frontier = [start]
    while frontier:
        for node in walk(rules[frontier.pop()]):
            if isinstance(node, Ref) and node.name in rules and node.name not in seen:
                seen.add(node.name)
                frontier.append(node.name)
    seen.discard(start)
    return sorted(seen)


def _out_of_range(text):
    """A float literal may be well formed yet not finite; §2.3 rejects those."""
    try:
        return re.fullmatch(r"[0-9_.eE+-]+", text) and float(text.replace("_", "")) == float("inf")
    except ValueError:
        return False


def check_tokens(spec, keywords=None, one=None, two=None, three=None, token_enum=T):
    """Lexer tables, grammar terminals and the written keyword list must agree."""
    keywords = KEYWORDS if keywords is None else keywords
    one = lexer_module._ONE_CHAR if one is None else one
    two = lexer_module._TWO_CHAR if two is None else two
    three = lexer_module._THREE_CHAR if three is None else three
    try:
        lexical = LexicalGrammar(spec).rules
        syntax = parse_ebnf(grammar_text(spec, "syntax"))
    except EbnfError as error:
        return [f"grammar does not load: {error}"]
    problems = []
    used = terminals(syntax)
    keyword_rule = lexical.get("keyword")
    punctuator_rule = lexical.get("punctuator")
    if keyword_rule is None or punctuator_rule is None:
        return ["the lexical grammar needs 'keyword' and 'punctuator' rules"]
    written_keywords = {n.text for n in walk(keyword_rule) if isinstance(n, Lit)}
    if written_keywords != set(keywords):
        problems.append(
            f"keyword rule differs from lexer: {written_keywords ^ set(keywords)}")
    written_punctuators = {n.text for n in walk(punctuator_rule) if isinstance(n, Lit)}
    lexer_punctuators = set(one) | set(two) | set(three)
    if written_punctuators != lexer_punctuators:
        problems.append(
            f"punctuator rule differs from lexer: {written_punctuators ^ lexer_punctuators}")
    for text in sorted(set(keywords) | lexer_punctuators):
        if text not in used:
            problems.append(f"token {text!r} never appears in the syntactic grammar")
    allowed_names = {"_", *FIXED_TYPE_NAMES}
    for text in sorted(used):
        if text in keywords or text in lexer_punctuators or text in allowed_names:
            continue
        problems.append(f"grammar terminal {text!r} is not a lexer token")
    mapped = set(keywords.values()) | set(one.values()) | set(two.values()) | set(three.values())
    for member in token_enum:
        if member not in mapped and member.name not in (*TOKEN_RULES, "EOF"):
            problems.append(f"token kind {member.name} has no spelling in the grammar")
    match = re.search(r"The reserved words are:\s+```text\n(.*?)```", spec, re.DOTALL)
    if match is None:
        problems.append("the reserved word list is missing")
    elif set(match.group(1).split()) != set(keywords):
        problems.append("the reserved word list differs from the lexer")
    return problems


FIXED_TYPE_NAMES = (
    "i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64", "f32", "f64",
    "c_char", "c_uchar", "c_short", "c_ushort", "c_int", "c_uint",
    "c_long", "c_ulong", "c_size",
)


# ----- the tests --------------------------------------------------------------

def test_lexical_grammar_matches_the_lexer():
    assert check_lexical(SPEC_TEXT) == []


def test_syntax_grammar_matches_the_parser():
    assert check_syntax(SPEC_TEXT) == []


def test_tokens_keywords_and_grammar_agree():
    assert check_tokens(SPEC_TEXT) == []


def test_grammar_rule_inventory_is_substantial():
    syntax = parse_ebnf(grammar_text(SPEC_TEXT, "syntax"))
    lexical = parse_ebnf(grammar_text(SPEC_TEXT, "lexical"))
    assert len(syntax) >= 60
    assert len(lexical) >= 20


def test_constraints_named_by_the_parser_are_written_in_the_spec():
    for phrase in ("call target", "assignment target"):
        assert phrase in SPEC_TEXT.lower()


@pytest.mark.parametrize("source", [
    "fn f(a: i64,) {}",                       # no trailing comma in parameters
    "fn f() { g(1,); }",                      # or in arguments
    "fn f<T,>() {}",                          # or in generic parameters
    "fn f() { let x: V<i64,> = y; }",         # or in type arguments
    "module a; module b;",                    # module only once
    "fn f() {} module a;",                    # and only first
    "pub struct S {}",                        # pub applies to functions
    "pub let a = 1;",
    "resource fn f() {}",                     # resource applies to structs
    "extern fn f<T>();",                      # extern is never generic
    "extern fn f(a: i64, ...);",              # no variadics
    "extern let a: i64;",                     # no extern data
    "trait T {}",                             # no traits
    "fn f() { (a,); }",                       # tuples have at least two elements
    "fn f(a: (i64,)) {}",
    "fn f() { []; }",                         # no empty array literal
    "fn f() { let a = [1; n]; }",             # repeat counts are literals
    "fn f(a: [i64; n]) {}",                   # as are array sizes
    "fn f() { if P { x: 1 }.x == 1 {} }",     # struct literal in a condition
    "fn f() { while g(P { x: 1 }) {} }",      # including inside call arguments
    "fn f() { match P { x: 1 } {} }",
    "fn f() { for i in P { x: 1 }.x..2 {} }",
    "fn f() { let a: i64<u8> = 1; }",         # scalars take no type arguments
    "fn f(a: fn(i64)) {}",                    # function types need a result
    "fn f() { x as T < 3; }",                 # '<' after a named type opens arguments
    "fn f() { defer (x = 2); }",              # assignment is a statement
    "fn f() { let a = 1 }",                   # semicolons are mandatory
    "fn f() { if a { } else b; }",            # else needs an if or a block
    "fn f() { asm(1); }",                     # asm takes one string
    "fn f() { _ + 1 => {}; }",
    "fn f() { let x: A<B<i64>>= y; }",       # '>>=' is not split
])
def test_boundary_programs_are_rejected_by_both(source):
    grammar = SyntaxGrammar(SPEC_TEXT)
    assert parser_verdict(source) == "reject"
    assert grammar.accepts(lex(source)) is False


@pytest.mark.parametrize("source,message", [
    ("fn f() { g()(1); }", "only named functions can be called"),
    ("fn f() { a[0](1); }", "only named functions can be called"),
    ("fn f() { (a + b)<i64>(1); }", "only named functions can have type arguments"),
    ("fn f() { 1 = 2; }", "invalid assignment target"),
    ("fn f() { g() = 2; }", "invalid assignment target"),
    ("fn f() { a + b = 2; }", "invalid assignment target"),
])
def test_written_constraints_reject_what_the_grammar_derives(source, message):
    assert message in str(_parse_error(source))
    assert SyntaxGrammar(SPEC_TEXT).accepts(lex(source)) is True


def _parse_error(source):
    with pytest.raises(MortError) as caught:
        Parser(Lexer(source).tokenize()).parse()
    return caught.value


# ----- the harness itself must notice one-sided changes -----------------------

@pytest.mark.parametrize("old,new", [
    ('                | loop-stmt', ''),                        # spec narrows
    ('"pub", fn-decl', '"pub", ( fn-decl | struct-decl )'),     # spec widens
    ('"{", { statement }, "}"', '"{", { statement, [ ";" ] }, "}"'),
    ('"defer", expression', '"defer", [ "move" ], block'),      # spec changes a rule
    ('generic-params = "<"', 'generic-params = "["'),           # spec renames a token
])
def test_harness_detects_a_spec_only_grammar_change(old, new):
    assert old in SPEC_TEXT, old
    assert check_syntax(SPEC_TEXT.replace(old, new, 1), mutants=1500)


def test_harness_detects_a_rule_deleted_from_the_spec():
    changed = re.sub(r"^break-stmt\s*=.*?;\n", "", SPEC_TEXT, count=1, flags=re.MULTILINE)
    assert changed != SPEC_TEXT
    assert check_syntax(changed, mutants=0)


class _ParserRequiringReturnValue(Parser):
    def _return_stmt(self):
        line = self._advance().line
        expr = self._expression()
        self._expect(T.SEMI, "';'")
        from mort import mort_ast as A
        return A.Return(expr, line)


class _ParserAllowingEmptyStatements(Parser):
    def _stmt(self):
        if self._at(T.SEMI):
            from mort import mort_ast as A
            return A.Block([], self._advance().line)
        return super()._stmt()


class _ParserAllowingBareAsm(Parser):
    def _asm_stmt(self):
        from mort import mort_ast as A
        line = self._advance().line
        text = self._expect(T.STRING, "an assembly string").value
        self._expect(T.SEMI, "';'")
        return A.Asm(text, line)


@pytest.mark.parametrize("parser_cls", [
    _ParserRequiringReturnValue,     # parser narrows
    _ParserAllowingEmptyStatements,  # parser widens
    _ParserAllowingBareAsm,          # parser changes a rule
])
def test_harness_detects_a_parser_only_change(parser_cls):
    assert check_syntax(SPEC_TEXT, parser_cls=parser_cls)


class _LexerWithLongerSeparators(Lexer):
    def __init__(self, src):
        super().__init__(src.replace("__", "_"))


def test_harness_detects_a_lexer_only_change():
    assert check_lexical(SPEC_TEXT, lexer_cls=_LexerWithLongerSeparators)


def test_harness_detects_a_new_lexer_keyword():
    assert check_tokens(SPEC_TEXT, keywords={**KEYWORDS, "async": T.TEST})


def test_harness_detects_a_new_lexer_punctuator():
    assert check_tokens(SPEC_TEXT, two={**lexer_module._TWO_CHAR, "::": T.COLON})


def test_harness_detects_a_keyword_dropped_from_the_grammar():
    changed = SPEC_TEXT.replace('"loop", block', '"forever", block', 1)
    assert changed != SPEC_TEXT
    assert check_tokens(changed)


# ----- specification examples --------------------------------------------------

_CC = mortc.find_c_compiler()


@dataclass
class Example:
    line: int
    mode: str
    source: str
    expected: object
    attrs: dict


def collect_examples(spec=SPEC_TEXT):
    blocks = fenced_blocks(spec)
    examples = []
    for index, (info, body, line) in enumerate(blocks):
        if not info.startswith("mort"):
            continue
        words = shlex.split(info, posix=True)
        if words[0] != "mort":
            continue
        attrs = dict(word.split("=", 1) for word in words[1:] if "=" in word)
        flags = {word for word in words[1:] if "=" not in word}
        mode = ("run" if "run" in flags else "check" if "check" in flags
                else "reject" if "reject" in attrs else None)
        if mode is None:
            raise AssertionError(f"line {line}: example needs run, check or reject=")
        expected = None
        if mode == "run":
            following = blocks[index + 1] if index + 1 < len(blocks) else (None, None, 0)
            if following[0] != "output":
                raise AssertionError(f"line {line}: run example needs an output block")
            expected = following[1] if following[1] != "\n" else ""
        attrs["freestanding"] = "freestanding" in flags
        examples.append(Example(line, mode, body, expected, attrs))
    return examples


EXAMPLES = collect_examples()


def test_examples_cover_every_mode():
    modes = {example.mode for example in EXAMPLES}
    assert modes == {"run", "check", "reject"}
    assert sum(example.mode == "run" for example in EXAMPLES) >= 30


def _compile_example(example, directory):
    path = Path(directory) / "example.mx"
    path.write_text(example.source, encoding="utf-8")
    return mortc.compile_files_to_c(
        [str(path)], freestanding=example.attrs["freestanding"])


@pytest.mark.parametrize(
    "example", EXAMPLES, ids=[f"line{e.line}-{e.mode}" for e in EXAMPLES])
def test_specification_example(example):
    with tempfile.TemporaryDirectory() as directory:
        if example.mode == "reject":
            with pytest.raises(MortError) as caught:
                _compile_example(example, directory)
            assert example.attrs["reject"] in str(caught.value)
            return
        c_source = _compile_example(example, directory)
        if example.mode == "check":
            return
        if _CC is None:
            pytest.skip("no C compiler on PATH")
        c_path = os.path.join(directory, "example.c")
        Path(c_path).write_text(c_source, encoding="utf-8")
        executable = os.path.join(
            directory, "example.exe" if os.name == "nt" else "example")
        subprocess.run(
            [*_CC, c_path, "-o", executable, "-O1", "-std=c11", "-w"], check=True)
        result = subprocess.run(
            [executable], capture_output=True, text=True, check=False, timeout=60)
        assert result.stdout == example.expected
        assert result.returncode == int(example.attrs.get("exit", "0"))
        if "stderr" in example.attrs:
            assert example.attrs["stderr"] in result.stderr


# ----- disagreements resolved in the compiler ----------------------------------

def _check_program(source):
    return mortc.compile_to_c(source)


def _run_program(source):
    if _CC is None:
        pytest.skip("no C compiler on PATH")
    with tempfile.TemporaryDirectory() as directory:
        c_path = os.path.join(directory, "program.c")
        Path(c_path).write_text(mortc.compile_to_c(source), encoding="utf-8")
        executable = os.path.join(
            directory, "program.exe" if os.name == "nt" else "program")
        subprocess.run(
            [*_CC, c_path, "-o", executable, "-O1", "-std=c11", "-w"], check=True)
        return subprocess.run(
            [executable], capture_output=True, text=True, check=False, timeout=60)


def test_nested_generic_struct_literal_closes_with_a_shift_token():
    assert _run_program(
        "struct B<T> { v: T } fn main() -> int {"
        " let a = B<B<i64>> { v: B<i64> { v: 7 } }; return a.v.v; }"
    ).returncode == 7


def test_comparison_before_a_block_is_not_read_as_type_arguments():
    _check_program(
        "fn main() -> int { let a = 1; let b = 2; let c = 3; let d = 4;"
        " if a < b { if c > (d) { return 1; } } return 0; }")


RESOURCE = "resource struct R { v: i64 } fn destroy(r: *R) -> void { print((*r).v); } "


@pytest.mark.parametrize("body", [
    "struct H { r: R } fn main() -> int { let h = H { r: R { v: 1 } }; let a = h.r; return 0; }",
    ("struct H { r: R } fn f(r: R) -> void {} "
    "fn main() -> int { let h = H { r: R { v: 1 } }; f(h.r); return 0; }"),
    "fn main() -> int { let t = (R { v: 1 }, 2); let a = t.0; return 0; }",
    "fn main() -> int { let t = [R { v: 1 }, R { v: 2 }]; let a = t[0]; return 0; }",
])
def test_a_resource_cannot_be_copied_out_of_its_owner(body):
    with pytest.raises(MortError, match="cannot be copied out of"):
        _check_program(RESOURCE + body)


def test_reading_plain_fields_and_raw_storage_of_a_resource_stays_legal():
    _check_program(
        RESOURCE
        + "struct H { r: R, n: i64 } fn main() -> int {"
          " let h = H { r: R { v: 1 }, n: 2 }; let n = h.n; print(n);"
          " let p = alloc(8) as *R; let q = &h.r; let s = p; free(p as *void); return 0; }")


@pytest.mark.parametrize("program,expected", [
    (("fn h(x: i64) -> i64 { return x + 1; }"
     " fn g(f: fn(i64) -> i64, x: i64) -> i64 { return f(x); }"
     " fn main() -> int { let p: fn(fn(i64) -> i64, i64) -> i64 = g; print(p(h, 2)); return 0; }"),
     "3\n"),
    (("fn h() -> i64 { return 4; }"
     " fn main() -> int { let t: (fn() -> i64, i64) = (h, 2); let f = t.0;"
     " print(f()); return 0; }"),
     "4\n"),
    (("struct P<A, B> { a: A, b: B } fn h() -> i64 { return 5; }"
     " fn main() -> int { let t = P<fn() -> i64, i64> { a: h, b: 2 }; let f = t.a;"
     " print(f()); return 0; }"),
     "5\n"),
    (("enum E { A(fn() -> i64, i64), B } fn h() -> i64 { return 6; }"
     " fn main() -> int { let e = E.A(h, 2);"
     " match e { E.A(f, x) => { print(f()); } E.B => {} } return 0; }"),
     "6\n"),
])
def test_function_types_followed_by_another_type_in_a_list(program, expected):
    assert _run_program(program).stdout == expected


@pytest.mark.parametrize("program,expected", [
    (("struct B<T> { v: T } fn main() -> int {"
      " let b = B<(i64, i64)> { v: (3, 4) }; print(b.v.0 + b.v.1); return 0; }"),
     "7\n"),
    (("struct B<T, U> { v: T, w: U } fn main() -> int {"
      " let b = B<(i64, (i64, i64)), i64> { v: (1, (2, 3)), w: 4 };"
      " print(b.v.1.1 + b.w); return 0; }"),
     "7\n"),
    (("enum E<T> { N, S(T) } fn add(a: i64, b: i64) -> i64 { return a + b; }"
      " fn main() -> int { let e = E<fn(i64, i64) -> i64>.S(add);"
      " match e { E<fn(i64, i64) -> i64>.S(f) => { print(f(5, 6)); }"
      " E<fn(i64, i64) -> i64>.N => {} } return 0; }"),
     "11\n"),
    (("enum E<T> { N, S(T) } fn main() -> int {"
      " let e = E<fn(i64, i64) -> i64>.N;"
      " match e { E<fn(i64, i64) -> i64>.S(f) => { print(1); }"
      " E<fn(i64, i64) -> i64>.N => { print(2); } } return 0; }"),
     "2\n"),
    (("enum E<T, U> { N, S(T, U) } fn main() -> int {"
      " let e = E<(i64, i64), fn(i64, i64) -> i64>.N;"
      " match e { E<(i64, i64), fn(i64, i64) -> i64>.S(a, f) => {}"
      " E<(i64, i64), fn(i64, i64) -> i64>.N => { print(9); } } return 0; }"),
     "9\n"),
])
def test_a_comma_inside_parentheses_does_not_split_type_arguments(program, expected):
    assert _run_program(program).stdout == expected


def test_sizeof_accepts_array_types():
    assert _run_program(
        "type A = [u16; 4]; type B = [[u8; 3]; 2];"
        " fn main() -> int { print(sizeof<A>() as i64); print(sizeof<B>() as i64); return 0; }"
    ).stdout == "8\n6\n"


@pytest.mark.parametrize("body", [
    "fn main() -> int { let x: B = null; return 0; }",
    "struct S { f: B } fn main() -> int { return 0; }",
    "fn f(x: B) -> void {} fn main() -> int { return 0; }",
    "fn f() -> *B { return null; } fn main() -> int { return 0; }",
])
def test_an_alias_of_nested_arrays_is_only_measurable(body):
    with pytest.raises(MortError):
        _check_program("type B = [[u8; 3]; 2]; " + body)


def test_a_repeated_enum_variant_arm_is_rejected():
    with pytest.raises(MortError, match="more than once"):
        _check_program(
            "enum E { A, B } fn main() -> int {"
            " match E.A { E.A => {} E.A => {} E.B => {} } return 0; }")


def test_a_repeated_payload_variant_arm_is_rejected():
    with pytest.raises(MortError, match="more than once"):
        _check_program(
            "enum E { A(i64), B } fn main() -> int {"
            " match E.B { E.A(x) => {} E.A(y) => {} E.B => {} } return 0; }")


@pytest.mark.parametrize("body", [
    "let a = [1, 2]; if a == a {}",
    "let a = [1, 2]; let s = slice(&a[0], 2); if s != s {}",
])
def test_array_and_slice_equality_are_rejected(body):
    with pytest.raises(MortError, match="cannot compare aggregate"):
        _check_program("fn main() -> int { " + body + " return 0; }")


# ----- driver statuses and the freestanding failure path (section 22) ---------

def _driver(*arguments):
    return subprocess.run(
        [sys.executable, str(ROOT / "mortc.py"), *arguments],
        capture_output=True, text=True, check=False, timeout=120)


def test_driver_exit_statuses(tmp_path):
    good = tmp_path / "good.mx"
    bad = tmp_path / "bad.mx"
    unused = tmp_path / "unused.mx"
    good.write_text("fn main() -> int { return 0; }\n", encoding="utf-8")
    bad.write_text("fn main() -> int { return nope; }\n", encoding="utf-8")
    unused.write_text("fn main() -> int { let a = 1; return 0; }\n", encoding="utf-8")
    assert _driver("--check", str(good)).returncode == 0
    assert _driver("--check", str(unused)).returncode == 0
    failed = _driver("--check", str(bad))
    assert failed.returncode == 1
    assert "nope" in failed.stderr
    assert _driver("--check", "--deny-warnings", str(unused)).returncode == 1


def test_a_failed_assertion_in_a_test_block_ends_with_status_one(tmp_path):
    source = tmp_path / "suite.mx"
    source.write_text('test "fails" { assert(false); }\n', encoding="utf-8")
    assert _driver("test", str(source)).returncode == 1


def test_freestanding_arithmetic_failures_trap_and_indexing_is_unchecked():
    c_source = mortc.compile_to_c(
        "fn f(a: i64, b: i64) -> i64 { return a / b; }"
        " fn g(s: []i64, i: u64) -> i64 { return s[i]; }",
        freestanding=True)
    assert "__builtin_trap" in c_source
    assert "mort_bounds" not in c_source


def test_a_function_value_can_be_a_match_subject():
    assert _run_program(
        "fn g() -> void {} fn h() -> void {} fn main() -> int { let p = g;"
        " match p { h => { print(1); } g => { print(2); } _ => { print(3); } } return 0; }"
    ).stdout == "2\n"


def test_a_balanced_angle_before_a_paren_is_read_as_type_arguments():
    program = Parser(Lexer("fn f() { g(a < b, c > (d)); }").tokenize()).parse()
    outer = program.funcs[0].body.stmts[0].expr
    inner = outer.args[0]
    assert (outer.name, len(outer.args)) == ("g", 1)
    assert (inner.name, inner.type_args) == ("a", ["b", "c"])


# ----- findings of the independent review --------------------------------------

_OWNER = (
    "resource struct R { v: i64 } fn destroy(r: *R) -> void { print((*r).v); } "
    "struct O { items: *R, list: []R, r: R } "
)


@pytest.mark.parametrize("read", ["o.items[0]", "o.list[0]", "o.list.data[0]"])
def test_raw_storage_reached_through_an_owner_is_not_a_copy_of_it(read):
    _check_program(
        _OWNER + "fn f(o: O) -> void { let a = " + read + "; } fn main() -> int { return 0; }")


def test_raw_storage_of_a_vec_of_resources_stays_readable(tmp_path):
    source = tmp_path / "program.mx"
    source.write_text(
        "import std.vec; resource struct R { v: i64 }"
        " fn destroy(r: *R) -> void {}"
        " fn main() -> int { let v = vec.new<R>(); let a = v.data[0]; return 0; }\n",
        encoding="utf-8")
    mortc.compile_files_to_c([str(source)])


def test_the_owners_own_field_is_still_a_copy():
    with pytest.raises(MortError, match="cannot be copied out of"):
        _check_program(_OWNER + "fn f(o: O) -> void { let a = o.r; } fn main() -> int { return 0; }")


_REPEATABLE = (
    "resource struct R { v: i64 } fn destroy(r: *R) -> void { print((*r).v); } "
    "struct H { r: R } fn eat(r: R) -> i64 { return 1; } "
)


@pytest.mark.parametrize("body", [
    "let a = R { v: 1 }; let b = [a; 2];",
    "let h = H { r: R { v: 1 } }; let b = [h.r; 2];",
    "let a = R { v: 1 }; let b = [move a; 2];",
    "let a = R { v: 1 }; let b = [H { r: move a }; 2];",
    "let a = R { v: 1 }; let b = [eat(move a); 2];",
    "let a = R { v: 1 }; let b: [R; 2] = [move a; 2];",
])
def test_an_array_repeat_cannot_duplicate_one_resource(body):
    with pytest.raises(MortError, match="must be transferred|cannot be copied out of|array repeat"):
        _check_program(_REPEATABLE + "fn main() -> int { " + body + " return 0; }")


def test_an_array_repeat_of_a_fresh_resource_makes_one_per_element():
    assert _run_program(
        _REPEATABLE + "fn make() -> R { return R { v: 7 }; }"
        " fn main() -> int { let b = [make(); 2]; return 0; }"
    ).stdout == "7\n7\n"


def test_a_copyable_array_repeat_may_still_use_move():
    _check_program("fn main() -> int { let a = 1; let b = [move a; 2]; return 0; }")


def test_command_line_usage_errors_exit_with_status_two():
    assert _driver("--no-such-option").returncode == 2


@pytest.mark.parametrize("pattern", [
    r"\| 2 \|[^\n]*command line",
    r"`free\(null\)` is a compile-time error",
    r"parameters share the function body's outermost block",
    r"`\.len`[^\n]*`u64`",
    r"`\.data`",
    r"once per element",
])
def test_the_specification_states_the_reviewed_rule(pattern):
    assert re.search(pattern, SPEC_TEXT), pattern


# These parse-only cases hold the required behaviour: every lookahead counts '>>'
# as two closes and stops at a brace, whichever way it is implemented.
@pytest.mark.parametrize("source", [
    "fn f() { let a = B<B<i64>> { v: 1 }; }",
    "fn f() { let a = A<B<C<i64>>> { v: 1 }; }",
    "fn f() { let a = E<F<i64>>.V(1); g<H<i64>>(1); }",
    "fn f() { if a < b { if c > (d) {} } }",
    "fn f() { if a < b { g(); } else if c > (d) {} }",
])
def test_generic_lookaheads_required_behaviour(source):
    assert parser_verdict(source) == "accept"
    assert SyntaxGrammar(SPEC_TEXT).accepts(lex(source)) is True


# ----- one-sided changes the review showed the first harness missed ------------

def _problems(spec=SPEC_TEXT, parser_cls=Parser, lexer_cls=Lexer):
    found = list(check_tokens(spec))
    found += check_lexical(spec, lexer_cls=lexer_cls)
    found += check_syntax(spec, parser_cls=parser_cls, mutants=800)
    for detector in FOCUSED_DETECTORS:
        found += detector(spec, parser_cls, lexer_cls)
    return found


FOCUSED_DETECTORS = []

_SPEC_ONLY_CHANGES = {
    "index": ('index = "[", expression, "]" ;', 'index = "[", additive, "]" ;'),
    "call-args": ('call-args = "(", [ expression,', 'call-args = "(", [ primary,'),
    "pattern": ('match-pattern = "_" | expression ;', 'match-pattern = "_" | literal ;'),
    "tuple": ('tuple-type = "(", type, ",", type, [ ",", [ type-list ] ], ")" ;',
              'tuple-type = "(", type, ",", type, ")" ;'),
    "slice": ('"]", [ "const" ], type', '"]", type'),
    "field": ('field-access = ".", ( IDENT | INT ) ;', 'field-access = ".", IDENT ;'),
    "ladder": ('additive = multiply, { ( "+" | "-" ), multiply } ;',
               'additive = multiply, { ( "*" | "-" ), multiply } ;'),
    "table": ("| 9 | `+` `-` | left |", "| 9 | `-` `+` `*` | left |"),
    "prose": ("`>=` token it consumes the `>` and leaves a `=`",
              "`>=` token it consumes the `>` and leaves nothing"),
}


@pytest.mark.parametrize("name", sorted(_SPEC_ONLY_CHANGES))
def test_harness_detects_each_reviewed_spec_change(name):
    old, new = _SPEC_ONLY_CHANGES[name]
    assert old in SPEC_TEXT, old
    assert _problems(spec=SPEC_TEXT.replace(old, new, 1))


class _ParserLetWithoutInitializer(Parser):
    def _let_stmt(self, mutable=True):
        from mort import mort_ast as A
        if self._peek(2).type == T.SEMI:
            line = self._advance().line
            name = self._expect(T.IDENT, "name").value
            self._expect(T.SEMI, "';'")
            return A.Let(name, None, A.IntLit(0, line), line, mutable=mutable)
        return super()._let_stmt(mutable)


class _ParserNarrowPatterns(Parser):
    def _match_stmt(self):
        original = self._expression
        depth = {"n": 0}

        def narrowed():
            depth["n"] += 1
            try:
                return self._postfix() if depth["n"] > 1 else original()
            finally:
                depth["n"] -= 1
        self._expression = narrowed
        try:
            return super()._match_stmt()
        finally:
            self._expression = original


class _ParserFieldInitEquals(Parser):
    def _struct_lit(self):
        for offset in range(len(self.toks) - self.i):
            token = self._peek(offset)
            if token.type == T.ASSIGN:
                token.type = T.COLON
            if token.type in (T.RBRACE, T.SEMI):
                break
        return super()._struct_lit()


class _ParserSwappedPrecedence(Parser):
    def _term(self):
        from mort import mort_ast as A
        left = self._factor()
        while self._peek().type in (T.STAR, T.SLASH, T.PERCENT):
            op = self._advance()
            left = A.Binary(op.value, left, self._factor(), op.line)
        return left

    def _factor(self):
        from mort import mort_ast as A
        left = self._cast()
        while self._peek().type in (T.PLUS, T.MINUS):
            op = self._advance()
            left = A.Binary(op.value, left, self._cast(), op.line)
        return left


@pytest.mark.parametrize("parser_cls", [
    _ParserLetWithoutInitializer, _ParserNarrowPatterns,
    _ParserFieldInitEquals, _ParserSwappedPrecedence,
])
def test_harness_detects_each_reviewed_parser_change(parser_cls):
    assert _problems(parser_cls=parser_cls)


class _LexerFlatComments(Lexer):
    def _block_comment(self):
        line, col = self.line, self.col
        self._advance()
        self._advance()
        while not (self._peek() == "*" and self._peek(1) == "/"):
            if self.i >= len(self.src):
                raise MortError("unterminated block comment", line, col)
            self._advance()
        self._advance()
        self._advance()


class _LexerDollarLetters(Lexer):
    def __init__(self, src):
        super().__init__(src.replace("$", "d"))


@pytest.mark.parametrize("lexer_cls", [_LexerFlatComments, _LexerDollarLetters])
def test_harness_detects_each_reviewed_lexer_change(lexer_cls):
    assert _problems(lexer_cls=lexer_cls)


# ----- frozen, parser-validated corpus -----------------------------------------
# Generated sentences come from the spec, so a spec that loses a form just
# generates fewer of them. These files were validated against the parser and are
# independent of the grammar text: a form the spec stops deriving is a failure.

ACCEPTED_FORMS = ROOT / "tests" / "spec_accepted_forms.txt"
REJECTED_FORMS = ROOT / "tests" / "spec_rejected_forms.txt"


def read_forms(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line for line in lines if line.strip() and not line.startswith("//")]


def check_forms(spec, parser_cls=Parser, lexer_cls=Lexer):
    problems = []
    try:
        grammar = SyntaxGrammar(spec)
    except EbnfError as error:
        return [str(error)]
    for source in read_forms(ACCEPTED_FORMS):
        if parser_verdict(source, parser_cls) != "accept":
            problems.append(f"parser no longer accepts frozen form: {source}")
        if not grammar.accepts(lex(source)):
            problems.append(f"grammar no longer derives frozen form: {source}")
    for source in read_forms(REJECTED_FORMS):
        if parser_verdict(source, parser_cls) != "reject":
            problems.append(f"parser no longer rejects frozen form: {source}")
        if grammar.accepts(lex(source)):
            problems.append(f"grammar no longer rejects frozen form: {source}")
    return problems


def check_precedence(spec, parser_cls=Parser, lexer_cls=Lexer):
    """The table in section 3.5, the grammar's ladder and the parser must agree."""
    match = re.search(r"### 3\.5 Precedence\n(.*?)\n###? ", spec, re.DOTALL)
    if match is None:
        return ["section 3.5 is missing"]
    table = {}
    for level, cells in re.findall(r"^\| (\d+) \| (.+?) \| \w+ \|$", match.group(1), re.MULTILINE):
        table[int(level)] = re.findall(r"`([^`]+)`", cells.replace("\\|", "|"))
    try:
        rules = parse_ebnf(grammar_text(spec, "syntax"))
    except EbnfError as error:
        return [str(error)]
    problems = []
    ladder = []
    name = "logical-or"
    while name != "cast":
        body = rules.get(name)
        if not (isinstance(body, Seq) and len(body.items) == 2 and isinstance(body.items[1], Rep)):
            return [f"rule {name} is not a precedence level"]
        repeated = body.items[1].inner
        ladder.append(sorted(n.text for n in walk(repeated.items[0]) if isinstance(n, Lit)))
        name = body.items[0].name
    for index, operators in enumerate(ladder, start=1):
        if sorted(table.get(index, [])) != operators:
            problems.append(f"level {index}: table {table.get(index)} but grammar {operators}")
    prefix = sorted(n.text for n in walk(rules["unary-op"]) if isinstance(n, Lit))
    if table.get(11) != ["as"]:
        problems.append("level 11 must be the cast")
    if sorted(table.get(12, [])) != prefix:
        problems.append(f"prefix operators: table {table.get(12)} but grammar {prefix}")

    def parse(text):
        return parser_cls(Lexer(f"let v = {text};").tokenize()).parse().globals[0].expr

    levels = {op: level for level, ops in table.items() if level <= len(ladder) for op in ops}
    for first, first_level in levels.items():
        for second, second_level in levels.items():
            try:
                tree = parse(f"x {first} y {second} z")
            except MortError as error:
                problems.append(f"x {first} y {second} z does not parse: {error}")
                continue
            groups_left = first_level >= second_level
            top = second if groups_left else first
            if getattr(tree, "op", None) != top:
                problems.append(f"x {first} y {second} z groups with {tree.op}, table says {top}")
    for text, kind in (("- x as i64", "Cast"), ("x as i64 * y", "Binary"), ("- x . y", "Unary"),
                       ("* x [ y ]", "Unary"), ("! f ( )", "Unary"), ("x as i64 as u8", "Cast")):
        if type(parse(text)).__name__ != kind:
            problems.append(f"{text} is not a {kind} at the top")
    if type(parse("- x as i64").expr).__name__ != "Unary":
        problems.append("a cast applies to the preceding unary expression")
    return problems


LEXICAL_FORMS = {
    "tuple-index": (["0", "7", "09"], ["1_0", "1a"]),
    "trivia": (["", " \t\r\n", "//", "// c", "// c\n", "/* c */", "/* /* n */ */", "/**/",
                "/* * / */", " /**/ // x\n", "/* a /* b /* c */ d */ e */"],
               ["/*", "/* /* */", "/* */ */", "*/", "/"]),
    "IDENT": (["a", "_", "a1", "_1", "a_b", "letx", "\u00e9", "\u65e5\u672c"],
              ["1a", "a$", "$a", "a-b", "a b", "@a", "#a", "`a", "a?"]),
    "INT": (["0", "7", "09", "1_000", "0x1F", "0XaF", "0x1_F", "0o7", "0O7", "0o1_7", "0b1", "0B0",
             "0b1_0"],
            ["1_", "1__2", "0x", "0x_1", "0x1_", "0o", "0o8", "0b", "0b2", "12a", "1.5"]),
    "FLOAT": (["1.5", "1.5e3", "1e3", "1E3", "1e+3", "1e-3", "1_0.5", "1_0e3", "1.5E-2"],
              ["1.", ".5", "1e", "1e+", "1.5_0", "1.5e3x", "1_.5"]),
    "CHAR": (["'a'", "'\\n'", "'\\r'", "'\\t'", "'\\0'", "'\\\\'", "'\\''", "'\\\"'", "'\\x41'", "'\"'"],
             ["''", "'ab'", "'\\q'", "'\\x4'", "'\\xZZ'", "'a", "'\u20ac'"]),
    "STRING": (['""', '"a"', '"\\n"', '"\\""', '"\\\\"', '"\'"', '"\u00e9"'],
               ['"', '"a', '"\\"']),
}


# Beyond this many iterations a repetition counts as exercised. The check below
# narrows every repetition to this bound, so any smaller limit is rejected too.
REPETITION_BOUND = 8
_LONG = REPETITION_BOUND + 4
_DIGITS = "0123456789"
_HEX_DIGITS = _DIGITS + "abcdefABCDEF"
_ASCII_LETTERS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
# Every ASCII character, then samples of what the Unicode-wide classes also hold.
CHARACTER_SAMPLES = [chr(code) for code in range(0x80)] + list(
    "\u0080 ª²µ×éÿĀ٣€日\U0001f600")


def _exhaustive_lexical_forms():
    """Tokens written from the lexer's character sets, independent of the grammar."""
    letters = [c for c in CHARACTER_SAMPLES if c.isalpha() or c == "_"]
    continuing = [c for c in CHARACTER_SAMPLES if c.isalnum() or c == "_"]
    latin1 = [c for c in CHARACTER_SAMPLES if ord(c) <= 255 and c not in "'\\\n\0"]
    string = [c for c in CHARACTER_SAMPLES if c not in '"\\\n\0']
    forms = {
        "tuple-index": [*_DIGITS, *("1" + d for d in _DIGITS), "1" * _LONG],
        "IDENT": [*letters, *("z" + c for c in continuing), "z" * _LONG],
        "INT": [*("0" + p + d for p in "xX" for d in _HEX_DIGITS),
                *("0x0" + d for d in _HEX_DIGITS), *("0x0_" + d for d in _HEX_DIGITS),
                *("0" + p + d for p in "oO" for d in "01234567"),
                *("0o0" + d for d in "01234567"), *("0o0_" + d for d in "01234567"),
                *("0" + p + d for p in "bB" for d in "01"),
                *("0b0" + d for d in "01"), *("0b0_" + d for d in "01"),
                *_DIGITS, *("1" + d for d in _DIGITS), *("1_" + d for d in _DIGITS),
                "0x" + "f" * _LONG, "0o" + "7" * _LONG, "0b" + "1" * _LONG, "1" * _LONG,
                "0x" + "_f".join("f" * _LONG), "0o" + "_7".join("7" * _LONG),
                "0b" + "_1".join("1" * _LONG), "_1".join("1" * _LONG)],
        "FLOAT": [*(d + ".5" for d in _DIGITS), *("1" + d + ".5" for d in _DIGITS),
                  *("1_" + d + ".5" for d in _DIGITS), *("1." + d for d in _DIGITS),
                  *("1.5" + d for d in _DIGITS), *(d + "e1" for d in _DIGITS),
                  *("1e" + d for d in _DIGITS), *("1e0" + d for d in _DIGITS),
                  *("1.5e" + s + d for s in ("", "+", "-") for d in _DIGITS),
                  "1E1", "1.5E1", "1E+1", "1E-1", "1e+1", "1e-1",
                  "1." + "5" * _LONG, "1e" + "0" * _LONG, "1.5e" + "0" * _LONG,
                  "1" * _LONG + ".5", "_1".join("1" * _LONG) + ".5",
                  "1" * _LONG + "e1"],
        "CHAR": [*(f"'{c}'" for c in latin1),
                 *(f"'\\{e}'" for e in ("n", "r", "t", "0", "\\", "'", '"')),
                 *(f"'\\x{d}0'" for d in _HEX_DIGITS), *(f"'\\x0{d}'" for d in _HEX_DIGITS)],
        "STRING": [*(f'"{c}"' for c in string),
                   *(f'"\\{c}"' for c in CHARACTER_SAMPLES if c != "\0"),
                   '"' + "s" * _LONG + '"', '"' + "\\n" * _LONG + '"'],
        "trivia": [*(f"//{c}z" for c in CHARACTER_SAMPLES if c != "\n"),
                   *(f"/* {c} */" for c in CHARACTER_SAMPLES),
                   " " * _LONG, "\t" * _LONG, "\r" * _LONG, "\n" * _LONG,
                   "//" + "c" * _LONG, "/*" + "c" * _LONG + "*/",
                   "/*" * _LONG + "*/" * _LONG, "/**/" * _LONG, "//\n" * _LONG],
    }
    return forms


for _name, _accepted in _exhaustive_lexical_forms().items():
    LEXICAL_FORMS[_name][0].extend(_accepted)


def check_lexical_forms(spec, parser_cls=Parser, lexer_cls=Lexer):
    try:
        grammar = LexicalGrammar(spec)
    except EbnfError as error:
        return [str(error)]
    problems = []
    for name, (accepted, rejected) in LEXICAL_FORMS.items():
        if name == "trivia":
            lexed = lambda text: is_trivia(text, lexer_cls)
        elif name == "tuple-index":
            lexed = lambda text: is_tuple_index(text, lexer_cls)
        else:
            lexed = lambda text, n=name: single_token(text, lexer_cls) == T[n]
        for text in accepted:
            if not lexed(text):
                problems.append(f"lexer rejects frozen {name} {text!r}")
            if not grammar.full(name, text):
                problems.append(f"grammar rejects frozen {name} {text!r}")
        for text in rejected:
            if lexed(text):
                problems.append(f"lexer accepts frozen bad {name} {text!r}")
            if grammar.full(name, text):
                problems.append(f"grammar accepts frozen bad {name} {text!r}")
    tokens = ("IDENT", "INT", "keyword", "punctuator", "trivia")
    for code in range(0x20, 0x7f):
        for text in (chr(code), "a" + chr(code), chr(code) + "a"):
            whole = any(grammar.full(rule, text) for rule in tokens)
            if whole and lex_with(text, lexer_cls) is None:
                problems.append(f"grammar derives {text!r} but the lexer rejects it")
            if len(text) == 1 and not whole and lex_with(text, lexer_cls) is not None:
                problems.append(f"lexer accepts character {text!r} the grammar lacks")
    return problems


def lex_with(source, lexer_cls):
    try:
        return lexer_cls(source).tokenize()
    except MortError:
        return None


# Every numbered section is normative, and most of their sentences have no other
# executable guard, so all are pinned. The preamble and the appendices are not:
# the preamble states the document's status and the appendices state versioning
# policy and what is left undefined, so neither gives a rule a program can break.
PROSE_SECTIONS = tuple(str(number) for number in range(1, 24))


def prose_digests(spec):
    """A digest of each normative section's prose, with fenced blocks removed."""
    parts = re.split(r"^## (?=\d+\. )", spec, flags=re.MULTILINE)
    digests = {}
    for part in parts[1:]:
        number = part.split(".", 1)[0]
        if number in PROSE_SECTIONS:
            bare = re.sub(r"^```.*?^```$", "", part.split("\n## ")[0], flags=re.DOTALL | re.MULTILINE)
            digests[number] = hashlib.sha256(" ".join(bare.split()).encode()).hexdigest()[:16]
    return digests


# Update a digest only after confirming that the changed sentence still matches the
# implementation. It makes a normative edit a deliberate two-place change.
PROSE_DIGESTS = {
    "1": "35bf33708e8bff26",
    "2": "89b9c0bca995bc3d",
    "3": "08fbd9110264f7e2",
    "4": "972ff7caa597d066",
    "5": "0df028a68b1cef08",
    "6": "8409203f8c899018",
    "7": "395fbd276605eae2",
    "8": "20e068bac71b5af5",
    "9": "64bf888b11dfb6c5",
    "10": "a969deb1c430de10",
    "11": "ba36daa6139e9ff7",
    "12": "3a67e59abb518ecb",
    "13": "6c9eefbf90deb427",
    "14": "0873ac56ee42692b",
    "15": "cdd693f4e64d26b2",
    "16": "396f635b9b36e536",
    "17": "8f9e052c2d06308d",
    "18": "d83b1e349b0850f8",
    "19": "9a73fd9c2ae1d25a",
    "20": "3253b0571e3bacde",
    "21": "37d8b13040ddf4ca",
    "22": "ecedf07a78cb0402",
    "23": "5c1c7224d336be0f",
}


def check_prose(spec, parser_cls=Parser, lexer_cls=Lexer):
    found = prose_digests(spec)
    return [f"section {n} prose changed: re-verify it, then update PROSE_DIGESTS to {found.get(n)}"
            for n in PROSE_SECTIONS if found.get(n) != PROSE_DIGESTS.get(n)]


FOCUSED_DETECTORS[:] = [check_forms, check_precedence, check_prose, check_lexical_forms]


@pytest.mark.parametrize("check", FOCUSED_DETECTORS, ids=lambda f: f.__name__)
def test_focused_checks_hold(check):
    assert check(SPEC_TEXT) == []


def test_exhaustive_edits_of_the_witnesses_get_the_same_verdict():
    grammar = SyntaxGrammar(SPEC_TEXT)
    problems = []
    pool = [",", ";", ":", "=", "(", ")", "{", "}", "[", "]", "<", ">", ".", "..", "->", "=>",
            "*", "&", "1", "a", "_", "let", "as", "const"]
    for form in WITNESSES:
        words = form.split(" ")
        variants = []
        for start in range(len(words)):
            variants += [words[:start] + words[start + span:] for span in (1, 2, 3)]
            variants += [words[:start] + [token] + words[start + 1:] for token in pool]
        for variant in variants:
            source = " ".join(variant)
            tokens = lex(source)
            if tokens is None:
                continue
            verdict = parser_verdict(source)
            if verdict != "constraint" and grammar.accepts(tokens) != (verdict == "accept"):
                problems.append(f"{verdict}: {source}")
    assert problems == []


# ----- every (rule, reference, alternative) triple must be load-bearing ---------

def replace_node(node, target, replacement):
    if node is target:
        return replacement
    if isinstance(node, Seq):
        return Seq([replace_node(item, target, replacement) for item in node.items])
    if isinstance(node, Alt):
        return Alt([replace_node(item, target, replacement) for item in node.options])
    if isinstance(node, Opt):
        return Opt(replace_node(node.inner, target, replacement))
    if isinstance(node, Rep):
        return Rep(replace_node(node.inner, target, replacement))
    return node


NOTHING = Seq([])


def drops(body):
    """Each way to remove one alternative, repetition or option from a rule."""
    for node in walk(body):
        if isinstance(node, Alt):
            for index in range(len(node.options)):
                kept = node.options[:index] + node.options[index + 1:]
                yield f"drop alternative {index}", replace_node(body, node, Alt(kept))
        elif isinstance(node, (Opt, Rep)):
            yield f"remove {type(node).__name__}", replace_node(body, node, NOTHING)


def rule_narrowings(body):
    """Each one-place change to a rule that should make it match less."""
    yield from drops(body)
    for node in walk(body):
        if isinstance(node, Seq):
            for index in range(len(node.items)):
                if not isinstance(node.items[index], Special):
                    yield (f"drop item {index}",
                           replace_node(body, node, Seq(node.items[:index] + node.items[index + 1:])))
        elif isinstance(node, Opt):
            yield "require optional", replace_node(body, node, node.inner)
        elif isinstance(node, Rep):
            yield "repeat at most once", replace_node(body, node, Opt(node.inner))
            yield "repeat at least once", replace_node(body, node, Seq([node.inner, node]))


def head_references(rules, start):
    """Rules and tokens a reference to ``start`` can narrow to at its head."""
    found = []
    frontier = [start]
    while frontier:
        name = frontier.pop()
        for head in _heads(rules[name]):
            if head not in found and head != start:
                found.append(head)
                if head in rules and head not in OPAQUE_HEADS:
                    frontier.append(head)
    return found


def _heads(node):
    if isinstance(node, Ref):
        return [node.name]
    if isinstance(node, Alt):
        return [name for option in node.options for name in _heads(option)]
    if isinstance(node, Seq):
        names = []
        for item in node.items:
            if isinstance(item, Special):
                continue
            names += _heads(item)
            if not isinstance(item, (Opt, Rep)):
                break
        return names
    return []


# Reference targets whose alternatives are covered once at rule level. Types inside
# a struct literal or qualifier are checked at their own reference sites.
SITE_LEAVES = {"scalar-type", "struct-literal", "generic-qualifier"}
OPAQUE_HEADS = {"struct-literal", "generic-qualifier"}


def triples(grammar):
    """(label, overrides, site, key) for each narrowing, key being what must be reached."""
    rules = grammar.rules
    for name, body in rules.items():
        for what, changed in rule_narrowings(body):
            yield f"{name}: {what}", {name: changed}, None, name
        for ref in walk(body):
            if not isinstance(ref, Ref) or ref.name not in rules:
                continue
            target = ref.name
            if target in OPAQUE_HEADS or (name in OPAQUE_HEADS and target == "type"):
                continue  # the predicate fixes the first token, so no alternative differs
            for narrower in head_references(rules, target):
                unit = isinstance(rules[target], Ref) and rules[target].name == narrower
                if not unit:
                    label = f"{name}/{target}: only {narrower}"
                    yield label, {name: replace_node(body, ref, Ref(narrower))}, None, id(ref)
            for inner in [target, *head_references(rules, target)]:
                if inner in rules and inner not in SITE_LEAVES:
                    for what, changed in drops(rules[inner]):
                        yield f"{name}/{target}/{inner}: {what}", None, (id(ref), {inner: changed}), id(ref)


# Narrowings that cannot change what the grammar accepts. A stale entry fails the test.
EQUIVALENT_NARROWINGS = {
    "postfix-op: drop item 2": "the predicate guarantees '(' follows, and the next postfix-op reads it",
    "generic-qualifier: drop item 1": "the predicate guarantees '.' follows, and postfix reads it",
}


def undetected_triples():
    grammar = SyntaxGrammar(SPEC_TEXT)
    forms = [(lex(f), f) for f in read_forms(ACCEPTED_FORMS)]
    rejected = [lex(f) for f in read_forms(REJECTED_FORMS)]
    reaches = {}
    for tokens, _ in forms:
        visited = set()
        assert grammar.accepts(tokens, visited=visited)
        for key in visited:
            reaches.setdefault(key, []).append(tokens)
    for tokens_list in reaches.values():
        tokens_list.sort(key=len)
    missed = []
    total = 0
    for label, overrides, site, key in triples(grammar):
        total += 1
        for changed in [*(overrides or {}).values(), *(site[1].values() if site else ())]:
            grammar.index(changed)
        detected = False
        try:
            for tokens in reaches.get(key, ()):
                if not grammar.accepts(tokens, overrides=overrides, site=site):
                    detected = True
                    break
            if not detected:
                detected = any(grammar.accepts(t, overrides=overrides, site=site) for t in rejected)
        except RecursionError:
            detected = True  # dropping a prefix can leave a left-recursive rule
        if not detected:
            missed.append(label)
    return total, missed


def test_every_rule_reference_alternative_triple_is_load_bearing():
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(limit, 5000))
    try:
        total, missed = undetected_triples()
    finally:
        sys.setrecursionlimit(limit)
    assert total > 1000
    assert set(missed) == set(EQUIVALENT_NARROWINGS), missed[:40]


# ----- every repetition and every character must be load-bearing ----------------
# A triple drops a whole alternative or repetition. It says nothing about a limit
# on how many times a repetition runs, or about one member of a character set.

def at_most(inner, count):
    return Seq([Opt(inner) for _ in range(count)])


def is_recursive(rules, name):
    return any(target == name or name in _rules_under(rules, target)
               for target in referenced({name: rules[name]}) if target in rules)


def recursive_rules(rules):
    return [name for name in rules if is_recursive(rules, name)]


def undetected_repetitions():
    grammar = SyntaxGrammar(SPEC_TEXT)
    forms = [lex(f) for f in read_forms(ACCEPTED_FORMS)]
    forms = sorted((t for t in forms if len(t) > REPETITION_BOUND), key=len, reverse=True)
    total = 0
    missed = []

    def detected(**narrowing):
        return any(not grammar.accepts(tokens, **narrowing) for tokens in forms)

    for name, body in grammar.rules.items():
        for node in walk(body):
            if isinstance(node, Rep):
                total += 1
                changed = replace_node(body, node, at_most(node.inner, REPETITION_BOUND))
                grammar.index(changed)
                if not detected(overrides={name: changed}):
                    missed.append(f"{name}: repeat at most {REPETITION_BOUND}")
    for name in recursive_rules(grammar.rules):
        total += 1
        if not detected(nesting=(name, REPETITION_BOUND)):
            missed.append(f"{name}: nest at most {REPETITION_BOUND}")
    return total, missed


def test_every_repetition_is_exercised_beyond_small_counts():
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(limit, 8000))
    try:
        total, missed = undetected_repetitions()
    finally:
        sys.setrecursionlimit(limit)
    assert total > 40
    assert missed == []


def members(grammar, node):
    """The single characters ``node`` can match, or None if it matches longer text."""
    if isinstance(node, Lit):
        return [node.text] if len(node.text) == 1 else None
    if isinstance(node, Special):
        return [c for c in CHARACTER_SAMPLES if CHAR_CLASSES[node.name](c)]
    if isinstance(node, Ref):
        return members(grammar, grammar.rules[node.name])
    if isinstance(node, Alt):
        found = [members(grammar, option) for option in node.options]
        if any(item is None for item in found):
            return None
        return [c for item in found for c in item]
    if isinstance(node, Except):
        base = members(grammar, node.base)
        return None if base is None else [
            c for c in base if not any(True for _ in grammar.ends(node.excluded, c, 0))]
    return None


def lexical_narrowings(grammar):
    """(label, rule, changed body, characters a detecting token must contain)."""
    token_rules = (*TOKEN_RULES, "tuple-index", "trivia")
    names = {*token_rules}
    for name in token_rules:
        names |= set(_rules_under(grammar.rules, name))
    for name in sorted(names):
        body = grammar.rules[name]
        nodes = list(walk(body))
        excluded = {id(n) for e in nodes if isinstance(e, Except) for n in walk(e.excluded)}
        excepted = excluded | {id(n) for e in nodes if isinstance(e, Except) for n in walk(e.base)}
        for what, changed in drops(body):
            if not any(isinstance(n, Alt) and id(n) in excluded for n in nodes):
                yield f"{name}: {what}", name, changed, ""
        for node in nodes:
            if isinstance(node, Rep):
                yield (f"{name}: repeat at most {REPETITION_BOUND}", name,
                       replace_node(body, node, at_most(node.inner, REPETITION_BOUND)), "")
            if isinstance(node, Alt) or id(node) in excepted or isinstance(node, Lit):
                continue
            for character in members(grammar, node) or ():
                yield (f"{name}: {node!r} without {character!r}", name,
                       replace_node(body, node, Except(node, Lit(character))), character)
        if is_recursive(grammar.rules, name):
            nested = NOTHING
            for _ in range(REPETITION_BOUND):
                nested = _substitute(body, name, nested)
            yield f"{name}: nest at most {REPETITION_BOUND}", name, nested, ""


def _substitute(node, name, replacement):
    if isinstance(node, Ref) and node.name == name:
        return replacement
    if isinstance(node, Seq):
        return Seq([_substitute(item, name, replacement) for item in node.items])
    if isinstance(node, Alt):
        return Alt([_substitute(item, name, replacement) for item in node.options])
    if isinstance(node, Opt):
        return Opt(_substitute(node.inner, name, replacement))
    if isinstance(node, Rep):
        return Rep(_substitute(node.inner, name, replacement))
    return node


def undetected_lexical_narrowings():
    grammar = LexicalGrammar(SPEC_TEXT)
    corpus = [(name, text) for name, (accepted, _) in LEXICAL_FORMS.items() for text in accepted]
    total = 0
    missed = []
    for label, name, changed, character in lexical_narrowings(grammar):
        total += 1
        original = grammar.rules[name]
        grammar.rules[name] = changed
        try:
            caught = any(not grammar.full(rule, text) for rule, text in corpus
                         if character in text)
        finally:
            grammar.rules[name] = original
        if not caught:
            missed.append(label)
    return total, missed


def test_every_character_and_repetition_of_the_lexical_grammar_is_load_bearing():
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(limit, 8000))
    try:
        total, missed = undetected_lexical_narrowings()
    finally:
        sys.setrecursionlimit(limit)
    assert total > 1000
    assert missed == [], (len(missed), missed[:60])


REVIEWED_NARROWINGS = {
    "path": ('path = IDENT, { ".", IDENT } ;',
             'path = IDENT, [ ".", IDENT ], [ ".", IDENT ] ;'),
    "generic-params": ('generic-params = "<", IDENT, { ",", IDENT }, ">" ;',
                       'generic-params = "<", IDENT, [ ",", IDENT ], [ ",", IDENT ], ">" ;'),
    "hex-digit": ('"a" | "b" | "c" | "d" | "e" | "f"', '"a" | "b" | "d" | "e" | "f"'),
    "octal-digit": ('octal-digit = "0" | "1" | "2" | "3" | "4" | "5" | "6" | "7" ;',
                    'octal-digit = "0" | "1" | "2" | "3" | "4" | "6" | "7" ;'),
    "fraction": ('fraction = ".", digit, { digit } ;', 'fraction = ".", digit, [ digit ] ;'),
    "exponent": ('[ "+" | "-" ], digit, { digit } ;', '[ "+" | "-" ], digit, [ digit ] ;'),
    "cast": ('cast = unary, { "as", type } ;', 'cast = unary, [ "as", type ], [ "as", type ] ;'),
    "char-plain": ('char-plain = ? latin1 ? - ( "\'" | "\\" | ? LF ? | ? NUL ? ) ;',
                   'char-plain = ? latin1 ? - ( "\'" | "\\" | ? LF ? | ? NUL ? | "q" ) ;'),
    "ident-continue": ('ident-continue = ? alnum ? | "_" ;',
                       'ident-continue = ( ? alnum ? - "Q" ) | "_" ;'),
    "string-escape": ('string-escape = "\\", ( ? any ? - ? NUL ? ) ;',
                      'string-escape = "\\", ( ? any ? - ( ? NUL ? | "q" ) ) ;'),
}


@pytest.mark.parametrize("name", sorted(REVIEWED_NARROWINGS))
def test_harness_detects_each_repetition_and_character_narrowing(name):
    old, new = REVIEWED_NARROWINGS[name]
    assert old in SPEC_TEXT, old
    changed = SPEC_TEXT.replace(old, new, 1)
    assert check_forms(changed) + check_lexical_forms(changed)


def _section_body(number):
    start = re.search(rf"^## {number}\. .*$", SPEC_TEXT, re.MULTILINE).end()
    following = re.search(r"^## ", SPEC_TEXT[start:], re.MULTILINE)
    return start, start + (following.start() if following else len(SPEC_TEXT) - start)


@pytest.mark.parametrize("number", PROSE_SECTIONS)
def test_harness_detects_a_prose_edit_in_each_numbered_section(number):
    start, end = _section_body(number)
    word = re.compile(r"\b(must|may|is|are|the)\b")
    outside_fences = re.sub(r"^```.*?^```$", lambda m: "\0" * len(m.group()),
                            SPEC_TEXT[start:end], flags=re.DOTALL | re.MULTILINE)
    at = start + word.search(outside_fences).start()
    changed = SPEC_TEXT[:at] + "not " + SPEC_TEXT[at:]
    assert check_prose(changed)
