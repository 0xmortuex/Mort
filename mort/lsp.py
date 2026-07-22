"""Small, dependency-free Language Server Protocol implementation for Mort."""
import json
import os
import re
import sys
from urllib.parse import unquote, urlparse

from .errors import MortError
from .formatter import format_source
from .lexer import Lexer
from .parser import Parser, FIXED_INT_TYPES, FLOAT_TYPES
from .tokens import KEYWORDS


_BUILTINS = {
    "alloc": "fn alloc(size: u64) -> *void",
    "assert": "fn assert(condition: bool) -> void",
    "free": "fn free(pointer: *void) -> void",
    "len": "fn len(value) -> u64",
    "print": "fn print(value) -> void",
    "println": "fn println(text: *u8) -> void",
    "sizeof": "fn sizeof<T>() -> u64",
    "slice": "fn slice<T>(pointer: *T, length: u64) -> []T",
}


def _parse_document(text):
    try:
        return Parser(Lexer(text).tokenize()).parse()
    except MortError:
        return None


def _range(line, start=0, length=1):
    line = max(0, line - 1)
    return {
        "start": {"line": line, "character": start},
        "end": {"line": line, "character": start + max(1, length)},
    }


def _function_detail(function, prefix="fn"):
    generic = (
        "<" + ", ".join(function.generic_params) + ">"
        if getattr(function, "generic_params", None) else ""
    )
    params = ", ".join(f"{item.name}: {item.typ}" for item in function.params)
    return f"{prefix} {function.name}{generic}({params}) -> {function.ret}"


def document_symbols(text):
    """Return LSP document symbols derived from Mort's real parser."""
    program = _parse_document(text)
    if program is None:
        return []
    symbols = []

    def add(name, kind, line, detail=None, children=None):
        item = {
            "name": name,
            "kind": kind,
            "range": _range(line, length=len(name)),
            "selectionRange": _range(line, length=len(name)),
        }
        if detail:
            item["detail"] = detail
        if children:
            item["children"] = children
        symbols.append(item)

    if program.module_name:
        add(program.module_name, 3, 1, "module")
    for alias in program.aliases:
        add(alias.name, 5, alias.line, f"type {alias.name} = {alias.target}")
    for structure in program.structs:
        detail = "struct " + structure.name
        if structure.generic_params:
            detail += "<" + ", ".join(structure.generic_params) + ">"
        add(structure.name, 23, structure.line, detail)
    for enum in program.enums:
        detail = "enum " + enum.name
        if enum.generic_params:
            detail += "<" + ", ".join(enum.generic_params) + ">"
        add(enum.name, 10, enum.line, detail)
    for global_ in program.globals:
        detail = ("const " if not global_.mutable else "let ") + global_.name
        if global_.decl_type:
            detail += ": " + global_.decl_type
        add(global_.name, 14 if not global_.mutable else 13, global_.line, detail)
    for external in program.externs:
        add(external.name, 12, external.line, _function_detail(external, "extern fn"))
    for function in program.funcs:
        add(function.name, 12, function.line, _function_detail(function))
    for test in program.tests:
        add(test.name, 12, test.line, f'test "{test.name}"')
    return symbols


def completion_items(text):
    """Return stable keyword, type, builtin, and document-level completions."""
    items = {}

    def add(label, kind, detail):
        items[label] = {"label": label, "kind": kind, "detail": detail}

    for keyword in KEYWORDS:
        add(keyword, 14, "Mort keyword")
    for type_name in sorted(FIXED_INT_TYPES | FLOAT_TYPES | {"bool", "int", "void"}):
        add(type_name, 25, "Mort type")
    for name, detail in _BUILTINS.items():
        add(name, 3, detail)
    program = _parse_document(text)
    if program is not None:
        for function in [*program.funcs, *program.externs]:
            add(function.name, 3, _function_detail(function))
        for declaration in [*program.structs, *program.enums, *program.aliases]:
            add(declaration.name, 25, "Mort type")
        for global_ in program.globals:
            add(global_.name, 6, global_.decl_type or "inferred global")
        for import_ in program.imports:
            name = import_.alias or import_.parts[-1]
            add(name, 9, "Mort module")
    return [items[name] for name in sorted(items)]


