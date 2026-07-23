# Mort Language Specification 0.34

Status: Normative  
Language version: 0.34.0  
Document revision: 1  
Last updated: 2026-07-23

This document defines the source language accepted by a conforming Mort 0.34
implementation. The words **must**, **must not**, **should**, **should not**, and
**may** are normative. Examples are informative unless explicitly identified as
conformance cases.

The executable suite in `conformance/` is part of this specification. If prose
and a conformance case disagree, the prose controls and the case is a defect.
Implementation extensions must not change the meaning of a valid 0.34 program.

## 1. Conformance

A conforming implementation must:

1. accept every well-formed program required by this specification;
2. reject every program that violates a rule labeled a compile-time error;
3. preserve the observable behavior defined here;
4. identify itself and the language version it implements;
5. pass every applicable case in `conformance/manifest.json`.

An implementation may reject a program that exceeds a documented resource
limit. It must report a controlled diagnostic rather than crash or execute
partially compiled code.

Two execution profiles exist:

- **hosted** provides an operating-system process, the hosted builtins, and a
  `main` entry point;
- **freestanding** has no C library or process entry wrapper and is intended for
  kernels and embedded targets.

Unless a section says otherwise, a rule applies to both profiles.

## 2. Source text and lexical structure

Mort source files conventionally use the `.mx` suffix and must be UTF-8.
Language identifiers are currently limited to Unicode characters for which the
implementation's `isalpha`/`isalnum` classification succeeds, plus `_`.
Portable public source should use ASCII identifiers until identifier
normalization is standardized.

Whitespace separates tokens where adjacent tokens would otherwise merge.
Newlines are not statement terminators.

### 2.1 Comments

`//` begins a line comment that ends before the next newline or end of file.
`/*` begins a block comment and `*/` ends it. Block comments nest.
An unterminated block comment is a compile-time error.

### 2.2 Identifiers and keywords

An identifier begins with a letter or `_` and continues with letters, decimal
digits, or `_`.

The reserved words are:

```text
asm as bool break const continue defer else enum extern false fn for
if import in int let loop match module move null pub resource return
struct test true try type void while
```

Keywords cannot be used as ordinary declarations. For compatibility, `null`
may still name a previously declared function when immediately called.

### 2.3 Literals

Integer literals have arbitrary precision during checking:

```text
42          decimal
0xff        hexadecimal
0b1010      binary
0o755       octal
1_000_000   separators
```

Separators may occur only between digits. A prefixed literal must contain at
least one digit. Integer literals are initially untyped and adopt an integer
type from context. A literal value that does not fit that type is a compile-time
error.

Floating literals use decimal notation with an optional fraction and exponent,
for example `1.5`, `2e3`, or `6.02e+23`. They initially have type `f64`, may
adopt `f32` from context, and must be finite and representable in the selected
type.

A character literal represents one byte and has an untyped integer value in
`0..255`. Supported escapes are `\n`, `\r`, `\t`, `\0`, `\\`, `\'`, `\"`, and
`\xNN`.

A string literal contains bytes terminated by an implicit zero byte and has
type `*u8`. The supported escape spelling is preserved for the backend. A
newline or end of file before the closing quote is a compile-time error.
String storage is mutable and has static lifetime.

`true` and `false` have type `bool`. `null` requires a pointer or function
pointer context and denotes a null pointer.

## 3. Grammar

The following EBNF is normative. `IDENT`, `INT`, `FLOAT`, `CHAR`, and `STRING`
are lexical tokens. A trailing comma is accepted where shown by `[","]`.

