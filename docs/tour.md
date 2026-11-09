# A tour of Mort

Mort is a small, statically typed language that compiles to C. This tour shows
the language as the compiler implements it today. The normative reference is the
[language specification](language-specification.md).

Every program below is a complete file. `tests/test_tour.py` compiles and runs
each one and compares its output with the block printed under it, and checks
that each rejected program fails with the quoted message. To try one, save it as
`tour.mx` and run `mortc tour.mx --run`.

## What Mort does not have

So you do not go looking for them: Mort has **no traits or interfaces** and **no
closures**. A function value is a plain reference to a named function and cannot
capture local variables. Generics are monomorphized and have no bounds. There are
no methods; functions take the value or a pointer as an ordinary argument.

Mort is alpha software. See the
[production-readiness checklist](production-readiness.md) for its limits.

## Hello

`println` prints a string, `print` prints a number. `main` returns the process
exit code.

```mort
fn main() -> int {
    println("Hello, Mort");
    print(42);
    return 0;
}
```

```output
Hello, Mort
42
```

## Variables and arithmetic

`let` bindings are mutable and their type is inferred unless annotated. `const`
bindings cannot be reassigned. `int` is an alias for `i64`.

```mort
fn main() -> int {
    let total = 10;
    let step: i64 = 3;
    const limit: i64 = 100;
    total += step * 2;
    total = total % limit;
    print(total);
    print(0xFF + 0b11 + 1_000);
    return 0;
}
```

```output
16
1258
```

## Integer types and casts

Integers are fixed width and never mix implicitly; `as` converts between them.
Runtime arithmetic wraps. An untyped literal adopts the type it is used with.

```mort
fn main() -> int {
    let small: u8 = 250;
    let wrapped: u8 = small + 10;
    print(wrapped as i64);
    let wide: i64 = (small as i64) + 10;
    print(wide);
    return 0;
}
```

```output
4
260
```

Mixing widths without a cast is a compile-time error:

```mort-reject
fn main() -> int {
    let a: u8 = 1;
    let b: i32 = 2;
    let c = a + b;
    return 0;
}
```

```error
mismatched integer types
```

## Functions and control flow

Functions can call each other in any order. Mort has `if`/`else`, `while`,
`loop`, and range `for`, with `break` and `continue`. `0..n` excludes the end and
`0..=n` includes it.

```mort
fn main() -> int {
    let sum = 0;
    for i in 1..=4 {
        if is_even(i) {
            continue;
        }
        sum += i;
    }
    print(sum);

    let n = 0;
    loop {
        n += 1;
        if n == 3 {
            break;
        }
    }
    while n < 5 {
        n += 1;
    }
    print(n);
    return 0;
}

fn is_even(value: i64) -> bool {
    return value % 2 == 0;
}
```

```output
4
5
```

## Strings

A string literal has type `*u8`: a pointer to static, NUL-terminated bytes.
`len` gives its length and indexing reads a byte. `std.owned_string` and
`std.strings` provide allocated and borrowed text.

```mort
fn main() -> int {
    let name = "Mort";
    print(len(name) as i64);
    print(name[0] as i64);
    return 0;
}
```

```output
4
77
```

## Arrays and slices

`[T; N]` is a fixed-size array. `[]T` is a pointer and a length, created with
`slice`. Indexing is bounds checked.

```mort
fn sum(values: []i32) -> i64 {
    let total: i64 = 0;
    for index: u64 in 0..values.len {
        total += values[index] as i64;
    }
    return total;
}

fn main() -> int {
    let values: [i32; 3] = [10, 20, 12];
    values[1] = 30;
    print(sum(slice(&values[0], len(values))));
    return 0;
}
```

```output
52
```

## Structs and pointers

Structs pass by value. To mutate the caller's copy, pass a pointer, and write
through it with `(*p).field`.

```mort
struct Point {
    x: i64,
    y: i64,
}

fn shift(point: *Point, by: i64) -> void {
    (*point).x += by;
}

fn main() -> int {
    let p = Point { x: 3, y: 4 };
    shift(&p, 10);
    print(p.x + p.y);
    return 0;
}
```

```output
17
```

## Tuples and type aliases

```mort
type Pair = (i64, bool);

fn main() -> int {
    let value: Pair = (41, true);
    value.0 += 1;
    if value.1 {
        print(value.0);
    }
    return 0;
}
```

```output
42
```

## Enums and match

Enum variants can carry data. `match` must cover every variant.

```mort
enum Shape {
    Square(i64),
    Rect(i64, i64),
    Empty,
}

fn area(shape: Shape) -> i64 {
    match shape {
        Shape.Square(side) => { return side * side; },
        Shape.Rect(w, h) => { return w * h; },
        Shape.Empty => { return 0; },
    }
}

fn main() -> int {
    print(area(Shape.Square(3)));
    print(area(Shape.Rect(2, 5)));
    print(area(Shape.Empty));
    return 0;
}
```

