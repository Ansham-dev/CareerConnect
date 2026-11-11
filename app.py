import os
import sqlite3
import uuid
from datetime import date
from functools import wraps

from flask import (Flask, flash, g, redirect, render_template, request,
                   send_from_directory, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "careerconnect.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
ALLOWED_RESUME = {".pdf", ".doc", ".docx"}
MAX_RESUME_SIZE = 2 * 1024 * 1024  # 2 MB

app = Flask(__name__)
app.secret_key = "careerconnect-secret-key-change-in-production"
app.config["UPLOAD_FOLDER"] = UPLOAD_DIR

STATUSES = ("Applied", "Shortlisted", "Rejected")


# ---------- Database helpers ----------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query(sql, params=(), one=False):
    cur = get_db().execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    return (rows[0] if rows else None) if one else rows


def execute(sql, params=()):
    db = get_db()
    cur = db.execute(sql, params)
    db.commit()
    last_id = cur.lastrowid
    cur.close()
    return last_id


def init_db():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    db = sqlite3.connect(DATABASE)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('seeker','employer','admin')),
        approved INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS seeker_profile (
        profile_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL UNIQUE REFERENCES users(user_id) ON DELETE CASCADE,
        phone TEXT,
        education TEXT,
        skills TEXT,
        resume_path TEXT
    );
    CREATE TABLE IF NOT EXISTS employer_profile (
        emp_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL UNIQUE REFERENCES users(user_id) ON DELETE CASCADE,
        company_name TEXT NOT NULL,
        website TEXT,
        description TEXT
    );
    CREATE TABLE IF NOT EXISTS categories (
        cat_id INTEGER PRIMARY KEY AUTOINCREMENT,
        cat_name TEXT NOT NULL UNIQUE
    );
    CREATE TABLE IF NOT EXISTS jobs (
        job_id INTEGER PRIMARY KEY AUTOINCREMENT,
        emp_id INTEGER NOT NULL REFERENCES employer_profile(emp_id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        location TEXT,
        salary TEXT,
        category TEXT,
        last_date TEXT,
        posted_on TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS applications (
        app_id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        applied_on TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Applied',
        UNIQUE(job_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS contact_messages (
        msg_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        message TEXT NOT NULL,
        sent_on TEXT NOT NULL
    );
    """)
    # Seed data
    admin = db.execute("SELECT 1 FROM users WHERE role='admin'").fetchone()
    if not admin:
        db.execute(
            "INSERT INTO users (name, email, password_hash, role, approved) VALUES (?,?,?,?,1)",
            ("Admin", "admin@careerconnect.com",
             generate_password_hash("admin123"), "admin"))
    cats = db.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    if not cats:
        db.executemany("INSERT INTO categories (cat_name) VALUES (?)",
                       [("IT / Software",), ("Sales",), ("Marketing",),
                        ("Finance",), ("HR",), ("Education",)])
    demo = db.execute("SELECT 1 FROM users WHERE email='hr@techcorp.com'").fetchone()
    if not demo:
        cur = db.execute(
            "INSERT INTO users (name, email, password_hash, role, approved) VALUES (?,?,?,?,1)",
            ("TechCorp HR", "hr@techcorp.com",
             generate_password_hash("employer123"), "employer"))
        emp = cur.lastrowid
        db.execute(
            "INSERT INTO employer_profile (user_id, company_name, website, description)"
            " VALUES (?,?,?,?)",
            (emp, "TechCorp Solutions", "https://techcorp.example.com",
             "A leading IT services company delivering web, mobile and cloud solutions."))
        today = date.today().isoformat()
        db.executemany(
            "INSERT INTO jobs (emp_id, title, description, location, salary, category,"
            " last_date, posted_on) VALUES (?,?,?,?,?,?,?,?)",
            [
                (1, "Python Developer",
                 "Build and maintain backend services using Python and Flask. "
                 "2+ years experience required.",
                 "Delhi", "6-8 LPA", "IT / Software", "2026-12-31", today),
                (1, "Frontend Developer",
                 "Work with HTML, CSS and JavaScript to build responsive user "
                 "interfaces for client projects.",
                 "Remote", "5-7 LPA", "IT / Software", "2026-12-15", today),
                (1, "Sales Executive",
                 "Generate leads, meet sales targets and maintain client "
                 "relationships for our product line.",
                 "Mumbai", "4-5 LPA", "Sales", "2026-11-30", today),
            ])
    db.commit()
    db.close()


# ---------- Auth helpers ----------

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return query("SELECT * FROM users WHERE user_id=?", (uid,), one=True)


def login_required(role=None):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                flash("Please log in first.", "error")
                return redirect(url_for("login"))
            if role and user["role"] != role:
                flash("You are not allowed to access that page.", "error")
                return redirect(url_for("home"))
            return f(*args, **kwargs)
        return wrapper
    return decorator


@app.context_processor
def inject_globals():
    return {"current_user": current_user(), "STATUSES": STATUSES}


# ---------- Public pages ----------

@app.route("/")
def home():
    recent = query(
        "SELECT j.*, e.company_name FROM jobs j "
        "JOIN employer_profile e ON j.emp_id = e.emp_id "
        "ORDER BY j.job_id DESC LIMIT 6")
    stats = {
        "jobs": query("SELECT COUNT(*) c FROM jobs", one=True)["c"],
        "companies": query(
            "SELECT COUNT(*) c FROM employer_profile e JOIN users u "
            "ON e.user_id = u.user_id WHERE u.approved=1", one=True)["c"],
        "seekers": query(
            "SELECT COUNT(*) c FROM users WHERE role='seeker'", one=True)["c"],
    }
    return render_template("home.html", recent=recent, stats=stats)


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        message = request.form.get("message", "").strip()
        if not name or not email or not message or "@" not in email:
            flash("Please fill all fields with a valid email.", "error")
        else:
            execute(
                "INSERT INTO contact_messages (name, email, message, sent_on)"
                " VALUES (?,?,?,?)", (name, email, message, date.today().isoformat()))
            flash("Thank you! Your message has been sent.", "success")
            return redirect(url_for("contact"))
    return render_template("contact.html")


# ---------- Auth ----------

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        role = request.form.get("role")
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        company = request.form.get("company", "").strip()

        errors = []
        if role not in ("seeker", "employer"):
            errors.append("Please select a valid role.")
        if not name:
            errors.append("Name is required.")
        if not email or "@" not in email:
            errors.append("A valid email is required.")
        if len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        if password != confirm:
            errors.append("Passwords do not match.")
        if role == "employer" and not company:
            errors.append("Company name is required for employers.")
        if query("SELECT 1 FROM users WHERE email=?", (email,), one=True):
            errors.append("This email is already registered.")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("register.html"), 400

        # Employers need admin approval before they can post jobs.
        execute("INSERT INTO users (name, email, password_hash, role, approved)"
                " VALUES (?,?,?,?,?)",
                (name, email, generate_password_hash(password), role,
                 1 if role == "seeker" else 0))
        user = query("SELECT * FROM users WHERE email=?", (email,), one=True)
        if role == "seeker":
            execute("INSERT INTO seeker_profile (user_id) VALUES (?)", (user["user_id"],))
        else:
            execute("INSERT INTO employer_profile (user_id, company_name) VALUES (?,?)",
                    (user["user_id"], company))
        flash("Registration successful! Please log in.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = query("SELECT * FROM users WHERE email=?", (email,), one=True)
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "error")
            return render_template("login.html"), 401
        session["user_id"] = user["user_id"]
        flash(f"Welcome, {user['name']}!", "success")
        if user["role"] == "seeker":
            return redirect(url_for("seeker_dashboard"))
        if user["role"] == "employer":
            return redirect(url_for("employer_dashboard"))
        return redirect(url_for("admin_panel"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("home"))


# ---------- Jobs (public) ----------

@app.route("/jobs")
def jobs():
    q = request.args.get("q", "").strip()
    location = request.args.get("location", "").strip()
    category = request.args.get("category", "").strip()
    min_salary = request.args.get("min_salary", "").strip()

    sql = ("SELECT j.*, e.company_name FROM jobs j "
           "JOIN employer_profile e ON j.emp_id = e.emp_id WHERE 1=1")
    params = []
    if q:
        sql += " AND (j.title LIKE ? OR j.description LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    if location:
        sql += " AND j.location LIKE ?"
        params.append(f"%{location}%")
    if category:
        sql += " AND j.category = ?"
        params.append(category)
    if min_salary:
        sql += " AND CAST(REPLACE(REPLACE(REPLACE(j.salary, 'LPA', ''), '-', ''), ' ', '') >= ?"
        sql += " AND j.salary LIKE '%LPA%'"
        params.append(min_salary)
    sql += " ORDER BY j.job_id DESC"
    results = query(sql, params)

    applied_ids = set()
    user = current_user()
    if user and user["role"] == "seeker":
        applied_ids = {r["job_id"] for r in query(
            "SELECT job_id FROM applications WHERE user_id=?", (user["user_id"],))}
    return render_template("jobs.html", results=results, categories=query(
        "SELECT * FROM categories ORDER BY cat_name"), applied_ids=applied_ids,
        today=date.today().isoformat())


@app.route("/jobs/<int:job_id>")
def job_detail(job_id):
    job = query(
        "SELECT j.*, e.company_name, e.website, e.description AS company_desc "
        "FROM jobs j JOIN employer_profile e ON j.emp_id = e.emp_id "
        "WHERE j.job_id=?", (job_id,), one=True)
    if not job:
        flash("Job not found.", "error")
        return redirect(url_for("jobs"))
    user = current_user()
    applied = False
    if user and user["role"] == "seeker":
        applied = bool(query(
            "SELECT 1 FROM applications WHERE job_id=? AND user_id=?",
            (job_id, user["user_id"]), one=True))
    return render_template("job_detail.html", job=job, applied=applied,
                           expired=bool(job["last_date"] and job["last_date"] < date.today().isoformat()),
                           today=date.today().isoformat())


@app.route("/jobs/<int:job_id>/apply", methods=["GET", "POST"])
@login_required("seeker")
def apply(job_id):
    job = query(
        "SELECT j.*, e.company_name FROM jobs j "
        "JOIN employer_profile e ON j.emp_id = e.emp_id WHERE j.job_id=?",
        (job_id,), one=True)
    if not job:
        flash("Job not found.", "error")
        return redirect(url_for("jobs"))
    if job["last_date"] and job["last_date"] < date.today().isoformat():
        flash("The application deadline for this job has expired.", "error")
        return redirect(url_for("job_detail", job_id=job_id))
    if query("SELECT 1 FROM applications WHERE job_id=? AND user_id=?",
             (job_id, session["user_id"]), one=True):
        flash("You have already applied to this job.", "error")
        return redirect(url_for("job_detail", job_id=job_id))

    profile = query("SELECT * FROM seeker_profile WHERE user_id=?",
                    (session["user_id"],), one=True)
    if request.method == "POST":
        # Optional fresh resume upload; falls back to profile resume
        file = request.files.get("resume")
        resume_path = profile["resume_path"] if profile else None
        if file and file.filename:
            filename = secure_filename(file.filename)
            ext = os.path.splitext(filename)[1].lower()
            if ext not in ALLOWED_RESUME:
                flash("Resume must be a PDF or Word document.", "error")
                return render_template("apply.html", job=job), 400
            file.stream.seek(0, os.SEEK_END)
            size = file.stream.tell()
            file.stream.seek(0)
            if size > MAX_RESUME_SIZE:
                flash("Resume must be smaller than 2 MB.", "error")
                return render_template("apply.html", job=job), 400
            saved = f"{uuid.uuid4().hex}{ext}"
            file.save(os.path.join(UPLOAD_DIR, saved))
            resume_path = saved
            if profile:
                execute("UPDATE seeker_profile SET resume_path=? WHERE user_id=?",
                        (saved, session["user_id"]))
        if not resume_path:
            flash("Please upload your resume before applying.", "error")
            return render_template("apply.html", job=job), 400
        execute("INSERT INTO applications (job_id, user_id, applied_on, status)"
                " VALUES (?,?,?, 'Applied')",
                (job_id, session["user_id"], date.today().isoformat()))
        flash("Application submitted successfully!", "success")
        return redirect(url_for("seeker_dashboard"))
    return render_template("apply.html", job=job, profile=profile)


# ---------- Seeker ----------

@app.route("/dashboard")
@login_required("seeker")
def seeker_dashboard():
    apps = query(
        "SELECT a.*, j.title, j.location, e.company_name "
        "FROM applications a JOIN jobs j ON a.job_id = j.job_id "
        "JOIN employer_profile e ON j.emp_id = e.emp_id "
        "WHERE a.user_id=? ORDER BY a.app_id DESC", (session["user_id"],))
    profile = query("SELECT * FROM seeker_profile WHERE user_id=?",
                    (session["user_id"],), one=True)
    return render_template("seeker_dashboard.html", apps=apps, profile=profile)


@app.route("/profile", methods=["GET", "POST"])
@login_required("seeker")
def profile():
    if request.method == "POST":
        phone = request.form.get("phone", "").strip()
        education = request.form.get("education", "").strip()
        skills = request.form.get("skills", "").strip()
        if phone and not phone.replace("+", "").replace("-", "").replace(" ", "").isdigit():
            flash("Phone number must contain only digits.", "error")
            return render_template("profile.html"), 400
        file = request.files.get("resume")
        resume_path = None
        if file and file.filename:
            filename = secure_filename(file.filename)
            ext = os.path.splitext(filename)[1].lower()
            if ext not in ALLOWED_RESUME:
                flash("Resume must be a PDF or Word document.", "error")
                return render_template("profile.html"), 400
            file.stream.seek(0, os.SEEK_END)
            if file.stream.tell() > MAX_RESUME_SIZE:
                flash("Resume must be smaller than 2 MB.", "error")
                return render_template("profile.html"), 400
            file.stream.seek(0)
            resume_path = f"{uuid.uuid4().hex}{ext}"
            file.save(os.path.join(UPLOAD_DIR, resume_path))
        if not query("SELECT 1 FROM seeker_profile WHERE user_id=?",
                     (session["user_id"],), one=True):
            execute("INSERT INTO seeker_profile (user_id) VALUES (?)", (session["user_id"],))
        if resume_path:
            execute("UPDATE seeker_profile SET phone=?, education=?, skills=?,"
                    " resume_path=? WHERE user_id=?",
                    (phone, education, skills, resume_path, session["user_id"]))
        else:
            execute("UPDATE seeker_profile SET phone=?, education=?, skills=?"
                    " WHERE user_id=?",
                    (phone, education, skills, session["user_id"]))
        flash("Profile updated successfully.", "success")
        return redirect(url_for("profile"))
    p = query("SELECT * FROM seeker_profile WHERE user_id=?",
              (session["user_id"],), one=True)
    return render_template("profile.html", profile=p)


# ---------- Employer ----------

def employer_of(job_id):
    return query(
        "SELECT j.* FROM jobs j JOIN employer_profile e ON j.emp_id=e.emp_id "
        "WHERE j.job_id=? AND e.user_id=?", (job_id, session["user_id"]), one=True)


@app.route("/employer")
@login_required("employer")
def employer_dashboard():
    emp = query("SELECT * FROM employer_profile WHERE user_id=?",
                (session["user_id"],), one=True)
    approved = query("SELECT approved FROM users WHERE user_id=?",
                     (session["user_id"],), one=True)["approved"]
    jobs = query(
        "SELECT j.*, (SELECT COUNT(*) FROM applications a WHERE a.job_id=j.job_id)"
        " AS applicant_count FROM jobs j WHERE j.emp_id=? ORDER BY j.job_id DESC",
        (emp["emp_id"],))
    return render_template("employer_dashboard.html", emp=emp, jobs=jobs,
                           approved=approved)


@app.route("/employer/profile", methods=["GET", "POST"])
@login_required("employer")
def employer_profile_edit():
    if request.method == "POST":
        company = request.form.get("company_name", "").strip()
        website = request.form.get("website", "").strip()
        desc = request.form.get("description", "").strip()
        if not company:
            flash("Company name is required.", "error")
            return render_template("employer_profile.html"), 400
        execute("UPDATE employer_profile SET company_name=?, website=?,"
                " description=? WHERE user_id=?",
                (company, website, desc, session["user_id"]))
        flash("Company profile updated.", "success")
        return redirect(url_for("employer_dashboard"))
    emp = query("SELECT * FROM employer_profile WHERE user_id=?",
                (session["user_id"],), one=True)
    return render_template("employer_profile.html", emp=emp)


def valid_job_form():
    title = request.form.get("title", "").strip()
    desc = request.form.get("description", "").strip()
    location = request.form.get("location", "").strip()
    salary = request.form.get("salary", "").strip()
    category = request.form.get("category", "").strip()
    last_date = request.form.get("last_date", "").strip()
    if not title or not desc:
        return None, "Title and description are required."
    if last_date and last_date < date.today().isoformat():
        return None, "Deadline cannot be in the past."
    return {"title": title, "description": desc, "location": location,
            "salary": salary, "category": category, "last_date": last_date}, None


@app.route("/employer/jobs/new", methods=["GET", "POST"])
@login_required("employer")
def post_job():
    approved = query("SELECT approved FROM users WHERE user_id=?",
                     (session["user_id"],), one=True)["approved"]
    if not approved:
        flash("Your employer account is awaiting admin approval.", "error")
        return redirect(url_for("employer_dashboard"))
    if request.method == "POST":
        data, error = valid_job_form()
        if error:
            flash(error, "error")
            return render_template("post_job.html", form=request.form, categories=query(
                "SELECT * FROM categories ORDER BY cat_name"), job=None), 400
        emp = query("SELECT emp_id FROM employer_profile WHERE user_id=?",
                    (session["user_id"],), one=True)
        execute("INSERT INTO jobs (emp_id, title, description, location, salary,"
                " category, last_date, posted_on) VALUES (?,?,?,?,?,?,?,?)",
                (emp["emp_id"], data["title"], data["description"], data["location"],
                 data["salary"], data["category"], data["last_date"],
                 date.today().isoformat()))
        flash("Job posted successfully!", "success")
        return redirect(url_for("employer_dashboard"))
    return render_template("post_job.html", form={}, job=None, categories=query(
        "SELECT * FROM categories ORDER BY cat_name"))


@app.route("/employer/jobs/<int:job_id>/edit", methods=["GET", "POST"])
@login_required("employer")
def edit_job(job_id):
    job = employer_of(job_id)
    if not job:
        flash("Job not found.", "error")
        return redirect(url_for("employer_dashboard"))
    if request.method == "POST":
        data, error = valid_job_form()
        if error:
            flash(error, "error")
            form = request.form
        else:
            execute("UPDATE jobs SET title=?, description=?, location=?, salary=?,"
                    " category=?, last_date=? WHERE job_id=?",
                    (data["title"], data["description"], data["location"],
                     data["salary"], data["category"], data["last_date"], job_id))
            flash("Job updated successfully.", "success")
            return redirect(url_for("employer_dashboard"))
    else:
        form = dict(job)
    return render_template("post_job.html", form=form, job=job, categories=query(
        "SELECT * FROM categories ORDER BY cat_name"))


@app.route("/employer/jobs/<int:job_id>/delete", methods=["POST"])
@login_required("employer")
def delete_job(job_id):
    if employer_of(job_id):
        execute("DELETE FROM jobs WHERE job_id=?", (job_id,))
        flash("Job deleted.", "success")
    else:
        flash("Job not found.", "error")
    return redirect(url_for("employer_dashboard"))


@app.route("/employer/jobs/<int:job_id>/applicants")
@login_required("employer")
def applicants(job_id):
    job = employer_of(job_id)
    if not job:
        flash("Job not found.", "error")
        return redirect(url_for("employer_dashboard"))
    rows = query(
        "SELECT a.app_id, a.status, a.applied_on, u.name, u.email, p.phone,"
        " p.education, p.skills, p.resume_path "
        "FROM applications a JOIN users u ON a.user_id=u.user_id "
        "LEFT JOIN seeker_profile p ON p.user_id=u.user_id "
        "WHERE a.job_id=? ORDER BY a.app_id DESC", (job_id,))
    return render_template("applicants.html", job=job, rows=rows)


@app.route("/employer/applications/<int:app_id>/status", methods=["POST"])
@login_required("employer")
def set_application_status(app_id):
    row = query(
        "SELECT a.app_id, j.emp_id FROM applications a JOIN jobs j "
        "ON a.job_id=j.job_id WHERE a.app_id=?", (app_id,), one=True)
    emp = query("SELECT emp_id FROM employer_profile WHERE user_id=?",
                (session["user_id"],), one=True)
    if not row or not emp or row["emp_id"] != emp["emp_id"]:
        flash("Application not found.", "error")
        return redirect(url_for("employer_dashboard"))
    status = request.form.get("status")
    if status not in STATUSES:
        flash("Invalid status.", "error")
    else:
        execute("UPDATE applications SET status=? WHERE app_id=?", (status, app_id))
        flash(f"Application marked as {status}.", "success")
    return redirect(request.referrer or url_for("employer_dashboard"))


# ---------- Admin ----------

@app.route("/admin")
@login_required("admin")
def admin_panel():
    pending = query(
        "SELECT u.*, e.company_name FROM users u "
        "JOIN employer_profile e ON e.user_id=u.user_id "
        "WHERE u.role='employer' AND u.approved=0 ORDER BY u.user_id")
    employers = query(
        "SELECT u.*, e.company_name FROM users u "
        "JOIN employer_profile e ON e.user_id=u.user_id "
        "WHERE u.role='employer' ORDER BY u.approved, u.user_id")
    seekers = query("SELECT * FROM users WHERE role='seeker' ORDER BY user_id")
    categories = query(
        "SELECT c.*, (SELECT COUNT(*) FROM jobs j WHERE j.category=c.cat_name)"
        " AS job_count FROM categories c ORDER BY c.cat_name")
    jobs = query(
        "SELECT j.*, e.company_name FROM jobs j "
        "JOIN employer_profile e ON j.emp_id=e.emp_id ORDER BY j.job_id DESC")
    messages = query("SELECT * FROM contact_messages ORDER BY msg_id DESC")
    return render_template("admin_panel.html", pending=pending, employers=employers,
                           seekers=seekers, categories=categories, jobs=jobs,
                           messages=messages)


@app.route("/admin/employers/<int:user_id>/approve", methods=["POST"])
@login_required("admin")
def approve_employer(user_id):
    execute("UPDATE users SET approved=1 WHERE user_id=? AND role='employer'", (user_id,))
    flash("Employer approved.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/employers/<int:user_id>/revoke", methods=["POST"])
@login_required("admin")
def revoke_employer(user_id):
    execute("UPDATE users SET approved=0 WHERE user_id=? AND role='employer'", (user_id,))
    flash("Employer approval revoked.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required("admin")
def delete_user(user_id):
    if user_id == session["user_id"]:
        flash("You cannot delete your own account.", "error")
    else:
        execute("DELETE FROM users WHERE user_id=?", (user_id,))
        flash("User deleted.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/categories", methods=["POST"])
@login_required("admin")
def add_category():
    name = request.form.get("cat_name", "").strip()
    if not name:
        flash("Category name is required.", "error")
    elif query("SELECT 1 FROM categories WHERE cat_name=?", (name,), one=True):
        flash("Category already exists.", "error")
    else:
        execute("INSERT INTO categories (cat_name) VALUES (?)", (name,))
        flash("Category added.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/categories/<int:cat_id>/delete", methods=["POST"])
@login_required("admin")
def delete_category(cat_id):
    execute("DELETE FROM categories WHERE cat_id=?", (cat_id,))
    flash("Category deleted.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/jobs/<int:job_id>/delete", methods=["POST"])
@login_required("admin")
def admin_delete_job(job_id):
    execute("DELETE FROM jobs WHERE job_id=?", (job_id,))
    flash("Job removed.", "success")
    return redirect(url_for("admin_panel"))


# Resume download (seekers can view their own, employers see applicant resumes,
# admin can access everything)
@app.route("/resume/<path:filename>")
@login_required()
def resume_file(filename):
    user = current_user()
    path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(path):
        flash("File not found.", "error")
        return redirect(url_for("home"))
    if user["role"] == "seeker":
        p = query("SELECT resume_path FROM seeker_profile WHERE user_id=?",
                  (user["user_id"],), one=True)
        if not p or p["resume_path"] != filename:
            flash("You can only access your own resume.", "error")
            return redirect(url_for("home"))
    return send_from_directory(UPLOAD_DIR, filename)


# Idempotent: creates tables + seed data once per worker startup (gunicorn / dev)
init_db()

if __name__ == "__main__":
    app.run(debug=True)
