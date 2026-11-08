"""Differential test: selfhost/lexer.mx must agree with mort/lexer.py.

Run from the repository root:

    python -m pytest selfhost -q

Every expected value here comes from running the Python lexer; nothing is
hardcoded. See py_lex_dump.py for the dump format.
"""
import ast
import collections
import math
import os
import random
import re
import struct
import subprocess
import sys
import time
import unicodedata
from fractions import Fraction
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import py_lex_dump  # noqa: E402

SKIP_DIRS = {".git", "__pycache__", ".mort", "build", "dist", ".venv", "node_modules"}


def repo_mx_files():
    found = []
    for directory, subdirs, files in os.walk(ROOT):
        subdirs[:] = sorted(d for d in subdirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if name.endswith(".mx"):
                found.append(Path(directory) / name)
    return found


REPO_FILES = repo_mx_files()


@pytest.fixture(scope="session")
def lex_exe(tmp_path_factory):
    build = tmp_path_factory.mktemp("selfhost-build")
    exe = build / ("lex_main.exe" if os.name == "nt" else "lex_main")
    result = subprocess.run(
        [sys.executable, str(ROOT / "mortc.py"),
         str(HERE / "lex_main.mx"), "-o", str(exe)],
        capture_output=True, text=True, cwd=build)
    assert result.returncode == 0, (
        f"compiling lex_main.mx failed:\n{result.stdout}\n{result.stderr}")
    assert exe.exists()
    return exe


def run_mort_lexer(exe, source_path, out_path):
    """Run the Mort lexer; returns (exit code, stdout, dump text)."""
    if out_path.exists():
        out_path.unlink()
    env = dict(os.environ)
    env["MORT_LEX_IN"] = str(source_path)
    env["MORT_LEX_OUT"] = str(out_path)
    result = subprocess.run(
        [str(exe)], env=env, capture_output=True, timeout=120)
    assert out_path.exists(), (
        f"no dump written for {source_path}: exit {result.returncode}\n"
        f"{result.stdout!r}\n{result.stderr!r}")
    return result.returncode, result.stdout, out_path.read_bytes().decode("ascii")


def first_difference(expected, actual):
    expected_lines = expected.splitlines()
    actual_lines = actual.splitlines()
    for index, (want, got) in enumerate(zip(expected_lines, actual_lines)):
        if want != got:
            return f"line {index + 1}\n  python: {want}\n  mort:   {got}"
    return (f"line counts differ: python {len(expected_lines)}, "
            f"mort {len(actual_lines)}")


def assert_agrees(exe, source_path, out_path):
    expected = py_lex_dump.dump_file(source_path)
    code, stdout, actual = run_mort_lexer(exe, source_path, out_path)
    assert expected == actual, (
        f"{source_path}: {first_difference(expected, actual)}")
    # A lexical error is exit 1 with a located diagnostic; anything else
    # (a crash, a signal, a runtime assertion) is a failure of the lexer.
    if "\nERROR " in "\n" + expected:
        assert code == 1, f"{source_path}: expected exit 1, got {code}"
        assert b": error: " in stdout, f"{source_path}: no diagnostic: {stdout!r}"
    else:
        assert code == 0, f"{source_path}: expected exit 0, got {code}: {stdout!r}"


def test_corpus_is_not_empty():
    assert len(REPO_FILES) >= 100


@pytest.mark.parametrize(
    "path", REPO_FILES, ids=[p.relative_to(ROOT).as_posix() for p in REPO_FILES])
def test_repo_file(lex_exe, tmp_path, path):
    assert_agrees(lex_exe, path, tmp_path / "out.txt")


LONG_DECIMAL = b"1234567890" * 500
LONG_HEX = b"0x" + b"fedcba9876543210" * 300

EDGE_CASES = {
    # --- whitespace and newlines (text-mode translation applies)
    "empty": b"",
    "spaces": b"   \t  ",
    "lf": b"a\nb\n",
    "crlf": b"a\r\nb\r\n",
    "lone_cr": b"a\rb\rc",
    "cr_crlf_mix": b"a\r\r\nb\n\rc",
    "crlf_in_string": b'"a\r\nb"',
    "cr_after_backslash": b'"a\\\rb"',
    "crlf_in_comment": b"// x\r\ny /* a\r\nb */ z",
    "tabs_col": b"\t\tabc\t+",
    "form_feed": b"a\x0cb",
    "vtab": b"a\x0bb",
    # --- comments
    "line_comment_eof": b"// no newline",
    "line_comment": b"a // b\nc",
    "block": b"a /* b */ c",
    "block_nested": b"/* a /* b */ c */ d",
    "block_deep": b"/*" * 50 + b"x" + b"*/" * 50 + b"y",
    "block_unterminated": b"a /* b",
    "block_nested_unterminated": b"/* /* */",
    "block_slash_star_slash": b"/*/ x */ y",
    "block_empty": b"/**/a",
    "block_star_run": b"/***/b/****/c",
    "block_multiline_position": b"/* a\nb */ c\n/* d */ e",
    "block_nul": b"/* \x00 */ a",
    "line_comment_nul": b"// \x00 x\na",
    "slash_alone": b"a / b",
    "slash_eof": b"/",
    # --- integers
    "int_zero": b"0",
    "int_leading_zero": b"007 000",
    "int_sep": b"1_000_000",
    "int_sep_trailing": b"1_",
    "int_sep_double": b"1__0",
    "int_sep_alpha": b"1_0a",
    "int_alpha": b"12abc",
    "int_upper_alpha": b"12Z",
    "int_underscore_alpha": b"12_z",
    "int_unicode_alpha": "12é".encode(),
    "int_unicode_digit": "12٣".encode(),
    "int_u64max": b"18446744073709551615 18446744073709551616",
    "int_long": LONG_DECIMAL,
    "hex": b"0xff 0XFF 0xAbCd 0x0",
    "hex_sep": b"0xff_ff",
    "hex_empty": b"0x",
    "hex_empty_then": b"0x;",
    "hex_lead_sep": b"0x_ff",
    "hex_trail_sep": b"0xff_",
    "hex_double_sep": b"0xf__f",
    "hex_bad_digit": b"0xfg",
    "hex_unicode_after": "0xfé".encode(),
    "hex_long": LONG_HEX,
    "bin": b"0b1010 0B11 0b0",
    "bin_sep": b"0b1_0",
    "bin_empty": b"0b",
    "bin_bad": b"0b2",
    "bin_lead_sep": b"0b_1",
    "bin_trail_sep": b"0b1_",
    "bin_alpha": b"0b1a",
    "oct": b"0o755 0O7 0o0",
    "oct_sep": b"0o7_7",
    "oct_empty": b"0o",
    "oct_bad": b"0o8",
    "oct_trail_sep": b"0o7_",
    "oct_double_sep": b"0o7__7",
    "oct_alpha": b"0o7z",
    # --- floats
    "float_simple": b"1.5 0.0 00.50",
    "float_exp": b"2e3 2E3 2e+3 2e-3 1.5e3 1.5E-3",
    "float_sep": b"1_000.5 1_0e2",
    "float_frac_sep": b"1.5_0",
    "float_exp_sep": b"1e1_0",
    "float_bad_exp": b"1e",
    "float_bad_exp_sign": b"1e+",
    "float_bad_exp_alpha": b"1ex",
    "float_frac_bad_exp": b"1.5e",
    "float_alpha_after": b"1.5a",
    "float_exp_alpha_after": b"1e5a",
    "float_unicode_after": "1.5é".encode(),
    "float_overflow": b"1e999",
    "float_frac_overflow": b"1.0e999",
    "float_underflow": b"1e-999 1.0e-999",
    "float_max": b"1.7976931348623157e308",
    "float_just_over_max": b"1.797693134862315807e308",
    "float_min_normal": b"2.2250738585072014e-308",
    "float_min_subnormal": b"4.9e-324 5e-324 2.4703282292062328e-324",
    "float_below_subnormal": b"2.4703282292062327e-324",
    "float_hard_rounding": (b"1e23 8.41e21 2.0e22 9007199254740993.0 "
                            b"9007199254740992.5 0.1 0.30000000000000004"),
    "float_halfway": b"1.00000000000000011102230246251565404236316680908203125",
    "float_halfway_up": b"1.00000000000000011102230246251565404236316680908203126",
    "float_many_digits": b"3." + b"14159265358979323846" * 40,
    "float_big_mantissa": b"1" + b"0" * 400 + b".0e-400",
    "dot_method": b"1.foo",
    "number_dotdot": b"1..2 1..=2 1...3",
    "number_dot_eof": b"1.",
    "number_dot_e": b"1.e5",
    "float_tie_regression": b"27966680151194.158203125",
    "float_fraction_zeros_cancel": b"0." + b"0" * 5200 + b"1e5201",
    "float_mantissa_zeros_cancel": b"1" + b"0" * 5201 + b"e-5201",
    "float_zero_huge_exponents": b"0e99999999999 0.0e-99999999999 0.000e400",
    "float_huge_positive_exponent": b"1e99999999999999999999999999",
    "float_huge_negative_exponent": b"1e-99999999999999999999999999",
    # 2**1024 - 2**970 is the tie between the largest double and overflow.
    "float_overflow_tie": str(2**1024 - 2**970).encode() + b".0",
    "float_below_overflow_tie": str(2**1024 - 2**970 - 1).encode() + b".0",
    "float_tie_sticky_past_800_digits": (
        str(2**1024 - 2**970 - 2**969).encode() + b".0" + b"0" * 900 + b"1"),
    # 2**-1075 is the tie between zero and the smallest subnormal.
    "float_smallest_tie": str(5**1075).encode() + b"e-1075",
    "float_smallest_tie_sticky": (
        str(5**1075).encode() + b"0" * 900 + b"1e-1976"),
    # --- integer literal length limit, every base and both sides of it
    "limit_decimal_at": b"1" * 4300,
    "limit_decimal_over": b"1" * 4301,
    "limit_decimal_leading_zeros_count": b"0" * 4300 + b"1",
    "limit_decimal_separators_ignored": b"1_" * 4300 + b"1",
    "limit_decimal_scanner_error_first": b"1" * 4301 + b"a",
    "limit_tuple_index_at": b"a." + b"1" * 4300,
    "limit_tuple_index_over": b"a." + b"1" * 4301,
    "limit_hex_at": b"0x" + b"f" * 4300,
    "limit_hex_over": b"0X" + b"f" * 4301,
    "limit_hex_separators_ignored": b"0x" + b"f_" * 4300 + b"f",
    "limit_hex_scanner_error_first": b"0x" + b"f" * 4301 + b"g",
    "limit_binary_at": b"0b" + b"1" * 4300,
    "limit_binary_over": b"0B" + b"1" * 4301,
    "limit_octal_at": b"0o" + b"7" * 4300,
    "limit_octal_over": b"0O" + b"7" * 4301,
    "limit_tokens_before_kept": b"let x = " + b"1" * 4301,
    # --- tuple index context
    "tuple_index": b"a.0 a.1.2 m.0.1",
    "tuple_index_zeros": b"a.007",
    "tuple_index_exp": b"a.1e5",
    "tuple_index_hex": b"a.0x10",
    "tuple_index_alpha": b"a.1b",
    "tuple_index_underscore": b"a.1_0",
    "tuple_index_float_shape": b"a.1.5",
    "tuple_index_unicode": "a.1é".encode(),
    "tuple_index_space": b"a. 5 . 6",
    "dot_then_float_after_space": b"a.\n1.5",
    "tuple_after_dotdot": b"a..0.5",
    # --- characters
    "char_plain": b"'a' ' ' '~' '\"'",
    "char_escapes": b"'\\n' '\\r' '\\t' '\\0' '\\\\' '\\'' '\\\"'",
    "char_hex": b"'\\x41' '\\xff' '\\x00' '\\xAb'",
    "char_hex_bad": b"'\\x4G'",
    "char_hex_short": b"'\\x4'",
    "char_hex_eof": b"'\\x",
    "char_unknown_escape": b"'\\q'",
    "char_unknown_escape_unicode": "'\\é'".encode(),
    "char_unknown_escape_astral": "'\\\U0001f600'".encode(),
    "char_empty": b"''",
    "char_unterminated_eof": b"'",
    "char_unterminated_newline": b"'\n",
    "char_unterminated_after": b"'a",
    "char_two": b"'ab'",
    "char_backslash_eof": b"'\\",
    "char_backslash_newline": b"'\\\n'",
    "char_nul_raw": b"'\x00'",
    "char_latin1": "'é' 'ÿ' '\u0080'".encode(),
    "char_over_u8": "'Ā'".encode(),
    "char_euro": "'€'".encode(),
    "char_astral": "'\U0001f600'".encode(),
    "char_tab_raw": b"'\t'",
    # --- strings
    "str_empty": b'""',
    "str_plain": b'"hello world"',
    "str_escapes_raw": b'"a\\n\\t\\"b\\\\"',
    "str_unknown_escape_kept": b'"\\q \\x \\u1234"',
    "str_unterminated_eof": b'"abc',
    "str_unterminated_newline": b'"abc\ndef"',
    "str_backslash_eof": b'"abc\\',
    "str_backslash_newline": b'"abc\\\ndef" x',
    "str_nul": b'"a\x00b"',
    "str_backslash_nul": b'"a\\\x00b"',
    "str_unicode": '"héllo € \U0001f600"'.encode(),
    "str_position_after_unicode": '"€\U0001f600" x'.encode(),
    "str_tab_and_del": b'"a\tb\x7fc\x01"',
    # --- identifiers and keywords
    "keywords": (b"let fn return if else while loop for in true false int "
                 b"bool as struct asm extern break continue void import enum "
                 b"match const test module pub defer try type null resource "
                 b"move"),
    "near_keywords": b"lets fnn iff In LET _let let_ let1 asms",
    "underscore_ident": b"_ __ _a a_b a1",
    "ident_unicode": "café été 中文 αβ".encode(),
    "ident_astral": "\U00010400x \U00020000".encode(),
    "ident_cont_digit": "a٣ a² a①".encode(),
    "ident_cont_combining": "á".encode(),
    "ident_after_unicode_col": "é a\n€".encode(),
    # --- symbols
    "all_ops": (b"( ) { } , ; : -> => . .. ..= & [ ] = += -= *= /= %= &= |= "
                b"^= <<= >>= + - * / % == != < > <= >= && || ! | ^ ~ << >>"),
    "ops_glued": b"<<=>>=..=->=>==!=<=>=&&||<<>>+=-=*=/=%=&=|=^=",
    "dots_run": b".... ..... ..=.",
    "shift_eof": b"<<",
    "shl_assign_split": b"<< =",
    "fat_arrow_split": b"= >",
    "dot_eof": b".",
    # --- unexpected characters and repr formatting
    "bad_at": b"a @ b",
    "bad_hash": b"#",
    "bad_dollar": b"$",
    "bad_backtick": b"`",
    "bad_question": b"?",
    "bad_backslash": b"\\",
    "bad_nul": b"a \x00 b",
    "nul_only": b"\x00\x00\x00",
    "bad_soh": b"\x01",
    "bad_esc": b"\x1b",
    "bad_del": b"\x7f",
    "bad_c1": "\u0085".encode(),
    "bad_nbsp": " ".encode(),
    "bad_soft_hyphen": "­".encode(),
    "bad_euro": "€".encode(),
    "bad_zwsp": "​".encode(),
    "bad_line_sep": " ".encode(),
    "bad_bom": b"\xef\xbb\xbffn",
    "bad_private_use": "".encode(),
    "bad_unassigned_bmp": "͸".encode(),
    "bad_noncharacter": "￿".encode(),
    "bad_emoji": "\U0001f600".encode(),
    "bad_astral_private": "\U0010ffff".encode(),
    "bad_arabic_digit": "٣".encode(),
    "bad_after_tokens_position": "let x = €;".encode(),
    # --- invalid UTF-8
    "utf8_ff": b"\xff",
    "utf8_fe_in_ident": b"ab\xfe",
    "utf8_lone_continuation": b"\x80",
    "utf8_overlong_2": b"\xc0\x80",
    "utf8_overlong_c1": b"\xc1\xbf",
    "utf8_overlong_3": b"\xe0\x80\x80",
    "utf8_overlong_4": b"\xf0\x80\x80\x80",
    "utf8_surrogate": b"\xed\xa0\x80",
    "utf8_above_max": b"\xf4\x90\x80\x80",
    "utf8_f5": b"\xf5\x80\x80\x80",
    "utf8_truncated_2": b"\xc3",
    "utf8_truncated_3": b"\xe2\x82",
    "utf8_truncated_4": b"\xf0\x9f\x98",
    "utf8_bad_second": b"\xe2\x28\xa1",
    "utf8_in_comment": b"// \xff\nfn",
    "utf8_in_string": b'"\xff"',
    "utf8_located_after_lf": b"let x;\n  \xff",
    "utf8_located_after_crlf": b"let x;\r\n  \xff",
    "utf8_located_after_lone_cr": b"let x;\r  \xff",
    "utf8_located_after_cr_before_bad_byte": b"\r\xff",
    "utf8_located_after_multibyte": "é€\U0001f600".encode() + b"\xff",
    "utf8_located_truncated_on_line_3": b"a\nb\nc\xe2\x82",
    "utf8_located_bad_second_on_line_2": b"\n\xe2\x28\xa1",
    "utf8_valid_edges": (b"\xc2\x80 \xdf\xbf \xe0\xa0\x80 \xed\x9f\xbf "
                         b"\xee\x80\x80 \xf0\x90\x80\x80 \xf4\x8f\xbf\xbf"),
    # --- realistic fragments
    "small_program": (b"fn main() -> int {\n    let x: [u8; 4] = [0; 4];\n"
                      b"    x[0] = 'a';\n    return x.0 as int;\n}\n"),
    "generic_shift_ambiguity": b"Vec<Vec<i64>> a >>= 1;",
}


@pytest.mark.parametrize("name", sorted(EDGE_CASES))
def test_edge_case(lex_exe, tmp_path, name):
    source = tmp_path / "input.mx"
    source.write_bytes(EDGE_CASES[name])
    assert_agrees(lex_exe, source, tmp_path / "out.txt")


TEXT_ALPHABET = list("\"'\\/*\n\t._-+=<>!&|^%~0123456789eExXbBoOaz{}()[];:,@#$`?") + [
    "\r", "\r\n", "\x00", "\x7f", "é", "€", "\U0001f600", "٣",
    " ", "﻿", "let ", "0x", "1e5", "1.5", "..=", ">>=", "/*", "*/", "//",
]
BYTE_ALPHABET = b"\x80\xbf\xc0\xc3\xe2\xed\xa0\xf0\xf4\xf5\xff"


def _mutate_text(rng, text):
    """Edits on code points, so the result is always valid UTF-8."""
    chars = list(text)
    for _ in range(rng.randint(1, 4)):
        operation = rng.choice(("insert", "delete", "replace", "dup"))
        position = rng.randrange(len(chars) + 1)
        if operation == "insert":
            chars[position:position] = [rng.choice(TEXT_ALPHABET)]
        elif operation == "delete" and chars:
            del chars[min(position, len(chars) - 1)]
        elif operation == "replace" and chars:
            chars[min(position, len(chars) - 1)] = rng.choice(TEXT_ALPHABET)
        elif operation == "dup" and chars:
            end = min(len(chars), position + rng.randint(1, 16))
            chars[position:position] = chars[position:end]
    return "".join(chars)


def _mutate_bytes(rng, data):
    """Raw byte corruption, for the decoder rather than the token scanner."""
    text = bytearray(data)
    for _ in range(rng.randint(1, 3)):
        position = rng.randrange(len(text) + 1)
        text[position:position] = bytes([rng.choice(BYTE_ALPHABET)])
    return bytes(text)


FUZZ_CASES = int(os.environ.get("MORT_SELFHOST_FUZZ_CASES", "400"))
BYTE_FUZZ_CASES = int(os.environ.get("MORT_SELFHOST_BYTE_FUZZ_CASES", "60"))
FUZZ_SEED = int(os.environ.get("MORT_SELFHOST_FUZZ_SEED", "20261108"))


def _text_seeds():
    """Files big enough that mutants still carry many tokens, then small cases."""
    large = []
    for path in REPO_FILES:
        text = path.read_bytes().decode("utf-8")
        if len(text) >= 1500:
            large.append(text[:4000])
    small = []
    for value in EDGE_CASES.values():
        if len(value) < 4000:
            try:
                small.append(value.decode("utf-8"))
            except UnicodeDecodeError:
                pass
    return large, small


def test_mutation_differential_at_token_level(lex_exe, tmp_path):
    rng = random.Random(FUZZ_SEED)
    large, small = _text_seeds()
    assert len(large) >= 30
    source = tmp_path / "input.mx"
    out = tmp_path / "out.txt"
    rich = 0
    for index in range(FUZZ_CASES):
        pool = small if index % 8 == 0 else large
        data = _mutate_text(rng, rng.choice(pool)).encode("utf-8")
        source.write_bytes(data)
        try:
            assert_agrees(lex_exe, source, out)
        except AssertionError as error:
            raise AssertionError(
                f"fuzz case {index} (seed {FUZZ_SEED}), input {data!r}:\n{error}"
            ) from error
        if out.read_bytes().count(b"\n") >= 10:
            rich += 1
    # The point of this class is comparing token streams, so prove it did.
    assert rich >= FUZZ_CASES * 3 // 4, f"only {rich} of {FUZZ_CASES} had 10+ lines"


def test_mutation_differential_invalid_bytes(lex_exe, tmp_path):
    rng = random.Random(FUZZ_SEED + 1)
    seeds = [path.read_bytes()[:4000] for path in REPO_FILES[:40]]
    source = tmp_path / "input.mx"
    out = tmp_path / "out.txt"
    for index in range(BYTE_FUZZ_CASES):
        data = _mutate_bytes(rng, rng.choice(seeds))
        source.write_bytes(data)
        try:
            assert_agrees(lex_exe, source, out)
        except AssertionError as error:
            raise AssertionError(
                f"byte fuzz case {index} (seed {FUZZ_SEED}), input {data!r}:\n{error}"
            ) from error


def _random_double(rng):
    """A finite positive double, uniform over the exponent range."""
    while True:
        bits = (rng.randrange(0, 2047) << 52) | rng.getrandbits(52)
        value = struct.unpack(">d", struct.pack(">Q", bits))[0]
        if value > 0:
            return value


def _as_decimal_literal(fraction, extra=""):
    """Exact decimal literal of a dyadic Fraction, with digits appended."""
    power = fraction.denominator.bit_length() - 1
    digits = str(fraction.numerator * 5 ** power) + extra
    return f"{digits}e-{power + len(extra)}"


def float_rounding_corpus(rng, count):
    """Ties between adjacent doubles, and values just either side of a tie."""
    literals = []
    for _ in range(count):
        value = _random_double(rng)
        low = Fraction(value)
        high = Fraction(math.nextafter(value, math.inf))
        tie = (low + high) / 2
        literals.append(_as_decimal_literal(tie))
        literals.append(_as_decimal_literal(tie, "0" * 40 + "1"))
        power = tie.denominator.bit_length() - 1
        below = tie.numerator * 5 ** power * 10 - 1
        literals.append(f"{below}e-{power + 1}")
        literals.append(repr(value).replace("e+", "e"))
    usable = [text for text in literals if math.isfinite(float(text))]
    return " ".join(lit for lit in usable if "." in lit or "e" in lit).encode()


def test_float_rounding_ties_match_python(lex_exe, tmp_path):
    rng = random.Random(FUZZ_SEED + 2)
    source = tmp_path / "input.mx"
    source.write_bytes(float_rounding_corpus(rng, 1500))
    assert_agrees(lex_exe, source, tmp_path / "out.txt")
    assert (tmp_path / "out.txt").read_bytes().count(b" FLOAT ") > 4000


HUGE_LITERALS = {
    "decimal": b"1" * 400_000,
    "hex": b"0x" + b"f" * 400_000,
    "binary": b"0b" + b"1" * 400_000,
    "octal": b"0o" + b"7" * 400_000,
    "float_fraction": b"1." + b"3" * 400_000,
    "float_integer_part": b"3" * 300 + b"." + b"3" * 400_000,
    "float_exponent_digits": b"1e" + b"0" * 400_000 + b"5",
}


@pytest.mark.parametrize("name", sorted(HUGE_LITERALS))
def test_huge_literal_is_fast_and_agrees(lex_exe, tmp_path, name):
    source = tmp_path / "input.mx"
    source.write_bytes(HUGE_LITERALS[name])
    started = time.monotonic()
    try:
        code, _, _ = run_mort_lexer(lex_exe, source, tmp_path / "out.txt")
    except subprocess.TimeoutExpired:
        pytest.fail(f"{name} timed out")
    elapsed = time.monotonic() - started
    assert code in (0, 1)
    assert elapsed < 10, f"{name} took {elapsed:.1f}s"
    assert_agrees(lex_exe, source, tmp_path / "out.txt")


def unicode_table_version():
    header = (HERE / "lexer_unicode.mx").read_text(encoding="ascii")
    return re.search(r"Unicode ([0-9.]+)\.", header).group(1)


def python_ranges(name, predicate):
    lines = []
    start = None
    for code in range(0x110000 + 1):
        member = code < 0x110000 and predicate(chr(code))
        if member and start is None:
            start = code
        elif not member and start is not None:
            lines.append(f"{name} {start} {code - 1}")
            start = None
    return lines


def test_unicode_predicates_match_python_for_every_code_point(tmp_path):
    if unicodedata.unidata_version != unicode_table_version():
        pytest.skip(f"tables are Unicode {unicode_table_version()}, "
                    f"this Python has {unicodedata.unidata_version}")
    exe = tmp_path / ("unicode_dump.exe" if os.name == "nt" else "unicode_dump")
    compiled = subprocess.run(
        [sys.executable, str(ROOT / "mortc.py"),
         str(HERE / "unicode_dump_main.mx"), "-o", str(exe)],
        capture_output=True, text=True, cwd=tmp_path)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    out = tmp_path / "ranges.txt"
    env = dict(os.environ, MORT_LEX_OUT=str(out))
    subprocess.run([str(exe)], env=env, check=True, timeout=120)
    actual = out.read_text(encoding="ascii").splitlines()
    expected = (python_ranges("alpha", str.isalpha)
                + python_ranges("alnum", str.isalnum)
                + python_ranges("nonprint", lambda c: not c.isprintable()))
    assert expected == actual


def test_sources_stay_in_the_bootstrap_subset():
    result = subprocess.run(
        [sys.executable, str(HERE / "tools" / "check_subset.py")],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


SUBSET_VIOLATIONS = {
    "generic_fn": "fn f<T>(x: T) -> T { return x; }",
    "generic_struct": "struct S<T> { x: T }",
    "resource": "resource struct R { x: u8 } fn destroy(r: *R) -> void { }",
    "enum_payload": "enum E { A(u8) }",
    "payload_pattern": ("enum E { A, B } fn f(e: E) { match e { "
                        "E.A(x) => { } _ => { } } }"),
    "tuple_type": "fn f(p: (u8, u8)) { }",
    "tuple_literal": "fn f() { let p = (1, 2); }",
    "slice_type": "fn f(s: []u8) { }",
    "local_array_type": "fn f() { let a: [u8; 4] = [1, 2, 3, 4]; }",
    "array_repeat": "fn f() { let a = [0; 4]; }",
    "fn_pointer": "fn f(g: fn(u8)->u8) { }",
    "try_expr": "fn f() { let x = try g(); } fn g() -> u8 { return 1; }",
    "move_expr": "fn f() { let a = 1; let b = move a; }",
    "defer_stmt": "fn f() { defer free(0 as *void); }",
    "asm_stmt": 'fn f() { asm("nop"); }',
    "float_literal": "fn f() { let x = 1.5; }",
    "null_literal": "fn f() { let p: *u8 = null; }",
    "module_decl": "module a.b;",
    "pub_fn": "pub fn f() { }",
    "test_decl": 'test "t" { }',
    "type_alias": "type A = u8;",
    "import_std": "import std.vec;",
    "import_alias": "import other as x;",
    "unlisted_builtin": "fn f() { thread_sleep_millis(1); }",
    "port_io_builtin": "fn f() { outb(1 as u16, 2 as u8); }",
    "f64_param": "fn f(x: f64) { }",
    "f64_return": "extern fn g() -> f64;",
    "f64_field": "struct S { x: f64 }",
    "f64_local": "fn f(p: *u64) { let x: f64 = *(p as *f64); }",
    "f64_cast": "fn f(p: *u8) -> u64 { return *(p as *f64) as u64; }",
    "f64_global": "let G: *f64 = 0 as *f64;",
    "cast_to_narrow_int": "fn f(x: u64) -> u64 { return (x as i32) as u64; }",
    "cast_to_tuple_pointer": "fn f(x: *u8) { let p = x as *(u8, u8); }",
    "cast_to_array_pointer": "fn f(x: *u8) { let p = x as *[u8; 4]; }",
    "cast_to_slice_pointer": "fn f(x: *u8) { let p = x as *[]u8; }",
    "cast_to_fn_type": "fn f(x: *u8) { let p = x as fn(u8)->u8; }",
    "sizeof_narrow_int": "fn f() -> u64 { return sizeof<i32>(); }",
    "sizeof_array": "fn f() -> u64 { return sizeof<[u8; 4]>(); }",
    "sizeof_tuple": "fn f() -> u64 { return sizeof<(u8, u8)>(); }",
    "sizeof_generic": "fn f() -> u64 { return sizeof<Vec<u8>>(); }",
    "for_narrow_type": "fn f() { for i: i32 in 0..3 { } }",
    "for_f64_type": "fn f() { for i: f64 in 0..3 { } }",
    "match_integer": "fn f(x: u64) { match x { 1 => { } _ => { } } }",
    "match_only_wildcard": "fn f(x: u64) { match x { _ => { } } }",
    "match_bool": "fn f(x: bool) { match x { true => { } _ => { } } }",
    "match_char": "fn f(x: u8) { match x { 'a' => { } _ => { } } }",
    "match_variable_pattern": "fn f(x: u64, y: u64) { match x { y => { } _ => { } } }",
    "match_unknown_enum": "fn f(x: u64) { match x { Other.A => { } _ => { } } }",
    "function_value_let": "fn g() { } fn f() { let h = g; }",
    "function_value_arg": "fn g() { } fn k(x: *void) { } fn f() { k(g); }",
    "function_value_return": "fn g() { } fn f() -> *void { return g as *void; }",
    "function_value_global": "fn g() { } let H: *void = g as *void;",
    "extern_function_value": "extern fn g() -> c_int; fn f() { let h = g; }",
    "builtin_function_value": "fn f() { let h = println; }",
    "tuple_index": "struct S { a: u8 } fn f(s: S) -> u8 { return s.0; }",
    "generic_call": "fn g(x: u8) -> u8 { return x; } fn f() -> u8 { return g<u8>(1); }",
    "payload_constructor": "enum E { A, B } fn f() { let e = E.A(1); }",
    "qualified_call": "fn f() { other.g(); }",
    "generic_struct_literal": "struct S { a: u8 } fn f() { let s = S<u8> { a: 1 }; }",
    "nested_global_array": "let G: [[u8; 2]; 2] = [[1, 2], [3, 4]];",
    "array_field": "struct S { a: [u8; 4] }",
    "array_param": "fn f(a: [u8; 4]) { }",
}

SUBSET_ACCEPTED = """
enum Color { Red, Green }
struct Span { data: *u8, len: u64 }
const LIMIT: u64 = 4;
let TABLE: [u32; 2] = [1, 2];
let NAMES: [*u8; 2] = ["a", "b"];
let COLORS: [Color; 1] = [Color.Green];
extern fn getenv(name: *const c_char) -> *c_char;
fn helper(x: u64) -> u64 { return x + 1; }
fn name_of(c: Color) -> *u8 {
    match c {
        Color.Red => { return "r"; }
        _ => { return "g"; }
    }
}
fn shadow(helper: u64) -> u64 { return helper; }
fn main() -> int {
    let s = Span { data: alloc(sizeof<u64>()) as *u8, len: 0 };
    for i: u64 in 0..=LIMIT { s.len += helper(i); }
    let n = 0;
    loop { n += 1; if n > 3 { break; } else { continue; } }
    while n > 0 { n -= 1; }
    let shadowed = helper(TABLE[0] as u64);
    let c = COLORS[0] as u64;
    if s.data[0] == 'x' && shadowed != c { println(name_of(Color.Red)); }
    free(s.data as *void);
    return 0;
}
"""


def test_subset_checker_accepts_every_included_construct(tmp_path):
    sys.path.insert(0, str(HERE / "tools"))
    import check_subset
    path = tmp_path / "good.mx"
    path.write_text(SUBSET_ACCEPTED, encoding="utf-8")
    assert check_subset.check_file(path, collections.Counter()) == []


@pytest.mark.parametrize("name", sorted(SUBSET_VIOLATIONS))
def test_subset_checker_rejects(tmp_path, name):
    sys.path.insert(0, str(HERE / "tools"))
    import check_subset
    path = tmp_path / "bad.mx"
    path.write_text(SUBSET_VIOLATIONS[name], encoding="utf-8")
    assert check_subset.check_file(path, collections.Counter()), name


# Each entry is (source, text one reported problem must contain), so a sample
# cannot pass by tripping some unrelated rule.
SUBSET_SCOPE_VIOLATIONS = {
    # A shadow must end with its block and must not start before its `let`.
    "fn_after_if_block": ("fn g() { } fn main() -> int { if true { let g = 1; } "
                          "let h = g; return 0; }", "function value g"),
    "fn_in_else_of_shadowing_then": ("fn g() { } fn f() { if true { let g = 1; } "
                                     "else { let h = g; } }", "function value g"),
    "fn_after_nested_block": ("fn g() { } fn f() { { let g = 1; } let h = g; }",
                              "function value g"),
    "fn_after_while_body": ("fn g() { } fn f() { while true { let g = 1; } "
                            "let h = g; }", "function value g"),
    "fn_after_loop_body": ("fn g() { } fn f() { loop { let g = 1; } let h = g; }",
                           "function value g"),
    "fn_after_for_loop_variable": ("fn g() { } fn f() { for g: u64 in 0..3 { } "
                                   "let h = g; }", "function value g"),
    "fn_in_for_bound_of_same_name": ("fn g() { } fn f() { for g: u64 in 0..(g as u64) "
                                     "{ } }", "function value g"),
    "fn_after_match_arm": ("enum E { A } fn g() { } fn f(e: E) { match e { "
                           "E.A => { let g = 1; } _ => { } } let h = g; }",
                           "function value g"),
    "fn_in_other_match_arm": ("enum E { A, B } fn g() { } fn f(e: E) { match e { "
                              "E.A => { let g = 1; } E.B => { let h = g; } "
                              "_ => { } } }", "function value g"),
    "fn_used_before_let": ("fn g() { } fn f() { let h = g; let g = 1; }",
                           "function value g"),
    "fn_in_own_initializer": ("fn g() { } fn f() { let g = g; }", "function value g"),
    # A global array may only be indexed; any other use makes an array value.
    "array_let": ("let G: [u8; 2] = [1, 2]; fn main() -> int { let a = G; return 0; }",
                  "array value G"),
    "array_address": ("let G: [u8; 2] = [1, 2]; fn f() { let p = &G; }", "array value G"),
    "array_argument": ("let G: [u8; 2] = [1, 2]; fn k(x: u64) { } fn f() { k(G); }",
                       "array value G"),
    "array_return": ("let G: [u8; 2] = [1, 2]; fn f() -> u64 { return G; }",
                     "array value G"),
    "array_cast": ("let G: [u8; 2] = [1, 2]; fn f() { let p = G as *u8; }",
                   "array value G"),
    "array_field": ("let G: [u8; 2] = [1, 2]; fn f() { let n = G.len; }", "array value G"),
    "array_assignment_source": ("let G: [u8; 2] = [1, 2]; fn f() { let a = 0; a = G; }",
                                "array value G"),
    "array_as_index": ("let G: [u8; 2] = [1, 2]; fn f(p: *u8) { let a = p[G]; }",
                       "array value G"),
    "array_global_alias": ("let G: [u8; 2] = [1, 2]; let H = G;", "array value G"),
    "array_inferred_global": ("let G = [1, 2]; fn f() { let a = G; }", "array value G"),
    "array_after_shadow_block": ("let G: [u8; 2] = [1, 2]; fn f() { if true { let G = 1; } "
                                 "let a = G; }", "array value G"),
    "array_before_shadow": ("let G: [u8; 2] = [1, 2]; fn f() { let a = G; let G = 1; }",
                            "array value G"),
    "array_pointer_global_type": ("let P: *[u8; 2] = 0 as *u8;", "outside the subset"),
    "array_nested_inferred_global": ("let G = [[1, 2], [3, 4]];", "nested array"),
}

SUBSET_SCOPE_ACCEPTED = {
    "local_shadow_in_scope": "fn g() { } fn f() { let g = 1; let h = g; g = h; }",
    "shadow_inside_inner_block": ("fn g() { } fn f() { if true { let g = 1; let h = g; } "
                                  "g(); }"),
    "shadow_visible_in_nested_block": ("fn g() { } fn f() { let g = 1; "
                                       "if true { let h = g; } while g > 2 { g -= 1; } }"),
    "parameter_shadow": "fn g() { } fn f(g: u64) -> u64 { return g; }",
    "for_variable_shadow": "fn g() { } fn f() { for g: u64 in 0..3 { let h = g; } g(); }",
    "match_arm_shadow": ("enum E { A } fn g() { } fn f(e: E) { match e { "
                         "E.A => { let g = 1; let h = g; } _ => { } } g(); }"),
    "else_shadow": "fn g() { } fn f() { if true { } else { let g = 1; let h = g; } }",
    "global_shadows_function": "fn g() { } let g: u64 = 1; fn f() -> u64 { return g; }",
    "direct_call_after_shadow_ended": ("fn g() -> u64 { return 1; } "
                                       "fn f() -> u64 { { let g = 1; } return g(); }"),
    "array_index": "let G: [u8; 2] = [1, 2]; fn f(i: u64) -> u8 { G[i] = 3; return G[i]; }",
    "array_element_address": "let G: [u8; 2] = [1, 2]; fn f() -> *u8 { return &G[0]; }",
    "array_name_shadowed_by_local": ("let G: [u8; 2] = [1, 2]; fn f() -> u64 { let G: u64 = 1; "
                                     "let a = G; return a; }"),
    "array_name_shadowed_by_parameter": ("let G: [u8; 2] = [1, 2]; fn f(G: u64) -> u64 { "
                                         "return G; }"),
    "array_index_after_shadow_ended": ("let G: [u8; 2] = [1, 2]; fn f() -> u8 { "
                                       "if true { let G: u64 = 1; } return G[0]; }"),
}


def _gate():
    sys.path.insert(0, str(HERE / "tools"))
    import check_subset
    return check_subset


@pytest.mark.parametrize("name", sorted(SUBSET_SCOPE_VIOLATIONS))
def test_subset_checker_rejects_scope_holes(tmp_path, name):
    source, reason = SUBSET_SCOPE_VIOLATIONS[name]
    path = tmp_path / "bad.mx"
    path.write_text(source, encoding="utf-8")
    problems = _gate().check_file(path, collections.Counter())
    assert any(reason in problem for problem in problems), (name, problems)


@pytest.mark.parametrize("name", sorted(SUBSET_SCOPE_ACCEPTED))
def test_subset_checker_has_no_scope_false_rejects(tmp_path, name):
    path = tmp_path / "good.mx"
    path.write_text(SUBSET_SCOPE_ACCEPTED[name] + " fn main() -> int { return 0; }",
                    encoding="utf-8")
    assert _gate().check_file(path, collections.Counter()) == []
    # The sample must be a real program, or accepting it would prove nothing.
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); import mortc; "
         "mortc.compile_files_to_c([sys.argv[2]])", str(ROOT), str(path)],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_subset_checker_sees_array_declared_in_sibling_file(tmp_path):
    (tmp_path / "tables.mx").write_text("let G: [u8; 2] = [1, 2];", encoding="utf-8")
    path = tmp_path / "user.mx"
    path.write_text("import tables; fn f() { let a = G; }", encoding="utf-8")
    problems = _gate().check_file(path, collections.Counter())
    assert any("array value G" in problem for problem in problems), problems