```output
9
10
0
```

A match that misses a variant does not compile:

```mort-reject
enum State { On, Off }

fn main() -> int {
    let s: State = State.On;
    match s {
        State.On => { print(1); },
    }
    return 0;
}
```

```error
non-exhaustive match
```

## Generics

Generic functions and types are monomorphized. Type arguments are inferred at
calls where possible.

```mort
struct Box<T> {
    value: T,
}

fn identity<T>(value: T) -> T {
    return value;
}

fn main() -> int {
    let box: Box<i64> = Box<i64> { value: identity(40) };
    print(box.value + identity<i64>(2));
    return 0;
}
```

```output
42
```

## Function values

A named function can be stored and passed as a typed value. There are no closures,
so it receives everything it needs through its parameters.

```mort
fn double(value: i64) -> i64 {
    return value * 2;
}

fn apply(operation: fn(i64) -> i64, value: i64) -> i64 {
    return operation(value);
}

fn main() -> int {
    print(apply(double, 21));
    return 0;
}
```

```output
42
```

## Errors with Option and Result

Mort has no exceptions. `try` unwraps `Result.Ok` or returns the `Err` from the
enclosing function.

```mort
import std.result;

fn parse(ok: bool) -> Result<i64, *u8> {
    if ok {
        return Result<i64, *u8>.Ok(21);
    }
    return Result<i64, *u8>.Err("invalid input");
}

fn doubled(ok: bool) -> Result<i64, *u8> {
    let value = try parse(ok);
    return Result<i64, *u8>.Ok(value * 2);
}

fn main() -> int {
    print(result.unwrap_or(doubled(true), 0));
    print(result.unwrap_or(doubled(false), -1));
    return 0;
}
```

```output
42
-1
```

## Collections

`std.vec` and `std.map` are the standard containers. They own their memory and
clean up automatically at the end of the scope.

```mort
import std.map;
import std.option;
import std.vec;

fn main() -> int {
    let numbers: Vec<i64> = vec.new<i64>();
    vec.push(&numbers, 20);
    vec.push(&numbers, 22);
    print(numbers.length as i64);

    let scores: Map<i64, i64> = map.new<i64, i64>();
    map.insert(&scores, 7, 42);
    print(option.unwrap_or(map.get(&scores, 7), 0));
    return 0;
}
```

```output
2
42
```

## Ownership and resources

A `resource struct` is move-only and has a `destroy` function, which runs
automatically in reverse order at scope exit. Transfers are explicit with `move`.

```mort
resource struct Ticket {
    label: *u8,
}

fn destroy(value: *Ticket) -> void {
    println((*value).label);
}

fn consume(ticket: Ticket) -> void {
    println("consumed");
}

fn main() -> int {
    let kept = Ticket { label: "kept released" };
    let moved = Ticket { label: "moved released" };
    consume(move moved);
    println("end of main");
    return 0;
}
```

```output
consumed
moved released
end of main
kept released
```

Using a value after moving it is a compile-time error:

```mort-reject
resource struct Ticket {
    label: *u8,
}

fn destroy(value: *Ticket) -> void {
    println((*value).label);
}

fn main() -> int {
    let first = Ticket { label: "a" };
    let second = move first;
    let third = move first;
    return 0;
}
```

```error
was already moved
```

## defer

`defer` runs an expression when the enclosing scope exits, in reverse order, on
every path out of it.

```mort
fn main() -> int {
    defer println("first registered, last run");
    defer println("second registered, first run");
    println("body");
    return 0;
}
```

```output
body
second registered, first run
first registered, last run
```

## Calling C

`extern fn` declares a C-ABI function. The linker resolves it.

```mort
extern fn abs(value: i32) -> i32;

fn main() -> int {
    print(abs(0 - 42) as i64);
    return 0;
}
```

```output
42
```

## Low-level access

Raw pointers, `as` casts between integers and pointers, inline `asm`, port I/O,
and `--freestanding` builds exist for kernel work; MORT OS is written this way.
`volatile_read` and `volatile_write` perform accesses the C compiler may not
remove or reorder relative to each other.

```mort
fn main() -> int {
    let register: u32 = 0;
    volatile_write<u32>(&register, 7);
    print(volatile_read<u32>(&register) as i64);
    return 0;
}
```

```output
7
```

## Where next

- [`examples/`](../examples) has larger programs, including JSON, sockets, HTTP
  and threads, all exercised by the test suite.
- [`README.md`](../README.md) covers the command-line tools.
- [`CONTRIBUTING.md`](../CONTRIBUTING.md) explains how to work on the compiler.