```ebnf
program        = { declaration } ;

declaration    = module-decl | import-decl | pub-fn | resource-struct
               | struct-decl | enum-decl | type-decl | test-decl
               | global-decl | extern-decl | fn-decl ;
module-decl    = "module", path, ";" ;
import-decl    = "import", path, [ "as", IDENT ], ";" ;
path           = IDENT, { ".", IDENT } ;
pub-fn         = "pub", fn-decl ;
resource-struct= "resource", struct-decl ;
type-decl      = "type", IDENT, "=", type, ";" ;
test-decl      = "test", STRING, block ;
global-decl    = ("let" | "const"), IDENT, [":", type], "=", expression, ";" ;

struct-decl    = "struct", IDENT, [ generic-params ], "{",
                 [ field, { ",", field }, [","] ], "}" ;
field          = IDENT, ":", type ;
enum-decl      = "enum", IDENT, [ generic-params ], "{",
                 [ variant, { ",", variant }, [","] ], "}" ;
variant        = IDENT, [ "(", type, { ",", type }, ")" ] ;
generic-params = "<", IDENT, { ",", IDENT }, ">" ;

fn-decl        = "fn", IDENT, [ generic-params ], "(", [ params ], ")",
                 [ "->", type ], block ;
extern-decl    = "extern", "fn", IDENT, "(", [ params ], ")",
                 [ "->", type ], ";" ;
params         = param, { ",", param } ;
param          = IDENT, ":", type ;

type           = scalar-type | IDENT, [ type-args ] | "*", [ "const" ], type
               | "[", type, ";", INT, "]" | "[", "]", [ "const" ], type
               | "(", type, ",", type, { ",", type }, [","], ")"
               | "fn", "(", [ type, { ",", type } ], ")", "->", type ;
type-args      = "<", type, { ",", type }, ">" ;
scalar-type    = "bool" | "void" | "int"
               | "i8" | "i16" | "i32" | "i64"
               | "u8" | "u16" | "u32" | "u64"
               | "f32" | "f64"
               | "c_char" | "c_uchar" | "c_short" | "c_ushort"
               | "c_int" | "c_uint" | "c_long" | "c_ulong" | "c_size" ;

block          = "{", { statement }, "}" ;
statement      = binding | return-stmt | if-stmt | while-stmt | loop-stmt
               | for-stmt | match-stmt | asm-stmt | defer-stmt
               | break-stmt | continue-stmt | block | expr-stmt ;
binding        = ("let" | "const"), IDENT, [":", type], "=", expression, ";" ;
return-stmt    = "return", [ expression ], ";" ;
if-stmt        = "if", expression, block, [ "else", (if-stmt | block) ] ;
while-stmt     = "while", expression, block ;
loop-stmt      = "loop", block ;
for-stmt       = "for", IDENT, [":", type], "in", expression,
                 (".." | "..="), expression, block ;
match-stmt     = "match", expression, "{", { match-arm, [","] }, "}" ;
match-arm      = ("_" | expression), "=>", block ;
asm-stmt       = "asm", "(", STRING, ")", ";" ;
defer-stmt     = "defer", expression, ";" ;
break-stmt     = "break", ";" ;
continue-stmt  = "continue", ";" ;
expr-stmt      = expression,
                 [ assignment-op, expression ], ";" ;
assignment-op  = "=" | "+=" | "-=" | "*=" | "/=" | "%="
               | "&=" | "|=" | "^=" | "<<=" | ">>=" ;

expression     = logical-or ;
logical-or     = logical-and, { "||", logical-and } ;
logical-and    = bit-or, { "&&", bit-or } ;
bit-or         = bit-xor, { "|", bit-xor } ;
bit-xor        = bit-and, { "^", bit-and } ;
bit-and        = equality, { "&", equality } ;
equality       = comparison, { ("==" | "!="), comparison } ;
comparison     = shift, { ("<" | ">" | "<=" | ">="), shift } ;
shift          = additive, { ("<<" | ">>"), additive } ;
additive       = multiply, { ("+" | "-"), multiply } ;
multiply       = cast, { ("*" | "/" | "%"), cast } ;
cast           = unary, { "as", type } ;
unary          = ("!" | "-" | "~" | "&" | "*" | "try" | "move"), unary
               | postfix ;
postfix        = primary, { call | field-access | index } ;
call           = [ type-args ], "(", [ expression, { ",", expression } ], ")" ;
field-access   = ".", (IDENT | INT) ;
index          = "[", expression, "]" ;
primary        = literal | IDENT | array-literal | struct-literal
               | "(", expression, ")"
               | "(", expression, ",", expression,
                   { ",", expression }, [","], ")" ;
array-literal  = "[", expression, ";", INT, "]"
               | "[", expression, { ",", expression }, [","], "]" ;
struct-literal = IDENT, [ type-args ], "{",
                 [ IDENT, ":", expression,
                   { ",", IDENT, ":", expression }, [","] ], "}" ;
```

Operators at the top of the expression grammar have lower precedence than
operators below them. Operators in one repetition are left-associative. Unary
operators and casts are right-associative and left-associative respectively.

## 4. Types and values

Mort is statically typed. Every expression has exactly one checked type.

### 4.1 Primitive types

`bool` has exactly the values `false` and `true`. It is not an integer and
cannot be used as one without a future explicit conversion facility.

`void` has no values. It is valid as a function result and pointer pointee but
not as a variable, field, array element, tuple element, or parameter.

`int` is an alias for `i64`.

