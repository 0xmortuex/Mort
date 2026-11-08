# Mort Language Specification 0.41

Status: Normative  
Language version: 0.41.0
Document revision: 2  
Last updated: 2026-11-08

This document defines the source language accepted by a conforming Mort 0.41
implementation. The words **must**, **must not**, **should**, **should not**, and
**may** are normative. Examples are informative unless explicitly identified as
conformance cases.

The executable suite in `conformance/` is part of this specification. If prose
and a conformance case disagree, the prose controls and the case is a defect.
Implementation extensions must not change the meaning of a valid 0.41 program.

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
Section 2.4 gives the complete lexical grammar.
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

A character literal represents one byte in `0..255`. It has type `u8` and, like
an integer literal, adopts an integer type from context. Supported escapes are `\n`, `\r`, `\t`, `\0`, `\\`, `\'`, `\"`, and
`\xNN`.

A string literal contains bytes terminated by an implicit zero byte and has
type `*u8`. The supported escape spelling is preserved for the backend. A
newline or end of file before the closing quote is a compile-time error.
String storage is mutable and has static lifetime.

`true` and `false` have type `bool`. `null` requires a pointer or function
pointer context and denotes a null pointer.

### 2.4 Lexical grammar

The longest possible token is always formed: `<<=` is one token, never `<`, `<=`.
After a `.` token a following digit sequence is a `tuple-index`, lexed as a
decimal `INT` with no separators, prefix, fraction or exponent, so `t.1.0` is
four tokens after `t`. A number or tuple index followed directly by a letter,
digit or `_` that does not continue it is a compile-time error.

The special sequences name character classes. `? any ?` is any character; `? LF ?`,
`? CR ?`, `? TAB ?` and `? NUL ?` are those control characters; `? alpha ?` and
`? alnum ?` are the classifications given in section 2; `? latin1 ?` is any character
up to U+00FF.

```ebnf lexical
trivia = { whitespace | line-comment | block-comment } ;
whitespace = " " | ? TAB ? | ? CR ? | ? LF ? ;
line-comment = "//", { ? any ? - ? LF ? } ;
block-comment = "/*", { block-comment | comment-char }, "*/" ;
comment-char = ? any ? - ( "/*" | "*/" ) ;

IDENT = ident-start, { ident-continue } ;
ident-start = ? alpha ? | "_" ;
ident-continue = ? alnum ? | "_" ;

INT = decimal-int | hex-int | binary-int | octal-int ;
decimal-int = digit, { [ "_" ], digit } ;
hex-int = "0", ( "x" | "X" ), hex-digit, { [ "_" ], hex-digit } ;
binary-int = "0", ( "b" | "B" ), binary-digit, { [ "_" ], binary-digit } ;
octal-int = "0", ( "o" | "O" ), octal-digit, { [ "_" ], octal-digit } ;
tuple-index = digit, { digit } ;
digit = "0" | "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" ;
hex-digit = digit | "a" | "b" | "c" | "d" | "e" | "f"
                | "A" | "B" | "C" | "D" | "E" | "F" ;
binary-digit = "0" | "1" ;
octal-digit = "0" | "1" | "2" | "3" | "4" | "5" | "6" | "7" ;

FLOAT = decimal-int, ( fraction, [ exponent ] | exponent ) ;
fraction = ".", digit, { digit } ;
exponent = ( "e" | "E" ), [ "+" | "-" ], digit, { digit } ;

CHAR = "'", ( char-plain | char-escape ), "'" ;
char-plain = ? latin1 ? - ( "'" | "\" | ? LF ? | ? NUL ? ) ;
char-escape = "\", ( "n" | "r" | "t" | "0" | "\" | "'" | '"'
                | "x", hex-digit, hex-digit ) ;

STRING = '"', { string-plain | string-escape }, '"' ;
string-plain = ? any ? - ( '"' | "\" | ? LF ? | ? NUL ? ) ;
string-escape = "\", ( ? any ? - ? NUL ? ) ;

keyword = "as" | "asm" | "bool" | "break" | "const" | "continue" | "defer"
                | "else" | "enum" | "extern" | "false" | "fn" | "for" | "if"
                | "import" | "in" | "int" | "let" | "loop" | "match" | "module"
                | "move" | "null" | "pub" | "resource" | "return" | "struct"
                | "test" | "true" | "try" | "type" | "void" | "while" ;
punctuator = "<<=" | ">>=" | "..=" | ".." | "->" | "=>" | "==" | "!=" | "<="
                | ">=" | "&&" | "||" | "<<" | ">>" | "+=" | "-=" | "*=" | "/="
                | "%=" | "&=" | "|=" | "^=" | "(" | ")" | "{" | "}" | ","
                | ";" | ":" | "." | "&" | "[" | "]" | "=" | "+" | "-" | "*"
                | "/" | "%" | "<" | ">" | "!" | "|" | "^" | "~" ;
```

An `IDENT` must not be one of the keywords, which lex as their own tokens. A
`STRING` escape is preserved for the backend: only the spellings accepted by the
target C compiler are valid (section 22.4). A `FLOAT` must be finite, so `1e999`
is a compile-time error. A `CHAR` denotes a value up to 255.

## 3. Grammar

### 3.1 Notation

The grammars in this document are ISO EBNF. Terminals are quoted, `,` is
sequence, `|` is alternation, `[ ]` is zero or one, `{ }` is zero or more,
`(* *)` is a comment, and `? name ?` is a special sequence defined in the text.
`-` is exception: `A - B` matches what `A` matches unless `B` matches at the same
position.

The fenced blocks labelled `ebnf lexical` and `ebnf syntax` are extracted by
`tests/test_spec_conformance.py`, which builds a recognizer and a sentence
generator from this text and compares them with the implementation. A grammar
change must be made here and in the parser together or that test fails.

The syntactic grammar is read as **LL(1) with named predicates**. At an
alternation the first alternative that can begin with the next token is taken;
`[ ]` and `{ }` are entered exactly when their body can begin with the next
token. Having chosen, a parser never reconsiders. A terminal spelled like an
identifier, such as `"_"` or `"i64"`, matches an `IDENT` token with that text and
is preferred over a plain `IDENT` alternative listed after it.

### 3.2 Syntactic grammar

`IDENT`, `INT`, `FLOAT`, `CHAR` and `STRING` are the lexical tokens of section
2.4.

```ebnf syntax
program = [ module-decl ], { declaration } ;

declaration = import-decl | pub-fn | resource-struct | struct-decl | enum-decl
                | type-decl | test-decl | global-decl | extern-decl | fn-decl ;
module-decl = "module", path, ";" ;
import-decl = "import", path, [ "as", IDENT ], ";" ;
path = IDENT, { ".", IDENT } ;
pub-fn = "pub", fn-decl ;
resource-struct = "resource", struct-decl ;
type-decl = "type", IDENT, "=", type, ";" ;
test-decl = "test", STRING, block ;
global-decl = binding ;

struct-decl = "struct", IDENT, [ generic-params ], "{", [ field-list ], "}" ;
field-list = field, [ ",", [ field-list ] ] ;
field = IDENT, ":", type ;
enum-decl = "enum", IDENT, [ generic-params ], "{", [ variant-list ], "}" ;
variant-list = variant, [ ",", [ variant-list ] ] ;
variant = IDENT, [ "(", type, { ",", type }, ")" ] ;
generic-params = "<", IDENT, { ",", IDENT }, ">" ;

fn-decl = "fn", fn-name, [ generic-params ], "(", [ params ], ")",
                [ "->", type ], block ;
extern-decl = "extern", "fn", fn-name, "(", [ params ], ")",
                [ "->", type ], ";" ;
fn-name = IDENT | "null" ;
params = param, { ",", param } ;
param = IDENT, ":", type ;

type = fn-type | tuple-type | pointer-type | bracket-type | scalar-type
                | named-type ;
fn-type = "fn", "(", [ type, { ",", type } ], ")", "->", type ;
tuple-type = "(", type, ",", type, [ ",", [ type-list ] ], ")" ;
type-list = type, [ ",", [ type-list ] ] ;
pointer-type = "*", [ "const" ], type ;
bracket-type = "[", ( "]", [ "const" ], type | type, ";", INT, "]" ) ;
named-type = IDENT, [ type-args ] ;
type-args = "<", type, { ",", type }, type-close ;
type-close = ? angle-close ? ;
scalar-type = "bool" | "void" | "int"
                | "i8" | "i16" | "i32" | "i64"
                | "u8" | "u16" | "u32" | "u64"
                | "f32" | "f64"
                | "c_char" | "c_uchar" | "c_short" | "c_ushort"
                | "c_int" | "c_uint" | "c_long" | "c_ulong" | "c_size" ;

block = "{", { statement }, "}" ;
statement = binding | return-stmt | if-stmt | while-stmt
                | loop-stmt | for-stmt | match-stmt | asm-stmt | defer-stmt
                | break-stmt | continue-stmt | block | expr-stmt ;
binding = ( "let" | "const" ), IDENT, [ ":", type ], "=", expression, ";" ;
return-stmt = "return", [ expression ], ";" ;
if-stmt = "if", condition, block, [ "else", ( if-stmt | block ) ] ;
condition = ? no-struct ?, expression ;
while-stmt = "while", condition, block ;
loop-stmt = "loop", block ;
for-stmt = "for", IDENT, [ ":", type ], "in", range-bounds, block ;
range-bounds = ? no-struct ?, expression, ( ".." | "..=" ), expression ;
match-stmt = "match", condition, "{", { match-arm, [ "," ] }, "}" ;
match-arm = match-pattern, "=>", block ;
match-pattern = "_" | expression ;
asm-stmt = "asm", "(", STRING, ")", ";" ;
defer-stmt = "defer", expression, ";" ;
break-stmt = "break", ";" ;
continue-stmt = "continue", ";" ;
expr-stmt = expression, [ assignment-op, expression ], ";" ;
assignment-op = "=" | "+=" | "-=" | "*=" | "/=" | "%="
                | "&=" | "|=" | "^=" | "<<=" | ">>=" ;

expression = logical-or ;
logical-or = logical-and, { "||", logical-and } ;
logical-and = bit-or, { "&&", bit-or } ;
bit-or = bit-xor, { "|", bit-xor } ;
bit-xor = bit-and, { "^", bit-and } ;
bit-and = equality, { "&", equality } ;
equality = comparison, { ( "==" | "!=" ), comparison } ;
comparison = shift, { ( "<" | ">" | "<=" | ">=" ), shift } ;
shift = additive, { ( "<<" | ">>" ), additive } ;
additive = multiply, { ( "+" | "-" ), multiply } ;
multiply = cast, { ( "*" | "/" | "%" ), cast } ;
cast = unary, { "as", type } ;
unary = unary-op, unary | postfix ;
unary-op = "!" | "-" | "~" | "&" | "*" | "try" | "move" ;
postfix = primary, { postfix-op } ;
postfix-op = ? call-type-args ?, type-args, call-args
                | call-args | field-access | index ;
call-args = "(", [ expression, { ",", expression } ], ")" ;
field-access = ".", ( IDENT | INT ) ;
index = "[", expression, "]" ;
primary = literal | array-literal
                | ? struct-literal ?, struct-literal
                | ? generic-qualifier ?, generic-qualifier
                | IDENT | paren-expr ;
literal = INT | FLOAT | CHAR | STRING | "true" | "false" | "null" ;
array-literal = "[", expression, [ array-tail ], "]" ;
array-tail = ";", INT | ",", [ expression-list ] ;
expression-list = expression, [ ",", [ expression-list ] ] ;
struct-literal = type, "{", [ field-init-list ], "}" ;
field-init-list = field-init, [ ",", [ field-init-list ] ] ;
field-init = IDENT, ":", expression ;
generic-qualifier = type, field-access ;
paren-expr = "(", paren-body, ")" ;
paren-body = ? allow-struct ?, expression,
                [ ",", expression, [ ",", [ expression-list ] ] ] ;
```

