import streamlit as st
import pandas as pd
import smtplib
import time
import mimetypes
import sqlite3
from datetime import datetime
from email.message import EmailMessage
from email_validator import validate_email, EmailNotValidError
from dotenv import load_dotenv
import os

# ==============================
# LOAD ENV
# ==============================
load_dotenv()

# ==============================
# PAGE CONFIG
# ==============================
st.set_page_config(
    page_title="Email Sender Bot",
    page_icon="📧",
    layout="wide"
)

# ==============================
# CUSTOM STYLING
# ==============================
st.markdown("""
<style>
.main-title {
    font-size: 2.4rem;
    font-weight: 800;
    color: #1f2937;
    margin-bottom: 0.2rem;
}
.subtitle {
    color: #4b5563;
    margin-bottom: 1.2rem;
}
.stButton>button {
    border-radius: 10px;
}
</style>
""", unsafe_allow_html=True)

# ==============================
# HEADER
# ==============================
st.markdown('<div class="main-title">📧 Email Sender Pro V2</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">Multi-SMTP • Scheduler • Database • Templates • Reports • Analytics</div>', unsafe_allow_html=True)

# ==============================
# DB SETUP
# ==============================
DB_FILE = "email_sender.db"

def get_db_connection():
    return sqlite3.connect(DB_FILE, check_same_thread=False)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        template_name TEXT UNIQUE,
        subject TEXT,
        plain_text TEXT,
        html_text TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS email_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recipient_name TEXT,
        recipient_email TEXT,
        subject TEXT,
        status TEXT,
        error_message TEXT,
        smtp_provider TEXT,
        scheduled_time TEXT,
        sent_time TEXT
    )
    """)

    conn.commit()
    conn.close()

init_db()

# ==============================
# SESSION STATE INIT
# ==============================
for key, default in {
    "success_list": [],
    "failed_list": [],
    "invalid_list": [],
    "validated_df": None,
    "logs": [],
    "send_completed": False
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ==============================
# SMTP CONFIG
# ==============================
SMTP_PRESETS = {
    "Gmail": {"host": "smtp.gmail.com", "port": 587},
    "Outlook": {"host": "smtp.office365.com", "port": 587},
    "Yahoo": {"host": "smtp.mail.yahoo.com", "port": 587},
    "Custom": {"host": "", "port": 587}
}

# ==============================
# HELPER FUNCTIONS
# ==============================
def add_log(message):
    st.session_state.logs.append(message)

def parse_email_list(email_string):
    if not email_string.strip():
        return []
    return [e.strip() for e in email_string.split(",") if e.strip()]

def is_valid_email(email):
    try:
        validate_email(email, check_deliverability=False)
        return True, ""
    except EmailNotValidError as e:
        return False, str(e)

def safe_read_csv(uploaded_file):
    if uploaded_file is None:
        raise ValueError("No CSV file uploaded.")

    uploaded_file.seek(0)
    content = uploaded_file.read()

    if len(content) == 0:
        raise ValueError("Uploaded CSV file is empty.")

    uploaded_file.seek(0)

    try:
        df = pd.read_csv(uploaded_file)
    except pd.errors.EmptyDataError:
        raise ValueError("CSV file has no columns or no data.")
    except Exception as e:
        raise ValueError(f"Unable to read CSV: {e}")

    if df.empty:
        raise ValueError("CSV contains headers but no rows.")

    df.columns = [str(col).strip().lower() for col in df.columns]
    return df

def validate_csv(df):
    valid_rows = []
    invalid_rows = []

    if "name" not in df.columns or "email" not in df.columns:
        return None, [{"error": "CSV must contain 'name' and 'email' columns"}]

    df = df.dropna(how="all")

    for _, row in df.iterrows():
        name = str(row.get("name", "")).strip()
        email = str(row.get("email", "")).strip()

        if not name and not email:
            continue

        if not email:
            invalid_rows.append({
                "name": name,
                "email": email,
                "status": "Invalid",
                "error": "Missing email"
            })
            continue

        ok, err = is_valid_email(email)
        if ok:
            valid_rows.append({"name": name if name else "User", "email": email})
        else:
            invalid_rows.append({
                "name": name if name else "User",
                "email": email,
                "status": "Invalid",
                "error": err
            })

    return pd.DataFrame(valid_rows), invalid_rows

def safe_format(template, name):
    try:
        return template.format(name=name)
    except Exception:
        return template

def get_attachment_mime(filename):
    mime_type, _ = mimetypes.guess_type(filename)
    if mime_type:
        maintype, subtype = mime_type.split("/", 1)
    else:
        maintype, subtype = "application", "octet-stream"
    return maintype, subtype

def render_html_preview(html_content):
    st.components.v1.html(html_content, height=300, scrolling=True)

def save_template_to_db(template_name, subject, plain_text, html_text):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT OR REPLACE INTO templates (template_name, subject, plain_text, html_text, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (template_name, subject, plain_text, html_text, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        return True, "Template saved successfully."
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()

def load_templates_from_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT template_name, subject, plain_text, html_text, created_at FROM templates ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return rows

def log_email_to_db(name, email, subject, status, error_message, smtp_provider, scheduled_time):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO email_logs (recipient_name, recipient_email, subject, status, error_message, smtp_provider, scheduled_time, sent_time)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        name,
        email,
        subject,
        status,
        error_message,
        smtp_provider,
        scheduled_time,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))
    conn.commit()
    conn.close()

def fetch_email_logs():
    conn = get_db_connection()
    df = pd.read_sql_query("SELECT * FROM email_logs ORDER BY id DESC", conn)
    conn.close()
    return df

def send_email(receiver_name, receiver_email, subject, plain_template, html_template, sender_name,
               sender_email, app_password, smtp_host, smtp_port, cc_emails, bcc_emails,
               uploaded_attachments, retry_attempts, retry_delay, smtp_provider, scheduled_time):
    
    personalized_text = safe_format(plain_template, receiver_name)
    personalized_html = safe_format(html_template, receiver_name)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{sender_name} <{sender_email}>"
    msg["To"] = receiver_email

    cc_list = parse_email_list(cc_emails)
    bcc_list = parse_email_list(bcc_emails)

    if cc_list:
        msg["Cc"] = ", ".join(cc_list)

    all_recipients = [receiver_email] + cc_list + bcc_list

    msg.set_content(personalized_text)
    msg.add_alternative(personalized_html, subtype="html")

    if uploaded_attachments:
        for file in uploaded_attachments:
            try:
                file.seek(0)
                file_bytes = file.read()
                maintype, subtype = get_attachment_mime(file.name)
                msg.add_attachment(file_bytes, maintype=maintype, subtype=subtype, filename=file.name)
                file.seek(0)
            except Exception as e:
                add_log(f"⚠️ Attachment issue with {file.name}: {e}")

    attempt = 0
    last_error = None

    while attempt < retry_attempts:
        try:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
                server.starttls()
                server.login(sender_email, app_password)
                server.send_message(msg, from_addr=sender_email, to_addrs=all_recipients)

            log_email_to_db(receiver_name, receiver_email, subject, "Success", "", smtp_provider, scheduled_time)
            return True, "Success"

        except Exception as e:
            attempt += 1
            last_error = str(e)
            add_log(f"⚠️ Retry {attempt}/{retry_attempts} for {receiver_email} - {last_error}")
            if attempt < retry_attempts:
                time.sleep(retry_delay)

    log_email_to_db(receiver_name, receiver_email, subject, "Failed", last_error, smtp_provider, scheduled_time)
    return False, last_error

# ==============================
# SIDEBAR
# ==============================
st.sidebar.header("⚙️ SMTP & Send Settings")

smtp_provider = st.sidebar.selectbox("SMTP Provider", list(SMTP_PRESETS.keys()))
smtp_host = SMTP_PRESETS[smtp_provider]["host"]
smtp_port = SMTP_PRESETS[smtp_provider]["port"]

if smtp_provider == "Custom":
    smtp_host = st.sidebar.text_input("Custom SMTP Host", value="smtp.example.com")
    smtp_port = st.sidebar.number_input("Custom SMTP Port", min_value=1, max_value=65535, value=587)

sender_name = st.sidebar.text_input("Sender Name", value=os.getenv("DEFAULT_SENDER_NAME", "Rahul Reports"))
sender_email = st.sidebar.text_input("Sender Email", value=os.getenv("DEFAULT_SENDER_EMAIL", ""))
app_password = st.sidebar.text_input("App Password / SMTP Password", type="password", value=os.getenv("DEFAULT_APP_PASSWORD", ""))

st.sidebar.markdown("---")
retry_attempts = st.sidebar.number_input("Retry Attempts", min_value=1, max_value=10, value=3)
delay_between_emails = st.sidebar.number_input("Delay Between Emails (seconds)", min_value=0.0, max_value=60.0, value=1.0, step=0.5)
retry_delay = st.sidebar.number_input("Retry Delay (seconds)", min_value=0.0, max_value=60.0, value=3.0, step=0.5)

st.sidebar.markdown("---")
cc_emails = st.sidebar.text_input("CC Emails (comma separated)")
bcc_emails = st.sidebar.text_input("BCC Emails (comma separated)")
test_mode = st.sidebar.checkbox("Test Mode (only first valid recipient)", value=False)

if st.sidebar.button("🧹 Reset Session Reports"):
    st.session_state.success_list = []
    st.session_state.failed_list = []
    st.session_state.invalid_list = []
    st.session_state.validated_df = None
    st.session_state.logs = []
    st.session_state.send_completed = False
    st.sidebar.success("Session reset done.")

# ==============================
# MAIN INPUTS
# ==============================
subject = st.text_input("📌 Email Subject", value="Automated Report from Email Sender Pro V2")
uploaded_csv = st.file_uploader("📂 Upload Recipients CSV", type=["csv"])
uploaded_attachments = st.file_uploader("📎 Upload Attachments (optional)", accept_multiple_files=True)

sample_csv = "name,email\nRahul,rahul@example.com\nAmit,amit@example.com\nPriya,priya@example.com\n"
st.download_button("⬇️ Download Sample CSV", data=sample_csv.encode("utf-8"), file_name="sample_recipients.csv", mime="text/csv")

col_a, col_b = st.columns(2)

with col_a:
    plain_template = st.text_area(
        "📝 Plain Text Template",
        value="Hello {name},\n\nThis is an automated email from Email Sender Pro V2.\nPlease find the attached file.\n\nBest regards,\nRahul",
        height=220
    )

with col_b:
    html_template = st.text_area(
        "🌐 HTML Template",
        value="""<html><body style="font-family: Arial, sans-serif;"><h2>Hello {name},</h2><p>This is an <b>automated email</b> from <b>Email Sender Pro V2</b>.</p><p>Please find the attached file.</p><p>Best regards,<br><b>Rahul</b></p></body></html>""",
        height=220
    )

# ==============================
# TEMPLATE SAVE/LOAD
# ==============================
st.markdown("### 💾 Template Manager")
temp_col1, temp_col2, temp_col3 = st.columns([2, 1, 2])

with temp_col1:
    template_name = st.text_input("Template Name", value="Default Template")

with temp_col2:
    if st.button("💾 Save Template"):
        ok, msg = save_template_to_db(template_name, subject, plain_template, html_template)
        if ok:
            st.success(msg)
        else:
            st.error(msg)

with temp_col3:
    templates = load_templates_from_db()
    template_options = ["-- Select Template --"] + [t[0] for t in templates]
    selected_template = st.selectbox("Load Template", template_options)

if selected_template != "-- Select Template --":
    selected_data = next((t for t in templates if t[0] == selected_template), None)
    if selected_data:
        loaded_subject = selected_data[1]
        loaded_plain = selected_data[2]
        loaded_html = selected_data[3]
        st.info("Template loaded below. Copy values manually if you want to preserve edits.")
        st.code(f"Subject: {loaded_subject}")
        st.code(loaded_plain)
        st.code(loaded_html)

# ==============================
# METRICS
# ==============================
total_recipients = 0
valid_count = 0
invalid_count = 0
attachment_count = len(uploaded_attachments) if uploaded_attachments else 0

if uploaded_csv is not None:
    try:
        preview_df = safe_read_csv(uploaded_csv)
        total_recipients = len(preview_df)
        validated_df, invalid_rows = validate_csv(preview_df)
        valid_count = 0 if validated_df is None else len(validated_df)
        invalid_count = len(invalid_rows)
    except Exception:
        pass

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Total Recipients", total_recipients)
m2.metric("Valid Emails", valid_count)
m3.metric("Invalid Emails", invalid_count)
m4.metric("Attachments", attachment_count)
m5.metric("Successful Sends", len(st.session_state.success_list))

# ==============================
# TABS
# ==============================
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "✍️ Compose", "👀 Preview", "🚀 Send", "📊 Reports", "🗃️ DB Logs", "⚙️ Settings"
])

# ==============================
# TAB 1 - COMPOSE
# ==============================
with tab1:
    st.subheader("Compose Email & Upload Data")

    if uploaded_csv is not None:
        try:
            df_preview = safe_read_csv(uploaded_csv)
            st.success("CSV loaded successfully.")
            st.dataframe(df_preview.head(10), use_container_width=True)
        except Exception as e:
            st.error(f"CSV Read Error: {e}")
    else:
        st.info("Upload a recipients CSV file to begin.")

    if uploaded_attachments:
        st.write("### 📎 Uploaded Attachments")
        for file in uploaded_attachments:
            size_kb = round(file.size / 1024, 2)
            st.write(f"- {file.name} ({size_kb} KB)")

# ==============================
# TAB 2 - PREVIEW
# ==============================
with tab2:
    st.subheader("Preview Personalized Email")

    sample_name = "Rahul"
    if uploaded_csv is not None:
        try:
            temp_df = safe_read_csv(uploaded_csv)
            if "name" in temp_df.columns and len(temp_df) > 0:
                sample_name = str(temp_df.iloc[0]["name"]).strip()
        except Exception:
            pass

    preview_text = safe_format(plain_template, sample_name)
    preview_html = safe_format(html_template, sample_name)

    p1, p2 = st.columns(2)
    with p1:
        st.write("### 📝 Plain Text Preview")
        st.text_area("Plain Preview", value=preview_text, height=250, disabled=True, label_visibility="collapsed")
    with p2:
        st.write("### 🌐 HTML Preview")
        render_html_preview(preview_html)

# ==============================
# TAB 3 - SEND
# ==============================
with tab3:
    st.subheader("Validate, Schedule & Send Emails")

    schedule_enabled = st.checkbox("⏰ Schedule send later")
    scheduled_datetime = None
    scheduled_time_str = ""

    if schedule_enabled:
        scheduled_date = st.date_input("Select Scheduled Date")
        scheduled_time = st.time_input("Select Scheduled Time")
        scheduled_datetime = datetime.combine(scheduled_date, scheduled_time)
        scheduled_time_str = scheduled_datetime.strftime("%Y-%m-%d %H:%M:%S")
        st.info(f"Emails will be sent at: {scheduled_time_str}")

    col_v1, col_v2 = st.columns(2)

    with col_v1:
        if st.button("✅ Validate Recipients"):
            if not uploaded_csv:
                st.error("Please upload a CSV file first.")
            else:
                try:
                    df = safe_read_csv(uploaded_csv)
                    validated_df, invalid_rows = validate_csv(df)
                    st.session_state.validated_df = validated_df
                    st.session_state.invalid_list = invalid_rows

                    if validated_df is not None:
                        st.success(f"Validation completed. {len(validated_df)} valid email(s), {len(invalid_rows)} invalid email(s).")
                    else:
                        st.error("Validation failed. Check CSV format.")
                except Exception as e:
                    st.error(f"Validation error: {e}")

    with col_v2:
        if st.button("🚀 Start Sending"):
            st.session_state.success_list = []
            st.session_state.failed_list = []
            st.session_state.logs = []
            st.session_state.send_completed = False

            if not sender_email or not app_password:
                st.error("Please enter sender email and password.")
            elif st.session_state.validated_df is None:
                st.error("Please validate recipients first.")
            elif len(st.session_state.validated_df) == 0:
                st.error("No valid recipients found.")
            else:
                if schedule_enabled and scheduled_datetime:
                    now = datetime.now()
                    if scheduled_datetime > now:
                        wait_seconds = (scheduled_datetime - now).total_seconds()
                        st.warning(f"Waiting {int(wait_seconds)} seconds until scheduled time...")
                        time.sleep(wait_seconds)

                recipients_df = st.session_state.validated_df.copy()

                if test_mode:
                    recipients_df = recipients_df.head(1)
                    st.warning("Test Mode enabled: only first valid recipient will receive the email.")

                total = len(recipients_df)
                progress_bar = st.progress(0)
                progress_text = st.empty()
                log_box = st.empty()

                for _, row in recipients_df.iterrows():
                    name = str(row["name"]).strip()
                    email = str(row["email"]).strip()

                    add_log(f"📨 Sending to {name} ({email}) via {smtp_provider}...")

                    status, message = send_email(
                        receiver_name=name,
                        receiver_email=email,
                        subject=subject,
                        plain_template=plain_template,
                        html_template=html_template,
                        sender_name=sender_name,
                        sender_email=sender_email,
                        app_password=app_password,
                        smtp_host=smtp_host,
                        smtp_port=smtp_port,
                        cc_emails=cc_emails,
                        bcc_emails=bcc_emails,
                        uploaded_attachments=uploaded_attachments,
                        retry_attempts=retry_attempts,
                        retry_delay=retry_delay,
                        smtp_provider=smtp_provider,
                        scheduled_time=scheduled_time_str
                    )

                    if status:
                        st.session_state.success_list.append({"name": name, "email": email, "status": "Success"})
                        add_log(f"✅ Success: {name} ({email})")
                    else:
                        st.session_state.failed_list.append({"name": name, "email": email, "status": "Failed", "error": message})
                        add_log(f"❌ Failed: {name} ({email}) | {message}")

                    processed = len(st.session_state.success_list) + len(st.session_state.failed_list)
                    progress_bar.progress(processed / total)
                    progress_text.info(f"Progress: {processed} / {total} emails processed")

                    log_box.text_area(
                        "📜 Live Logs",
                        value="\n".join(st.session_state.logs[-15:]),
                        height=260,
                        disabled=True
                    )

                    time.sleep(delay_between_emails)

                st.session_state.send_completed = True
                st.success("Email sending process completed.")

# ==============================
# TAB 4 - REPORTS
# ==============================
with tab4:
    st.subheader("Execution Reports")

    if st.session_state.invalid_list:
        invalid_df = pd.DataFrame(st.session_state.invalid_list)
        st.warning("Invalid Emails")
        st.dataframe(invalid_df, use_container_width=True)
        st.download_button("⬇️ Download Invalid Report", invalid_df.to_csv(index=False).encode("utf-8"), "invalid_report.csv", "text/csv")

    if st.session_state.success_list:
        success_df = pd.DataFrame(st.session_state.success_list)
        st.success("Successful Emails")
        st.dataframe(success_df, use_container_width=True)
        st.download_button("⬇️ Download Success Report", success_df.to_csv(index=False).encode("utf-8"), "success_report.csv", "text/csv")

    if st.session_state.failed_list:
        failed_df = pd.DataFrame(st.session_state.failed_list)
        st.error("Failed Emails")
        st.dataframe(failed_df, use_container_width=True)
        st.download_button("⬇️ Download Failed Report", failed_df.to_csv(index=False).encode("utf-8"), "failed_report.csv", "text/csv")

    if st.session_state.logs:
        logs_text = "\n".join(st.session_state.logs)
        st.info("Execution Logs")
        st.text_area("Logs", logs_text, height=250, disabled=True)
        st.download_button("⬇️ Download Logs", logs_text.encode("utf-8"), "email_logs.txt", "text/plain")

# ==============================
# TAB 5 - DB LOGS / ANALYTICS
# ==============================
with tab5:
    st.subheader("Database Logs & Analytics")

    db_logs = fetch_email_logs()

    if not db_logs.empty:
        st.dataframe(db_logs, use_container_width=True)

        st.markdown("### 📈 Analytics")
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Logged Emails", len(db_logs))
        col2.metric("Total Success", len(db_logs[db_logs["status"] == "Success"]))
        col3.metric("Total Failed", len(db_logs[db_logs["status"] == "Failed"]))

        provider_counts = db_logs["smtp_provider"].value_counts()
        st.bar_chart(provider_counts)
    else:
        st.info("No email logs found in database yet.")

# ==============================
# TAB 6 - SETTINGS
# ==============================
with tab6:
    st.subheader("Current Settings & Usage Guide")

    s1, s2 = st.columns(2)

    with s1:
        st.write("### Current Settings")
        st.write(f"**SMTP Provider:** {smtp_provider}")
        st.write(f"**SMTP Host:** {smtp_host}")
        st.write(f"**SMTP Port:** {smtp_port}")
        st.write(f"**Sender Name:** {sender_name}")
        st.write(f"**Retry Attempts:** {retry_attempts}")
        st.write(f"**Delay Between Emails:** {delay_between_emails} sec")
        st.write(f"**Retry Delay:** {retry_delay} sec")
        st.write(f"**Test Mode:** {'Enabled' if test_mode else 'Disabled'}")

    with s2:
        st.write("### Recommended CSV Format")
        st.code("name,email\nRahul,example1@gmail.com\nAmit,example2@gmail.com", language="csv")

    st.markdown("---")
    st.write("### Best Practices")
    st.markdown("""
- Use **Gmail App Password** for Gmail.
- Outlook/Yahoo may require app passwords or SMTP permissions.
- Validate recipients before sending.
- Use **Test Mode** first.
- Add delay between emails to avoid temporary SMTP blocking.
- Save templates for repeated campaigns.
- Use scheduling for automated report distribution.
""")