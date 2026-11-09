# Contributing to Mort

Mort's compiler is Python and emits C. You need [uv](https://docs.astral.sh/uv/)
and Git. You do not need a C compiler: the `dev` extra installs
[ziglang](https://pypi.org/project/ziglang/), and Mort uses `python -m ziglang cc`
when no `cc`, `gcc` or `clang` is on your `PATH`. Set `MORT_CC` to choose a
specific backend.

## Setup

```bash
git clone https://github.com/0xmortuex/Mort.git
cd Mort
uv sync --extra dev
uv run mortc --version
uv run mortc doctor
```

`uv.lock` is not tracked. `uv sync` creates it locally, so leave it out of your
commits.

## Tests

```bash
uv run pytest tests/ -q
```

Run one suite at a time on machines with limited memory. Use
`uv run pytest tests/test_mort.py -k name` while working on one area.

Bugs get a failing test first. Do not delete or weaken a test to make a change
pass. Each program in `examples/` must be listed in `tests/test_mort.py`
(`EXPECTED`, or `EXAMPLES_TESTED_ELSEWHERE` with the test that covers it), and
every code block in `docs/tour.md` is compiled by `tests/test_tour.py`. Tests
compare output exactly, so update the expected text together with the code.

Static checks:

```bash
uv run ruff check mort mortc.py conformance/run.py tests/test_mort.py
```

CI's full `ruff` file list is in `.github/workflows/ci.yml`.

## Self-hosting

`selfhost/` holds the Mort compiler written in Mort. It is incomplete; status and
task order are in `selfhost/PLAN.md`. Its tests compare the Mort components with
the Python compiler, so a change to the lexer, parser or their diagnostics usually
needs the matching change in `selfhost/`:

```bash
uv run pytest selfhost -q
```

Sources must stay in the bootstrap subset, which `selfhost/tools/check_subset.py`
defines and those tests enforce. CI's `selfhost` job runs this suite.

## Conformance

`conformance/` is a black-box suite for the language specification. New normative
behavior must add or update a case in `conformance/manifest.json` that cites the
specification section:

```bash
uv run python conformance/run.py
uv run python conformance/run.py --case <case-id>
```

## Fuzzing

The deterministic front-end fuzzer needs no extra packages:

```bash
uv run mortc fuzz --cases 1000 --seed 0
```

A crash or hang is a bug; add a reduced regression test with the fix. The
coverage-guided harness `fuzz/atheris_frontend.py` needs
[Atheris](https://pypi.org/project/atheris/) and runs in
`.github/workflows/fuzz.yml`.

## Commits

- One logical change per commit, with its tests.
- Write the subject in the imperative and describe the behavior, for example
  `Reject pointer-to-array types in the checker`. Standard-library releases add
  the version: `Add std.vec.swap, in-place two-element exchange (0.73.0)`.
- Add user-visible changes to `CHANGELOG.md`. Breaking changes must follow
  [the compatibility policy](docs/compatibility-policy.md).
- Pull requests target `main`, and CI must pass.

Report security problems through [SECURITY.md](SECURITY.md), not public issues.