### 3.3 Predicates and parsing modes

**Type-argument lookahead.** Let `angle-followed-by(p, F)` start at a `<` token
and scan forward counting depth: `<` adds 1, `>` subtracts 1, `>>` subtracts 2.
When the depth reaches exactly zero, the result is true if the following token is
`F`. The result is false if the depth goes below zero, or if the scan reaches `;`,
`{`, `}` or the end of the file first.

- `? call-type-args ?` holds at a `<` when `angle-followed-by` that `<` and `(`.
- `? struct-literal ?` holds at an `IDENT` when the mode is not *no-struct* and the
  next token is `{`, or the next token is `<` and `angle-followed-by` it and `{`.
- `? generic-qualifier ?` holds at an `IDENT` when the next token is `<` and
  `angle-followed-by` it and `.`.

The scan does not parse. Consequently `f(a < b, c > (d))` is the generic call
`f(a<b, c>(d))`, and a type argument containing an array type, whose `;` ends the
scan, cannot be written in expression position. Name the type with an alias.

**Angle close.** `? angle-close ?` closes one type-argument list. It consumes a
`>` token. Given a `>>` token it consumes the first `>` and leaves a `>`; given a
`>=` token it consumes the `>` and leaves a `=`. `>>=` is not split. Generic
parameter lists in declarations accept only a `>` token.

**No-struct mode.** `? no-struct ?` turns no-struct mode on and `? allow-struct ?`
turns it off, each until the end of the rule that contains it. It therefore holds
for the condition of `if` and `while`, the subject of `match`, and both bounds of
`for`, where a `{` would otherwise be ambiguous with the following block. Only a
parenthesized expression turns it back off: call arguments, indices and array
elements do not.

```mort reject="expected"
struct P { x: i64 }
fn main() -> int { if P { x: 1 }.x == 1 { return 1; } return 0; }
```

```mort run
struct P { x: i64 }
fn main() -> int { if (P { x: 1 }).x == 1 { print(1); } return 0; }
```

```output
1
```

```mort reject="expected"
struct P { x: i64 }
fn f(p: P) -> bool { return true; }
fn main() -> int { if f(P { x: 1 }) { return 1; } return 0; }
```

### 3.4 Syntactic constraints

The grammar derives two forms that a conforming parser must reject.

1. **Call target.** The expression before call arguments must be a *path*: an
   identifier, `null` written directly before the call, a generic qualifier, a
   path followed by a field access, or a parenthesized path. Calling the result of
   a call or an index is a compile-time error. Section 19.7 restricts what a path
   may name.
2. **Assignment target.** The left side of an assignment must be an identifier, a
   dereference, a field access or an index, possibly parenthesized.

```mort reject="only named functions can be called"
fn g() -> i64 { return 1; }
fn main() -> int { return g()(); }
```

```mort run
fn g() -> i64 { return 7; }
fn main() -> int { print((g)()); return 0; }
```

```output
7
```

```mort reject="invalid assignment target"
fn main() -> int { let a = 1; a + 1 = 2; return 0; }
```

### 3.5 Precedence

Operators bind as the expression rules nest, from loosest to tightest:

| Level | Operators | Associativity |
| --- | --- | --- |
| 1 | `\|\|` | left |
| 2 | `&&` | left |
| 3 | `\|` | left |
| 4 | `^` | left |
| 5 | `&` | left |
| 6 | `==` `!=` | left |
| 7 | `<` `>` `<=` `>=` | left |
| 8 | `<<` `>>` | left |
| 9 | `+` `-` | left |
| 10 | `*` `/` `%` | left |
| 11 | `as` | left |
| 12 | prefix `!` `-` `~` `&` `*` `try` `move` | right |
| 13 | call, field access, index | left |

Note that bitwise operators bind looser than comparison, and that `as` followed
by a named type reads a following `<` as type arguments.

```mort run
fn main() -> int { print(1 | 2 & 3); print(1 + 2 * 3); return 0; }
```

```output
3
7
```

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
the programmer's responsibility in Mort 0.41.

`[T; N]` is a fixed array of `N` values. `N` is a positive integer literal.
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
between integer and float types, between pointers, between pointers and integers,
and between integers and unit enums. Casting `null` is permitted only to a
pointer. Pointer casts do not make an invalid address safe. Section 19.8 is the
complete table.

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
must contain exactly one function `main` that takes no parameters, returns `int`,
and is not declared in a named module; freestanding programs are not required to
define `main`.

`extern fn` declares a native symbol with the target C ABI. Linking that symbol
is an implementation-driver responsibility. Calling an incompatible native
symbol has undefined foreign-interface behavior. Section 23 is the complete
foreign-function interface.

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
wildcard arm is present; other subject types need not be. Payload patterns bind
values by position; `_` ignores a payload or provides a whole-arm wildcard.
Duplicate or unreachable enum variants are compile-time errors. Section 19.6 gives
the rules.

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

Mort 0.41 does not specify the relative evaluation order of ordinary binary
operands, call arguments, aggregate fields, or array elements. Each such
subexpression is evaluated before the containing operation completes. Programs
whose result depends on that relative order are non-portable. Section 20 lists
what is ordered.

Equality and ordering produce `bool`. Aggregate equality is not defined and is
a compile-time error.
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
and shifts are not defined for floats. Mort 0.41 does not promise identical
NaN payloads or exceptional-status flags across targets.

### 7.3 Calls and builtins

A call must provide the declared number and type of arguments. Generic type
arguments may be explicit or inferred from parameter positions. Inference that
does not produce one unambiguous concrete instantiation is a compile-time
error.

Hosted builtins:

| Builtin | Meaning |
| --- | --- |
| `print(value)` | print an integer or float followed by newline; integers print as `i64`, so a `u64` of 2^63 or more prints negative |
| `println(*u8)` | print a zero-terminated byte string followed by newline |
| `eprintln(*u8)` | like `println`, on standard error |
| `assert(bool)` | terminate with a source-line diagnostic when false |
| `alloc(u64)` | allocate bytes, returning `*void` |
| `free(*void)` | release a prior allocation |
| `len(value)` | byte-string, array, or slice length |
| `slice(pointer, length)` | construct a typed slice |
| `sizeof<T>()` | target byte size of concrete `T` |
| `unix_time()` | Unix seconds as `i64` |
| `cpu_millis()` | process CPU milliseconds as `u64` |
| `file_*` | typed hosted file operations |
| `arg_count()` | number of command-line arguments, program name included, as `u64` |
| `arg_get(u64)` | zero-terminated argument string; an out-of-range index terminates with a source-line diagnostic |

`len`, `slice`, and `sizeof` are available in both profiles when their operands
are otherwise valid. File, allocation, time, printing, and assertion builtins
are unavailable in freestanding mode.

The `inb/outb`, `inw/outw`, and `inl/outl` builtins are privileged x86 port-I/O
operations. They are target-specific and not valid portable hosted behavior.

### 7.4 Volatile memory access

Volatile operations access memory that may change, or be observed, outside the
program, such as memory-mapped device registers. They are available in both
profiles.

| Builtin | Meaning |
| --- | --- |
| `volatile_read<T>(pointer)` | read one `T` through `*T` or `*const T`, returning `T` |
| `volatile_write<T>(pointer, value)` | write one `T` through `*T`, returning `void` |

The type argument is required. After alias resolution `T` must be exactly one
of `u8`, `u16`, `u32`, `u64`, `i8`, `i16`, `i32`, or `i64`. The pointer's pointee
must be exactly `T`, so `*void` or a pointer to another width needs an explicit
cast first. `value` must have type `T`, or be a literal that fits `T`. A
violation of any of these rules, or writing through `*const T`, is a
compile-time error that identifies the line and column of the call.

Mort 0.41 has no `unsafe` construct. Like raw dereference, these operations are
legal in any function, and the rules for pointer validity in section 4.2 apply.

Operands are evaluated once, before the access. Their relative order follows
section 7. Each executed operation then has these effects:

- **No elision.** It performs its access, even if the result is unused, the
  value written equals the value already stored, or an adjacent operation
  accesses the same location. Operations are not merged, duplicated, or
  introduced by the implementation.
- **No reordering relative to other volatile operations.** Within one thread of
  execution, volatile operations occur in program order. Volatile operations
  within one expression are ordered only as far as section 7 orders their
  operands.
- **Natural-width access only.** Each operation that the target can perform as
  a single naturally aligned access of `T`'s width is performed as that one
  access, and it does not tear. No guarantee against tearing exists beyond
  this: a width wider than the target's natural access, such as `u64` on 32-bit
  x86, may be performed as several smaller accesses in unspecified order.

Volatile operations are not atomic operations and do not synchronize threads.
They impose no ordering on non-volatile accesses and are not processor memory
barriers, so hardware that needs a barrier needs target-specific code such as
inline assembly. A misaligned or invalid pointer is raw pointer misuse under
section 10.

## 8. Ownership and cleanup

`resource struct R { ... }` declares a move-only resource type. The same
program must provide a compatible `destroy(*R) -> void` function. A type that
contains a resource by value is itself resource-bearing.

Section 21.3 and section 21.4 state these rules exactly. Resource values:

- cannot be copied implicitly, including out of a field or element of their owner;
- cannot be stored in globals;
- cannot be overwritten by assignment, except through a raw pointer;
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

`try expression` requires a `Result<Value, Error>` operand in a function that
returns `Result<Other, Error>` (section 19.10). On `Ok(payload)`, it evaluates to
the payload. On `Err(error)`, it runs cleanup for exited scopes and immediately
returns an `Err` with that error payload from the enclosing function.

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
- a negative runtime shift count;
- an out-of-range or non-finite float-to-fixed-integer cast.

A controlled failure must return a nonzero process status and emit a diagnostic
to standard error. Freestanding builds use a target trap because no process or
standard error exists. Section 22 gives the messages and statuses.

Raw pointer misuse, invalid inline assembly, an incompatible foreign symbol,
data races in foreign code, and violations explicitly delegated to a native API
are outside Mort 0.41's safety guarantees.

## 11. Implementation limits and portability

An implementation must document supported targets and backend prerequisites.
Mort 0.41's reference implementation emits C11, but C is not part of the
language semantics and another backend may be conforming.

<!-- PLACEHOLDER: front-end resource limits. The fix/frontend-crashes branch adds
the reference implementation's numeric limits here (generic instantiation, literal
size, constant size). Keep this comment until that branch merges, then delete it. -->

Portable Mort source must not depend on:

- C spelling or generated symbol names, or a layout except as guaranteed by
  section 21.7 at a declared and documented FFI boundary;
