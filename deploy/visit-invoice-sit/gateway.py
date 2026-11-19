"""Disposable loopback ingress; never log request targets, bodies or headers."""
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from policy import allows

MAX_BODY = 16384
REQUEST_HEADERS = ("accept", "content-type", "cookie", "x-frappe-csrf-token")
RESPONSE_HEADERS = ("content-type", "cache-control", "set-cookie", "location", "etag", "last-modified")


def request_length(headers):
    lengths = headers.get_all("Content-Length", [])
    if headers.get_all("Transfer-Encoding") or len(lengths) > 1:
        return None
    if not lengths:
        return 0
    value = lengths[0]
    if not value.isascii() or not value.isdecimal() or len(value) > 5:
        return None
    length = int(value)
    return length if length <= MAX_BODY else None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):
        pass

    def handle_request(self):
        self.close_connection = True
        self.connection.settimeout(10)
        raw_parts = self.raw_requestline.rstrip(b"\r\n").split(b" ")
        if len(raw_parts) != 3 or raw_parts[1] != self.path.encode("iso-8859-1"):
            self.send_error(403)
            return
        length = request_length(self.headers)
        if length is None or any(len(self.headers.get_all(h, [])) > 1 for h in REQUEST_HEADERS):
            self.send_error(400)
            return
        try:
            body = self.rfile.read(length)
            if len(body) != length or not allows(self.command, self.path, body, self.headers.get("content-type", "")):
                self.send_error(403)
                return
            headers = {h: self.headers[h] for h in REQUEST_HEADERS if h in self.headers}
            headers["Host"] = "clinic-visit-invoice.local"
            connection = HTTPConnection("frontend", 8080, timeout=15)
            try:
                connection.request(self.command, self.path, body=body if self.command == "POST" else None, headers=headers)
                response = connection.getresponse()
                self.send_response(response.status)
                for name, value in response.getheaders():
                    if name.lower() in RESPONSE_HEADERS:
                        self.send_header(name, value)
                self.end_headers()
                while chunk := response.read(65536):
                    self.wfile.write(chunk)
            finally:
                connection.close()
        except (OSError, HTTPException):
            self.close_connection = True

    do_GET = handle_request
    do_POST = handle_request


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
