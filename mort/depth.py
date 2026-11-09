"""How deep one compile may recurse, counted in the compiler's own frames.

The limit used to be CPython's recursion limit, which also counts the caller's
frames and, depending on the version, some C calls: the same source was accepted
by one Python and rejected by another. Python frames of this compiler are the
same on every version, so they are counted here instead, and the interpreter's
limit is only a safety net above the budget.
"""
import functools
import sys
import threading

COMPILE_FRAME_BUDGET = 1000
_INTERPRETER_HEADROOM = 200


class _FrameLimit(threading.local):
    frames = None


_limit = _FrameLimit()


def check_frames(extra=0):
    """Raise RecursionError if `extra` more frames would exceed the budget."""
    frames = _limit.frames
    if frames is None:
        return
    try:
        sys._getframe(frames - extra)
    except ValueError:
        return
    raise RecursionError("compile frame budget exhausted")


def with_frame_budget(compile_function):
    """Give a compile the same budget wherever it is called from."""
    @functools.wraps(compile_function)
    def budgeted(*args, **kwargs):
        depth = 0
        frame = sys._getframe()
        while frame is not None:
            depth += 1
            frame = frame.f_back
        previous_frames = _limit.frames
        previous_limit = sys.getrecursionlimit()
        _limit.frames = depth + COMPILE_FRAME_BUDGET
        sys.setrecursionlimit(depth + COMPILE_FRAME_BUDGET + _INTERPRETER_HEADROOM)
        try:
            return compile_function(*args, **kwargs)
        finally:
            _limit.frames = previous_frames
            sys.setrecursionlimit(previous_limit)
    return budgeted