The fixed-width integers are:

| Type | Range |
| --- | --- |
| `i8` | -2^7 through 2^7-1 |
| `i16` | -2^15 through 2^15-1 |
| `i32` | -2^31 through 2^31-1 |
| `i64` | -2^63 through 2^63-1 |
| `u8` | 0 through 2^8-1 |
| `u16` | 0 through 2^16-1 |
| `u32` | 0 through 2^32-1 |
| `u64` | 0 through 2^64-1 |

`f32` is an IEEE-754 binary32 value and `f64` is an IEEE-754 binary64 value on
conforming supported targets. Ordinary IEEE exceptional values may arise from
runtime arithmetic, but source literals must be finite.

The `c_*` integer types are ABI bridge types. Their layout and arithmetic width
follow the target C ABI. Portable code should convert them to a fixed-width Mort
integer before arithmetic. `c_long` literals are conservatively restricted to
the signed 32-bit range, `c_ulong` to the unsigned 32-bit range, and `c_size` to
the unsigned 64-bit range so the same source checks on LLP64 and LP64 hosts.

### 4.2 Compound types

`*T` is a mutable pointer to `T`; `*const T` is a pointer through which `T`
cannot be modified. Pointer validity, alignment, provenance, and lifetime are
the programmer's responsibility in Mort 0.34.

`[T; N]` is a fixed array of `N` values. `N` is a non-negative integer literal.
Arrays have value semantics except that assigning a whole array is not
supported. `[]T` is a mutable slice and `[]const T` is a read-only slice. A
slice contains a pointer and a `u64` length.

`(T, U, ...)` is a structural tuple with at least two elements. Tuple fields
are selected by zero-based decimal fields such as `.0`.

`struct` types are nominal. All fields are required exactly once in a struct
literal. `enum` types are nominal tagged unions. A variant has zero or more
payload values. Generic structs, enums, and functions are monomorphized for
each concrete type argument list.

`fn(T, U)->R` is a function pointer type. A function value may refer to a Mort
function or an ABI-compatible `extern fn`.

A `type Name = T;` declaration creates a transparent alias. It does not create
a distinct nominal type.

### 4.3 Type equality and coercion

Non-literal operands of an integer binary operation must have the same integer
type. Non-literal float operands must have the same float type. Mixing an
integer and float is a compile-time error.

An untyped integer or character literal may adopt the required integer type if
its arbitrary-precision value fits. An `f64` literal may adopt `f32` if its
finite magnitude fits. Other conversions require `as`.

An explicit `as` cast is permitted between integer types, between float types,
between integer and float types, between pointers, and between pointers and
integers. Casting `null` is permitted only to a pointer or function pointer.
Pointer casts do not make an invalid address safe.

Casting an integer to a fixed-width integer preserves the source value modulo
2^N and interprets the resulting N-bit pattern according to the target
signedness. Casting a float to a fixed-width integer truncates toward zero. If
that truncated value is outside the target range, or the float is a NaN or
infinity, execution fails through the controlled numeric-cast failure path.
Casting an integer to a float uses the target IEEE rounding behavior.

Casts to or from a `c_*` integer use the target C ABI and may be
implementation-defined when the value is not representable. A pointer-to-
integer cast produces the target's address representation reduced modulo the
fixed-width integer target; integer-to-pointer validity remains target-defined.

## 5. Declarations, names, and modules

A declaration is in scope according to its containing program or block.
Top-level declarations are available regardless of source order. Local
bindings are available after their initializer and may shadow an outer local
subject to duplicate-name checks in the same scope.

`let` introduces a mutable binding. `const` introduces an immutable binding;
assignment through one of its fields, indices, or derived const pointers is a
compile-time error. Global initializers must be compile-time-compatible with
static initialization.

A source file may begin with one `module a.b;` declaration. In a named module,
functions are private by default and `pub fn` makes a function callable through
an importing alias. `pub` currently applies only to functions.

`import path;` recursively loads a source module. A relative import is resolved
relative to the importing file; `std.*` selects a bundled standard module.
`import path as alias;` changes the local qualification prefix. Duplicate
imports are loaded once. Import cycles that cannot be resolved are a
compile-time error.

All root files passed to one compilation form one program. A hosted executable
must contain exactly one suitable `main`; freestanding programs are not
required to define `main`.

`extern fn` declares a native symbol with the target C ABI. Linking that symbol
is an implementation-driver responsibility. Calling an incompatible native
symbol has undefined foreign-interface behavior.

## 6. Statements and control flow

