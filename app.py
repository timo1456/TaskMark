import os, re, secrets, hmac
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, abort, send_from_directory, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE=Path(__file__).resolve().parent
UPLOAD=BASE/"uploads"; UPLOAD.mkdir(exist_ok=True)
app=Flask(__name__)
app.config["SECRET_KEY"]=os.getenv("SECRET_KEY","dev-change-this-secret")
db_url=os.getenv("DATABASE_URL",f"sqlite:///{BASE/'taskmark.db'}")
if db_url.startswith("postgres://"): db_url=db_url.replace("postgres://","postgresql+psycopg://",1)
elif db_url.startswith("postgresql://"): db_url=db_url.replace("postgresql://","postgresql+psycopg://",1)
app.config.update(SQLALCHEMY_DATABASE_URI=db_url,SQLALCHEMY_TRACK_MODIFICATIONS=False,
                  MAX_CONTENT_LENGTH=int(os.getenv("MAX_UPLOAD_MB","50"))*1024*1024,
                  UPLOAD_FOLDER=str(UPLOAD))
db=SQLAlchemy(app); login=LoginManager(app); login.login_view="login"

def utc(): return datetime.now(timezone.utc)
def dt(v):
    try:
        x=datetime.fromisoformat(v)
        return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
    except: return None
def code(): return secrets.token_urlsafe(12).replace("-","").replace("_","")[:12]
def upload(f,folder="files"):
    if not f or not f.filename:return None
    p=UPLOAD/folder;p.mkdir(parents=True,exist_ok=True)
    name=f"{secrets.token_hex(6)}-{secure_filename(f.filename) or 'file'}";f.save(p/name)
    return f"{folder}/{name}"
def notify(uid,title,msg,link,kind="general"):
    db.session.add(Notification(user_id=uid,title=title,message=msg,link=link,kind=kind))
def enrolled(cid,uid): return db.session.query(Enrollment.id).filter_by(course_id=cid,student_id=uid).first() is not None

class User(UserMixin,db.Model):
    id=db.Column(db.Integer,primary_key=True); role=db.Column(db.String(20),nullable=False)
    first_name=db.Column(db.String(80),nullable=False); last_name=db.Column(db.String(80),nullable=False)
    username=db.Column(db.String(40),unique=True,nullable=False,index=True); email=db.Column(db.String(160),unique=True,nullable=False,index=True)
    password_hash=db.Column(db.String(255),nullable=False); profile_image=db.Column(db.String(255)); theme=db.Column(db.String(10),default="system",nullable=False)
    created_at=db.Column(db.DateTime,default=utc)
    @property
    def full_name(self): return f"{self.first_name} {self.last_name}"
    def set_password(self,p): self.password_hash=generate_password_hash(p)
    def check_password(self,p): return check_password_hash(self.password_hash,p)

class ClassGroup(db.Model):
    id=db.Column(db.Integer,primary_key=True); tutor_id=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=False)
    name=db.Column(db.String(120),nullable=False); description=db.Column(db.Text); created_at=db.Column(db.DateTime,default=utc)
    tutor=db.relationship("User",backref="classes")

class Course(db.Model):
    id=db.Column(db.Integer,primary_key=True); class_id=db.Column(db.Integer,db.ForeignKey("class_group.id"),nullable=False)
    tutor_id=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=False); name=db.Column(db.String(160),nullable=False)
    description=db.Column(db.Text); capacity=db.Column(db.Integer,default=50,nullable=False)
    invite_code=db.Column(db.String(40),unique=True,nullable=False,index=True); invite_active=db.Column(db.Boolean,default=True,nullable=False)
    created_at=db.Column(db.DateTime,default=utc)
    class_group=db.relationship("ClassGroup",backref="courses"); tutor=db.relationship("User",backref="courses")

class Enrollment(db.Model):
    id=db.Column(db.Integer,primary_key=True); student_id=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=False)
    course_id=db.Column(db.Integer,db.ForeignKey("course.id"),nullable=False); joined_at=db.Column(db.DateTime,default=utc)
    __table_args__=(db.UniqueConstraint("student_id","course_id",name="uq_enroll"),)
    student=db.relationship("User",backref="enrollments"); course=db.relationship("Course",backref="enrollments")

