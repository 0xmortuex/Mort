"""Deterministic grammar/mutation fuzzing for the Mort front end."""
import random
import threading

from .errors import MortError
from .lexer import decode_source

_CORPUS = [
    "fn main() -> int { return 0; }",
    "fn main() -> int { let value: i64 = 42; print(value); return 0; }",
    "struct Pair<T> { left: T, right: T } fn main() -> int { return 0; }",
    ("enum State { Ready, Done } fn main() -> int { let s = State.Ready; "
    "match s { State.Ready => {}, State.Done => {} } return 0; }"),
    ("fn main() -> int { let values: [i64; 3] = [1, 2, 3]; "
    "return values[1] as int; }"),
    ("enum Value { None, Number(i64) } fn main() -> int { "
    "let value = Value.Number(42); match value { Value.None => {}, "
    "Value.Number(number) => { print(number); } } return 0; }"),
]

_ADVERSARIAL = [
    # Parser recursion must become a controlled MortError, never a Python crash.
    "fn main() -> int { return " + "(" * 160 + "1" + ")" * 160 + "; }",
    "/*" * 200 + "x" + "*/" * 200,
    "\x00\x00\x00",
    "fn main() -> int { return 0; }\n" + "}" * 500,
]


# Messages from the compiler's resource limits. These must always say where.
_LIMIT_MESSAGES = (
    "safety limit", "integer literal has", "not valid UTF-8", "array repeat",
    "array size", "does not fit", "constant expression", "generic instantiation",
)
_SIZES = (16, 64, 256, 1024, 8192)
_BINARY_OPERATORS = (
    "+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>",
    "<", ">", "<=", ">=", "==", "!=", "&&", "||",
)


def _in_main(body):
    return (
        "fn main() -> int { let a: i64 = 1; let b: bool = true; "
        f"let t = (1, 2); let s: [i64; 1] = [1]; {body} return 0; }}"
    )


def _huge_literal(rng):
    length = rng.choice((32, 4299, 4300, 4301, 20000))
    digit = rng.choice("123456789")
    decimal = digit * length
    hexadecimal = "0x" + rng.choice("123456789ABCDEF") * length
    literal = rng.choice((
        decimal, hexadecimal, "-" + hexadecimal, digit + "_0" * length,
        "0b" + "1" * length, "0o" + "7" * length,
    ))
    return _in_main(rng.choice((
        f"let x: i64 = {literal};",
        f"let x = {literal};",
        f"print({literal});",
        f"let x = a + {literal};",
        f"let x = a << {literal};",
        f"let x = s[{literal}];",
        f"let x = {literal} as u8;",
        f"let x = t.{decimal};",
        f"let x = [0; {literal}];",
        f"let x: [i64; {literal}] = [0; 1];",
    )))


def _invalid_bytes(rng):
    data = bytearray(_valid_program(rng).encode("utf-8"))
    for _ in range(rng.randint(1, 4)):
        data.insert(rng.randrange(len(data) + 1), rng.choice((0x80, 0xBF, 0xC0, 0xED, 0xF8, 0xFF)))
    return bytes(data)


def _deep_nesting(rng):
    size = rng.choice(_SIZES)
    opener, closer = rng.choice((
        ("(", ")"), ("[", "]"), ("{ ", "}"), ("if b { ", "}"), ("while b { ", "}"),
        ("for i in 0..1 { ", "}"), ("- ", ""), ("! ", ""), ("~ ", ""), ("& ", ""), ("* ", ""),
        ("f(", ")"), ("try ", ""), ("move ", ""),
    ))
    closers = closer * size if rng.random() < 0.8 else ""
    shape = rng.randrange(4)
    if shape == 0 and opener[0] in "{iwf" and opener != "f(":
        return _in_main(opener * size + closers)
    if shape == 1:
        pointer = rng.choice(("*", "*const "))
        return _in_main(f"let x: {pointer * size}i64 = null;")
    if shape == 2:
        return _in_main("let x: " + rng.choice(("(", "[", "fn(")) * size)
    return _in_main(f"let x = {opener * size}a{closers};")


