"""A conservative, comment-preserving formatter for Mort source files."""


def _brace_delta(line):
    """Count structural braces outside strings and line comments."""
    opens = closes = 0
    in_string = escaped = False
    index = 0
    while index < len(line):
        char = line[index]
        if not in_string and char == "/" and index + 1 < len(line) and line[index + 1] == "/":
            break
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            opens += 1
        elif char == "}":
            closes += 1
        index += 1
    return opens, closes


def format_source(source):
    """Normalize indentation and trailing whitespace without losing comments."""
    output = []
    depth = 0
    blank = False
    for raw in source.splitlines():
        text = raw.strip()
        if not text:
            if output and not blank:
                output.append("")
            blank = True
            continue
        blank = False
        opens, closes = _brace_delta(text)
        leading_closes = 0
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