class Assignment(db.Model):
    id=db.Column(db.Integer,primary_key=True); course_id=db.Column(db.Integer,db.ForeignKey("course.id"),nullable=False)
    tutor_id=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=False); title=db.Column(db.String(180),nullable=False)
    instructions=db.Column(db.Text,nullable=False); start_at=db.Column(db.DateTime,nullable=False); deadline=db.Column(db.DateTime,nullable=False)
    allow_edit=db.Column(db.Boolean,default=False,nullable=False); status=db.Column(db.String(20),default="draft",nullable=False)
    results_released=db.Column(db.Boolean,default=False,nullable=False); created_at=db.Column(db.DateTime,default=utc)
    course=db.relationship("Course",backref="assignments"); tutor=db.relationship("User",backref="assignments")
    @property
    def total_marks(self): return round(sum(q.marks for q in self.questions),2)
    @property
    def state(self):
        if self.status=="draft": return "Draft"
        n=utc()
        if n<self.start_at:return "Scheduled"
        if n>self.deadline:return "Closed"
        return "Results Released" if self.results_released else "Active"

class Question(db.Model):
    id=db.Column(db.Integer,primary_key=True); assignment_id=db.Column(db.Integer,db.ForeignKey("assignment.id"),nullable=False)
    type=db.Column(db.String(20),nullable=False); prompt=db.Column(db.Text,nullable=False); marks=db.Column(db.Float,nullable=False,default=1)
    order_index=db.Column(db.Integer,default=0,nullable=False); support_file=db.Column(db.String(255))
    assignment=db.relationship("Assignment",backref=db.backref("questions",cascade="all, delete-orphan",order_by="Question.order_index"))

class QuestionOption(db.Model):
    id=db.Column(db.Integer,primary_key=True); question_id=db.Column(db.Integer,db.ForeignKey("question.id"),nullable=False)
    text=db.Column(db.String(500),nullable=False); is_correct=db.Column(db.Boolean,default=False,nullable=False)
    question=db.relationship("Question",backref=db.backref("options",cascade="all, delete-orphan"))

class Submission(db.Model):
    id=db.Column(db.Integer,primary_key=True); assignment_id=db.Column(db.Integer,db.ForeignKey("assignment.id"),nullable=False)
    student_id=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=False); submitted_at=db.Column(db.DateTime); marked_at=db.Column(db.DateTime)
    overall_feedback=db.Column(db.Text)
    __table_args__=(db.UniqueConstraint("assignment_id","student_id",name="uq_submission"),)
    assignment=db.relationship("Assignment",backref="submissions"); student=db.relationship("User",backref="submissions")
    @property
    def score(self): return round(sum(a.awarded_mark or 0 for a in self.answers),2)
    @property
    def percentage(self):
        return round(self.score/self.assignment.total_marks*100,2) if self.assignment.total_marks else 0

class Answer(db.Model):
    id=db.Column(db.Integer,primary_key=True); submission_id=db.Column(db.Integer,db.ForeignKey("submission.id"),nullable=False)
    question_id=db.Column(db.Integer,db.ForeignKey("question.id"),nullable=False); text_answer=db.Column(db.Text)
    selected_option_id=db.Column(db.Integer,db.ForeignKey("question_option.id")); file_name=db.Column(db.String(255))
    awarded_mark=db.Column(db.Float); feedback=db.Column(db.Text)
    submission=db.relationship("Submission",backref=db.backref("answers",cascade="all, delete-orphan"))
    question=db.relationship("Question"); selected_option=db.relationship("QuestionOption")

class Notification(db.Model):
    id=db.Column(db.Integer,primary_key=True); user_id=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=False)
    title=db.Column(db.String(180),nullable=False); message=db.Column(db.Text,nullable=False); link=db.Column(db.String(500),nullable=False)
    kind=db.Column(db.String(30),default="general"); is_read=db.Column(db.Boolean,default=False,nullable=False); created_at=db.Column(db.DateTime,default=utc)
    user=db.relationship("User",backref="notifications")

@login.user_loader
def load(uid): return db.session.get(User,int(uid))

@app.context_processor
def globals():
    if "csrf_token" not in session: session["csrf_token"]=secrets.token_hex(32)
    unread=Notification.query.filter_by(user_id=current_user.id,is_read=False).count() if current_user.is_authenticated else 0
    return {"unread_notifications":unread,"now":utc(),"csrf_token":session["csrf_token"]}

@app.before_request
def protect_posts():
    if request.method=="POST":
        expected=session.get("csrf_token"); supplied=request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
        if not expected or not supplied or not hmac.compare_digest(expected,supplied): abort(400, description="Invalid CSRF token")