def hover_for_document(text, line, character):
    """Return signature hover information for a top-level symbol or builtin."""
    lines = text.splitlines()
    if line < 0 or line >= len(lines):
        return None
    source_line = lines[line]
    if character < 0 or character > len(source_line):
        return None
    start = character
    while start > 0 and (source_line[start - 1].isalnum() or source_line[start - 1] == "_"):
        start -= 1
    end = character
    while end < len(source_line) and (source_line[end].isalnum() or source_line[end] == "_"):
        end += 1
    word = source_line[start:end]
    if not word:
        return None
    detail = _BUILTINS.get(word)
    if detail is None:
        for symbol in document_symbols(text):
            if symbol["name"] == word:
                detail = symbol.get("detail", word)
                break
    if detail is None:
        return None
    return {
        "contents": {"kind": "markdown", "value": f"```mort\n{detail}\n```"},
        "range": {
            "start": {"line": line, "character": start},
            "end": {"line": line, "character": end},
        },
    }


def _signature_parameters(label):
    start = label.find("(")
    end = label.rfind(")")
    if start < 0 or end < start or end == start + 1:
        return []
    inner = label[start + 1:end]
    result = []
    part_start = 0
    depth = 0
    for index, char in enumerate(inner):
        if char in "<([":
            depth += 1
        elif char in ">)]":
            depth -= 1
        elif char == "," and depth == 0:
            result.append(inner[part_start:index].strip())
            part_start = index + 1
    result.append(inner[part_start:].strip())
    return result


def signature_help(text, line, character):
    """Return call signature help at a zero-based LSP position."""
    lines = text.splitlines()
    if line < 0 or line >= len(lines) or character < 0 or character > len(lines[line]):
        return None
    prefix = "\n".join(lines[:line])
    if line:
        prefix += "\n"
    prefix += lines[line][:character]
    depth = 0
    active_parameter = 0
    opening = None
    for index in range(len(prefix) - 1, -1, -1):
        char = prefix[index]
        if char == ")":
            depth += 1
        elif char == "(":
            if depth == 0:
                opening = index
                break
            depth -= 1
        elif char == "," and depth == 0:
            active_parameter += 1
    if opening is None:
        return None
    match = re.search(r"([A-Za-z_][A-Za-z0-9_.]*)\s*$", prefix[:opening])
    if match is None:
        return None
    name = match.group(1)
    detail = _BUILTINS.get(name)
    if detail is None:
        program = _parse_document(text)
        if program is not None:
            for function in [*program.funcs, *program.externs]:
                if function.name == name:
                    detail = _function_detail(function)
                    break
    if detail is None:
        return None
    parameters = _signature_parameters(detail)
    return {
        "signatures": [{
            "label": detail,
            "parameters": [{"label": parameter} for parameter in parameters],
        }],
        "activeSignature": 0,
        "activeParameter": min(active_parameter, max(0, len(parameters) - 1)),
    }


def uri_to_path(uri):
    parsed = urlparse(uri)
    path = unquote(parsed.path)
    if parsed.netloc:
        path = f"//{parsed.netloc}{path}"
    if os.name == "nt" and len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return os.path.abspath(path)


def _lsp_diagnostic(diagnostic):
    line = max(1, diagnostic.get("line") or 1)
    column = max(1, diagnostic.get("column") or 1)
    result = {
        "range": {
            "start": {"line": line - 1, "character": column - 1},
            "end": {"line": line - 1, "character": column},
        },
        "severity": 1 if diagnostic["severity"] == "error" else 2,
        "source": "mort",
        "message": diagnostic["message"],
    }
    if diagnostic.get("code"):
        result["code"] = diagnostic["code"]
    return result


def diagnostics_for_document(uri, text):
    """Compile an in-memory document and return LSP-shaped diagnostics."""
    from mortc import compile_files_to_c

    path = uri_to_path(uri)
    warnings = []
    try:
        compile_files_to_c(
            [path],
            warnings=warnings,
            source_overrides={path: text},
        )
    except MortError as error:
        return [_lsp_diagnostic(error.to_diagnostic())]
    return [_lsp_diagnostic(warning.to_diagnostic()) for warning in warnings]


