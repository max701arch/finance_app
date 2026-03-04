import csv
import hmac
import io
import os
import secrets
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from functools import wraps

from flask import Flask, Response, abort, g, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from database import get_connection, init_db

# ----------------------------
# App config
# ----------------------------
app = Flask(__name__)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


_secret_key = os.getenv("FLASK_SECRET_KEY")
if not _secret_key:
    # Dev fallback: random per process. In production set FLASK_SECRET_KEY.
    _secret_key = secrets.token_hex(32)

app.config.update(
    SECRET_KEY=_secret_key,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=env_bool("FLASK_SECURE_COOKIES", default=env_bool("FLASK_USE_HTTPS", False)),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    MAX_CONTENT_LENGTH=1024 * 1024,
)

if env_bool("TRUST_PROXY", default=False):
    # Required on platforms behind reverse proxy/load balancer (Render/Railway/Nginx).
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

init_db()

# Simple in-memory throttle for auth endpoints.
AUTH_ATTEMPTS = {}
AUTH_WINDOW_SECONDS = 10 * 60
AUTH_MAX_ATTEMPTS = 8


# ----------------------------
# Validation and filter helpers
# ----------------------------
def normalize_month(month_value: str | None) -> str | None:
    if not month_value:
        return None
    try:
        parsed = date.fromisoformat(f"{month_value}-01")
    except ValueError:
        return None
    return parsed.strftime("%Y-%m")


def normalize_date(date_value: str | None) -> str | None:
    if not date_value:
        return None
    try:
        return date.fromisoformat(date_value).isoformat()
    except ValueError:
        return None


def parse_filters(args):
    month = normalize_month(args.get("month"))

    search = (args.get("q") or "").strip()
    if len(search) > 120:
        search = search[:120]

    tx_type = (args.get("tx_type") or "").strip().lower()
    if tx_type not in {"income", "expense"}:
        tx_type = ""

    category = (args.get("category") or "").strip()
    if len(category) > 80:
        category = category[:80]

    date_from = normalize_date(args.get("from_date"))
    date_to = normalize_date(args.get("to_date"))

    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from

    return {
        "month": month,
        "search": search,
        "tx_type": tx_type,
        "category": category,
        "from_date": date_from,
        "to_date": date_to,
    }


def build_where_clause(filters, user_id):
    conditions = ["user_id = ?"]
    params = [user_id]

    if filters["month"]:
        conditions.append("date LIKE ?")
        params.append(f"{filters['month']}%")

    if filters["tx_type"]:
        conditions.append("type = ?")
        params.append(filters["tx_type"])

    if filters["category"]:
        conditions.append("LOWER(category) = LOWER(?)")
        params.append(filters["category"])

    if filters["from_date"]:
        conditions.append("date >= ?")
        params.append(filters["from_date"])

    if filters["to_date"]:
        conditions.append("date <= ?")
        params.append(filters["to_date"])

    if filters["search"]:
        search_lower = f"%{filters['search'].lower()}%"
        search_raw = f"%{filters['search']}%"
        conditions.append(
            "(LOWER(category) LIKE ? OR LOWER(type) LIKE ? OR date LIKE ? OR CAST(amount AS TEXT) LIKE ?)"
        )
        params.extend([search_lower, search_lower, search_raw, search_raw])

    return " WHERE " + " AND ".join(conditions), params


def build_active_filters(filters):
    tags = []
    if filters["search"]:
        tags.append(f"Search: {filters['search']}")
    if filters["month"]:
        tags.append(f"Month: {filters['month']}")
    if filters["tx_type"]:
        tags.append(f"Type: {filters['tx_type']}")
    if filters["category"]:
        tags.append(f"Category: {filters['category']}")
    if filters["from_date"]:
        tags.append(f"From: {filters['from_date']}")
    if filters["to_date"]:
        tags.append(f"To: {filters['to_date']}")
    return tags


def build_filter_kwargs(filters):
    return {
        "month": filters["month"] or None,
        "q": filters["search"] or None,
        "tx_type": filters["tx_type"] or None,
        "category": filters["category"] or None,
        "from_date": filters["from_date"] or None,
        "to_date": filters["to_date"] or None,
    }


def build_filter_url(fmt: str | None, filters):
    endpoint = "index" if fmt is None else "export_transactions"
    kwargs = build_filter_kwargs(filters)
    if fmt is None:
        return url_for(endpoint, **kwargs)
    return url_for(endpoint, fmt=fmt, **kwargs)


# ----------------------------
# Security helpers
# ----------------------------
def client_ip():
    return request.remote_addr or "unknown"


def auth_throttle_key(scope: str, username: str = ""):
    return f"{scope}:{client_ip()}:{username.lower()}"