@app.before_request
def init():
    if not getattr(app,"_db_ready",False): db.create_all();app._db_ready=True

@app.route("/")
def index(): return redirect(url_for("dashboard")) if current_user.is_authenticated else render_template("landing.html")

@app.route("/register",methods=["GET","POST"])
def register():
    if request.method=="POST":
        role=request.form.get("role","student"); first=request.form.get("first_name","").strip();last=request.form.get("last_name","").strip()
        username=request.form.get("username","").strip().lower();email=request.form.get("email","").strip().lower();pw=request.form.get("password","")
        if role not in ("student","tutor"):role="student"
        if not all((first,last,username,email)) or len(pw)<8:flash("Complete all fields and use an 8+ character password.","error")
        elif User.query.filter((User.username==username)|(User.email==email)).first():flash("Username or email is already in use.","error")
        else:
            u=User(role=role,first_name=first,last_name=last,username=username,email=email);u.set_password(pw);db.session.add(u);db.session.commit();login_user(u)
            return redirect(url_for("dashboard"))
    return render_template("auth.html",mode="register")

@app.route("/login",methods=["GET","POST"])
def login():
    if request.method=="POST":
        u=User.query.filter_by(username=request.form.get("username","").strip().lower()).first()
        if not u or not u.check_password(request.form.get("password","")):flash("Invalid username or password.","error")
        else:login_user(u,remember=True);return redirect(url_for("dashboard"))
    return render_template("auth.html",mode="login")

@app.route("/logout")
@login_required
def logout(): logout_user();return redirect(url_for("index"))

@app.route("/dashboard")
@login_required
def dashboard():
    if current_user.role=="tutor":
        classes=ClassGroup.query.filter_by(tutor_id=current_user.id).order_by(ClassGroup.created_at.desc()).all()
        courses=Course.query.filter_by(tutor_id=current_user.id).order_by(Course.created_at.desc()).all()
        assignments=Assignment.query.filter_by(tutor_id=current_user.id).order_by(Assignment.created_at.desc()).limit(12).all()
        return render_template("dashboard.html",classes=classes,courses=courses,assignments=assignments)
    enrollments=Enrollment.query.filter_by(student_id=current_user.id).all();assignments=[a for e in enrollments for a in e.course.assignments]
    assignments=sorted(assignments,key=lambda a:a.deadline,reverse=True)[:12]
    return render_template("dashboard.html",enrollments=enrollments,assignments=assignments)

@app.route("/settings",methods=["GET","POST"])
@login_required
def settings():
    if request.method=="POST":
        first=request.form.get("first_name","").strip();last=request.form.get("last_name","").strip()
        username=request.form.get("username","").strip().lower();email=request.form.get("email","").strip().lower()
        theme=request.form.get("theme","system")
        dup=User.query.filter(User.id!=current_user.id,(User.username==username)|(User.email==email)).first()
        if not all((first,last,username,email)) or dup:flash("Use valid, unique account details.","error")
        else:
            current_user.first_name=first;current_user.last_name=last;current_user.username=username;current_user.email=email
            current_user.theme=theme if theme in ("light","dark","system") else "system"
            pw=request.form.get("password","")
            if pw: 
                if len(pw)<8:flash("New password must be at least 8 characters.","error");return render_template("settings.html")
                current_user.set_password(pw)
            p=upload(request.files.get("profile_image"),"profiles")
            if p:current_user.profile_image=p
            db.session.commit();flash("Settings updated.","success");return redirect(url_for("settings"))
    return render_template("settings.html")

@app.route("/class/new",methods=["GET","POST"])
@login_required
def class_new():
    if current_user.role!="tutor":abort(403)
    if request.method=="POST":
        name=request.form.get("name","").strip();desc=request.form.get("description","").strip()
        if not name:flash("Class name is required.","error")
        else:
            c=ClassGroup(tutor_id=current_user.id,name=name,description=desc);db.session.add(c);db.session.commit()
            return redirect(url_for("course_new",class_id=c.id))
    return render_template("form.html",kind="class",title="Create class")

@app.route("/course/new/<int:class_id>",methods=["GET","POST"])
@login_required
def course_new(class_id):
    g=db.session.get(ClassGroup,class_id)
    if current_user.role!="tutor" or not g or g.tutor_id!=current_user.id:abort(403)
    if request.method=="POST":
        name=request.form.get("name","").strip();desc=request.form.get("description","").strip()
        try:cap=max(1,int(request.form.get("capacity","50")))
        except:cap=50
        if not name:flash("Course name is required.","error")
        else:
            c=Course(class_id=g.id,tutor_id=current_user.id,name=name,description=desc,capacity=cap,invite_code=code())
            db.session.add(c);db.session.commit();return redirect(url_for("course",course_id=c.id))
    return render_template("form.html",kind="course",title=f"Create course · {g.name}",group=g)

