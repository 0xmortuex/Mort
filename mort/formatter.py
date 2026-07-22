"""A conservative, comment-preserving formatter for Mort source files."""


def _brace_delta(line, block_depth=0):
    """Count structural braces outside literals and nested comments."""
    opens = closes = 0
    quote = None
    escaped = False
    index = 0
    while index < len(line):
        char = line[index]
        following = line[index + 1] if index + 1 < len(line) else ""
        if block_depth:
            if char == "/" and following == "*":
                block_depth += 1
                index += 2
                continue
            if char == "*" and following == "/":
                block_depth -= 1
                index += 2
                continue
            index += 1
            continue
        if quote is None and char == "/" and following == "/":
            break
        if quote is None and char == "/" and following == "*":
            block_depth = 1
            index += 2
            continue
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
        elif char == "{":
            opens += 1
        elif char == "}":
            closes += 1
        index += 1
    return opens, closes, block_depth


def format_source(source):
    """Normalize indentation and trailing whitespace without losing comments."""
    output = []
    depth = 0
    block_depth = 0
    blank = False
    for raw in source.splitlines():
        text = raw.strip()
        if not text:
            if output and not blank:
                output.append("")
            blank = True
            continue
        blank = False
        started_in_block_comment = block_depth > 0
        opens, closes, block_depth = _brace_delta(text, block_depth)
        leading_closes = 0
        if not started_in_block_comment:
            for char in text:
                if char == "}":
                    leading_closes += 1
                elif not char.isspace():
                    break
        line_depth = max(0, depth - leading_closes)
        output.append("    " * line_depth + text)
        depth = max(0, depth + opens - closes)
    while output and not output[-1]:
        output.pop()
    return "\n".join(output) + "\n"


def format_file(path, check=False):
    with open(path, "r", encoding="utf-8") as handle:
        original = handle.read()
    formatted = format_source(original)
    changed = formatted != original
    if changed and not check:
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(formatted)
    return changed
