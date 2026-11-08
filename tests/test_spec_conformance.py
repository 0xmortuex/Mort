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
        return 0 if isinstance(option, Ref) and option.name == "IDENT" else 1

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
            return self.expand(self.rules[node.name], forced)
        return self.leaf(node)

    def sentences(self, name):
        """One expansion per choice in the rule's own body, plus the default."""
        body = self.rules[name]
        variants = [self.expand(body)]
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
        for name in sorted(set(self.rules) - referenced(self.rules) - {"program"}):
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
            if clash and not self._gated(option):
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

    def accepts(self, tokens):
        return Recognizer(self, tokens).run()


class Recognizer:
    """LL(1) reading of the grammar, with the predicates §3.3 defines."""

    def __init__(self, grammar, tokens):
        self.g = grammar
        self.toks = [[t.type, t.value] for t in tokens]
        self.i = 0
        self.no_struct = False

    def run(self):
        try:
            self.rule("program")
        except Reject:
            return False
        return self.toks[self.i][0] == T.EOF

    def rule(self, name):
        saved = self.no_struct
        self.match(self.g.rules[name])
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
            return self.can_start(self.g.rules[node.name])
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
        if any(text in error.message for text in CONSTRAINT_MESSAGES):
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
    "fn f<T>(a: T, b: []u8) -> Vec<Vec<T>> { let x: A<B<i64>>= y; return x; }",
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
        grammar_says = grammar.accepts(tokens)
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
    return len(tokens) == 4 and tokens[2].type == T.INT


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
    pool = "_.xeE09'\"\\+-/*\na"
    for name, verdict in verdicts.items():
        negatives = 0
        for sentence in generator.sentences(name):
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
        assert phrase in SPEC_TEXT


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
    def _number(self, line, col):
        if self._peek() == "9" and self._peek(1) == "_" and self._peek(2) == "_":
            self._advance()
            self._advance()
            self._advance()
            self._add(T.INT, 9, line, col)
            return
        super()._number(line, col)


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
    "struct H { r: R } fn f(r: R) -> void {} "
    "fn main() -> int { let h = H { r: R { v: 1 } }; f(h.r); return 0; }",
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
    ("fn h(x: i64) -> i64 { return x + 1; }"
     " fn g(f: fn(i64) -> i64, x: i64) -> i64 { return f(x); }"
     " fn main() -> int { let p: fn(fn(i64) -> i64, i64) -> i64 = g; print(p(h, 2)); return 0; }",
     "3\n"),
    ("fn h() -> i64 { return 4; }"
     " fn main() -> int { let t: (fn() -> i64, i64) = (h, 2); let f = t.0;"
     " print(f()); return 0; }",
     "4\n"),
    ("struct P<A, B> { a: A, b: B } fn h() -> i64 { return 5; }"
     " fn main() -> int { let t = P<fn() -> i64, i64> { a: h, b: 2 }; let f = t.a;"
     " print(f()); return 0; }",
     "5\n"),
    ("enum E { A(fn() -> i64, i64), B } fn h() -> i64 { return 6; }"
     " fn main() -> int { let e = E.A(h, 2);"
     " match e { E.A(f, x) => { print(f()); } E.B => {} } return 0; }",
     "6\n"),
])
def test_function_types_followed_by_another_type_in_a_list(program, expected):
    assert _run_program(program).stdout == expected