FRONTEND_BRANCH = "fix/frontend-crashes"


def real_decode_source():
    """The Python compiler's own decoder, from this tree or its branch.

    mort/lexer.py gains decode_source when fix/frontend-crashes merges. Until
    then its text comes from that branch, never from a copy in this directory,
    and a missing branch is an error: passing without it would prove nothing.
    """
    from mort import lexer as python_lexer
    from mort.errors import MortError
    if hasattr(python_lexer, "decode_source"):
        return python_lexer.decode_source
    text = subprocess.run(
        ["git", "show", f"{FRONTEND_BRANCH}:mort/lexer.py"],
        capture_output=True, encoding="utf-8", cwd=ROOT, check=True).stdout
    function = next(
        node for node in ast.parse(text).body
        if isinstance(node, ast.FunctionDef) and node.name == "decode_source")
    namespace = {"MortError": MortError}
    exec(compile(ast.Module([function], []), "decode_source", "exec"), namespace)
    return namespace["decode_source"]


INVALID_UTF8_CASES = sorted(
    name for name in EDGE_CASES
    if name.startswith("utf8_") and name != "utf8_valid_edges")


@pytest.mark.parametrize("name", INVALID_UTF8_CASES)
def test_invalid_utf8_diagnostic_is_the_python_compilers(lex_exe, tmp_path, name):
    from mort.errors import MortError
    data = EDGE_CASES[name]
    with pytest.raises(MortError) as caught:
        real_decode_source()(data)
    error = caught.value
    assert error.line >= 1 and error.col >= 1
    source = tmp_path / "input.mx"
    source.write_bytes(data)
    expected = f"ERROR {error.line}:{error.col} {error.msg}\n"
    assert py_lex_dump.dump_file(source) == expected
    code, stdout, actual = run_mort_lexer(lex_exe, source, tmp_path / "out.txt")
    assert actual == expected
    assert code == 1
    diagnostic = f"{source}:{error.line}:{error.col}: error: {error.msg}"
    assert diagnostic.encode("utf-8") in stdout, stdout