class Server:
    def __init__(self, input_stream=None, output_stream=None):
        self.input = input_stream or sys.stdin.buffer
        self.output = output_stream or sys.stdout.buffer
        self.documents = {}
        self.shutdown_requested = False

    def _read(self):
        length = None
        while True:
            line = self.input.readline()
            if not line:
                return None
            if line in (b"\r\n", b"\n"):
                break
            name, _, value = line.decode("ascii").partition(":")
            if name.lower() == "content-length":
                length = int(value.strip())
        if length is None:
            return None
        payload = self.input.read(length)
        return json.loads(payload.decode("utf-8"))

    def _write(self, message):
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.output.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii"))
        self.output.write(payload)
        self.output.flush()

    def _respond(self, request, result=None, error=None):
        response = {"jsonrpc": "2.0", "id": request.get("id")}
        if error is None:
            response["result"] = result
        else:
            response["error"] = error
        self._write(response)

    def _publish(self, uri, text):
        self._write({
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {
                "uri": uri,
                "diagnostics": diagnostics_for_document(uri, text),
            },
        })

    def handle(self, request):
        method = request.get("method")
        params = request.get("params") or {}
        if method == "initialize":
            self._respond(request, {
                "capabilities": {
                    "textDocumentSync": {"openClose": True, "change": 1, "save": True},
                    "completionProvider": {"triggerCharacters": ["."]},
                    "documentSymbolProvider": True,
                    "hoverProvider": True,
                    "signatureHelpProvider": {"triggerCharacters": ["(", ","]},
                    "documentFormattingProvider": True,
                },
                "serverInfo": {"name": "mort-lsp"},
            })
        elif method == "shutdown":
            self.shutdown_requested = True
            self._respond(request, None)
        elif method == "exit":
            return False
        elif method == "textDocument/didOpen":
            document = params["textDocument"]
            self.documents[document["uri"]] = document["text"]
            self._publish(document["uri"], document["text"])
        elif method == "textDocument/didChange":
            uri = params["textDocument"]["uri"]
            changes = params.get("contentChanges") or []
            if changes:
                self.documents[uri] = changes[-1]["text"]
                self._publish(uri, self.documents[uri])
        elif method == "textDocument/didSave":
            uri = params["textDocument"]["uri"]
            text = params.get("text", self.documents.get(uri))
            if text is not None:
                self.documents[uri] = text
                self._publish(uri, text)
        elif method == "textDocument/didClose":
            uri = params["textDocument"]["uri"]
            self.documents.pop(uri, None)
            self._write({
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {"uri": uri, "diagnostics": []},
            })
        elif method == "textDocument/completion":
            uri = params["textDocument"]["uri"]
            self._respond(request, completion_items(self.documents.get(uri, "")))
        elif method == "textDocument/documentSymbol":
            uri = params["textDocument"]["uri"]
            self._respond(request, document_symbols(self.documents.get(uri, "")))
        elif method == "textDocument/hover":
            uri = params["textDocument"]["uri"]
            position = params["position"]
            self._respond(request, hover_for_document(
                self.documents.get(uri, ""), position["line"], position["character"]))
        elif method == "textDocument/signatureHelp":
            uri = params["textDocument"]["uri"]
            position = params["position"]
            self._respond(request, signature_help(
                self.documents.get(uri, ""), position["line"], position["character"]))
        elif method == "textDocument/formatting":
            uri = params["textDocument"]["uri"]
            source = self.documents.get(uri, "")
            self._respond(request, [{
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": len(source.splitlines()) + 1, "character": 0},
                },
                "newText": format_source(source),
            }])
        elif "id" in request:
            self._respond(request, error={
                "code": -32601,
                "message": f"method not found: {method}",
            })
        return True

    def run(self):
        while True:
            request = self._read()
            if request is None or not self.handle(request):
                break
        return 0 if self.shutdown_requested else 1


def run():
    return Server().run()
