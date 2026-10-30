import os
import re
import html
import base64
import secrets
import logging

from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import psycopg
import resend


# CONFIGURATION

BASE_DIR = Path(__file__).resolve().parent

DATABASE_URL = os.environ.get("DATABASE_URL")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
EMAIL_FROM = os.environ.get("EMAIL_FROM")
SITE_NAME = os.environ.get("SITE_NAME", "FFCF Jr. Club")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")

PORT = int(os.environ.get("PORT", "10000"))

MAX_FORM_SIZE = 10000

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ffcf")
# DATABASE CONNECTION

def get_connection():

    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing.")

    return psycopg.connect(
        DATABASE_URL,
        connect_timeout=10
    )


# CREATE TABLES

def init_database():

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                CREATE TABLE IF NOT EXISTS members (
                    id BIGSERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL UNIQUE,
                    created_at TIMESTAMPTZ
                        NOT NULL DEFAULT NOW()
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS member_emails (
                    id BIGSERIAL PRIMARY KEY,
                    member_id BIGINT NOT NULL
                        REFERENCES members(id)
                        ON DELETE CASCADE,
                    email TEXT NOT NULL,
                    normalized_email TEXT NOT NULL,
                    created_at TIMESTAMPTZ
                        NOT NULL DEFAULT NOW(),
                    UNIQUE(member_id, normalized_email)
                );
            """)

    logger.info("Database initialized successfully.")
    # REGISTER MEMBER

def register_member(name, email):

    normalized_name = " ".join(
        name.casefold().split()
    )

    normalized_email = email.strip().casefold()

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                INSERT INTO members (
                    name,
                    normalized_name
                )

                VALUES (%s, %s)

                ON CONFLICT (normalized_name)
                DO UPDATE SET
                    normalized_name = EXCLUDED.normalized_name

                RETURNING id;
            """, (name, normalized_name))

            member_id = cur.fetchone()[0]

            cur.execute("""
                INSERT INTO member_emails (
                    member_id,
                    email,
                    normalized_email
                )

                VALUES (%s, %s, %s)

                ON CONFLICT (
                    member_id,
                    normalized_email
                )
                DO NOTHING

                RETURNING id;
            """, (
                member_id,
                email,
                normalized_email
            ))

            email_added = cur.fetchone() is not None

    return member_id, email_added
    # SEND WELCOME EMAIL

def send_welcome_email(email, name):

    if not RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY is missing.")

    if not EMAIL_FROM:
        raise RuntimeError("EMAIL_FROM is missing.")

    resend.api_key = RESEND_API_KEY

    safe_name = html.escape(name)
    safe_site = html.escape(SITE_NAME)

    response = resend.Emails.send({

        "from": EMAIL_FROM,

        "to": [email],

        "subject": f"Welcome to {SITE_NAME}!",

        "html": f"""
        <!DOCTYPE html>
        <html lang="en">

        <body style="
            margin: 0;
            padding: 30px;
            background: #f4f5ef;
            font-family: Arial, sans-serif;
            color: #18352e;
        ">

            <div style="
                max-width: 550px;
                margin: auto;
                padding: 30px;
                background: white;
                border-top: 5px solid #176b58;
            ">

                <h1 style="color: #176b58;">
                    Welcome to {safe_site}!
                </h1>

                <h2>Hello {safe_name}!</h2>

                <p>
                    Thank you for joining FFCF Jr. Club.
                </p>

                <p>
                    We are excited to have you on the team.
                </p>

                <p>
                    We look forward to sharing opportunities
                    to support your community.
                </p>

                <p>Welcome aboard!</p>

                <hr style="border: 1px solid #d3a23b;">

                <p style="color: #5e7069;">
                    Friends for a Cause Foundation<br>
                    Jr. Club
                </p>

            </div>

        </body>
        </html>
        """
    })

    logger.info("Email accepted by Resend for %s", email)

    return response
    # ADMIN AUTHENTICATION

