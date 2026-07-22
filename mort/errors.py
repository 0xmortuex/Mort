"""A single error type used across every compiler phase."""


class MortError(Exception):
    def __init__(self, msg, line=None, col=None, filename=None):
        self.msg = msg
        self.line = line
        self.col = col
        self.filename = filename
        super().__init__(self.format())

    def format(self):
        if self.filename:
            location = self.filename
            if self.line is not None:
                location += f":{self.line}"
                if self.col is not None:
                    location += f":{self.col}"
            return f"{location}: error: {self.msg}"
        loc = f" (line {self.line})" if self.line is not None else ""
        return f"error{loc}: {self.msg}"

    def render(self):
        """Render a source-aware diagnostic with a compact code excerpt."""
        header = self.format()
        if not self.filename or self.line is None:
            return header
        try:
            with open(self.filename, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except OSError:
            return header
        if not (1 <= self.line <= len(lines)):
            return header
        source = lines[self.line - 1].rstrip("\r\n")
        width = len(str(self.line))
        column = max(1, self.col or 1)
        return (
            f"{header}\n"
            f" {' ' * width} |\n"
            f" {self.line:>{width}} | {source}\n"
            f" {' ' * width} | {' ' * (column - 1)}^"
        )