Statements in a block execute in source order.

An `if` or `while` condition must have type `bool`. `loop` is equivalent to an
infinite `while true`. `break` and `continue` are permitted only within a loop.

A range loop evaluates its lower and upper bounds exactly once, before its
first iteration. `a..b` visits increasing values from `a` while the counter is
less than `b`. `a..=b` visits increasing values through `b`. If `a > b`, the
range is empty. The loop counter type is the explicit annotation or the common
inferred integer type. Inclusive iteration at the maximum value must not
overflow.

`return` leaves the current function after running required cleanup. A
non-`void` function must return a compatible value on every reachable path.

`match` evaluates its subject once. Enum matches must be exhaustive unless a
wildcard arm is present. Payload patterns bind values by position; `_` ignores
a payload or provides a whole-arm wildcard. Duplicate or unreachable enum
variants are compile-time errors.

An assignment target must be a mutable variable, dereference, field, or index.
A compound assignment evaluates the target location once and applies the
corresponding binary operator before storing the result.

`defer expression;` records the expression for execution when its lexical scope
is left. Defers execute in reverse registration order on fallthrough, return,
`break`, `continue`, and `try` propagation. A defer expression is not executed
when registered.

`asm("...");` emits target-specific volatile inline assembly. Its validity and
effects are outside the portable language and are the programmer's
responsibility.

## 7. Expression semantics

Every expression is evaluated at most once except an array repeat initializer:
`[expression; N]` behaves as `N` element initializers and may evaluate
`expression` once for each element.

`&&` evaluates its left operand first and evaluates the right operand only when
the left value is `true`. `||` evaluates its left operand first and evaluates
the right operand only when the left value is `false`.

Mort 0.34 does not specify the relative evaluation order of ordinary binary
operands, call arguments, aggregate fields, or array elements. Each such
subexpression is evaluated before the containing operation completes. Programs
whose result depends on that relative order are non-portable.

Equality and ordering produce `bool`. Aggregate equality is not defined.
Pointers may be compared only for equality with a pointer of the same type or
with typed `null`.

Indexing a hosted array or slice with an out-of-bounds runtime index terminates
the process with a diagnostic. A statically known invalid index is a
compile-time error. Freestanding indexing has no inserted hosted failure path;
the program must establish bounds.

### 7.1 Fixed-width integer semantics

The operations `+`, `-`, `*`, unary `-`, `~`, `&`, `|`, and `^` on a
fixed-width integer operate on its N-bit two's-complement bit pattern. Results
are reduced modulo 2^N. This rule applies equally in optimized and unoptimized
builds and does not invoke backend signed-overflow behavior.

Integer `/` truncates toward zero. `%` satisfies
`left == (left / right) * right + (left % right)` and its nonzero result has the
sign of `left`. For signed minimum divided by `-1`, `/` returns the same minimum
value and `%` returns zero. A runtime zero divisor is a controlled execution
failure. A statically known zero divisor is a compile-time error.

For `left << count` and `left >> count`, the result type is the type of `left`;
`count` may have any integer type. A negative count is a compile-time error
when constant and a controlled execution failure otherwise.

- If `count >= N`, left shift returns zero.
- If `count >= N`, unsigned right shift returns zero.
- If `count >= N`, signed right shift returns `-1` for a negative left value
  and zero otherwise.
- For smaller counts, left shift shifts the N-bit pattern and discards bits
  above N.
- For smaller counts, signed right shift is arithmetic and unsigned right shift
  is logical.

All-literal integer expressions are evaluated with arbitrary precision before
contextual range checking. Consequently an out-of-range literal expression is
a compile-time error rather than a wrapping runtime expression.

The deterministic wrapping rules in this subsection apply to fixed-width Mort
types. Arithmetic on `c_*` types follows the target ABI and should be confined
to interoperation boundaries.

Numeric `as` casts use the rules in section 4.3. In particular, casts to a
fixed-width integer cannot trigger backend conversion undefined behavior.

### 7.2 Floating-point semantics

Floating arithmetic uses the selected IEEE type and target default
round-to-nearest behavior. `/` follows IEEE division. `%`, bitwise operators,
and shifts are not defined for floats. Mort 0.34 does not promise identical
NaN payloads or exceptional-status flags across targets.

### 7.3 Calls and builtins

A call must provide the declared number and type of arguments. Generic type
arguments may be explicit or inferred from parameter positions. Inference that
does not produce one unambiguous concrete instantiation is a compile-time
error.

Hosted builtins:

