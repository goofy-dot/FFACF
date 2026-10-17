from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs


PAGE_DIRECTORY = Path(__file__).resolve().parent
DATA_FILE = PAGE_DIRECTORY / "join_details.txt"
DATA_FILE_LOCK = Lock()


class JoinPageHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PAGE_DIRECTORY), **kwargs)

    def do_POST(self):
        if self.path != "/download":
            self.send_error(404)
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > 10_000:
            self.send_error(413, "Form submission is too large")
            return

        form_data = parse_qs(
            self.rfile.read(content_length).decode("utf-8"),
            keep_blank_values=True,
        )
        name = form_data.get("name", [""])[0].strip().replace("\r", " ").replace("\n", " ")
        email = form_data.get("email", [""])[0].strip().replace("\r", " ").replace("\n", " ")

        if not name or not email:
            self.send_error(400, "Both name and email are required")
            return

        if "@" not in email:
            self.send_error(400, "Invalid email address")
            return

        with DATA_FILE_LOCK:
            with DATA_FILE.open("a", encoding="utf-8") as records:
                records.write(f"Name: {name}\nEmail: {email}\n\n")

        confirmation = (
            "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"
            "<title>Join form saved</title><body>"
            "<p>Your details were added to the list.</p>"
            "<p><a href=\"/Join_page_for_FFACF.html\">Back to the join form</a></p>"
            "<p><a href=\"/join_details.txt\">View the saved list</a></p>"
            "<p><a href=\"friends for a cause foundation jr.html\">back to main page</a></p>"
            "</body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(confirmation)))
        self.end_headers()
        self.wfile.write(confirmation)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8000), JoinPageHandler)
    print("Open http://127.0.0.1:8000/Join_page_for_FFACF.html")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()
