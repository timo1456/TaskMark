# TaskMark

TaskMark is a general-purpose assignment/task platform for tutors and students.

## V1
- Tutor/student authentication and editable profiles
- Classes, courses, capacities and invitation links
- MCQ, theory and file questions
- Drafts, immediate launch or scheduled start, deadlines and edit-after-submit control
- Autosaving and submissions
- Automatic MCQ marking and manual theory/file marking
- Broad file uploads with browser-accessible previews for supported formats
- Result release and result history
- Clickable notifications
- Light/dark/system appearance with purple accent
- Responsive installable PWA shell
- SQLite by default and PostgreSQL through DATABASE_URL
- Production WSGI configuration

## Local development

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000.

Set a strong SECRET_KEY in production. For production uploads, use persistent disk/object storage rather than ephemeral local storage.

## Render
The repository includes render.yaml. Configure a PostgreSQL DATABASE_URL and persistent storage for uploads before production use.
