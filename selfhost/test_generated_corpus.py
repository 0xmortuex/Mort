"""The accepted-program corpus: its floors, and the generator that supplies it.

    python -m pytest selfhost/test_generated_corpus.py -q

See selfhost/corpus.py for the classes and selfhost/PLAN.md section 5.1 for why
the floors have these values.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))

import corpus  # noqa: E402
import gen_subset_programs as gen  # noqa: E402


def test_accepted_corpus_meets_the_plan_floor(tmp_path):
    """Section 5.1: at least 8000 accepted lines, from real and generated programs."""
    existing = [p for p in corpus.existing_subset_files() if corpus.compiles(p)]
    kernel = corpus.write_stubbed_kernel(tmp_path)
    programs = [gen.generate(spec) for spec in gen.default_corpus()]
    generated_lines = sum(program.line_count for program in programs)

    assert len(programs) >= corpus.MIN_GENERATED_PROGRAMS
    assert generated_lines >= corpus.MIN_GENERATED_LINES
    large = [p for p in programs if p.line_count >= corpus.LARGE_PROGRAM_LINES]
    assert len(large) >= corpus.MIN_LARGE_PROGRAMS

    kernel_lines = corpus.line_count(kernel)
    assert kernel_lines >= corpus.MIN_KERNEL_LINES
    assert kernel_lines >= corpus.MIN_REAL_PROGRAM_LINES

    accepted = (sum(corpus.line_count(p) for p in existing)
                + kernel_lines + generated_lines)
    assert accepted >= corpus.MIN_ACCEPTED_LINES, accepted