@app.route("/course/<int:course_id>")
@login_required
def course(course_id):
    c=db.session.get(Course,course_id)
    if not c:abort(404)
    if current_user.role=="tutor":
        if c.tutor_id!=current_user.id:abort(403)
        return render_template("course.html",course=c,tutor_view=True,students=[e.student for e in c.enrollments])
    if not enrolled(c.id,current_user.id):abort(403)
    return render_template("course.html",course=c,tutor_view=False)

@app.route("/course/<int:course_id>/invite/<action>",methods=["POST"])
@login_required
def invite_action(course_id,action):
    c=db.session.get(Course,course_id)
    if not c or c.tutor_id!=current_user.id:abort(403)
    if action=="regenerate":c.invite_code=code();c.invite_active=True
    elif action=="toggle":c.invite_active=not c.invite_active
    else:abort(404)
    db.session.commit();return redirect(url_for("course",course_id=c.id))

@app.route("/join/<code>")
@login_required
def join(code):
    c=Course.query.filter_by(invite_code=code).first()
    if not c or not c.invite_active or current_user.role!="student":abort(404)
    if not enrolled(c.id,current_user.id):
        if len(c.enrollments)>=c.capacity:flash("Course capacity has been reached.","error");return redirect(url_for("dashboard"))
        db.session.add(Enrollment(student_id=current_user.id,course_id=c.id));notify(c.tutor_id,"New student joined",f"{current_user.full_name} joined {c.name}.",url_for("course",course_id=c.id),"enrollment");db.session.commit()
    return redirect(url_for("course",course_id=c.id))

@app.route("/course/<int:course_id>/students/<int:student_id>/remove",methods=["POST"])
@login_required
def remove_student(course_id,student_id):
    c=db.session.get(Course,course_id)
    if not c or c.tutor_id!=current_user.id:abort(403)
    e=Enrollment.query.filter_by(course_id=course_id,student_id=student_id).first()
    if e:db.session.delete(e);db.session.commit()
    return redirect(url_for("course",course_id=course_id))

@app.route("/assignment/new/<int:course_id>",methods=["GET","POST"])
@login_required
def assignment_new(course_id):
    c=db.session.get(Course,course_id)
    if not c or c.tutor_id!=current_user.id:abort(403)
    if request.method=="POST":
        title=request.form.get("title","").strip();instructions=request.form.get("instructions","").strip()
        start=dt(request.form.get("start_at",""));deadline=dt(request.form.get("deadline",""))
        publish=request.form.get("action")=="publish";allow=request.form.get("allow_edit")=="on"
        if not title or not instructions or not start or not deadline or deadline<=start:flash("Enter valid assignment details and a deadline after the start.","error");return render_template("assignment_builder.html",course=c)
        a=Assignment(course_id=c.id,tutor_id=current_user.id,title=title,instructions=instructions,start_at=start,deadline=deadline,allow_edit=allow,status="published" if publish else "draft")
        db.session.add(a);db.session.flush()
        ids=sorted({int(m.group(1)) for k in request.form if (m:=re.match(r"q_type_(\d+)$",k))})
        if not ids:db.session.rollback();flash("Add at least one question.","error");return render_template("assignment_builder.html",course=c)
        for order,i in enumerate(ids):
            typ=request.form.get(f"q_type_{i}");prompt=request.form.get(f"q_prompt_{i}","").strip()
            try:marks=float(request.form.get(f"q_marks_{i}","1"))
            except:marks=1
            if typ not in ("mcq","theory","file") or not prompt or marks<=0:db.session.rollback();flash("Every question needs a type, prompt and positive marks.","error");return render_template("assignment_builder.html",course=c)
            q=Question(assignment_id=a.id,type=typ,prompt=prompt,marks=marks,order_index=order);db.session.add(q);db.session.flush()
            if typ=="mcq":
                opts=[x.strip() for x in request.form.getlist(f"q_option_{i}") if x.strip()];correct=request.form.get(f"q_correct_{i}")
                if len(opts)<2 or correct not in {str(x) for x in range(len(request.form.getlist(f"q_option_{i}")))}:db.session.rollback();flash("Each MCQ needs at least two options and one correct answer.","error");return render_template("assignment_builder.html",course=c)
                for n,opt in enumerate(opts):db.session.add(QuestionOption(question_id=q.id,text=opt,is_correct=str(n)==correct))
            elif typ=="file":
                q.support_file=upload(request.files.get(f"q_file_{i}"),f"assignment-{a.id}")
        db.session.commit()
        if publish:
            for e in c.enrollments:notify(e.student_id,"New assignment",f"{a.title} is available in {c.name}.",url_for("assignment",assignment_id=a.id),"assignment")
            db.session.commit()
        return redirect(url_for("assignment",assignment_id=a.id))
    return render_template("assignment_builder.html",course=c)