def _prune_attempts(key):
    cutoff = time.time() - AUTH_WINDOW_SECONDS
    attempts = AUTH_ATTEMPTS.get(key, [])
    attempts = [ts for ts in attempts if ts >= cutoff]
    AUTH_ATTEMPTS[key] = attempts
    return attempts


def is_auth_limited(key):
    attempts = _prune_attempts(key)
    return len(attempts) >= AUTH_MAX_ATTEMPTS


def record_auth_failure(key):
    attempts = _prune_attempts(key)
    attempts.append(time.time())
    AUTH_ATTEMPTS[key] = attempts


def clear_auth_failures(key):
    AUTH_ATTEMPTS.pop(key, None)


def generate_csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def validate_csrf_token():
    session_token = session.get("_csrf_token")
    request_token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")

    if not session_token or not request_token:
        return False

    return hmac.compare_digest(session_token, request_token)


def get_redirect_target(default_endpoint="index"):
    next_url = (request.args.get("next") or request.form.get("next") or "").strip()
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return url_for(default_endpoint)


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if g.user is None:
            next_path = request.path
            if request.query_string:
                next_path += "?" + request.query_string.decode("utf-8", errors="ignore")
            return redirect(url_for("login", next=next_path))
        return view_func(*args, **kwargs)

    return wrapped


@app.before_request
def load_logged_in_user():
    user_id = session.get("user_id")
    if not user_id:
        g.user = None
        return

    user = get_user_by_id(user_id)
    if user is None:
        session.clear()
        g.user = None
        return

    g.user = {"id": user[0], "username": user[1]}


@app.before_request
def enforce_csrf():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if request.endpoint == "static":
            return
        if not validate_csrf_token():
            abort(400, description="Invalid CSRF token")


@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers[
        "Content-Security-Policy"
    ] = (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "script-src 'self'; "
        "style-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'; "
        "form-action 'self'"
    )
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.context_processor
def inject_template_globals():
    return {"current_user": g.get("user"), "csrf_token": generate_csrf_token}


# ----------------------------
# Auth helpers
# ----------------------------
def get_user_by_username(username):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, username, password_hash FROM users WHERE LOWER(username)=LOWER(?)",
            (username,),
        )
        return cursor.fetchone()


def get_user_by_id(user_id):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, password_hash FROM users WHERE id=?", (user_id,))
        return cursor.fetchone()


# ----------------------------
# Transaction data helpers
# ----------------------------
def fetch_transactions(user_id, filters):
    query = "SELECT id, date, type, category, amount FROM transactions"
    where_sql, params = build_where_clause(filters, user_id)
    query += where_sql + " ORDER BY date DESC, id DESC"

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchall()


