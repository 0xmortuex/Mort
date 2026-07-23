"""Deterministic grammar/mutation fuzzing for the Mort front end."""
import random

from .errors import MortError


_CORPUS = [
    "fn main() -> int { return 0; }",
    "fn main() -> int { let value: i64 = 42; print(value); return 0; }",
    "struct Pair<T> { left: T, right: T } fn main() -> int { return 0; }",
    "enum State { Ready, Done } fn main() -> int { let s = State.Ready; "
    "match s { State.Ready => {}, State.Done => {} } return 0; }",
    "fn main() -> int { let values: [i64; 3] = [1, 2, 3]; "
    "return values[1] as int; }",
    "enum Value { None, Number(i64) } fn main() -> int { "
    "let value = Value.Number(42); match value { Value.None => {}, "
    "Value.Number(number) => { print(number); } } return 0; }",
]

_ADVERSARIAL = [
    # Parser recursion must become a controlled MortError, never a Python crash.
    "fn main() -> int { return " + "(" * 160 + "1" + ")" * 160 + "; }",
    "/*" * 200 + "x" + "*/" * 200,
    "\x00\x00\x00",
    "fn main() -> int { return 0; }\n" + "}" * 500,
]


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


def run_fuzz(cases=1000, seed=0):
    """Run deterministic cases; raise AssertionError on a compiler crash."""
    from mortc import compile_to_c

    rng = random.Random(seed)
    accepted = rejected = 0
    for index in range(cases):
        adversarial = index % 20 == 19
        valid = index % 2 == 0 and not adversarial
        if adversarial:
            source = rng.choice(_ADVERSARIAL)
        else:
            source = _valid_program(rng) if valid else _mutate(
                rng, rng.choice(_CORPUS))
        try:
            compile_to_c(source)
            accepted += 1
        except MortError:
            if valid:
                raise AssertionError(
                    f"valid fuzz case {index} was rejected (seed={seed}):\n{source}"
                )
            rejected += 1
        except Exception as error:
            raise AssertionError(
                f"compiler crashed on fuzz case {index} (seed={seed}): "
                f"{type(error).__name__}: {error}\n{source}"
            ) from error
    return {"cases": cases, "seed": seed, "accepted": accepted, "rejected": rejected}