- ordinary operand or argument evaluation order;
- `c_*` widths beyond the target ABI;
- pointer size or integer-to-pointer validity;
- target inline assembly or port I/O;
- host filesystem, clock, allocator, or locale behavior not specified by the
  corresponding API.

## 12. Hosted concurrency

Concurrency is available only in the hosted profile. The portable public API is
provided by `std.thread`, `std.mutex`, and `std.atomic`. Their backing builtins
are reserved implementation interfaces.

### 12.1 Threads

A thread callback has type `fn(*void)->i64`. `thread_spawn(callback, context)`
starts one callback invocation and returns an opaque `*void` handle, or `null`
if the host cannot create the thread. The callback's result is retained by the
handle. `thread_join(handle)` waits for completion, releases the handle, and
returns that result. A handle must be joined exactly once. Joining a null,
already joined, or otherwise invalid handle is an invalid program and may
produce a controlled concurrency failure.

`std.thread.Thread` is a move-only resource wrapper. `thread.spawn` converts
creation failure to an assertion failure. `thread.join(&value)` joins early and
marks the wrapper empty; otherwise its destructor joins automatically. Thus a
live `Thread` cannot be silently detached in Mort 0.41.

The context pointer and every object reachable through it must remain valid
until the thread has been joined. Raw pointers do not acquire lifetime
protection merely because they cross a thread boundary.

`thread.sleep_millis(duration)` suspends the current thread for at least the
requested host-clock duration, except that host scheduling and clock failures
may delay it further.

All evaluations sequenced before a successful spawn **happen before** the first
callback evaluation. All callback evaluations happen before evaluations
sequenced after a successful join of that thread.

### 12.2 Mutexes

`std.mutex.Mutex` is a move-only, non-recursive mutual-exclusion resource.
`mutex.lock(&value)` blocks until the calling thread owns the mutex;
`mutex.unlock(&value)` releases it. Only the owning thread may unlock it. A
thread must not recursively lock the same mutex, destroy a locked mutex, or
access a destroyed handle.

An unlock operation on a mutex synchronizes with the next successful lock of
that mutex. Evaluations sequenced before the unlock happen before evaluations
sequenced after that lock.

### 12.3 Atomics

`std.atomic.AtomicI64` is a move-only heap-backed atomic signed 64-bit integer.
It provides `load`, `store`, `exchange`, `fetch_add`, `fetch_sub`, and
`compare_exchange`. All operations are sequentially consistent. Therefore all
atomic operations participate in one total order consistent with each thread's
sequenced order.

`fetch_add` and `fetch_sub` return the value before modification and use the
same modulo-2^64 wrapping semantics as ordinary `i64`. `compare_exchange`
changes the stored value to `desired` only when it equals `expected`, returning
whether the exchange occurred.

An atomic handle must not be used after destruction. Atomic operations on a
null or invalid handle produce a controlled concurrency failure or constitute
invalid raw-handle use.

### 12.4 Data-race rule

Two memory actions conflict when they access overlapping non-atomic storage,
occur in different threads, and at least one writes. A Mort execution has a
data race when conflicting actions are not ordered by the happens-before
relations in this section.

A program that can execute a data race is invalid. Implementations are not
required to diagnose it in an ordinary build, and no behavior is guaranteed
after the race. Portable concurrent programs must communicate through
`AtomicI64`, protect conflicting storage with `Mutex`, or establish an
equivalent native synchronization relation at a documented FFI boundary.

ThreadSanitizer is a verification aid, not a replacement for this rule.
Conforming implementations should provide a race-detection build mode when the
target toolchain supports one.

## 13. Hosted networking

Networking is available only in the hosted profile. Mort 0.41 specifies
blocking TCP streams, UDP datagrams, and host-name resolution through the
portable `std.net` module. The `net_*` builtins are reserved implementation
interfaces.

Network payloads are byte sequences. This version does not implicitly decode
text, frame messages, encrypt traffic, or convert application integers to
network byte order.

### 13.1 Socket ownership

`std.net.Socket` is a move-only resource containing one opaque native socket
handle. Its destructor closes a live handle exactly once. `net.destroy`
may close it early and marks the wrapper empty. A socket must not be used after
it is moved, destroyed, or closed.

The backing `net_socket_close` operation accepts `null` as a no-op. Any other
invalid raw handle is outside the safety guarantees of the public module.
Socket closure releases the native handle but does not guarantee that pending
data has reached its peer.

### 13.2 Name resolution, connections, and listeners

`net.tcp_connect(host, port)` resolves the NUL-terminated UTF-8-compatible host
byte string using the operating system's name service. It tries suitable IPv4
and IPv6 TCP addresses until one connects. Resolution or connection failure is
converted to a controlled assertion failure by the public wrapper.

`net.tcp_listen(host, port, backlog)` resolves and binds the requested local
host, enables address reuse where the platform supports it, and starts a TCP
listener. Port zero asks the host to choose an available ephemeral port.
`net.local_port(&socket)` returns the bound port in host integer order, or zero
when it cannot be queried. Listener creation failure is a controlled assertion
failure.

`net.accept(&listener)` blocks until it accepts one connection. Failure is a
controlled assertion failure. Connecting to `"localhost"` must use host-name
resolution; portable programs must not assume whether IPv4 or IPv6 is selected.

The `host` pointer and its terminating zero byte must remain readable for the
duration of `tcp_connect` or `tcp_listen`.

### 13.3 Stream I/O

`net.send(&socket, buffer, length)` and `net.receive(&socket, buffer, length)`
are blocking byte-stream operations. A positive result is the number of bytes
transferred and may be less than `length`. A send or receive failure returns
`-1`. A receive result of zero indicates an orderly peer close, except that a
zero-length request also returns zero.

`net.send_all` repeats sends until exactly `length` bytes have been accepted by
the host socket or an operation returns zero or `-1`. `net.receive_exact`
similarly repeats receives. They return `true` only after transferring the
entire requested length. A successful send does not imply remote processing or
durable storage.

The buffer must remain valid for the complete operation and must contain at
least `length` readable bytes for sending or writable bytes for receiving.
Mort suppresses process termination from a broken-pipe signal where the target
socket API permits it and reports the send as a failure instead.

`net.shutdown(&socket)` requests shutdown in both directions and reports
whether the host accepted the request. It does not release ownership; the
socket must still be destroyed.

### 13.4 UDP datagrams

`net.udp_bind(host, port)` creates a UDP socket bound to the requested local
host and port. Port zero asks the host to choose an ephemeral port.
`net.udp_connect(host, port)` creates a UDP socket with a default peer; this
permits the ordinary `send` and `receive` operations but does not perform a
handshake or prove that the peer exists.

`net.send_to(&socket, host, port, buffer, length)` resolves `host` and sends one
datagram to a compatible resolved address. A successful result equals
`length`; `-1` indicates failure. Sending a datagram is atomic at this API
boundary: it must not report a positive partial transfer.

`net.receive_from` receives one datagram and writes its numeric source address
as a NUL-terminated byte string plus its source port. The source-host buffer
must have nonzero capacity; portable callers should provide at least 46 bytes
so any IPv4 or IPv6 numeric address fits. The result is the number of payload
bytes written or `-1` on failure.

Datagram boundaries are preserved. If a datagram is larger than the supplied
payload buffer, exactly the bytes that fit are returned and the rest of that
datagram is discarded. A zero result is a valid zero-length datagram and does
not mean end-of-stream. If conversion of the source address cannot fit or
fails, the operation returns `-1` after consuming the datagram.

UDP provides no language-level guarantee of delivery, uniqueness, ordering,
peer liveness, congestion behavior, or path MTU. Applications needing those
properties must implement a protocol above UDP. Maximum accepted datagram size
and fragmentation behavior are host and network properties.

### 13.5 Nonblocking operation and readiness

`net.set_nonblocking(&socket, enabled)` changes whether native operations on
the socket may wait for readiness and reports whether the host accepted the
change. In nonblocking mode, an operation that cannot make immediate progress
returns `-1`.

`net.would_block()` reports whether the current thread's most recent failed
socket operation failed specifically because it would block. Its result is
meaningful only immediately after that operation and before any other socket
operation on the same thread.

`net.wait(&socket, readable, writable, timeout_millis)` waits for requested
read or write readiness. `wait_readable` and `wait_writable` are single-event
conveniences. The result is a bit mask:

- bit 0 (`1`) means readable, including a stream end that can be received;
- bit 1 (`2`) means writable;
- bit 2 (`4`) means a hangup, invalid descriptor, or asynchronous socket error.

Zero means the timeout elapsed. `-1` means the wait itself failed or no event
was requested. A timeout of `-1` waits indefinitely, zero only polls current
state, and a positive value is an approximate upper bound in milliseconds;
host scheduling and repeated signal interruption may delay return.

Readiness is advisory. Another thread or consumer may change socket state
before the next operation. Event-driven code must keep the socket nonblocking,
handle a subsequent would-block result, preserve its buffers, and wait again.

### 13.6 Concurrency and portability

Blocking name resolution and socket calls may suspend the calling thread for
an operating-system-defined duration. Readiness waits provide bounded waiting
for established sockets but do not impose DNS, connection, or scheduling
fairness guarantees.

Distinct sockets may be used by distinct threads. Portable programs must
synchronize concurrent operations on the same `Socket` wrapper and keep the
wrapper alive until all such operations finish. The concurrency happens-before
and data-race rules in Section 12 apply to socket buffers and wrapper storage.

Conforming hosted implementations must provide these TCP, UDP, DNS,
nonblocking, and readiness operations on Windows, Linux, and macOS. Resolver
policy, address ordering, firewall behavior, interface availability, routing,
and failures beyond the process boundary are host environment properties
rather than deterministic language behavior.

## 14. Structured tasks and cooperative cancellation

The hosted `std.task` module provides task groups for bounded concurrent work.
Its backing thread and atomic builtins remain reserved implementation
interfaces.

### 14.1 Task-group ownership

`task.new()` creates a move-only `TaskGroup` with no children and an
uncancelled sequentially consistent cancellation flag. A group owns every
native task handle successfully added to it.

`task.spawn(&group, callback, context)` starts one `fn(*void)->i64` callback and
returns `true`. It returns `false` without adding a child if the group is
already cancelled or the host cannot create the task. Children are retained in
spawn order. A group must have one owning thread; `spawn`, `join_all`, `count`,
and destruction must not execute concurrently on the same group.

The context pointer and all data reachable through it must remain valid until
that child has been joined. A pointer to the group remains valid until all
children finish because group destruction joins them before releasing group
storage.

### 14.2 Cancellation

`task.cancel(&group)` atomically and idempotently requests cancellation.
`task.cancelled(&group)` observes that flag with sequentially consistent
ordering. All children may call `cancelled` concurrently.

Cancellation is cooperative: it does not terminate a thread, unwind its stack,
close its resources, or interrupt a native blocking operation. A cancellable
child must check the flag at documented progress points. For I/O, portable
tasks should use nonblocking sockets and finite readiness waits so they regain
control and observe cancellation.

After cancellation, new spawns are rejected for the lifetime of that group.

### 14.3 Joining and structured exit

`task.count(&group)` reports the number of retained, unjoined children.
`task.join_all(&group)` joins every retained child in spawn order, adds their
`i64` results with ordinary wrapping semantics, clears the child list, and
returns the sum. Calling it on an empty group returns zero.