def fetch_overall_summary(user_id):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN type='income' THEN amount END), 0),
                COALESCE(SUM(CASE WHEN type='expense' THEN amount END), 0)
            FROM transactions
            WHERE user_id = ?
            """,
            (user_id,),
        )
        income, expense = cursor.fetchone()
    return round(income, 2), round(expense, 2), round(income - expense, 2)


def fetch_category_options(user_id):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT category FROM transactions WHERE user_id=? ORDER BY LOWER(category), category",
            (user_id,),
        )
        rows = cursor.fetchall()
    return [row[0] for row in rows]


def build_view_summary(transactions):
    income = round(sum(t[4] for t in transactions if t[2] == "income"), 2)
    expense = round(sum(t[4] for t in transactions if t[2] == "expense"), 2)
    return {
        "income": income,
        "expense": expense,
        "balance": round(income - expense, 2),
    }


def build_monthly_chart_data(transactions):
    monthly = defaultdict(lambda: {"income": 0.0, "expense": 0.0})

    for _, tx_date, tx_type, _, amount in transactions:
        month = tx_date[:7]
        monthly[month][tx_type] += amount

    months = sorted(monthly.keys())
    income_data = [round(monthly[m]["income"], 2) for m in months]
    expense_data = [round(monthly[m]["expense"], 2) for m in months]
    return months, income_data, expense_data


def build_category_chart_data(transactions):
    category_totals = defaultdict(float)
    for _, _, tx_type, category, amount in transactions:
        if tx_type == "expense":
            category_totals[category] += amount

    sorted_items = sorted(category_totals.items(), key=lambda x: x[1], reverse=True)
    categories = [item[0] for item in sorted_items]
    category_data = [round(item[1], 2) for item in sorted_items]
    return categories, category_data


# ----------------------------
# Export helpers
# ----------------------------
def csv_response(transactions, filters, summary):
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["Date", "Type", "Category", "Amount"])
    for _, tx_date, tx_type, category, amount in transactions:
        writer.writerow([tx_date, tx_type, category, f"{amount:.2f}"])

    writer.writerow([])
    writer.writerow(["Filtered income", f"{summary['income']:.2f}"])
    writer.writerow(["Filtered expense", f"{summary['expense']:.2f}"])
    writer.writerow(["Filtered balance", f"{summary['balance']:.2f}"])

    active_filters = build_active_filters(filters)
    writer.writerow([])
    writer.writerow(
        ["Applied filters", " | ".join(active_filters) if active_filters else "None"]
    )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"transactions_{stamp}.csv"
    data = "\ufeff" + output.getvalue()

    return Response(
        data,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def pdf_escape(text):
    return str(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def wrap_line(text, max_len=92):
    text = str(text)
    if len(text) <= max_len:
        return [text]

    words = text.split()
    if not words:
        return [""]

    lines = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if len(candidate) <= max_len:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def build_pdf_stream(lines):
    commands = ["BT", "/F1 10 Tf", "14 TL", "40 770 Td"]
    for line in lines:
        commands.append(f"({pdf_escape(line)}) Tj")
        commands.append("T*")
    commands.append("ET")
    return "\n".join(commands).encode("latin-1", "replace")


def render_pdf_document(lines):
    normalized = []
    for line in lines:
        if line == "":
            normalized.append("")
        else:
            normalized.extend(wrap_line(line))

    if not normalized:
        normalized = ["No data"]

    lines_per_page = 45
    pages = [
        normalized[i : i + lines_per_page]
        for i in range(0, len(normalized), lines_per_page)
    ]

    page_count = max(1, len(pages))
    font_obj = 3 + page_count * 2
    total_objects = font_obj
    objects = {}

    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"

    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(page_count))
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode("ascii")

    for i in range(page_count):
        page_id = 3 + i * 2
        content_id = 4 + i * 2

        stream = build_pdf_stream(pages[i])
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_obj} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        ).encode("ascii")
        objects[content_id] = (
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )

    objects[font_obj] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0] * (total_objects + 1)

    for obj_id in range(1, total_objects + 1):
        offsets[obj_id] = len(pdf)
        pdf.extend(f"{obj_id} 0 obj\n".encode("ascii"))
        pdf.extend(objects[obj_id])
        pdf.extend(b"\nendobj\n")

    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {total_objects + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for obj_id in range(1, total_objects + 1):
        pdf.extend(f"{offsets[obj_id]:010d} 00000 n \n".encode("ascii"))

    pdf.extend(
        (
            f"trailer\n<< /Size {total_objects + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF"
        ).encode("ascii")
    )

    return bytes(pdf)


def pdf_response(transactions, filters, summary):
    lines = [
        "Finance Tracker Report",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "Applied filters:",
    ]

    active_filters = build_active_filters(filters)
    if active_filters:
        for item in active_filters:
            lines.append(f"- {item}")
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            f"Filtered income:  {summary['income']:.2f}",
            f"Filtered expense: {summary['expense']:.2f}",
            f"Filtered balance: {summary['balance']:.2f}",
            "",
            "Date       | Type    | Category                     | Amount",
            "--------------------------------------------------------------",
        ]
    )

    if transactions:
        for _, tx_date, tx_type, category, amount in transactions:
            line = f"{tx_date} | {tx_type:<7} | {category[:28]:<28} | {amount:>10.2f}"
            lines.append(line)
    else:
        lines.append("No transactions for current filter.")

    pdf_bytes = render_pdf_document(lines)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"transactions_{stamp}.pdf"

    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ----------------------------
# Auth routes
# ----------------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user is not None:
        return redirect(url_for("index"))

    error = ""
    username = ""

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        password2 = request.form.get("password2") or ""

        throttle_key = auth_throttle_key("register")
        if is_auth_limited(throttle_key):
            error = "Juda ko'p urinish bo'ldi. Bir ozdan keyin qayta urinib ko'ring."
        elif len(username) < 3 or len(username) > 30:
            error = "Username 3-30 ta belgidan iborat bo'lishi kerak."
        elif len(password) < 8:
            error = "Parol kamida 8 ta belgidan iborat bo'lishi kerak."
        elif password != password2:
            error = "Parollar bir xil emas."
        elif get_user_by_username(username) is not None:
            error = "Bu username allaqachon band."
        else:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                    (
                        username,
                        generate_password_hash(password),
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                )
                conn.commit()
                user_id = cursor.lastrowid

            clear_auth_failures(throttle_key)
            session.clear()
            session["user_id"] = user_id
            session["_csrf_token"] = secrets.token_urlsafe(32)
            session.permanent = True
            return redirect(get_redirect_target())

        if error:
            record_auth_failure(throttle_key)

    return render_template(
        "register.html",
        error=error,
        username=username,
        next_url=(request.form.get("next") or request.args.get("next") or ""),
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(url_for("index"))

    error = ""
    username = ""

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        throttle_key = auth_throttle_key("login", username)
        if is_auth_limited(throttle_key):
            error = "Juda ko'p urinish bo'ldi. Bir ozdan keyin qayta urinib ko'ring."
        else:
            user = get_user_by_username(username)
            if user is None or not check_password_hash(user[2], password):
                record_auth_failure(throttle_key)
                error = "Username yoki parol xato."
            else:
                clear_auth_failures(throttle_key)
                session.clear()
                session["user_id"] = user[0]
                session["_csrf_token"] = secrets.token_urlsafe(32)
                session.permanent = True
                return redirect(get_redirect_target())

    return render_template(
        "login.html",
        error=error,
        username=username,
        next_url=(request.form.get("next") or request.args.get("next") or ""),
    )


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


# ----------------------------
# Finance routes
# ----------------------------
@app.route("/health")
def health():
    return {"status": "ok"}, 200


@app.route("/")
@login_required
def index():
    user_id = g.user["id"]
    filters = parse_filters(request.args)
    transactions = fetch_transactions(user_id, filters)
    view_summary = build_view_summary(transactions)

    total_income, total_expense, total_balance = fetch_overall_summary(user_id)
    months, income_data, expense_data = build_monthly_chart_data(transactions)
    categories, category_data = build_category_chart_data(transactions)

    active_filters = build_active_filters(filters)

    return render_template(
        "index.html",
        transactions=transactions,
        filters=filters,
        active_filters=active_filters,
        filters_active=bool(active_filters),
        clear_filters_url=url_for("index"),
        csv_export_url=build_filter_url("csv", filters),
        pdf_export_url=build_filter_url("pdf", filters),
        category_options=fetch_category_options(user_id),
        today=date.today().isoformat(),
        view_income=view_summary["income"],
        view_expense=view_summary["expense"],
        view_balance=view_summary["balance"],
        total_income=total_income,
        total_expense=total_expense,
        total_balance=total_balance,
        months=months,
        income_data=income_data,
        expense_data=expense_data,
        categories=categories,
        category_data=category_data,
    )


@app.route("/export/<fmt>")
@login_required
def export_transactions(fmt):
    fmt = (fmt or "").strip().lower()
    if fmt not in {"csv", "pdf"}:
        return redirect(url_for("index"))

    filters = parse_filters(request.args)
    transactions = fetch_transactions(g.user["id"], filters)
    summary = build_view_summary(transactions)

    if fmt == "csv":
        return csv_response(transactions, filters, summary)
    return pdf_response(transactions, filters, summary)


@app.route("/history/delete/<int:tx_id>", methods=["POST"])
@login_required
def delete_transaction(tx_id):
    filters = parse_filters(request.form)

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM transactions WHERE id = ? AND user_id = ?",
            (tx_id, g.user["id"]),
        )
        conn.commit()

    return redirect(build_filter_url(None, filters))


@app.route("/history/clear", methods=["POST"])
@login_required
def clear_history():
    scope = (request.form.get("scope") or "filtered").strip().lower()
    filters = parse_filters(request.form)

    if scope not in {"filtered", "all"}:
        return redirect(build_filter_url(None, filters))

    with get_connection() as conn:
        cursor = conn.cursor()

        if scope == "all":
            cursor.execute(
                "DELETE FROM transactions WHERE user_id = ?",
                (g.user["id"],),
            )
            conn.commit()
            return redirect(url_for("index"))

        # "Filtered" clear requires at least one explicit filter.
        if not build_active_filters(filters):
            return redirect(url_for("index"))

        where_sql, params = build_where_clause(filters, g.user["id"])
        cursor.execute("DELETE FROM transactions" + where_sql, params)
        conn.commit()

    return redirect(build_filter_url(None, filters))


@app.route("/add", methods=["POST"])
@login_required
def add_transaction():
    tx_type = request.form.get("type", "").strip().lower()
    if tx_type not in {"income", "expense"}:
        return redirect(url_for("index"))

    amount_text = request.form.get("amount", "").strip()
    try:
        amount = float(amount_text)
    except ValueError:
        return redirect(url_for("index"))

    if amount <= 0:
        return redirect(url_for("index"))
    amount = round(amount, 2)

    category = request.form.get("category", "").strip() or "Uncategorized"
    if len(category) > 80:
        category = category[:80]

    submitted_date = request.form.get("date", "").strip()
    tx_date = normalize_date(submitted_date) or date.today().isoformat()

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO transactions (user_id, type, amount, category, date) VALUES (?, ?, ?, ?, ?)",
            (g.user["id"], tx_type, amount, category, tx_date),
        )
        conn.commit()

    return redirect(url_for("index", month=tx_date[:7]))


if __name__ == "__main__":
    debug_mode = env_bool("FLASK_DEBUG", default=False)
    host = os.getenv("FLASK_HOST", "127.0.0.1")
    port = int(os.getenv("PORT", os.getenv("FLASK_PORT", "5000")))
    app.run(host=host, port=port, debug=debug_mode)
