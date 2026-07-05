"""A single error type used across every compiler phase."""


class MortError(Exception):
    def __init__(self, msg, line=None, col=None):
        self.msg = msg
        self.line = line
        self.col = col
        super().__init__(self.format())

    def format(self):
        loc = f" (line {self.line})" if self.line is not None else ""
        return f"error{loc}: {self.msg}"
