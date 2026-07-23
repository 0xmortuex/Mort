#!/usr/bin/env python3
"""Coverage-guided Mort front-end fuzz target for Atheris/libFuzzer."""
import os
import sys

import atheris


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

with atheris.instrument_imports():
    from mort.errors import MortError
    from mortc import compile_to_c


def test_one_input(data):
    # Replacement decoding lets arbitrary bytes reach the lexer while keeping
    # compiler input in its public text API. Keep individual cases bounded so
    # one generated input cannot monopolize the scheduled fuzz worker.
    source = data[:256 * 1024].decode("utf-8", errors="replace")
    try:
        compile_to_c(source)
    except MortError:
        return
    # Every other exception is deliberately allowed to escape. Atheris records
    # and minimizes it as a compiler crash.


def main():
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