def _long_chain(rng):
    size = rng.choice(_SIZES)
    term = rng.choice(("a", "b", "1", "'x'", "null"))
    shape = rng.randrange(6)
    if shape == 5:
        big = "0x" + "F" * rng.choice((16, 500, 4300))
        return _in_main("let x = " + f" {rng.choice(('*', '+', '|'))} ".join([big] * size) + ";")
    if shape == 0:
        operator = rng.choice(_BINARY_OPERATORS)
        chain = f" {operator} ".join([term] * size)
    elif shape == 1:
        chain = term + "".join(
            f" {rng.choice(_BINARY_OPERATORS)} {term}" for _ in range(size))
    elif shape == 2:
        chain = "a" + " as i64" * size
    elif shape == 3:
        chain = "t" + ".0" * size
    else:
        chain = "s" + "[0]" * size
    return _in_main(f"let x = {chain};")


def _big_array_repeat(rng):
    if rng.random() < 0.2:
        return _in_main(" ".join(
            f"let v{i}: [u8; 20000] = [1; 20000];" for i in range(20)))
    count = rng.choice((1, 1000, 12000000, 2 ** 31, 2000000000, 10 ** 18, 2 ** 70))
    element = rng.choice(("0", "1", "a", "a + a + a"))
    return _in_main(rng.choice((
        f"let x = [{element}; {count}];",
        f"let x: [i64; {count}] = [{element}; {count}];",
    )))


def _type_text(rng, depth=0):
    if depth >= 4 or rng.random() < 0.3:
        return rng.choice(("i64", "u8", "bool", "void", "P", "E"))
    inner = _type_text(rng, depth + 1)
    return rng.choice((
        f"*{inner}", f"*const {inner}", f"[{inner}; 2]", f"[]{inner}", f"[]const {inner}",
        f"({inner}, {inner})", f"fn({inner}) -> {inner}", f"W<{inner}>",
    ))


def _type_shapes(rng):
    typ = _type_text(rng)
    declarations = "struct P { v: i64 } enum E { A } struct W<T> { v: T } "
    return declarations + rng.choice((
        _in_main(f"let x: {typ} = null;"),
        _in_main(f"let x = null as {typ};"),
        _in_main(f"let x = sizeof<{typ}>();"),
        f"struct S {{ f: {typ} }} " + _in_main(""),
        f"fn f(x: {typ}) {{}} " + _in_main(""),
        f"fn f() -> {typ} {{ return null; }} " + _in_main(""),
        f"let g: {typ} = null; " + _in_main(""),
        "fn f<T>(x: T) {} " + _in_main(f"f<{typ}>(null);"),
    ))


def _type_like_strings(rng):
    text = "".join(rng.choice(("[]", "[", "]", ";", "(", ")", ",", "*", "fn(", ")->", "<", ">", "x", "2"))
                   for _ in range(rng.randint(1, 5)))
    return _in_main(rng.choice((f'println("{text}");', f'asm("{text}");'))) + f' // {text}'


def _runaway_generics(rng):
    size = rng.choice((8, 64, 200, 390))
    return rng.choice((
        "fn g<T>(x: T, n: i64) -> i64 { return g(&x, n); } " + _in_main("g(1, 2);"),
        "fn a<T>(x: T) -> i64 { return b(&x); } fn b<T>(x: T) -> i64 { return a(&x); } "
        + _in_main("a(1);"),
        "struct S<T> { next: *S<*T> } " + _in_main("let x: *S<i64> = null;"),
        "struct W<T> { v: T } " + _in_main(f"let x: *{'W<' * size}i64{'>' * size} = null;"),
        _in_main(f"let x: {'fn(' * size}i64{')->i64' * size} = null;"),
    ))


_HOSTILE = (
    _huge_literal, _invalid_bytes, _deep_nesting, _long_chain, _big_array_repeat,
    _type_shapes, _type_like_strings, _runaway_generics,
)


def _integer_expression(rng, depth=0):
    if depth >= 4 or rng.random() < 0.35:
        return str(rng.randint(0, 100))
    operator = rng.choice(("+", "-", "&", "|", "^"))
    return (
        f"({_integer_expression(rng, depth + 1)} {operator} "
        f"{_integer_expression(rng, depth + 1)})"
    )