The `TaskGroup` destructor first requests cancellation, then joins every
remaining child, destroys the cancellation flag, and releases its handle
storage. Consequently no task spawned into a group can outlive that group's
lexical scope, including exits through `return`, `break`, `continue`, or error
propagation. A non-cooperative child that never returns can keep joining or
scope exit blocked; Mort does not forcibly kill it.

Task-group spawn and join inherit the happens-before edges in Section 12.
Cancellation observation uses the sequentially consistent atomic order but
does not by itself make unrelated non-atomic shared storage race-free.

## 15. Bounded HTTP/1.1

The hosted `std.http` module provides a byte-oriented, one-message HTTP/1.1
subset over `std.net.Socket`. It is deliberately bounded by caller-provided
buffers and uses `Connection: close`. It does not imply TLS; cleartext HTTP and
authenticated HTTPS are distinct protocols.

### 15.1 Message writing

`http.build_request` serializes into an initially empty `Vec<u8>`, and
`http.write_request` sends the same serialization over a socket:

1. a caller-provided method and request target;
2. an HTTP/1.1 `Host` header;
3. exactly one decimal `Content-Length`;
4. `Connection: close`;
5. exactly `body_length` payload bytes.

The method must be a nonempty HTTP token. The target and host must be nonempty
and contain no whitespace, control characters, carriage return, or line feed.
Invalid inputs, a nonempty output vector, a null nonempty body, or a socket
write failure return `false` without claiming success. These checks prevent
caller data from injecting additional header lines.

`http.write_response` similarly writes an HTTP/1.1 status line, one
`Content-Length`, a `Content-Type`, `Connection: close`, and the exact body.
The status must be from 100 through 999. Reason and content-type values must not
contain carriage return, line feed, or disallowed controls.

Successful message writes mean that all serialized bytes were accepted by the
local socket. They do not prove peer receipt or application processing.

### 15.2 Framing and parsing

`http.find_header_end` returns the index immediately after the first
`"\r\n\r\n"` delimiter or `-1`. `http.status_code` accepts an HTTP/1.0 or
HTTP/1.1 response prefix and returns its three-digit code or `-1`.

`http.content_length` finds a case-insensitive, line-start
`Content-Length:` field, permits optional horizontal whitespace around its
decimal value, and returns a nonnegative `i64`. It returns `-1` for a missing,
duplicate, malformed, negative, or overflowing length.

`http.framed_length` returns header length plus content length only when both
are valid. It rejects any `Transfer-Encoding` field, even when a
`Content-Length` is also present. This prevents ambiguous framing from being
accepted by the bounded subset.

`http.read_message` reads until exactly one valid framed message is available.
The complete header and payload must fit the caller's buffer. Missing or
ambiguous framing, premature stream end, buffer exhaustion, or socket failure
returns `-1`. A successful result is the message length; the body starts at
`find_header_end`.

`http.read_headers` reads one byte at a time through the first complete header
delimiter. It is intended for protocol upgrades where no content-length body
follows and must not consume bytes from the upgraded protocol.

The reader may consume bytes beyond the returned message if a peer pipelines
data into the same native receive. Therefore portable use of this subset must
send one message per `Connection: close` connection and must not pipeline.

### 15.3 Convenience client and scope

`http.get(host, port, target, buffer, capacity)` opens a TCP connection, writes
a bodyless `GET`, reads one bounded response, and closes the socket at scope
exit. It returns the message length or `-1`. The caller chooses the port;
`http.get` does not perform TLS or infer schemes. Verified HTTPS is a separate
hosted API defined in Section 18.

Mort 0.41 does not define chunked transfer coding, informational-response
chains, persistent connections, pipelining, trailers, multipart parsing,
content decoding, redirects, cookies, authentication, proxies, URI parsing,
cache policy, or HTTP/2 and HTTP/3. Applications must not silently treat this
bounded API as support for those features.

## 16. Operating-system secure randomness

The hosted `std.crypto` module provides cryptographically secure random bytes
from the operating system. `crypto.random_fill(buffer, length)` fills exactly
`length` writable bytes and returns `true`, or returns `false` when the host
cannot provide entropy. A null buffer is valid only for length zero.

Each successful nonzero request must use the host's cryptographic random
generator and must not fall back to clocks, deterministic pseudorandom
generators, process identifiers, or uninitialized memory. The reference
implementation uses the system-preferred CNG generator on Windows and the
operating system random device on Linux and macOS.

`crypto.random_u64()` obtains eight secure bytes and interprets them in
little-endian order. It fails through a controlled assertion if entropy is
unavailable.

Secure randomness is nondeterministic. No particular byte value, uniqueness
between calls, or exact distribution can be asserted by a conformance case;
security depends on the host generator. Callers must check fallible fill
operations and must not treat a returned random value as secret after exposing
or copying it into unprotected storage.

## 17. Bounded WebSocket

The hosted `std.websocket` module implements the RFC 6455 HTTP/1.1 upgrade and
single-frame data/control messages over an established `std.net.Socket`.
`ws://` is cleartext. Mort provides a verified TLS client separately, but this
WebSocket API does not adapt its `Socket` parameter to a TLS stream and
therefore does not itself provide `wss://`.

### 17.1 Handshake

`websocket.client_handshake` generates a fresh 16-byte OS-random nonce, sends a
version-13 upgrade request with a Base64 key, reads a bounded response header,
requires status 101, validates `Upgrade` and `Connection`, and verifies the
server's `Sec-WebSocket-Accept`.

`websocket.server_handshake` reads a bounded request header, requires an
HTTP/1.1 `GET`, version 13, unique required headers, a valid Base64 encoding of
exactly 16 key bytes, and the required upgrade tokens. It replies with status
101 and the computed accept value.

Header names and required token values are case-insensitive. Duplicate
required headers are rejected. Host and target inputs must be nonempty and
contain no whitespace or controls, preventing header injection.

`websocket.accept_key` exposes the RFC-defined SHA-1 plus Base64 transform for
interoperability testing. SHA-1 in this transform is not an authentication,
signature, password, or integrity primitive and must not be reused as one.

Neither handshake validates `Origin`, negotiates subprotocols or extensions,
performs HTTP authentication, follows redirects, or adds TLS. Server
applications are responsible for their own origin and authorization policy.

### 17.2 Frames

`websocket.send(socket, client_side, opcode, payload, length)` sends one final
text (`1`), binary (`2`), close (`8`), ping (`9`), or pong (`10`) frame.
Client-side frames use a fresh OS-random four-byte mask; server-side frames are
unmasked. Control payloads are limited to 125 bytes. Text payloads and close
reasons must be well-formed UTF-8, and close status codes must be valid.

Payload lengths use the shortest valid 7-bit, 16-bit, or 64-bit encoding.
Lengths above signed 63-bit range are rejected. A successful send means all
frame bytes were accepted by the local socket.

`websocket.receive(socket, expect_masked, output, capacity, opcode)` reads and
validates one frame, unmasks when required, writes at most the caller-provided
capacity, stores the opcode, and returns payload length. It returns `-1` for
wrong-direction masking, reserved bits or opcodes, fragmentation, nonminimal
length encoding, oversized payload, malformed UTF-8, invalid close payload,
premature end, or socket failure.

When a frame exceeds capacity or is otherwise rejected before its full payload
is consumed, the connection is no longer synchronized and must be closed.

### 17.3 Scope

Mort 0.41 does not define fragmented messages, continuation frames, negotiated
extensions, per-message compression, subprotocol selection, automatic
ping/pong handling, automatic close replies, reconnect policy, multiplexing,
or `wss://`. Applications explicitly drive control frames and close handling.

## 18. Verified client TLS and HTTPS

TLS is available only in the hosted profile. The `std.tls` module provides a
blocking, move-only client stream. A successful connection authenticates the
server certificate chain, checks certificate validity time, verifies the
requested DNS name or IP address, sends that same name as SNI when applicable,
and negotiates TLS 1.2 or TLS 1.3. Verification is mandatory; this API has no
option to accept an untrusted certificate or skip identity matching.

The reference backend is checksum-pinned Mbed TLS 3.6.6 LTS. Its default trust
set is the checksum-pinned Mozilla CA bundle from certifi 2026.6.17. The
compiler verifies both upstream payload hashes before a one-time native build,
caches the result by compiler, platform, architecture, and build flags, and
links it only when TLS is used. Updating either security payload changes the
cache identity.

### 18.1 Connections and trust

`tls.connect(hostname, port)` performs DNS resolution, TCP connection, and the
verified TLS handshake, returning a `Stream`. `hostname` must be non-null,
nonempty, and NUL-terminated, and `port` must be nonzero. Failure produces a
controlled assertion in the standard wrapper; the lower-level
`tls_client_connect` builtin returns null.

`tls.connect_with_ca(hostname, port, ca_data, ca_length)` has identical
verification rules but replaces the default roots for that connection with
the caller-provided PEM CA set. The buffer must be non-null and nonempty.
Malformed roots, an incomplete or untrusted chain, an expired or not-yet-valid
certificate, an unsuitable certificate, or a hostname/IP mismatch must fail
the handshake. The custom-root API is for private services and tests; it is not
an insecure-verification switch.

Certificate-time checks depend on an accurate host clock. The embedded Mozilla
roots are a release snapshot, so applications must update Mort and rebuild TLS
programs when trust-store security updates are published.

### 18.2 Transfers and cleanup

`tls.send(stream, buffer, length)` and
`tls.receive(stream, buffer, capacity)` perform one blocking TLS transfer and
return the accepted byte count. Positive transfers may be partial. A zero
receive means an authenticated TLS close notification. `-1` means a transport
or TLS failure, including an unauthenticated TCP end that could truncate the
secured stream. A null buffer is valid only for zero length. `send_all` and
`receive_exact` loop until the full caller-selected length is transferred or
an error/end occurs.

Every receive is bounded by the caller's capacity. `tls.protocol_version`
returns `12` or `13` for the negotiated TLS version and zero for no recognized
active version. A `Stream` is a move-only resource. Its destructor attempts a
TLS close notification, closes the TCP socket, clears cryptographic state, and
releases all native allocations.

### 18.3 Bounded HTTPS

`std.https` combines `std.tls` with the bounded HTTP/1.1 rules from Section 15.
`https.get` uses the default verified roots; `https.get_with_ca` uses explicit
roots. Both serialize an injection-checked `GET`, require unambiguous
`Content-Length` framing, read no more than the caller's response capacity,
and close the TLS stream at lexical scope exit.

`https.write_request` and `https.read_message` expose the corresponding
operations on an established TLS stream. They retain the HTTP subset's
`Connection: close` and one-message restrictions.

Mort 0.41 does not define TLS servers, mutual TLS, application-controlled
cipher suites, ALPN, certificate pinning, revocation policy, session
resumption controls, early data, nonblocking TLS, key logging, QUIC, HTTP/2,
HTTP/3, or a WebSocket-over-TLS adapter. Applications needing those facilities
must use a reviewed native library through C interoperability and must not
weaken the verification guarantees of `std.tls`.

## 19. Static semantics

This section is the formal statement of the type rules. Prose in sections 4
through 9 is informative wherever it summarises a rule stated here. Every rule
here has a compiled example or a test in `tests/test_spec_conformance.py`.

### 19.1 Notation

