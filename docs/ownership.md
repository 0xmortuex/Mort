# Ownership and resources

This is a worked-example companion to [`language-specification.md`](language-specification.md)'s
"Ownership and cleanup" section. Every snippet below was compiled and run with
`python mortc.py --run` against the current compiler; expected output is noted
inline.

## Declaring a resource

`resource struct R { ... }` declares a move-only resource type. The program
must provide exactly one matching destructor:

```
fn destroy(value: *R) -> void { ... }
```

The destructor's single parameter must be a pointer to the resource type
(`*R`, not `R`), and it must return `void`. A resource struct with no
destructor, or one with the wrong signature (by-value instead of by-pointer,
extra parameters, wrong return type), fails to compile with
`resource struct 'R' requires exactly one fn destroy(value: *R) -> void`. For
a generic `resource struct R<T> { ... }`, the destructor needs the same
generic parameters: `fn destroy<T>(value: *R<T>) -> void`.

A struct that contains a resource field is itself resource-bearing and
inherits all of the same rules (see "Composite resources" below) — it does
not need its own `destroy`; the compiler synthesizes recursive cleanup for
it.

## The core rules

Resource values, as summarized in the spec and confirmed by the compiler's
error messages:

- **cannot be copied implicitly.** `let second = first;` where `first` is a
  resource fails with `resource 'first' must be transferred with 'move
  first'`.
- **must be transferred with `move binding`.** Moving is required anywhere a
  resource-typed value flows into a new owner: a `let`/`const` binding, a
  `return`, or a function-call argument. A move is only needed for a *named
  binding* — constructing a fresh resource literal in place (`Handle { value:
  1 }`) or forwarding a function's own return value do not need `move`,
  since there is no existing owner to transfer from.
- **cannot be used after a move.** `let second = move first; print(first.value);`
  fails with `use of moved resource 'first'`.
- **cannot be moved twice.** `let second = move first; let third = move
  first;` fails with `resource 'first' was already moved`.
- **are destroyed automatically** when their owning lexical binding leaves
  scope, unless they were moved out first.
- **destruction is recursive and runs in reverse binding order.** See
  "Destruction order" below.
- **cannot be stored in globals or overwritten by plain assignment** — only
  moved into a fresh binding, a call argument, or a return.

## Worked example: move on return

```
resource struct Buffer {
    data: *u8,
    length: u64,
}

fn destroy(value: *Buffer) -> void {
    free((*value).data);
}

// Constructing and returning a fresh literal needs no `move` — there is no
// existing binding being transferred from.
fn make(byte: u8) -> Buffer {
    let data = alloc(1) as *u8;
    data[0] = byte;
    return Buffer { data: data, length: 1 };
}

// `value` arrived as an owned parameter; handing it back out requires the
// same explicit `move` a `let` or call argument would.
fn rename(value: Buffer) -> Buffer {
    return move value;
}

fn main() -> int {
    let original = make(65);
    let renamed = rename(move original);
    print(renamed.data[0] as i64);
    destroy(&renamed);
    return 0;
}
```

Prints `65`. Two moves happen here: `original` transfers into `rename`'s
`value` parameter at the call site, and `value` transfers back out via
`return move value;` inside `rename`.

## Worked example: `match move`

`match move value` consumes a resource-bearing enum and transfers whichever
variant's payload is active into the selected arm, so the payload can be used
(and is destroyed) inside that arm without a separate move:

```
resource struct Leaf { label: *u8 }

fn destroy(value: *Leaf) -> void { println((*value).label); }

enum Owned { Some(Leaf), Empty }

fn consume(value: Owned) -> void {
    match move value {
        Owned.Some(leaf) => { println("matched"); }
        Owned.Empty => {}
    }
}

fn main() -> int {
    let value: Owned = Owned.Some(Leaf { label: "destroyed" });
    consume(move value);
    return 0;
}
```

Prints `matched` then `destroyed`: the `leaf` binding inside the `Some` arm
is destroyed automatically when the arm's scope ends, after the arm's own
`println("matched")` has already run.

## Destruction order

Locals are destroyed in reverse binding order when their scope ends:

```
resource struct Ticket { label: *u8 }
fn destroy(value: *Ticket) -> void { println((*value).label); }
fn main() -> int {
    let first = Ticket { label: "first" };
    let second = Ticket { label: "second" };
    return 0;
}
```

Prints `second` then `first`. The same reverse-order rule applies inside
composite values: a struct destroys its fields, a tuple its elements, and an
array its elements, each in reverse of their construction order, before the
containing value's own binding is considered fully torn down. A resource
created fresh inside a loop body is a new owner on every iteration and is
moved or destroyed each time through, independent of prior iterations.

## Composite resources

Resources compose transparently through structs, tuples, enums, and arrays —
a struct with a resource field, a tuple containing a resource, an enum
variant carrying one, or an array of resources are all themselves
resource-bearing, and the compiler synthesizes the recursive drop that walks
into each field/element in reverse order. You do not write a `destroy` for
the composite type yourself; only the leaf `resource struct` needs one.

## Branches and loops

A resource may be moved at most once along any single control-flow path, but
the same binding can be moved once in each of two *mutually exclusive*
branches (an `if`/`else`, or distinct `match` arms) — the compiler tracks
that only one of them executes:

```
if choose_left {
    consume(move value);
} else {
    consume(move value);
}
```

This compiles even though `value` is moved in both branches, because they
never both run. Moving it in one branch and then using it unconditionally
afterward (outside any branch) is still a compile-time error, since the
fall-through path would see a moved-from value.

## Containers of resources: a known gap

`std.vec.Vec<T>` and `std.map.Map<K, V>` are ordinary generic containers, not
resource-aware ones. They work correctly for plain (non-resource) element
types, and `push`/`set` do move a resource element into the container's
backing storage correctly. But two operations currently break resource
safety if you instantiate them with a resource element type:

- `Vec.get` / `Map.get` return `Option<T>` by value — for a resource `T`
  this is an aliasing copy of the stored element, not a move, which the
  ownership checker cannot see because it happens inside the generic
  container's own raw slot access.
- `Vec.destroy` only frees the backing array; it does not run each element's
  destructor first, so resource elements still stored in the container when
  it is destroyed are leaked, not dropped. `Map` has the same gap for
  values, including the overwrite-of-an-existing-key path in `Map.insert`.

In short: draining a `Vec<Resource>`/`Map<K, Resource>` with `pop` (which
does move the element out) is safe; reading with `get`, or destroying the
container while it still holds elements, is not. This is tracked as an open
backlog item (see `BACKLOG.md`'s "Resource-aware container API" entry) — a
real fix needs a `get_ref` returning `*T` and an element-dropping destroy,
which changes the generic container API and needs human review.
