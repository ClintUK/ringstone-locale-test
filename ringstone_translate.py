# ringstone_translate.py
import os
import json
import csv
import smtplib
from dotenv import load_dotenv
from typing import Dict
from github import Github, InputGitAuthor
from openai import OpenAI
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

# Load secrets from .env
load_dotenv()

client = OpenAI()
github_token = os.getenv("GITHUB_TOKEN")
repo_name = os.getenv("GITHUB_REPO")
model = os.getenv("OPENAI_MODEL", "gpt-4")

EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO = os.getenv("EMAIL_TO")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))

SOURCE_LANG = "English"
TARGET_LANGS = os.getenv("TRANSLATE_LANGS", "es,fr,ja").split(",")
LOCALE_PATH = "locales/en.json"
BRANCH_NAME = "translations-auto"
translation_log = {}
total_tokens_used = 0

CSV_LOG_FILE = "translation_report.csv"

def build_prompt(text: str, target_lang: str) -> str:
    return f"""
Translate the following UI string from English to {target_lang}.
Keep it concise, clear, and maintain a friendly and modern tone.

"{text}"

Only return the translated text, no commentary.
"""

def translate_text(text: str, target_lang: str) -> str:
    global total_tokens_used
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": build_prompt(text, target_lang)}],
        temperature=0.3,
    )
    total_tokens_used += response.usage.total_tokens
    return response.choices[0].message.content.strip()

def translate_locale(source_dict: Dict[str, str], target_lang: str) -> Dict[str, str]:
    translations = {}
    lang_tokens = 0
    for key, value in source_dict.items():
        translated = translate_text(value, target_lang)
        translations[key] = translated
        lang_tokens += len(value.split()) + len(translated.split())
        translation_log.setdefault(target_lang, []).append((key, value, translated, lang_tokens))
    return translations

def write_csv_log():
    with open(CSV_LOG_FILE, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(["Language", "Key", "Original Text", "Translated Text"])
        for lang, entries in translation_log.items():
            for key, original, translated, _ in entries:
                writer.writerow([lang, key, original, translated])

def send_translation_email():
    msg = MIMEMultipart()
    msg["Subject"] = "🌐 RingStone TMS MVP Translation Report"
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    total_cost_usd = total_tokens_used * 0.00001
    html = f"""
    <html><body style='font-family:Arial, sans-serif;'>
    <h2 style='color:#2c3e50;'>🌐 RingStone TMS MVP Translation Report</h2>
    <p style='font-size:14px;'>Model used: <b>{model}</b><br>
    Total tokens used: <b>{total_tokens_used:,}</b><br>
    Estimated cost: <b>${total_cost_usd:.4f} USD</b></p>
    <hr>
    """

    for lang, entries in translation_log.items():
        lang_total_tokens = entries[-1][3] if entries else 0
        lang_cost = lang_total_tokens * 0.00001
        html += f"<h3 style='color:#1a73e8;'>{lang.upper()} (≈ {lang_total_tokens:,} tokens | ${lang_cost:.4f})</h3>"
        html += "<table border='1' cellspacing='0' cellpadding='6' style='border-collapse:collapse; font-size:13px;'>"
        html += "<tr style='background:#f2f2f2;'><th>Key</th><th>Original</th><th>Translated</th></tr>"
        for key, original, translated, _ in entries:
            html += f"<tr><td>{key}</td><td>{original}</td><td>{translated}</td></tr>"
        html += "</table><br>"

    html += "</body></html>"
    msg.attach(MIMEText(html, "html"))

    # Attach CSV log
    write_csv_log()
    with open(CSV_LOG_FILE, "rb") as f:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={CSV_LOG_FILE}")
        msg.attach(part)

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.sendmail(EMAIL_FROM, EMAIL_TO.split(","), msg.as_string())
            print("✅ Email sent to:", EMAIL_TO)
    except Exception as e:
        print("❌ Email failed:", e)

def push_translations():
    gh = Github(github_token)
    repo = gh.get_repo(repo_name)
    main = repo.get_branch("main")

    try:
        repo.create_git_ref(ref=f"refs/heads/{BRANCH_NAME}", sha=main.commit.sha)
    except:
        print(f"Branch {BRANCH_NAME} already exists")

    contents = repo.get_contents(LOCALE_PATH, ref="main")
    source_data = json.loads(contents.decoded_content.decode())

    for lang in TARGET_LANGS:
        print(f"Translating to {lang}...")
        translated = translate_locale(source_data, lang)
        new_file_path = f"locales/{lang}.json"
        json_blob = json.dumps(translated, ensure_ascii=False, indent=2)

        try:
            repo.create_file(
                path=new_file_path,
                message=f"Add {lang} translation",
                content=json_blob,
                branch=BRANCH_NAME,
                author=InputGitAuthor("Clinton", "clinton@ringstone.ai")
            )
        except:
            repo.update_file(
                path=new_file_path,
                message=f"Update {lang} translation",
                content=json_blob,
                sha=repo.get_contents(new_file_path, ref=BRANCH_NAME).sha,
                branch=BRANCH_NAME,
                author=InputGitAuthor("Clinton", "clinton@ringstone.ai")
            )

    existing_prs = repo.get_pulls(state="open", head=f"ClintUK:{BRANCH_NAME}", base="main")
    if existing_prs.totalCount == 0:
        repo.create_pull(
            title="Automated translations via RingStone MVP Demo",
            body="This PR was auto-generated using LLM-based translation.",
            head=BRANCH_NAME,
            base="main"
        )
    else:
        print("⚠️ PR already exists. Skipping PR creation.")

    send_translation_email()

if __name__ == "__main__":
    push_translations()
