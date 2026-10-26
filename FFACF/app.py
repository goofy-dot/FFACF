
import os
import re
import html
import logging
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import psycopg
import resend
from psycopg.errors import UniqueViolation


PAGE_DIRECTORY = Path(__file__).resolve().parent

DATABASE_URL = os.environ.get("DATABASE_URL")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
EMAIL_FROM = os.environ.get("EMAIL_FROM")
SITE_NAME = os.environ.get("SITE_NAME", "Our Community")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def check_configuration():
    missing = [
        key
        for key, value in {
            "DATABASE_URL": DATABASE_URL,
            "RESEND_API_KEY": RESEND_API_KEY,
            "EMAIL_FROM": EMAIL_FROM,
        }.items()
        if not value
    ]

    if missing:
        raise RuntimeError(
            "Missing required environment variables: "
            + ", ".join(missing)
        )

    resend.api_key = RESEND_API_KEY


def initialize_database():
    """Create the signup table if it does not exist."""
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signups (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                welcome_email_status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (
                        welcome_email_status IN (
                            'pending', 'sent', 'failed'
                        )
                    ),
                welcome_email_sent_at TIMESTAMPTZ
            )
        """)


def save_signup(name, email):
    """Save the signup and return its database ID."""
    with psycopg.connect(DATABASE_URL) as conn:
        row = conn.execute(
            """
            INSERT INTO signups (name, email)
            VALUES (%s, %s)
            RETURNING id
            """,
            (name, email),
        ).fetchone()

        return row[0]


def update_email_status(signup_id, status):
    """Record whether the welcome email was sent."""
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute(
            """
            UPDATE signups
            SET welcome_email_status = %s,
                welcome_email_sent_at = CASE
                    WHEN %s = 'sent' THEN NOW()
                    ELSE welcome_email_sent_at
                END
            WHERE id = %s
            """,
            (status, status, signup_id),
        )


def send_welcome_email(name, email):
    """Send a personalized welcome email using Resend."""
    safe_name = html.escape(name)

    resend.Emails.send({
        "from": EMAIL_FROM,
        "to": [email],
        "subject": f"Welcome to {SITE_NAME}!",
        "html": (
            f"<h1>Welcome, {safe_name}!</h1>"
            f"<p>Thanks for signing up with {html.escape(SITE_NAME)}.</p>"
            "<p>We're glad to have you with us.</p>"
        ),
        "text": (
            f"Welcome, {name}!\n\n"
            f"Thanks for signing up with {SITE_NAME}.\n"
            "We're glad to have you with us."
        ),
    })


class JoinPageHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(
            *args,
            directory=str(PAGE_DIRECTORY),
            **kwargs,
        )

    def do_GET(self):
        route = urlsplit(self.path).path

        if route == "/healthz":
            self._send_body(
                200, "text/plain; charset=utf-8", b"ok"
            )
            return

        if route not in ("/", "/index.html"):
            self.send_error(404)
            return

        try:
            page = (PAGE_DIRECTORY / "index.html").read_bytes()
        except OSError:
            logger.exception("Could not read index.html")
            self.send_error(500, "Page unavailable")
            return

        self._send_body(
            200, "text/html; charset=utf-8", page
        )

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

        if "application/x-www-form-urlencoded" not in (
            self.headers.get("Content-Type", "").lower()
        ):
            self.send_error(415, "Unsupported form content type")
            return

        try:
            raw_body = self.rfile.read(content_length)
            form_data = parse_qs(
                raw_body.decode("utf-8"),
                keep_blank_values=True,
                max_num_fields=20,
            )
        except (UnicodeDecodeError, ValueError):
            self.send_error(400, "Invalid form data")
            return

        name = form_data.get("name", [""])[0].strip()
        email = form_data.get("email", [""])[0].strip()

        # Remove control characters and enforce reasonable limits.
        name = re.sub(r"[\r\n\t]", " ", name).strip()

        if not name or not email:
            self.send_error(
                400, "Both name and email are required"
            )
            return

        if len(name) > 200 or len(email) > 254:
            self.send_error(400, "Input is too long")
            return

        # Basic email validation; the email provider performs
        # additional delivery-related checks.
        if not re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+", email
        ):
            self.send_error(400, "Invalid email address")
            return

        try:
            signup_id = save_signup(name, email)
        except UniqueViolation:
            self._send_message(
                409,
                "You're already signed up with this email address.",
            )
            return
        except Exception:
            logger.exception("Could not save signup")
            self.send_error(500, "Could not save your signup")
            return

        # The signup is committed before attempting to send email.
        try:
            send_welcome_email(name, email)
        except Exception:
            logger.exception(
                "Welcome email failed for signup ID %s",
                signup_id,
            )

            try:
                update_email_status(signup_id, "failed")
            except Exception:
                logger.exception(
                    "Could not update email status for signup ID %s",
                    signup_id,
                )

            self._send_message(
                503,
                "Your signup was saved, but we couldn't send "
                "your welcome email. Please contact us if it "
                "doesn't arrive.",
            )
            return

        try:
            update_email_status(signup_id, "sent")
        except Exception:
            # The provider may already have accepted the email.
            # Don't claim that the signup failed.
            logger.exception(
                "Email sent, but status update failed for signup ID %s",
                signup_id,
            )

        self._send_message(
            200,
            "Thanks for signing up! Check your inbox for "
            "your welcome email.",
        )

    def _send_message(self, status, message):
        safe_message = html.escape(message)
        body = (
            "<!doctype html><html lang='en'>"
            "<meta charset='utf-8'>"
            "<title>Signup result</title><body>"
            f"<p>{safe_message}</p>"
            "<p><a href='/'>Back to the signup page</a></p>"
            "</body></html>"
        ).encode("utf-8")

        self._send_body(
            status, "text/html; charset=utf-8", body
        )

    def _send_body(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    check_configuration()
    initialize_database()

    port = int(os.environ.get("PORT", "10000"))
    server = ThreadingHTTPServer(
        ("0.0.0.0", port),
        JoinPageHandler,
    )

    logger.info("Listening on port %s", port)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server stopped")
    finally:
        server.server_close()
