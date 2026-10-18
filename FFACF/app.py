
import os
import smtplib
import logging
from email.message import EmailMessage
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlsplit


PAGE_DIRECTORY = Path(__file__).resolve().parent
DATA_FILE = Path(
    os.environ.get("DATA_FILE", PAGE_DIRECTORY / "join_details.txt")
)
DATA_FILE_LOCK = Lock()

logging.basicConfig(level=logging.INFO)


def send_confirmation_email(name, recipient_email):
    """Send a confirmation email to someone who joins."""

    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    sender_email = os.environ.get("FROM_EMAIL", smtp_user or "")

    if not smtp_user or not smtp_password or not sender_email:
        raise RuntimeError("Email settings are not configured.")

    message = EmailMessage()
    message["Subject"] = "Thanks for joining FFACF!"
    message["From"] = sender_email
    message["To"] = recipient_email

    message.set_content(
        f"""Hi {name},

Thank you for joining FFACF!

We're happy to have you on our list. Your details have been
received successfully.

Thank you for your interest and support!

Best regards,
The FFACF Team
"""
    )

    if smtp_port == 465:
        with smtplib.SMTP_SSL(
            smtp_host, smtp_port, timeout=20
        ) as server:
            server.login(smtp_user, smtp_password)
            server.send_message(message)
    else:
        with smtplib.SMTP(
            smtp_host, smtp_port, timeout=20
        ) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, smtp_password)
            server.send_message(message)


class JoinPageHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(
            *args, directory=str(PAGE_DIRECTORY), **kwargs
        )

    def do_GET(self):
        route = urlsplit(self.path).path

        if route == "/healthz":
            self._send_body(
                200, "text/plain; charset=utf-8", b"ok"
            )
            return

        if route not in ("/", "/Join_page_for_FFACF.html"):
            self.send_error(404)
            return

        page_path = PAGE_DIRECTORY / "Join_page_for_FFACF.html"

        try:
            page = page_path.read_bytes()
        except OSError:
            logging.exception("Could not read the join page")
            self.send_error(500, "Could not load join page")
            return

        self._send_body(200, "text/html; charset=utf-8", page)

    def do_POST(self):
        if urlsplit(self.path).path != "/download":
            self.send_error(404)
            return

        try:
            content_length = int(
                self.headers.get("Content-Length", "0")
            )
        except ValueError:
            self.send_error(400, "Invalid content length")
            return

        if content_length <= 0:
            self.send_error(400, "Empty form submission")
            return

        if content_length > 10_000:
            self.send_error(413, "Form submission is too large")
            return

        try:
            raw_data = self.rfile.read(content_length).decode(
                "utf-8"
            )
            form_data = parse_qs(
                raw_data, keep_blank_values=True
            )
        except (UnicodeDecodeError, ValueError):
            self.send_error(400, "Invalid form data")
            return

        name = form_data.get("name", [""])[0].strip()
        email = form_data.get("email", [""])[0].strip()

        # Prevent newlines from altering saved records or email headers.
        name = name.replace("\r", " ").replace("\n", " ")
        email = email.replace("\r", "").replace("\n", "")

        if not name or not email:
            self.send_error(
                400, "Both name and email are required"
            )
            return

        if (
            email.count("@") != 1
            or "." not in email.rsplit("@", 1)[-1]
            or any(char.isspace() for char in email)
        ):
            self.send_error(400, "Invalid email address")
            return

        # Save the submission first, even if email sending fails.
        try:
            with DATA_FILE_LOCK:
                DATA_FILE.parent.mkdir(
                    parents=True, exist_ok=True
                )
                with DATA_FILE.open("a", encoding="utf-8") as records:
                    records.write(
                        f"Name: {name}\nEmail: {email}\n\n"
                    )
        except OSError:
            logging.exception("Could not save join submission")
            self.send_error(500, "Could not save your details")
            return

        # Send the confirmation email.
        try:
            send_confirmation_email(name, email)
            logging.info("Confirmation email sent to %s", email)

            message = (
                "Your details have been saved successfully, "
                "and a confirmation email has been sent to you."
            )
        except Exception:
            logging.exception(
                "Confirmation email could not be sent to %s",
                email,
            )

            message = (
                "Your details have been saved successfully, "
                "but we could not send your confirmation email. "
                "Please contact us if you need assistance."
            )

        confirmation = f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>FFACF - Join Confirmation</title>
</head>
<body>
    <h1>Thank you for joining FFACF!</h1>
    <p>{message}</p>
    <p><a href="/">Back to the join form</a></p>
</body>
</html>"""

        # The message above is fixed text, not user-supplied HTML.
        self._send_body(
            200,
            "text/html; charset=utf-8",
            confirmation.encode("utf-8"),
        )

    def _send_body(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    server = ThreadingHTTPServer(
        ("0.0.0.0", port), JoinPageHandler
    )

    print(f"Listening on port {port}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()
