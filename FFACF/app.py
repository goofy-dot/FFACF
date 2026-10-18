import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlsplit


PAGE_DIRECTORY = Path(__file__).resolve().parent
DATA_FILE = Path(os.environ.get("DATA_FILE", PAGE_DIRECTORY / "join_details.txt"))
DATA_FILE_LOCK = Lock()


class JoinPageHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PAGE_DIRECTORY), **kwargs)

    def do_GET(self):
        route = urlsplit(self.path).path
        if route == "/healthz":
            self._send_body(200, "text/plain; charset=utf-8", b"ok")
            return
        if route not in ("/", "/Join_page_for_FFACF.html"):
            self.send_error(404)
            return

        page = (PAGE_DIRECTORY / "index.html").read_bytes()
        self._send_body(200, "text/html; charset=utf-8", page)

    def do_POST(self):
        if urlsplit(self.path).path != "/download":
            self.send_error(404)
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400, "Invalid content length")
            return
        if content_length > 10_000:
            self.send_error(413, "Form submission is too large")
            return

        form_data = parse_qs(
            self.rfile.read(content_length).decode("utf-8", errors="replace"),
            keep_blank_values=True,
        )
        name = form_data.get("name", [""])[0].strip().replace("\r", " ").replace("\n", " ")
        email = form_data.get("email", [""])[0].strip().replace("\r", " ").replace("\n", " ")

        if not name or not email:
            self.send_error(400, "Both name and email are required")
            return

        if email.count("@") != 1 or "." not in email.rsplit("@", 1)[-1]:
            self.send_error(400, "Invalid email address")
            return

        with DATA_FILE_LOCK:
            DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
            with DATA_FILE.open("a", encoding="utf-8") as records:
                records.write(f"Name: {name}\nEmail: {email}\n\n")

        confirmation = (
            "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"
            "<title>Join form saved</title><body>"
            "<p>Your details were added to the list.</p>"
            "<p><a href=\"/\">Back to the join form</a></p>"
            "</body></html>"
        ).encode("utf-8")
        self._send_body(200, "text/html; charset=utf-8", confirmation)

    def _send_body(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), JoinPageHandler)
    print(f"Listening on port {port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()