`Γ` is the environment of bindings in scope. `Γ ⊢ e : T` means `e` has type `T`.
`Γ ⊢ e ⇐ T` means `e` is acceptable where `T` is required (section 19.3). `lit(e)`
means `e` is a *constant integer expression*: an integer or character literal, or
`-`, `~`, or a binary `+ - * / % & | ^ << >>` applied to constant integer
expressions. Types are compared after alias expansion. `I` stands for any integer
type, `F` for `f32` or `f64`.

```text
                                          value of n is arbitrary precision
(T-Int)    Γ ⊢ n : i64       lit(n)
(T-Char)   Γ ⊢ 'c' : u8      lit('c')
(T-Float)  Γ ⊢ 1.5 : f64
(T-Bool)   Γ ⊢ true : bool             Γ ⊢ false : bool
(T-Str)    Γ ⊢ "s" : *u8
(T-Null)   Γ ⊢ null : null             null has no written type

(T-Var)    x : T ∈ Γ   ⟹   Γ ⊢ x : T   x is not moved at this point (21.3)
```

### 19.2 Inference

There is no inference across statements. An annotation-free binding takes the
type of its initializer, and the initializer is typed without any information
from later uses. A generic argument is inferred only from a call's parameter
positions (section 19.5). Consequently an unannotated integer literal is `i64` and an
unannotated character literal is `u8`.

```text
(Let)      Γ ⊢ e : T    T ∉ { void, null }   ⟹   let x = e;    binds x : T
(Let-Ann)  Γ ⊢ e ⇐ T   ⟹   let x: T = e;    binds x : T
```

An array annotation requires an array literal initializer. A `const` binding
behaves as `let` but cannot be assigned, even through a field or element, and
`&` of it yields a `*const` pointer (section 19.9).

```mort run
fn main() -> int {
    let a = 1;
    let c = 'a';
    print(a + 1);
    print(c as i64);
    return 0;
}
```

```output
2
97
```

```mort reject="null bindings require an explicit pointer type"
fn main() -> int { let p = null; return 0; }
```

```mort reject="cannot bind a void value"
fn v() -> void {}
fn main() -> int { let a = v(); return 0; }
```

```mort reject="an array must be initialised with an array literal"
fn main() -> int { let b = [1, 2]; let a: [i64; 2] = b; return 0; }
```

### 19.3 Coercion

These are the only implicit conversions:

```text
(C-Same)    Γ ⊢ e : T                                ⟹  Γ ⊢ e ⇐ T     e is not a resource binding
(C-Null)    Γ ⊢ null : null,   T a pointer or fn     ⟹  Γ ⊢ null ⇐ T
(C-Int)     lit(e),   value of e fits I              ⟹  Γ ⊢ e ⇐ I
(C-Float)   e is a FLOAT token,   finite in F        ⟹  Γ ⊢ e ⇐ F
(C-Const)   Γ ⊢ e : *U                               ⟹  Γ ⊢ e ⇐ *const U
(C-Tuple)   e = (e1, ..., en),   Γ ⊢ ei ⇐ Ti         ⟹  Γ ⊢ e ⇐ (T1, ..., Tn)
```

A literal value that does not fit the required type is a compile-time error rather
than a conversion. A negated float such as `-1.5` is not a `FLOAT` token, so it
has type `f64` and does not adopt `f32`. No implicit conversion exists from `*T` to
`*void`, from `*const T` to `*T`, from `[]T` to `[]const T`, between integer widths,
from an array to a pointer, or between `bool` and any integer.

```mort reject="does not fit in i8"
fn main() -> int { let a = 1 as i8; print((a + 200) as i64); return 0; }
```

```mort reject="is annotated f32"
fn main() -> int { let f: f32 = -1.5; return 0; }
```

```mort check
fn take(p: *const i64) -> void {}
fn main() -> int {
    let f: f32 = 1.5;
    let g = f + 1.0;
    let x = 1;
    take(&x);
    return 0;
}
```

```mort reject="expects *void, got *i64"
fn f(p: *void) -> void {}
fn main() -> int { let x = 1; f(&x); return 0; }
```

```mort reject="expects []const i64"
fn f(s: []const i64) -> void {}
fn main() -> int { let a = [1, 2]; f(slice(&a[0], 2)); return 0; }
```

### 19.4 Operators

```text
(Arith)    Γ ⊢ a : I   Γ ⊢ b : I   op ∈ + - * / % & | ^           ⟹  Γ ⊢ a op b : I
(Arith-F)  Γ ⊢ a : F   Γ ⊢ b : F   op ∈ + - * /                   ⟹  Γ ⊢ a op b : F
(Lit-L)    lit(a)   ¬lit(b)   Γ ⊢ a ⇐ I   Γ ⊢ b : I              ⟹  a takes type I
(Lit-R)    ¬lit(a)  lit(b)    Γ ⊢ b ⇐ I   Γ ⊢ a : I              ⟹  b takes type I
(Lit-LL)   lit(a)   lit(b)   types differ                         ⟹  Γ ⊢ a op b : i64
(Float-L)  one operand a FLOAT token, the other f32               ⟹  the token takes f32
(Shift)    Γ ⊢ a : I   Γ ⊢ b : J   op ∈ << >>                     ⟹  Γ ⊢ a op b : I
(Cmp)      Γ ⊢ a, b : numeric of the Arith rules, op ∈ < > <= >=   ⟹  Γ ⊢ a op b : bool
(Eq)       a, b unify as in Arith, or have one identical type T,
           T not struct, tuple, array, slice, or payload enum     ⟹  Γ ⊢ a == b : bool
(Eq-Null)  one of a, b is null, the other a pointer or fn value   ⟹  Γ ⊢ a == b : bool
(Bool)     Γ ⊢ a : bool   Γ ⊢ b : bool   op ∈ && ||               ⟹  Γ ⊢ a op b : bool
(Not)      Γ ⊢ a : bool                                           ⟹  Γ ⊢ !a : bool
(Neg)      Γ ⊢ a : I or F                                         ⟹  Γ ⊢ -a : same
(BitNot)   Γ ⊢ a : I                                              ⟹  Γ ⊢ ~a : I
```

`%`, `&`, `|`, `^`, `<<`, `>>` and `~` do not apply to floats. Pointers support only
`==` and `!=`. A constant integer expression is evaluated with arbitrary precision
before its range is checked. `<` and the other ordering operators do not produce
a constant integer expression. A constant zero divisor and a constant negative shift
count are compile-time errors.

```mort run
fn main() -> int {
    let a = 1 as i8;
    print((a + 1) as i64);
    let u = 1 as u8;
    print((u << 3) as i64);
    print(-7 / 2);
    print(-7 % 2);
    return 0;
}
```

```output
2
8
-3
-1
```

```mort reject="mismatched integer types i8 and i16"
fn main() -> int { let a = 1 as i8; let b = 2 as i16; print((a + b) as i64); return 0; }
```

```mort reject="cannot mix integer and float"
fn main() -> int { let f = 1.5; let g = f + 1; return 0; }
```

```mort reject="operator '<' requires int operands"
fn main() -> int { let a = 1; let p = &a; if p < p {} return 0; }
```

```mort reject="cannot compare aggregate values"
fn main() -> int { if (1, 2) == (1, 2) {} return 0; }
```

```mort reject="integer division by zero"
fn main() -> int { print(1 / 0); return 0; }
```

### 19.5 Generics and the absence of traits

Mort has no traits, interfaces, methods, or bounds on generic parameters. A generic
parameter names a type that is chosen at each use, and the body is checked
separately for each concrete instantiation. A generic function that is never
instantiated is never checked. A type error in an instantiated body is reported at
that instantiation.

```text
(Gen-Call)  f<T1..Tk>(p1: P1, ..., pn: Pn) -> R,   σ = [Ti := Ui]
            explicit <U1..Uk>   or   Pj unifies with actual type of ej
            ⟹  Γ ⊢ f(e1, ..., en) : σ(R),   and each ej must be ⇐ σ(Pj)
```

Unification walks pointers (a `*T` parameter does not match a `*const` argument, while a
`*const T` parameter matches both), slices, arrays of equal length, function types,
tuples and generic instantiations. A generic parameter that occurs twice must
unify with one identical type. Inference uses the *actual* type of an argument, so a
literal contributes `i64` and does not adopt its neighbour's type. Inference never
uses the expected result type. `void` is not a valid type argument. A generic
function cannot be used as a value.

Enums and structs are always instantiated explicitly, for example `E<i64>.A(1)` and
`B<i64> { v: 1 }`. Instantiating a generic type also produces its fields' types by
substitution. A by-value cycle among instantiations is a compile-time error.

```mort run
fn id<T>(x: T) -> T { return x; }
fn first<T>(p: *T) -> T { return p[0]; }
struct B<T> { v: T }
fn unbox<T>(b: B<T>) -> T { return b.v; }
fn main() -> int {
    print(id(1));
    print(id<i32>(2) as i64);
    let a = [5, 6];
    print(first(&a[0]));
    print(unbox(B<i64> { v: 8 }));
    return 0;
}
```

```output
1
2
5
8
```

```mort check
fn never_instantiated<T>(a: T) -> T { return nope; }
fn main() -> int { return 0; }
```

```mort reject="operator '+' requires int operands"
struct S { x: i64 }
fn add<T>(a: T, b: T) -> T { return a + b; }
fn main() -> int { let s = S { x: 1 }; add(s, s); return 0; }
```

```mort reject="cannot infer generic parameter"
fn f<T>() -> void {}
fn main() -> int { f(); return 0; }
```

```mort reject="cannot consistently infer"
fn f<T>(a: T, b: T) -> T { return a; }
fn main() -> int { let x = 1 as u8; f(x, 1); return 0; }
```

```mort reject="cannot consistently infer"
fn f<T>(a: *T) -> void {}
fn main() -> int { const a = 1; f(&a); return 0; }
```

```mort reject="cannot be used directly as a value"
fn f<T>(a: T) -> T { return a; }
fn main() -> int { let p = f; return 0; }
```

```mort reject="expected 'fn'"
trait T {}
fn main() -> int { return 0; }
```

### 19.6 Algebraic data types and exhaustiveness

A struct literal must name every field exactly once, in any order. An enum has at
least one variant. A unit variant is the expression `E.V` and a payload variant the call
`E.V(e1, ..., en)` with one expression per payload type. Unit enums are nominal: two
unit enums are not interchangeable, and they do not coerce to integers. Struct and
enum declarations may refer to one another freely, in any source order, but a by-value
cycle is a compile-time error. A cycle through a pointer, slice or function type is
permitted.

A match subject may be an integer, float, `bool`, enum or function value. It may not be
a struct, tuple, array, slice or pointer. An arm's pattern is one of:

- `_`, which must be the final arm and appear at most once;
- `E.V` for a unit variant, or `E.V(a, b, _)` for a payload variant, binding by
  position, with one identifier or `_` per payload;
- any other expression, which is compared to the subject with `==`.

```text
(Match-Enum)  subject : E,  every variant of E named exactly once or a final _   ok
```

A variant named twice is a compile-time error. An enum match with no wildcard that omits a
variant is a compile-time error naming the missing variants. A match on any other subject
type need not be exhaustive: when no arm matches, execution continues after the match.

