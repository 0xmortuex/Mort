"""Small, dependency-free Language Server Protocol implementation for Mort."""
import json
import os
import sys
from urllib.parse import unquote, urlparse

from .errors import MortError


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