def get_submission(a,uid):
    s=Submission.query.filter_by(assignment_id=a.id,student_id=uid).first()
    if not s:
        s=Submission(assignment_id=a.id,student_id=uid);db.session.add(s);db.session.flush()
        for q in a.questions:db.session.add(Answer(submission_id=s.id,question_id=q.id))
        db.session.commit()
    return s

@app.route("/assignment/<int:assignment_id>")
@login_required
def assignment(assignment_id):
    a=db.session.get(Assignment,assignment_id)
    if not a:abort(404)
    if current_user.role=="tutor":
        if a.tutor_id!=current_user.id:abort(403)
        return render_template("assignment.html",assignment=a,tutor_view=True)
    if a.status!="published" or not enrolled(a.course_id,current_user.id):abort(403)
    s=get_submission(a,current_user.id)
    return render_template("assignment.html",assignment=a,tutor_view=False,submission=s,available=(a.start_at<=utc()<=a.deadline))

def save_answers(a,s):
    for q in a.questions:
        ans=Answer.query.filter_by(submission_id=s.id,question_id=q.id).first()
        if q.type=="mcq":
            v=request.form.get(f"answer_{q.id}");ans.selected_option_id=int(v) if v and v.isdigit() else None
        elif q.type=="theory":ans.text_answer=request.form.get(f"answer_{q.id}","")
        else:
            f=request.files.get(f"answer_file_{q.id}");p=upload(f,f"submission-{s.id}")
            if p:ans.file_name=p
    db.session.commit()

@app.route("/assignment/<int:assignment_id>/autosave",methods=["POST"])
@login_required
def autosave(assignment_id):
    a=db.session.get(Assignment,assignment_id)
    if not a or current_user.role!="student" or not enrolled(a.course_id,current_user.id):return jsonify(ok=False),403
    if a.start_at>utc() or a.deadline<utc():return jsonify(ok=False,error="closed"),400
    s=get_submission(a,current_user.id)
    if s.submitted_at and not a.allow_edit:return jsonify(ok=False,error="locked"),403
    save_answers(a,s);return jsonify(ok=True,saved_at=utc().isoformat())

@app.route("/assignment/<int:assignment_id>/submit",methods=["POST"])
@login_required
def submit(assignment_id):
    a=db.session.get(Assignment,assignment_id)
    if not a or current_user.role!="student" or not enrolled(a.course_id,current_user.id):abort(403)
    if a.start_at>utc() or a.deadline<utc():flash("This assignment is not accepting submissions.","error");return redirect(url_for("assignment",assignment_id=a.id))
    s=get_submission(a,current_user.id)
    if s.submitted_at and not a.allow_edit:flash("Submission is locked.","error");return redirect(url_for("assignment",assignment_id=a.id))
    save_answers(a,s);s.submitted_at=utc()
    for ans in s.answers:
        if ans.question.type=="mcq":ans.awarded_mark=ans.question.marks if ans.selected_option and ans.selected_option.is_correct else 0
    if all(x.question.type=="mcq" for x in s.answers): s.marked_at=utc()
    notify(a.tutor_id,"Assignment submitted",f"{current_user.full_name} submitted {a.title}.",url_for("mark",assignment_id=a.id,student_id=current_user.id),"submission")
    db.session.commit();flash("Assignment submitted.","success");return redirect(url_for("assignment",assignment_id=a.id))

@app.route("/assignment/<int:assignment_id>/publish",methods=["POST"])
@login_required
def publish(assignment_id):
    a=db.session.get(Assignment,assignment_id)
    if not a or a.tutor_id!=current_user.id:abort(403)
    a.status="published"
    for e in a.course.enrollments:notify(e.student_id,"New assignment",f"{a.title} is now available.",url_for("assignment",assignment_id=a.id),"assignment")
    db.session.commit();return redirect(url_for("assignment",assignment_id=a.id))