A function that must return a value is checked conservatively: a statement sequence
returns if it ends in `return`, a block that returns, an `if` with `else` whose branches
both return, an exhaustive `match` whose arms all return, or a `loop`/`while true` that
contains no `break` of its own.

```mort run
enum Shape { Dot, Circle(i64), Rect(i64, i64) }
fn area(s: Shape) -> i64 {
    match s {
        Shape.Dot => { return 0; }
        Shape.Circle(r) => { return 3 * r * r; }
        Shape.Rect(w, _) => { return w * 2; }
    }
}
fn main() -> int {
    print(area(Shape.Circle(2)));
    print(area(Shape.Rect(4, 9)));
    match 3 { 1 => { print(100); } }
    print(7);
    return 0;
}
```

```output
12
8
7
```

```mort reject="non-exhaustive match on E; missing: B"
enum E { A, B }
fn main() -> int { match E.A { E.A => {} } return 0; }
```

```mort reject="more than once"
enum E { A, B }
fn main() -> int { match E.A { E.A => {} E.A => {} E.B => {} } return 0; }
```

```mort reject="the wildcard match arm must be last"
enum E { A, B }
fn main() -> int { match E.A { _ => {} E.A => {} } return 0; }
```

```mort reject="payload match patterns require 2 binding names"
enum E { A(i64, i64) }
fn main() -> int { match E.A(1, 2) { E.A(x) => {} } return 0; }
```

```mort reject="requires a payload"
enum E { A(i64), B }
fn main() -> int { match E.B { E.A => {} E.B => {} } return 0; }
```

```mort reject="aggregate by-value cycle"
struct S { a: S }
fn main() -> int { return 0; }
```

```mort reject="may finish without returning"
fn f(a: bool) -> i64 { if a { return 1; } }
fn main() -> int { return 0; }
```

```mort check
fn f() -> i64 { loop { return 1; } }
fn main() -> int { return f(); }
```

### 19.7 Names and calls

Top-level functions, globals, structs, enums and aliases are visible throughout a
program, in any source order. Functions and globals share one namespace with each other;
a function may not reuse the name of a builtin. A local may shadow a global, or a
binding of an enclosing block, but not a binding of its own block or a parameter of
its own function. A test block is always type checked, even outside `mortc test`.

A call's path selects, in this order:

1. a builtin, when the path is a single builtin name, and a binding of that name does
   not affect this;
2. an enum variant, when the path is `E.V` for enum `E`;
3. a binding with a function type, called indirectly, when the path is one identifier;
4. a function of the current module, then a global function.

Member paths other than enum variants are never field reads: `a.b(1)` requires `a` to
be an import alias. A function-typed field must first be read into a named binding. A
non-generic function is called only with its declared arguments and accepts no type
arguments. Generic builtins take explicit type arguments.

```mort run
fn g(x: i64) -> i64 { return x + 1; }
struct H { cb: fn(i64) -> i64 }
fn main() -> int {
    let h = H { cb: g };
    let c = h.cb;
    print(c(1));
    return 0;
}
```

```output
2
```

```mort reject="unknown module or import alias 'h'"
fn g(x: i64) -> i64 { return x + 1; }
struct H { cb: fn(i64) -> i64 }
fn main() -> int { let h = H { cb: g }; return h.cb(1); }
```

```mort reject="is not callable"
fn f() -> i64 { return 1; }
fn main() -> int { let f = 2; return f(); }
```

```mort reject="already declared in this scope"
fn main() -> int { let a = 1; let a = 2; return a; }
```

```mort run
fn main() -> int { let print = 2; print(print); return 0; }
```

```output
2
```

```mort run
let g = 1;
fn main() -> int { let g = 2; { let g = 3; print(g); } print(g); return 0; }
```

```output
3
2
```

```mort reject="call to undefined function 'nope'"
test "always checked" { nope(); }
fn main() -> int { return 0; }
```

### 19.8 Integer widths and casts

`int` is `i64`. `c_long` and `c_ulong` have target width, and their *literal* range is
restricted to 32 bits (section 4.1). A cast is permitted only as follows; every other
cast is a compile-time error.

| From | To | Result |
| --- | --- | --- |
| integer | fixed-width integer | value modulo 2^N, reinterpreted in the target's signedness |
| float | fixed-width integer | truncates toward zero; out of range or non-finite is a controlled failure (section 22) |
| integer | float | nearest representable under target rounding |
| float | float | target IEEE conversion |
| pointer | pointer | same address, new pointee type |
| pointer | integer | address reduced modulo the target's width |
| integer | pointer | target-defined address |
| unit enum | integer | the variant's declaration index, starting at 0 |
| integer | unit enum | the enum value with that underlying value |
| `null` | pointer | the null pointer |

`bool`, function values and payload enums have no casts. Casts involving a `c_*` type use
the target's C conversion rules. A cast applies to the preceding unary expression and
chains left to right. An integer cast to a unit enum outside the range of its variants
is outside the language: `==` compares the underlying value, and an exhaustive match
with no wildcard selects an unspecified arm.

```mort run
enum C { A, B }
fn main() -> int {
    let x = 300;
    print((x as u8) as i64);
    print((-1 as i64 as u8) as i64);
    print(3.9 as i64);
    print(-3.9 as i64);
    print(C.B as i64);
    return 0;
}
```

```output
44
255
3
-3
1
```

```mort reject="cannot cast bool to i64"
fn main() -> int { let b = true; print(b as i64); return 0; }
```

```mort reject="cannot cast C to i64"
enum C { A(i64), B }
fn main() -> int { let c = C.B; print(c as i64); return 0; }
```

```mort reject="cannot cast fn()->void to *void"
fn g() -> void {}
fn main() -> int { let p = g as *void; return 0; }
```

```mort reject="cannot cast null to fn()->void"
fn main() -> int { let p = null as fn() -> void; return 0; }
```

### 19.9 Pointers

```text
(Addr)        e is a location of type T,  root binding mutable      ⟹  Γ ⊢ &e : *T
(Addr-Const)  e is a location of type T,  root binding const        ⟹  Γ ⊢ &e : *const T
(Deref)       Γ ⊢ e : *T  or  *const T                              ⟹  Γ ⊢ *e : T
(Index-P)     Γ ⊢ p : *T  or  *const T,   Γ ⊢ i : I,   T ≠ void     ⟹  Γ ⊢ p[i] : T
(Assign-P)    p has type *T                                         ⟹  *p = e  and  p[i] = e  typed by T
```

A location is an identifier, a dereference, a field access or an index. Writing through a
`*const` pointer is a compile-time error. There is no pointer arithmetic: index, or cast
to `u64` and back. A pointer has no field access: dereference first with `(*p).x`. The
`const` of a *binding* restricts only that binding, not the object a pointer it holds
refers to.

```mort run
struct P { x: i64 }
fn main() -> int {
    let v = 1;
    const p = &v;
    *p = 2;
    print(v);
    let s = P { x: 3 };
    let q = &s;
    (*q).x = 4;
    print(s.x);
    return 0;
}
```

```output
2
4
```

```mort reject="cannot assign through a const pointer"
fn main() -> int { const a = 1; let p = &a; *p = 2; return 0; }
```

```mort reject="cannot take the address of this expression"
fn main() -> int { let p = &1; return 0; }
```

```mort reject="operator '+' requires int operands"
fn main() -> int { let a = 1; let p = &a; let q = p + 1; return 0; }
```

```mort reject="cannot access a field through a pointer"
struct P { x: i64 }
fn main() -> int { let p = P { x: 1 }; let q = &p; return q.x; }
```

```mort reject="cannot index a *void pointer"
fn main() -> int { let x = 1; let p = (&x) as *void; let y = p[0]; return 0; }
```

### 19.10 `Result`, `Option` and `try`

`Option<T>` and `Result<Value, Error>` are ordinary generic enums declared in
`std.option` and `std.result`. They are not builtin types: a program that uses them
imports the module, or declares an enum of that name.

```text
(Try)   Γ ⊢ e : Result<V, E>,  the enclosing function returns Result<V2, E>,
        Result has payload variants Ok and Err                ⟹  Γ ⊢ try e : V
```

`Result` is recognised by name and must have exactly two type arguments, and the
error types of the operand and the enclosing function must be identical. The success
types may differ. `try` does not apply to `Option`, to a type alias of a generic
enum with a different name, or outside a function returning `Result`, and is
forbidden in a `defer` or in a match pattern.

```mort run
import std.result;
fn pass(v: i64) -> Result<i64, i64> { return Result<i64, i64>.Ok(v); }
fn fail(v: i64) -> Result<u8, i64> { return Result<u8, i64>.Err(v); }
fn run(ok: bool) -> Result<i64, i64> {
    let a = try pass(1);
    if ok { return Result<i64, i64>.Ok(a + 1); }
    let b = try fail(9);
    return Result<i64, i64>.Ok(a + b as i64);
}
fn show(r: Result<i64, i64>) -> void {
    match r {
        Result<i64, i64>.Ok(v) => { print(v); }
        Result<i64, i64>.Err(e) => { print(0 - e); }
    }
}
fn main() -> int { show(run(true)); show(run(false)); return 0; }
```

```output
2
-9
```

```mort reject="try expects a Result"
enum Option<T> { Some(T), None }
fn f(o: Option<i64>) -> Option<i64> { let v = try o; return Option<i64>.Some(v); }
fn main() -> int { return 0; }
```

```mort reject="does not match return error type"
enum Result<T, E> { Ok(T), Err(E) }
fn f() -> Result<i64, bool> { return Result<i64, bool>.Ok(1); }
fn g() -> Result<i64, i64> { let v = try f(); return Result<i64, i64>.Ok(v); }
fn main() -> int { return 0; }
```

```mort reject="try requires the enclosing function to return Result"
enum Result<T, E> { Ok(T), Err(E) }
fn g() -> Result<i64, i64> { return Result<i64, i64>.Ok(1); }
fn f() -> i64 { return try g(); }
fn main() -> int { return 0; }
```

```mort reject="try is not allowed inside defer"
enum Result<T, E> { Ok(T), Err(E) }
fn g() -> Result<i64, i64> { return Result<i64, i64>.Ok(1); }
fn f() -> Result<i64, i64> { defer try g(); return g(); }
fn main() -> int { return 0; }
```

## 20. Evaluation order

Statements execute in source order. Within one statement the following is guaranteed;
everything else about the relative order of subexpressions is unspecified, as section
7 states, and a program must not depend on it.

1. `&&` and `||` evaluate the left operand first, and the right operand only when
   it is needed.
2. A `let` or `const` initializer is evaluated before the binding is in scope.
3. Every `try` in a statement is evaluated before the rest of that statement, in
   left-to-right order. A `try` in the right operand of `&&` or `||` is evaluated only
   when that operand is reached.
4. `return e;` evaluates `e`, then runs every pending `defer` and destruction, and
   only then returns. A later `defer` therefore cannot change the returned value.
5. `match` evaluates its subject exactly once, before any arm. Patterns that are ordinary
   expressions are evaluated in arm order and stop at the first match. A range `for`
   evaluates its bounds exactly once, before the first iteration.
6. A compound assignment evaluates its target once and before its right operand. A
   plain assignment's two sides are unordered.
7. An array repeat `[e; N]` evaluates `e` once per element.