def _valid_program(rng):
    template = rng.randrange(6)
    if template == 1:
        return (
            "fn choose(flag: bool, left: i64, right: i64) -> i64 { "
            "if flag { return left; } else { return right; } } "
            f"fn main() -> int {{ return choose(true, {_integer_expression(rng)}, "
            f"{_integer_expression(rng)}) as int; }}"
        )
    if template == 2:
        values = [rng.randint(0, 100) for _ in range(3)]
        return (
            "fn main() -> int { "
            f"let values: [i64; 3] = [{values[0]}, {values[1]}, {values[2]}]; "
            f"return values[{rng.randrange(3)}] as int; }}"
        )
    if template == 3:
        left, right = rng.randint(0, 100), rng.randint(0, 100)
        return (
            "struct Pair { left: i64, right: i64 } "
            "fn main() -> int { "
            f"let pair = Pair {{ left: {left}, right: {right} }}; "
            "return (pair.left + pair.right) as int; }"
        )
    if template == 4:
        value = rng.randint(0, 100)
        return (
            "enum Value { Empty, Number(i64) } "
            f"fn main() -> int {{ let value = Value.Number({value}); "
            "match value { Value.Empty => { return 0; }, "
            "Value.Number(number) => { return number as int; } } }"
        )
    if template == 5:
        left, right = rng.randint(0, 100), rng.randint(0, 100)
        return (
            "fn first<T>(pair: (T, T)) -> T { return pair.0; } "
            f"fn main() -> int {{ let pair: (i64, i64) = ({left}, {right}); "
            "return first(pair) as int; }"
        )
    declarations = []
    for index in range(rng.randint(0, 6)):
        declarations.append(f"let value_{index}: i64 = {_integer_expression(rng)};")
    returned = _integer_expression(rng)
    return "fn main() -> int { " + " ".join(declarations) + f" return {returned}; }}"


def _mutate(rng, source):
    alphabet = "{}()[]<>;:,.+-*/&|!_= abcdefghijklmnopqrstuvwxyz0123456789"
    text = source
    for _ in range(rng.randint(1, 8)):
        operation = rng.choice(("insert", "delete", "replace"))
        position = rng.randrange(len(text) + 1)
        if operation == "insert":
            text = text[:position] + rng.choice(alphabet) + text[position:]
        elif operation == "delete" and text:
            position = min(position, len(text) - 1)
            text = text[:position] + text[position + 1:]
        elif text:
            position = min(position, len(text) - 1)
            text = text[:position] + rng.choice(alphabet) + text[position + 1:]
    return text


def _preview(source):
    return source if len(source) <= 400 else f"{source[:200]!r} ... ({len(source)} long)"


def _run_with_timeout(compile_case, timeout):
    """Run one case on a worker thread. Returns (finished, error).

    A hang cannot be detected from the same thread, and Windows has no alarm
    signal. A timed-out worker is a daemon, so it cannot keep the process alive.
    """
    outcome = []

    def worker():
        try:
            compile_case()
        except Exception as error:  # noqa: BLE001 - run_fuzz reports it as a crash
            outcome.append(error)
        else:
            outcome.append(None)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return False, None
    return True, outcome[0]


def _check_located(error, description):
    """A hostile input must point at a source position, above all at a limit."""
    if error.line is None or (
            error.col is None and any(text in error.msg for text in _LIMIT_MESSAGES)):
        raise AssertionError(
            f"diagnostic without line and column on {description}: {error.format()}")


def run_fuzz(cases=1000, seed=0, case_timeout=10.0):
    """Run deterministic cases; raise AssertionError on a crash or hang."""
    from mortc import compile_to_c

    rng = random.Random(seed)
    accepted = rejected = 0
    for index in range(cases):
        adversarial = index % 20 == 19
        valid = index % 2 == 0 and not adversarial
        hostile = adversarial and rng.random() < 0.75
        if hostile:
            source = rng.choice(_HOSTILE)(rng)
        elif adversarial:
            source = rng.choice(_ADVERSARIAL)
        else:
            source = _valid_program(rng) if valid else _mutate(
                rng, rng.choice(_CORPUS))

        def compile_case(case=source):
            compile_to_c(decode_source(case) if isinstance(case, bytes) else case)

        description = f"fuzz case {index} (seed={seed}):\n{_preview(source)}"
        finished, error = _run_with_timeout(compile_case, case_timeout)
        if not finished:
            raise AssertionError(f"compiler hung for {case_timeout}s on {description}")
        if error is None:
            accepted += 1
        elif isinstance(error, MortError):
            if valid:
                raise AssertionError(f"valid {description}")
            if hostile:
                _check_located(error, description)
            rejected += 1
        else:
            raise AssertionError(
                f"compiler crashed with {type(error).__name__}: {error} on {description}"
            ) from error
    return {"cases": cases, "seed": seed, "accepted": accepted, "rejected": rejected}