@app.route("/assignment/<int:assignment_id>/release",methods=["POST"])
@login_required
def release(assignment_id):
    a=db.session.get(Assignment,assignment_id)
    if not a or a.tutor_id!=current_user.id:abort(403)
    a.results_released=True
    for s in a.submissions:
        if s.submitted_at and s.marked_at is None:
            flash("Mark every submitted student before releasing results.","error");return redirect(url_for("assignment",assignment_id=a.id))notify(s.student_id,"Result released",f"Your result for {a.title} is ready.",url_for("result",submission_id=s.id),"result")
    db.session.commit();return redirect(url_for("assignment",assignment_id=a.id))

@app.route("/assignment/<int:assignment_id>/mark/<int:student_id>")
@login_required
def mark(assignment_id,student_id):
    a=db.session.get(Assignment,assignment_id)
    if not a or a.tutor_id!=current_user.id:abort(403)
    s=Submission.query.filter_by(assignment_id=assignment_id,student_id=student_id).first()
    if not s:abort(404)
    return render_template("mark.html",assignment=a,student=s.student,submission=s)

@app.route("/assignment/<int:assignment_id>/mark/<int:student_id>/save",methods=["POST"])
@login_required
def save_marks(assignment_id,student_id):
    a=db.session.get(Assignment,assignment_id)
    if not a or a.tutor_id!=current_user.id:abort(403)
    s=Submission.query.filter_by(assignment_id=assignment_id,student_id=student_id).first()
    if not s:abort(404)
    for ans in s.answers:
        if ans.question.type=="mcq":continue
        try:v=float(request.form.get(f"mark_{ans.id}","0"))
        except:v=0
        ans.awarded_mark=max(0,min(v,ans.question.marks));ans.feedback=request.form.get(f"feedback_{ans.id}","").strip()
    s.overall_feedback=request.form.get("overall_feedback","").strip();s.marked_at=utc();db.session.commit()
    return redirect(url_for("mark",assignment_id=assignment_id,student_id=student_id))

@app.route("/result/<int:submission_id>")
@login_required
def result(submission_id):
    s=db.session.get(Submission,submission_id)
    if not s:abort(404)
    if current_user.id!=s.student_id and current_user.id!=s.assignment.tutor_id:abort(403)
    if current_user.role=="student" and not s.assignment.results_released:abort(403)
    return render_template("result.html",submission=s)

@app.route("/results")
@login_required
def results():
    if current_user.role=="tutor":return render_template("results.html",tutor_view=True,assignments=Assignment.query.filter_by(tutor_id=current_user.id).all())
    subs=Submission.query.filter_by(student_id=current_user.id).join(Assignment).filter(Assignment.results_released.is_(True)).order_by(Submission.submitted_at.desc()).all()
    return render_template("results.html",tutor_view=False,submissions=subs)

@app.route("/notifications")
@login_required
def notifications():
    ns=Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).all()
    for n in ns:n.is_read=True
    db.session.commit();return render_template("notifications.html",notifications=ns)

@app.route("/assignment/link",methods=["POST"])
@login_required
def assignment_link():
    m=re.fullmatch(r"/assignment/(\d+)",urlparse(request.form.get("assignment_link","").strip()).path.rstrip("/"))
    if not m:flash("Enter a valid TaskMark assignment link.","error");return redirect(url_for("dashboard"))
    return redirect(url_for("assignment",assignment_id=int(m.group(1))))

@app.route("/uploads/<path:name>")
@login_required
def files(name):return send_from_directory(app.config["UPLOAD_FOLDER"],name)

@app.route("/health")
def health():return jsonify(status="ok",service="TaskMark")

@app.route("/manifest.json")
def manifest():return jsonify(name="TaskMark",short_name="TaskMark",start_url="/dashboard",display="standalone",background_color="#0d0c12",theme_color="#7c3aed",icons=[{"src":"/static/icons/app-icon.svg","sizes":"any","type":"image/svg+xml","purpose":"any maskable"}])

@app.route("/service-worker.js")
def sw():return app.send_static_file("service-worker.js")

@app.errorhandler(413)
def too_large(e):flash("That file is too large.","error");return redirect(request.referrer or url_for("dashboard"))

with app.app_context():db.create_all()

if __name__=="__main__":app.run(host="0.0.0.0",port=int(os.getenv("PORT","5000")),debug=os.getenv("FLASK_DEBUG")=="1")