```mort run
fn t(x: i64) -> i64 { print(x); return x; }
enum Result<T, E> { Ok(T), Err(E) }
fn r(x: i64) -> Result<i64, i64> { print(x); return Result<i64, i64>.Ok(x); }
fn order() -> Result<i64, i64> {
    let v = t(1) + try r(2);
    return Result<i64, i64>.Ok(v);
}
fn after() -> i64 { let x = 5; defer print(x); x = 6; return x; }
fn main() -> int {
    order();
    print(after());
    for i in t(0)..t(2) { print(10); }
    match t(3) { 3 => { print(30); } _ => {} }
    if false && t(99) == 99 {}
    let a = [t(4); 2];
    print(a[1]);
    return 0;
}
```

```output
2
1
6
6
0
2
10
10
3
30
4
4
4
```

## 21. Memory model

### 21.1 Objects and storage

Every value of a non-`void` type occupies an object. Storage has one of these durations:

| Duration | Objects | Valid until |
| --- | --- | --- |
| static | globals; the bytes of string literals | end of the program |
| automatic | locals, parameters, match bindings and loop variables | the end of their scope |
| allocated | memory returned by `alloc` | the matching `free` |

An object has no address separate from its storage. Automatic storage is not initialised
by the language beyond the required initializer, so every local has a value when it comes
into scope, but memory from `alloc` is uninitialised and must be written before it is read
at a given type. String literal storage is writable, and each evaluation yields the same
object. `alloc` may return a null pointer on failure, and the language does not check it;
`free(null)` is a no-op.

### 21.2 Value semantics

Assignment, parameter passing, returning and binding copy the bytes of a value. Struct,
tuple and array values are copied in full, including arrays nested in a struct, so a
mutation of the copy never reaches the original. A slice or pointer copy aliases the storage
it refers to. A whole array cannot be assigned, parameterised or returned on its own; it
can travel inside a struct.

```mort run
struct S { a: [i64; 2] }
fn change(s: S) -> void { s.a[0] = 9; }
fn main() -> int {
    let s = S { a: [1, 2] };
    change(s);
    let t = s;
    t.a[1] = 7;
    print(s.a[0]);
    print(s.a[1]);
    print(t.a[1]);
    return 0;
}
```

```output
1
2
7
```

### 21.3 Ownership and move

A type is *resource-bearing* if it is a `resource struct`, or a struct, tuple, array or
enum containing a resource-bearing value. A pointer, slice or function value is never
resource-bearing: it does not own what it refers to.

Every automatic binding of a resource-bearing type has an ownership state, *live* or
*moved*, tracked per control-flow path:

- a binding starts live;
- `move x` requires `x` to be a local binding, makes it moved, and yields its value;
- reading a moved binding, or moving it again, is a compile-time error;
- at a branch join a binding is moved if it is moved on any path that reaches the join.
  A path that always returns does not reach the join;
- a binding declared outside a loop may not be moved inside that loop, because the
  move would repeat. A binding declared inside the body is a fresh owner per iteration;
- `&x` does not move `x`.

A named resource must be transferred with `move`. Applied to a binding that is not
resource-bearing, `move x` is a plain copy and `x` stays usable.

A resource cannot be overwritten by assignment, stored in a global, or copied out of a
field or element of its owner. The field or element of a resource-bearing binding
cannot be moved on its own: the whole owner moves, or a `match move` transfers an
active payload.

Raw storage is not tracked. A resource read through `*p` or `p[i]`, or assigned there,
copies bytes with no ownership effect. The programmer must then arrange for each
value to be destroyed once.

```mort run
resource struct R { v: i64 }
fn destroy(r: *R) -> void { print((*r).v); }
fn eat(r: R) -> void { print(5); }
fn make(v: i64) -> R { let a = R { v: v }; return move a; }
fn main() -> int {
    let a = make(1);
    eat(move a);
    print(6);
    for i in 2..4 { let b = make(i); eat(move b); }
    return 0;
}
```

```output
5
1
6
5
2
5
3
```

```mort reject="must be transferred with 'move a'"
resource struct R { v: i64 }
fn destroy(r: *R) -> void {}
fn main() -> int { let a = R { v: 1 }; let b = a; return 0; }
```

```mort reject="use of moved resource 'a'"
resource struct R { v: i64 }
fn destroy(r: *R) -> void {}
fn main() -> int { let a = R { v: 1 }; let b = move a; return a.v; }
```

```mort reject="was already moved"
resource struct R { v: i64 }
fn destroy(r: *R) -> void {}
fn main() -> int { let a = R { v: 1 }; let b = move a; let c = move a; return 0; }
```

```mort reject="use of moved resource 'a'"
resource struct R { v: i64 }
fn destroy(r: *R) -> void {}
fn f(c: bool) -> void { let a = R { v: 1 }; if c { let b = move a; } let x = a.v; }
fn main() -> int { return 0; }
```

```mort check
resource struct R { v: i64 }
fn destroy(r: *R) -> void {}
fn f(c: bool) -> void { let a = R { v: 1 }; if c { let b = move a; return; } let x = a.v; }
fn main() -> int { return 0; }
```

```mort reject="cannot move a resource declared outside a repeating loop"
resource struct R { v: i64 }
fn destroy(r: *R) -> void {}
fn eat(r: R) -> void {}
fn main() -> int { let a = R { v: 1 }; loop { eat(move a); } }
```

```mort reject="cannot be copied out of its owning binding"
resource struct R { v: i64 }
fn destroy(r: *R) -> void {}
struct H { r: R }
fn main() -> int { let h = H { r: R { v: 1 } }; let a = h.r; return 0; }
```

```mort reject="move expects a local binding"
resource struct R { v: i64 }
fn destroy(r: *R) -> void {}
struct H { r: R }
fn main() -> int { let h = H { r: R { v: 1 } }; let a = move h.r; return 0; }
```

```mort reject="cannot overwrite a resource binding"
resource struct R { v: i64 }
fn destroy(r: *R) -> void {}
fn main() -> int { let a = R { v: 1 }; a = R { v: 2 }; return 0; }
```

```mort reject="resource globals are not supported"
resource struct R { v: i64 }
fn destroy(r: *R) -> void {}
let g = R { v: 1 };
fn main() -> int { return 0; }
```

### 21.4 Destruction and `defer`

A `resource struct R` needs exactly one matching `fn destroy(value: *R) -> void`, and a
generic resource one `fn destroy<T>(value: *R<T>) -> void`.

On any scope exit, including fallthrough, `return`, `break`, `continue` and `try`, the scope's
`defer` statements and live resource bindings run in reverse order of their registration,
as one sequence. A composite destroys its members in reverse declaration order, and an array
its elements from last to first. A moved binding is skipped.

A `defer` runs when its enclosing block exits, not the function. The expression is not
evaluated at registration; variables are read when it runs, so it observes later assignments.
`break` and `continue` run the defers of the loop body they leave and the current iteration
only.

An explicit `destroy(&x)` on a local binding counts as moving it: `x` is not destroyed again,
and a later read is a use after move. `defer destroy(&x);` hands the responsibility to that
defer: the automatic cleanup of `x` is removed.

A resource-bearing value that is never bound is not destroyed: it is leaked. An
expression statement discarding one is accepted without a diagnostic.

```mort run
resource struct R { v: i64 }
fn destroy(r: *R) -> void { print((*r).v); }
fn make() -> R { return R { v: 7 }; }
struct H { a: R, b: R }
fn main() -> int {
    { let c = R { v: 1 }; defer print(2); let d = R { v: 3 }; defer print(4); }
    let h = H { a: R { v: 5 }, b: R { v: 6 } };
    let x = R { v: 8 };
    destroy(&x);
    let y = R { v: 9 };
    defer destroy(&y);
    make();
    for i in 0..2 { defer print(i + 20); if i == 0 { continue; } break; }
    return 0;
}
```

```output
4
3
2
1
8
20
21
9
6
5
```

```mort reject="an owning enum match must name every variant"
resource struct R { v: i64 }
fn destroy(r: *R) -> void {}
enum E { A(R), B }
fn main() -> int { let e = E.B; match move e { _ => {} } return 0; }
```

```mort reject="an owning payload cannot be ignored"
resource struct R { v: i64 }
fn destroy(r: *R) -> void {}
enum E { A(R), B }
fn main() -> int { let e = E.B; match move e { E.A(_) => {} E.B => {} } return 0; }
```

```mort reject="matching a resource-owning enum transfers it"
resource struct R { v: i64 }
fn destroy(r: *R) -> void {}
enum E { A(R), B }
fn main() -> int { let e = E.B; match e { E.A(r) => {} E.B => {} } return 0; }
```

### 21.5 Raw pointers

The compiler checks the type of a pointer and nothing else. A pointer is *valid* for an
access of `T` when it holds the address of live storage large enough for `T`, aligned for `T`
(section 21.7), and that storage holds a value of `T` for a read. Constructing an invalid
pointer is allowed; using it is not. A pointer into automatic storage is invalid after the
scope ends, and returning one is accepted without a diagnostic. Bounds are checked only
for arrays and slices.

### 21.6 Volatile

Volatile access is defined in section 7.4: `volatile_read<T>` and `volatile_write<T>` on
integer widths of 8 to 64 bits. They guarantee one access per execution in program order
relative to other volatile accesses, and nothing about non-volatile accesses, atomicity or
processor ordering.

```mort check
fn poke(p: *u32) -> u32 { volatile_write<u32>(p, 7); return volatile_read<u32>(p); }
fn main() -> int { return 0; }
```

```mort reject="width must be one of"
fn peek(p: *f64) -> f64 { return volatile_read<f64>(p); }
fn main() -> int { return 0; }
```

### 21.7 Layout and alignment

These guarantees hold on every supported target, unless stated otherwise:

- `bool` occupies one byte holding 0 or 1.
- Integer and float types are their stated width, naturally aligned. `c_*` types take
  their width and alignment from the target C ABI.
- Pointers, function values and `c_size` have the target pointer size.
- A struct or tuple is laid out as the target C compiler lays out a struct with the
  same member types in the same order. Fields are not reordered and no packing is
  applied. There is no syntax to change this.
- `[T; N]` is `N` adjacent elements with no padding between them beyond `T`'s own.
- `[]T` is a pointer at offset 0 followed by a `u64` length.
- A unit enum has the size and alignment of the target's C enum, and its values
  are the zero-based declaration indices.
- A payload enum is a tag followed by an unnamed union of its payloads. The layout of
  a payload enum is **not** part of the guaranteed ABI.
- `sizeof<T>()` is the size of a value of `T`, including trailing padding.

Mort has no alignment operator. Memory from `alloc` is aligned for any Mort type.

```mort run
struct A { a: u8, b: i32, c: u16 }
struct B { a: u8, b: i64 }
enum C { X, Y }
fn main() -> int {
    print(sizeof<A>() as i64);
    print(sizeof<B>() as i64);
    print(sizeof<bool>() as i64);
    print(sizeof<C>() as i64);
    print(sizeof<*u8>() as i64);
    print(sizeof<(u8, i32)>() as i64);
    print(sizeof<[]u8>() as i64);
    print(sizeof<fn() -> void>() as i64);
    let x = A { a: 1, b: 2, c: 3 };
    print((&x.b as u64 - &x.a as u64) as i64);
    print((&x.c as u64 - &x.a as u64) as i64);
    return 0;
}
```

```output
12
16
1
4
8
8
16
8
4
8
```