def test_reference_decoder_matches_the_python_compilers():
    from mort.errors import MortError
    real = real_decode_source()

    def outcome(decoder, data):
        try:
            return decoder(data)
        except MortError as error:
            return (error.msg, error.line, error.col)

    rng = random.Random(FUZZ_SEED + 2)
    alphabet = b"\r\n ab\x80\xbf\xc0\xc3\xe0\xe2\xed\xa0\xf0\xf4\xf5\xff"
    for _ in range(5000):
        data = bytes(rng.choice(alphabet) for _ in range(rng.randrange(12)))
        assert outcome(py_lex_dump.decode_source, data) == outcome(real, data), data


def test_diagnostic_has_file_line_col(lex_exe, tmp_path):
    source = tmp_path / "input.mx"
    source.write_bytes(b"fn main() {\n  let @;\n}\n")
    code, stdout, _ = run_mort_lexer(lex_exe, source, tmp_path / "out.txt")
    assert code == 1
    text = stdout.decode("ascii")
    assert re.search(
        r"input\.mx:2:7: error: unexpected character '@'", text), text


def test_missing_input_is_a_clean_failure(lex_exe, tmp_path):
    env = dict(os.environ)
    env["MORT_LEX_IN"] = str(tmp_path / "does-not-exist.mx")
    env["MORT_LEX_OUT"] = str(tmp_path / "out.txt")
    result = subprocess.run([str(lex_exe)], env=env, capture_output=True, timeout=60)
    assert result.returncode == 2
    assert b"cannot read" in result.stdout


def test_missing_environment_is_a_clean_failure(lex_exe):
    env = {k: v for k, v in os.environ.items()
           if k not in ("MORT_LEX_IN", "MORT_LEX_OUT")}
    result = subprocess.run([str(lex_exe)], env=env, capture_output=True, timeout=60)
    assert result.returncode == 2
    assert b"MORT_LEX_IN" in result.stdout
