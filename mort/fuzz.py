"""Deterministic grammar/mutation fuzzing for the Mort front end."""
import random

from .errors import MortError


_CORPUS = [
    "fn main() -> int { return 0; }",
    "fn main() -> int { let value: i64 = 42; print(value); return 0; }",
    "struct Pair<T> { left: T, right: T } fn main() -> int { return 0; }",
    "enum State { Ready, Done } fn main() -> int { let s = State.Ready; "
    "match s { State.Ready => {}, State.Done => {} } return 0; }",
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
        valid = index % 2 == 0
        source = _valid_program(rng) if valid else _mutate(rng, rng.choice(_CORPUS))
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