| Builtin | Meaning |
| --- | --- |
| `print(value)` | print an integer, bool, or float followed by newline |
| `println(*u8)` | print a zero-terminated byte string followed by newline |
| `assert(bool)` | terminate with a source-line diagnostic when false |
| `alloc(u64)` | allocate bytes, returning `*void` |
| `free(*void)` | release a prior allocation |
| `len(value)` | byte-string, array, or slice length |
| `slice(pointer, length)` | construct a typed slice |
| `sizeof<T>()` | target byte size of concrete `T` |
| `unix_time()` | Unix seconds as `i64` |
| `cpu_millis()` | process CPU milliseconds as `u64` |
| `file_*` | typed hosted file operations |

`len`, `slice`, and `sizeof` are available in both profiles when their operands
are otherwise valid. File, allocation, time, printing, and assertion builtins
are unavailable in freestanding mode.

The `inb/outb`, `inw/outw`, and `inl/outl` builtins are privileged x86 port-I/O
operations. They are target-specific and not valid portable hosted behavior.

## 8. Ownership and cleanup

`resource struct R { ... }` declares a move-only resource type. The same
program must provide a compatible `destroy(*R) -> void` function. A type that
contains a resource by value is itself resource-bearing.

Resource values:

- cannot be copied implicitly;
- cannot be stored in globals;
- cannot be overwritten by assignment;
- must be transferred with `move binding`;
- cannot be used after a move;
- are destroyed automatically when their owning lexical binding leaves scope.

Automatic destruction is recursive and in reverse field/element construction
order. Local resources are destroyed in reverse binding order. Control-flow
joins must have compatible ownership state. A double move, possible use after
move, or unsafe move across loop iterations is a compile-time error.

`match move value` consumes a resource-bearing enum and transfers the active
payload into the selected arm. A resource created inside a loop is a fresh
owner on each iteration.

An explicit deferred call to the matching destructor suppresses duplicate
automatic cleanup for that binding.

## 9. Error propagation

`try expression` requires an enum instantiation with `Ok` and `Err` variants.
On `Ok(payload)`, it evaluates to the payload. On `Err(error)`, it runs cleanup
for exited scopes and immediately returns an `Err` with a type-compatible error
payload from the enclosing function.

`try` may occur inside eager expressions, conditions, range bounds, aggregates,
calls, assignments, and matches. Its checks occur before the containing
operation consumes the unwrapped value. In `&&` and `||`, a `try` in the right
operand remains short-circuited.

## 10. Diagnostics and failures

A compile-time error must prevent executable output. A diagnostic must identify
the source file when known, a one-based line, and a human-readable reason.
Column information is recommended.

The following are controlled execution failures in hosted mode:

- failed `assert`;
- array or slice bounds violation;
- fixed-width integer division or remainder by zero;
- a negative runtime shift count.
- an out-of-range or non-finite float-to-fixed-integer cast.

A controlled failure must return a nonzero process status and emit a diagnostic
to standard error. Freestanding builds use a target trap because no process or
standard error exists.

Raw pointer misuse, invalid inline assembly, an incompatible foreign symbol,
data races in foreign code, and violations explicitly delegated to a native API
are outside Mort 0.34's safety guarantees.

## 11. Implementation limits and portability

An implementation must document supported targets and backend prerequisites.
Mort 0.34's reference implementation emits C11, but C is not part of the
language semantics and another backend may be conforming.

Portable Mort source must not depend on:

- C spelling, generated symbol names, or generated layout except at a declared
  and documented FFI boundary;
- ordinary operand or argument evaluation order;
- `c_*` widths beyond the target ABI;
- pointer size or integer-to-pointer validity;
- target inline assembly or port I/O;
- host filesystem, clock, allocator, or locale behavior not specified by the
  corresponding API.

## Appendix A. Compatibility

The language version uses semantic versioning:

- a patch revision clarifies wording or fixes a conformance case without
  changing valid program behavior;
- a minor revision may add backward-compatible syntax or behavior;
- a major revision may remove or change accepted behavior.

Deprecations must remain diagnosed for at least one minor language version
before removal unless retaining them would be a demonstrated security issue.

## Appendix B. Reserved future work

The following are intentionally not defined by Mort 0.34: checked borrows and
lifetimes, threads and a data-race model, atomics, asynchronous tasks,
exceptions, reflection, dynamic loading, stable binary package ABI, Unicode
text semantics, and WebAssembly/mobile platform profiles. Their absence is not
permission for an implementation to assign new meaning to currently valid
syntax.