def admin_authenticated(handler):

    if not ADMIN_PASSWORD:
        handler.send_error(
            503,
            "Admin password is not configured."
        )
        return False

    authorization = handler.headers.get(
        "Authorization", ""
    )

    try:

        scheme, encoded = authorization.split(" ", 1)

        if scheme.lower() != "basic":
            raise ValueError("Invalid scheme")

        decoded = base64.b64decode(
            encoded,
            validate=True
        ).decode("utf-8")

        username, password = decoded.split(":", 1)

        valid = (
            username == "admin"
            and secrets.compare_digest(
                password,
                ADMIN_PASSWORD
            )
        )

    except (ValueError, UnicodeDecodeError):

        valid = False

    if not valid:

        handler.send_response(401)

        handler.send_header(
            "WWW-Authenticate",
            'Basic realm="FFCF Admin"'
        )

        handler.send_header("Cache-Control", "no-store")

        handler.end_headers()

        return False

    return True


# GET MEMBERS FROM DATABASE

def get_members(search=""):

    search_pattern = f"%{search}%"

    with get_connection() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                SELECT
                    m.id,
                    m.name,
                    m.created_at,
                    COALESCE(
                        array_agg(me.email)
                        FILTER (WHERE me.email IS NOT NULL),
                        ARRAY[]::text[]
                    )

                FROM members m

                LEFT JOIN member_emails me
                    ON me.member_id = m.id

                WHERE
                    m.name ILIKE %s

                    OR EXISTS (
                        SELECT 1
                        FROM member_emails e
                        WHERE
                            e.member_id = m.id
                            AND e.email ILIKE %s
                    )

                GROUP BY m.id

                ORDER BY m.created_at DESC

                LIMIT 500;
            """, (
                search_pattern,
                search_pattern
            ))

            return cur.fetchall()
            # BUILD ADMIN PAGE

def build_admin_page(search=""):

    members = get_members(search)

    rows = ""

    for member_id, name, created_at, emails in members:

        safe_name = html.escape(name)

        email_items = "".join(
            f"<li>{html.escape(email)}</li>"
            for email in emails
        )

        joined = created_at.strftime(
            "%Y-%m-%d %H:%M UTC"
        )

        rows += f"""
            <tr>
                <td>{member_id}</td>
                <td>{safe_name}</td>
                <td><ul>{email_items}</ul></td>
                <td>{joined}</td>
            </tr>
        """

    if not rows:

        rows = """
            <tr>
                <td colspan="4">No members found.</td>
            </tr>
        """

    safe_search = html.escape(search, quote=True)

    return f"""
    <!DOCTYPE html>
    <html lang="en">

    <head>
        <meta charset="UTF-8">
        <meta name="viewport"
              content="width=device-width, initial-scale=1">

        <title>FFCF Jr. Club Admin</title>

        <style>

            * {{
                box-sizing: border-box;
            }}

            body {{
                margin: 0;
                padding: 30px;
                background: #f4f5ef;
                color: #18352e;
                font-family: Arial, sans-serif;
            }}

            main {{
                max-width: 1100px;
                margin: auto;
            }}

            h1 {{
                color: #176b58;
            }}

            form {{
                display: flex;
                gap: 10px;
                margin: 25px 0;
            }}

            input {{
                flex: 1;
                min-width: 0;
                padding: 13px;
                border: 1px solid #bac8c0;
                border-radius: 5px;
                font-size: 15px;
            }}

            button {{
                padding: 13px 22px;
                border: 0;
                border-radius: 5px;
                background: #176b58;
                color: white;
                font-weight: bold;
                cursor: pointer;
            }}

            .table-wrap {{
                overflow-x: auto;
                background: white;
                border-top: 4px solid #176b58;
            }}

            table {{
                width: 100%;
                border-collapse: collapse;
                text-align: left;
            }}

            th, td {{
                padding: 15px;
                border-bottom: 1px solid #d9dfd8;
            }}

            th {{
                background: #eef1e9;
            }}

            ul {{
                margin: 0;
                padding-left: 18px;
            }}

            @media(max-width: 600px) {{
                body {{
                    padding: 15px;
                }}
            }}

        </style>

    </head>

    <body>

        <main>

            <h1>FFCF Jr. Club Members</h1>

            <p>People who have submitted the join form.</p>

            <form action="/admin" method="GET">

                <input
                    type="search"
                    name="q"
                    placeholder="Search by name or email..."
                    value="{safe_search}"
                >

                <button type="submit">Search</button>

            </form>

            <p>Showing {len(members)} member(s).</p>

            <div class="table-wrap">

                <table>

                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Name</th>
                            <th>Email addresses</th>
                            <th>Joined</th>
                        </tr>
                    </thead>

                    <tbody>
                        {rows}
                    </tbody>

                </table>

            </div>

        </main>

    </body>

    </html>
    """
    # WEBSITE HANDLER

class JoinPageHandler(SimpleHTTPRequestHandler):

    def __init__(self, *args, **kwargs):

        super().__init__(
            *args,
            directory=str(BASE_DIR),
            **kwargs
        )

    # GET REQUESTS

    def do_GET(self):

        route = urlsplit(self.path)

        if route.path == "/healthz":

            self._send_body(
                200,
                "text/plain; charset=utf-8",
                b"ok"
            )

            return

        if route.path == "/admin":

            if not admin_authenticated(self):
                return

            try:

                params = parse_qs(route.query)

                search = params.get("q", [""])[0][:100]

                page = build_admin_page(search)

                self._send_body(
                    200,
                    "text/html; charset=utf-8",
                    page.encode("utf-8")
                )

            except Exception:

                logger.exception("Admin page failed")

                self.send_error(
                    500,
                    "Could not load members."
                )

            return

        if route.path not in ("/", "/index.html"):

            self.send_error(404)
            return

        try:

            page = (BASE_DIR / "index.html").read_bytes()

        except FileNotFoundError:

            self.send_error(404)
            return

        self._send_body(
            200,
            "text/html; charset=utf-8",
            page
        )

    # POST REQUESTS

    def do_POST(self):

        if urlsplit(self.path).path != "/download":

            self.send_error(404)
            return

        try:

            content_length = int(
                self.headers.get("Content-Length", "0")
            )

        except ValueError:

            self.send_error(400)
            return

        if content_length <= 0 or content_length > MAX_FORM_SIZE:

            self.send_error(
                413,
                "Invalid form size."
            )

            return

        form_data = parse_qs(
            self.rfile.read(content_length).decode(
                "utf-8",
                errors="replace"
            ),
            keep_blank_values=True
        )

        name = (
            form_data.get("name", [""])[0]
            .strip()
            .replace("\r", " ")
            .replace("\n", " ")
        )

        email = (
            form_data.get("email", [""])[0]
            .strip()
            .replace("\r", "")
            .replace("\n", "")
        )

        if not name or not email:

            self.send_error(
                400,
                "Both name and email are required."
            )

            return

        if len(name) > 100 or len(email) > 254:

            self.send_error(
                400,
                "Name or email is too long."
            )

            return

        if not re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+",
            email
        ):

            self.send_error(
                400,
                "Invalid email address."
            )

            return

        # SAVE TO POSTGRESQL

        try:

            member_id, email_added = register_member(
                name,
                email
            )

            logger.info(
                "Member saved: %s",
                member_id
            )

        except Exception:

            logger.exception(
                "Database registration failed"
            )

            self.send_error(
                500,
                "Registration could not be saved."
            )

            return

        # SEND WELCOME EMAIL

        if email_added:

            try:

                send_welcome_email(email, name)

            except Exception:

                logger.exception(
                    "Welcome email failed for %s",
                    email
                )

        # CONFIRMATION

        confirmation = """
        <!DOCTYPE html>
        <html lang="en">

        <head>
            <meta charset="utf-8">
            <meta name="viewport"
                  content="width=device-width, initial-scale=1">

            <title>Welcome to FFCF Jr. Club!</title>
        </head>

        <body style="
            font-family:Arial,sans-serif;
            background:#f4f5ef;
            color:#18352e;
            padding:40px;
        ">

            <h1>Thank you for joining!</h1>

            <p>Your information has been successfully saved.</p>

            <p>We're excited to have you on the team!</p>

            <a href="/">Back to the join form</a>

        </body>

        </html>
        """

        self._send_body(
            200,
            "text/html; charset=utf-8",
            confirmation.encode("utf-8")
        )

    # SEND RESPONSE

    def _send_body(self, status, content_type, body):

        self.send_response(status)

        self.send_header(
            "Content-Type",
            content_type
        )

        self.send_header(
            "Content-Length",
            str(len(body))
        )

        self.send_header(
            "Cache-Control",
            "no-store"
        )

        self.end_headers()

        self.wfile.write(body)


# START SERVER

if __name__ == "__main__":

    init_database()

    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        JoinPageHandler
    )

    logger.info(
        "%s running on port %s",
        SITE_NAME,
        PORT
    )

    try:

        server.serve_forever()

    except KeyboardInterrupt:

        logger.info("Server stopped.")

    finally:

        server.server_close()