### 21.8 Undefined behaviour

The following make the whole program's behaviour undefined. Implementations are not required
to diagnose them, and no behaviour is guaranteed after one occurs.

1. Reading or writing through an invalid pointer (section 21.5), including a null pointer, a
   dangling pointer, or one misaligned for the access.
2. Reading memory from `alloc` before writing it.
3. Calling `free` on anything but a pointer from `alloc`, or twice.
4. Calling a function value whose target does not have the called type, including a null
   function value.
5. Destroying a resource twice or using it after destruction through raw storage.
6. Overflow, division by zero, or an out-of-range float cast in arithmetic on `c_*` types.
   Fixed-width types are exempt: their operations are fully defined by section 7.1.
7. A data race (section 12.4), and a `thread_join` or handle use violating section 12.
8. Invalid inline assembly, a foreign function that misbehaves, or an incompatible `extern`
   declaration (section 23).
9. Indexing beyond an array or slice in a freestanding program, where no check is inserted.

### 21.9 Threads

Section 12 is the concurrency part of this model: spawn and join, mutex, and sequentially
consistent atomics establish *happens-before*, and a data race is undefined. A pointer or
handle crossing a thread keeps all the validity requirements of section 21.5. Resources are
not thread-safe by default: a resource shared through raw pointers needs a synchronization
relation from section 12.

## 22. Error semantics

### 22.1 Compile-time errors

A compile-time error prevents output and produces a diagnostic with the source file when
known, a line, and a reason. The reference driver `mortc` exits with:

| Status | Meaning |
| --- | --- |
| 0 | compilation, or `--check`, succeeded |
| 1 | a Mort diagnostic, a failed C backend, or a usage error |
| 2 | no C compiler was found (generated C is written to disk) |

Warnings, such as an unused binding, do not change the status unless `--deny-warnings`
is given.

### 22.2 Controlled execution failures

In hosted programs each failure in this table writes one line to standard error, and
terminates the process with status 1. It does not run `defer` statements or destructors.

| Failure | Message begins |
| --- | --- |
| `assert` is false | `assertion failed at Mort line` |
| array or slice index out of range | `index out of bounds at Mort line` |
| integer division or remainder by zero | `integer division by zero at Mort line`, `integer remainder by zero at Mort line` |
| negative shift count | `negative integer shift count at Mort line` |
| float cast out of range or non-finite | `floating-point to integer cast out of range at Mort line` |
| `arg_get` out of range | `argument index out of bounds at Mort line` |
| invalid thread or atomic operation | `Mort concurrency failure` |

A freestanding program executes a trap instruction instead for arithmetic failures and has
no bounds checks or `assert`.

```mort run exit=1 stderr="assertion failed at Mort line 2"
fn main() -> int {
    assert(false);
    return 0;
}
```

```output
```

```mort run exit=1 stderr="assertion failed at Mort line 3"
fn main() -> int {
    defer print(1);
    assert(false);
    return 0;
}
```

```output
```

```mort run exit=1 stderr="integer division by zero at Mort line 1"
fn z() -> i64 { return 0; } fn main() -> int { print(1 / z()); return 0; }
```

```output
```

```mort run exit=1 stderr="index out of bounds at Mort line 1"
fn z() -> i64 { return 9; } fn main() -> int { let a = [1, 2]; print(a[z()]); return 0; }
```

```output
```

```mort run exit=1 stderr="negative integer shift count at Mort line 1"
fn z() -> i64 { return -1; } fn main() -> int { print(1 << z()); return 0; }
```

```output
```

```mort run exit=1 stderr="floating-point to integer cast out of range at Mort line 1"
fn main() -> int { let f = 1e300; print(f as i64); return 0; }
```

```output
```

```mort run exit=1 stderr="integer remainder by zero at Mort line 1"
fn z() -> i64 { return 0; } fn main() -> int { print(1 % z()); return 0; }
```

```output
```

### 22.3 Process exit

A hosted program's status is the value returned by `main`, converted to the host's C `int`;
reporting a value outside the range the host supports is host-defined. Portable programs
return values from 0 through 125. `main` returning normally runs its own defers and
destruction like any function. In a test build, test blocks run in source order and a
failed assertion ends the run with status 1.

```mort run exit=3
fn main() -> int { return 3; }
```

```output
```

### 22.4 Programs reported by the backend

A program with these errors is ill-formed, and the reference implementation currently
reports them only through the C backend, as a failed compilation rather than a Mort
diagnostic:

- a `match` whose subject has type `void`;
- binding an array by name with `let b = a;` where `a` is an array;
- a cast between a pointer and a float;
- a string escape that the backend's C compiler does not accept, such as `"\xZZ"`.

The reference implementation passes valid C escapes through unchanged, so the meaning of
a string escape outside the set listed for character literals in section 2.3 is the
target C compiler's.

## 23. C ABI and foreign functions

### 23.1 Type mapping

| Mort | C |
| --- | --- |
| `bool` | `_Bool` |
| `i8`..`i64`, `u8`..`u64` | `int8_t`..`int64_t`, `uint8_t`..`uint64_t` |
| `f32`, `f64` | `float`, `double` |
| `c_char`, `c_uchar`, `c_short`, `c_ushort`, `c_int`, `c_uint`, `c_long`, `c_ulong`, `c_size` | the matching C types, `c_size` being `size_t` |
| `*T`, `*const T` | `T*`, `const T*` |
| `*void` | `void*` |
| `fn(A, B) -> R` | `R (*)(A, B)` |
| `struct S` | a C struct with the same member types in the same order (section 21.7) |
| `(A, B)` | a C struct with the same member types in the same order |
| `[]T` | `struct { T* data; uint64_t length; }` |
| unit enum | a C `enum` type |

Payload enums are not part of the C ABI (section 21.7). A generated C symbol or generated
type name is not part of the language.

### 23.2 `extern` declarations

`extern fn name(p: T, ...) -> R;` declares a C symbol with the exact name given and the target C
calling convention, and is called like an ordinary function. It takes no type parameters.
Parameters follow the same rules as a Mort function: an array is not a valid parameter
type, and pointing at the first element is the required form. A struct is passed and
returned by value, using the C ABI of the struct described above. An `extern` name must not be
a C keyword. Linking is the driver's responsibility, through `--library` and `--link`.

An `extern` declaration is a promise. Mort does not verify the symbol, its calling
convention, its signature or its ownership behaviour, and a mismatch is undefined (section
21.8). A resource may be passed by value to an `extern` function only with `move`, after
which it is not destroyed by Mort.

Not supported: variadic functions, `extern` data, exporting a Mort function under a chosen
symbol, generated headers, and shared-library output. Mort functions have generated symbol
names that are not part of the language, so C reaches them only through function values
handed to it.

```mort run
extern fn strlen(s: *const u8) -> c_size;
extern fn abs(x: c_int) -> c_int;
fn main() -> int {
    print(strlen("abc") as i64);
    print(abs(-5) as i64);
    return 0;
}
```

```output
3
5
```

```mort reject="cannot be an array"
extern fn f(p: [i64; 2]) -> void;
fn main() -> int { return 0; }
```

```mort reject="is reserved by C"
extern fn register(a: i64) -> void;
fn main() -> int { return 0; }
```

```mort reject="expected parameter name"
extern fn printf(f: *u8, ...) -> c_int;
fn main() -> int { return 0; }
```

```mort reject="expected 'fn'"
extern let x: i64;
fn main() -> int { return 0; }
```

```mort reject="expected '('"
extern fn f<T>(a: T) -> void;
fn main() -> int { return 0; }
```

### 23.3 Struct layout across the boundary

A struct shared with C is written with the same member types in the same order. Because
of the layout rules in section 21.7, the C compiler and Mort agree on every offset and on
padding. `c_*` types should be used for C `int` and similar fields, because a fixed-width
type does not change size with the target.

```mort run
struct Div { quot: c_int, rem: c_int }
extern fn div(a: c_int, b: c_int) -> Div;
fn main() -> int {
    let d = div(7 as c_int, 2 as c_int);
    print(d.quot as i64);
    print(d.rem as i64);
    return 0;
}
```

```output
3
1
```

### 23.4 Function pointers

A function value is a plain C function pointer with the C calling convention. It can refer
to a Mort function or an `extern fn`, can be stored in a struct field, array element, or
tuple, and can be passed to or received from C. It cannot be a closure: Mort has none, so
state travels in a `*void` context argument. A value must be called through a named binding
or parameter, not a field path (section 19.7). `null` is a valid function value and calling it
is undefined. Function values compare with `==` and `!=`.

```mort run
extern fn qsort(base: *void, count: c_size, size: c_size,
                compare: fn(*const void, *const void) -> c_int) -> void;
fn order(a: *const void, b: *const void) -> c_int {
    let x = *(a as *const i64);
    let y = *(b as *const i64);
    if x < y { return -1 as c_int; }
    if x > y { return 1 as c_int; }
    return 0 as c_int;
}
fn main() -> int {
    let a = [3, 1, 2];
    qsort(&a[0] as *void, 3 as c_size, 8 as c_size, order);
    print(a[0]);
    print(a[2]);
    return 0;
}
```

```output
1
3
```

### 23.5 Embedding interface shapes

An embedding interface such as the Vex ABI (`vex-lang/docs/ffi.md`) is expressible directly,
because a table of callbacks is just a struct of function values. A function type may be
followed by further elements in any type list, including another function type's parameters.

```mort run
struct Value { tag: i64, i: i64, f: f64, s: *u8, len: i64 }
type HostFn = fn(*void, *void, *Value, i64, *Value) -> i64;
struct Api { version: i64, bind: fn(*void, *u8, HostFn, *void) -> i64, ready: i64 }
fn bind(vm: *void, name: *u8, f: HostFn, ctx: *void) -> i64 { return 4; }
fn main() -> int {
    print(sizeof<Value>() as i64);
    let probe = Value { tag: 1, i: 2, f: 0.0, s: null, len: 3 };
    print((&probe.f as u64 - &probe.tag as u64) as i64);
    print((&probe.len as u64 - &probe.tag as u64) as i64);
    let api = Api { version: 1, bind: bind, ready: 1 };
    let call = api.bind;
    print(call(null, "n", api_host, null));
    return 0;
}
fn api_host(vm: *void, ctx: *void, argv: *Value, argc: i64, ret: *Value) -> i64 { return 0; }
```

```output
40
16
32
4
```

## Appendix A. Compatibility

The language version uses semantic versioning:

- a patch revision clarifies wording or fixes a conformance case without
  changing valid program behavior;
- a minor revision may add backward-compatible syntax or behavior;
- a major revision may remove or change accepted behavior.

Deprecations must remain diagnosed for at least one minor language version
before removal unless retaining them would be a demonstrated security issue.

## Appendix B. Reserved future work

The following are intentionally not defined by Mort 0.41: traits, generic bounds,
method syntax, closures, checked borrows and lifetimes, thread cancellation, detached threads, condition variables,
read/write locks, atomics other than sequentially consistent `i64`,
language-level `async`/`await` syntax, TLS servers, mutual TLS, exceptions,
reflection, dynamic loading, stable binary package ABI, Unicode text
semantics, and WebAssembly/mobile platform profiles. Their absence is not
permission for an implementation to assign new meaning to currently valid
syntax.
