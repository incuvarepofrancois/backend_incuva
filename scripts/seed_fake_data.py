#!/usr/bin/env python3
"""
Remplit la base avec des données fictives pour tous les modèles SQLAlchemy.

Sources open source utilisées (avec repli automatique sur Faker si l'API est indisponible) :
  - randomuser.me            -> identités des employés (noms, emails, dates)
  - date.nager.at            -> jours fériés français (pour des absences réalistes)
  - jsonplaceholder.typicode -> textes pour les transcripts de réunions

Installation :  pip install faker requests sqlalchemy
Placer le fichier dans backend/scripts/ puis :
  export DATABASE_URL="postgresql+psycopg2://user:pass@localhost/jarvis"
  python backend/scripts/seed_fake_data.py --orgs 2 --employees 40 --create-all

Remplir une organisation EXISTANTE (ex: id=1) de façon idempotente :
  python backend/scripts/seed_fake_data.py --org-id 1 --employees 40 --meetings 15

Le script lit le schéma via les métadonnées de app.models (SQLAlchemy Core) : il n'a donc
pas besoin de connaître les noms de classes, et respecte les valeurs par défaut des colonnes.
"""
import argparse
import base64
import importlib
import json
import os
import pkgutil
import random
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import requests
from faker import Faker
from sqlalchemy import create_engine, func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # -> backend/

fake = Faker("fr_FR")
TIMEOUT = 8
DEFAULT_PASSWORD = "Password123!"


# --------------------------------------------------------------------------- utilitaires
def load_metadata():
    """Importe tous les modules de app.models et retourne le MetaData SQLAlchemy."""
    pkg = importlib.import_module("app.models")
    if hasattr(pkg, "__path__"):
        for m in pkgutil.iter_modules(pkg.__path__):
            importlib.import_module(f"app.models.{m.name}")
    for obj in list(vars(pkg).values()):
        if hasattr(obj, "__table__"):
            return obj.__table__.metadata
    for mod in list(sys.modules.values()):
        for obj in list(vars(mod).values()) if mod and mod.__name__.startswith("app.models") else []:
            if hasattr(obj, "__table__"):
                return obj.__table__.metadata
    raise RuntimeError("Impossible de trouver le MetaData dans app.models")


def load_env():
    """Charge backend/.env puis le .env à la racine (sans écraser les variables déjà définies)."""
    root = Path(__file__).resolve().parents[2]
    for f in (root / "backend" / ".env", root / ".env"):
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.replace("export ", "").strip()
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))


def get_database_url():
    for key in ("DATABASE_URL", "SQLALCHEMY_DATABASE_URI", "POSTGRES_URL", "DB_URL"):
        if os.environ.get(key):
            url = os.environ[key]
            break
    else:
        raise SystemExit("Aucune URL de base trouvée dans .env (DATABASE_URL, SQLALCHEMY_DATABASE_URI, "
                         "POSTGRES_URL ou DB_URL). Ajoutez-la ou définissez $env:DATABASE_URL.")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    url = url.replace("+asyncpg", "+psycopg2").replace("+aiosqlite", "")  # le script est synchrone
    return url


def api_get(url, **params):
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        print(f"  [info] API indisponible ({url}): {exc} -> repli sur Faker")
        return None


def fetch_people(n):
    data = api_get("https://randomuser.me/api/", results=n, nat="fr", seed="jarvis",
                   inc="name,email,dob,registered")
    people = []
    if data and data.get("results"):
        for p in data["results"]:
            people.append({
                "name": f"{p['name']['first']} {p['name']['last']}",
                "email": p["email"],
                "hire": datetime.fromisoformat(p["registered"]["date"].replace("Z", "+00:00")).date(),
            })
    while len(people) < n:
        people.append({"name": fake.name(), "email": fake.unique.email(),
                       "hire": fake.date_between("-6y", "today")})
    return people[:n]


def fetch_holidays(year):
    data = api_get(f"https://date.nager.at/api/v3/PublicHolidays/{year}/FR")
    return {date.fromisoformat(h["date"]) for h in data} if data else set()


def fetch_texts(n):
    data = api_get("https://jsonplaceholder.typicode.com/comments")
    texts = [c["body"].replace("\n", " ") for c in data] if data else []
    return texts or [fake.paragraph(4) for _ in range(n)]


def fake_encrypt(payload: str) -> str:
    try:
        from cryptography.fernet import Fernet
        return Fernet(Fernet.generate_key()).encrypt(payload.encode()).decode()
    except Exception:  # noqa: BLE001
        return "fake:" + base64.b64encode(payload.encode()).decode()


def hash_password(pw: str) -> str:
    try:
        from passlib.hash import bcrypt
        return bcrypt.hash(pw)
    except Exception:  # noqa: BLE001
        import hashlib
        return "pbkdf2$" + hashlib.pbkdf2_hmac("sha256", pw.encode(), b"seed", 1000).hex()


def utcnow():
    return datetime.utcnow()


def rand_dt(days_back=180):
    return utcnow() - timedelta(days=random.randint(0, days_back), seconds=random.randint(0, 86400))


class Seeder:
    def __init__(self, conn, md):
        self.conn, self.t = conn, md.tables

    def one(self, table, **vals):
        res = self.conn.execute(self.t[table].insert().values(**vals))
        return res.inserted_primary_key[0]

    def exists(self, table, **conds):
        t = self.t[table]
        q = select(t.c.id).where(*[t.c[k] == v for k, v in conds.items()]).limit(1)
        return self.conn.execute(q).first() is not None

    def many(self, table, rows):
        if rows:
            self.conn.execute(self.t[table].insert(), rows)

    # --- helpers idempotents -------------------------------------------------
    def get_or_create(self, table, defaults=None, **conds):
        """Retourne l'id existant si trouvé, sinon insère avec defaults + conds."""
        t = self.t[table]
        q = select(t.c.id).where(*[t.c[k] == v for k, v in conds.items()]).limit(1)
        row = self.conn.execute(q).first()
        if row:
            return row[0], False
        vals = dict(conds)
        if defaults:
            vals.update(defaults)
        return self.one(table, **vals), True

    def filtered_rows(self, table, rows, key_fields):
        """Retourne uniquement les lignes dont les clés (key_fields) ne sont pas déjà en base."""
        if not rows:
            return []
        t = self.t[table]
        new_rows = []
        for r in rows:
            conds = [t.c[k] == r[k] for k in key_fields]
            q = select(t.c.id).where(*conds).limit(1)
            if self.conn.execute(q).first() is None:
                new_rows.append(r)
        return new_rows


# --------------------------------------------------------------------------- seed
def seed_org(s, args, holidays, texts):
    if args.org_id:
        org_id = args.org_id
        if not s.exists("organizations", id=org_id):
            raise SystemExit(f"Organisation id={org_id} introuvable en base.")
    else:
        org_id = s.one("organizations", name=fake.company(), created_at=rand_dt(700))

    # users + sessions
    user_ids = []
    if args.org_id:
        # réutilise les utilisateurs existants de l'organisation
        ut = s.t["users"]
        rows = s.conn.execute(select(ut.c.id, ut.c.role).where(ut.c.org_id == org_id)).all()
        if not rows:
            raise SystemExit(f"Aucun utilisateur trouvé pour org_id={org_id}. "
                             f"Créez au moins un utilisateur dans cette organisation.")
        user_ids = [r[0] for r in rows]
        admin = getattr(args, "admin_id", None) or next(
            (r[0] for r in rows if r[1] == "admin"), user_ids[0]
        )
    else:
        for role in ["admin", "admin", "hr", "manager", "viewer"][: args.users]:
            uid = s.one("users", email=fake.unique.company_email(),
                        password_hash=hash_password(DEFAULT_PASSWORD),
                        org_id=org_id, role=role, created_at=rand_dt(400))
            user_ids.append(uid)
            for _ in range(random.randint(1, 3)):
                created = datetime.now(timezone.utc) - timedelta(days=random.randint(0, 30))
                revoked = created + timedelta(hours=random.randint(1, 72)) if random.random() < 0.5 else None
                s.one("auth_sessions", id=str(uuid.uuid4()), user_id=uid,
                      created_at=created, revoked_at=revoked)
        admin = user_ids[0]

    # employees (hiérarchie : ~12 % de managers) — idempotent via external_id
    people = fetch_people(args.employees)
    teams = ["Engineering", "Sales", "Marketing", "Support", "Finance", "RH", "Produit", "Data"]
    roles = ["Développeur", "Data Analyst", "Account Executive", "Designer", "Comptable",
             "Customer Success", "Product Manager", "Recruteur"]
    emp_ids, managers = [], []
    n_mgr = max(1, args.employees // 8)
    for i, p in enumerate(people):
        is_mgr = i < n_mgr
        external_id = f"HR-{org_id}-{i:04d}"
        manager_id = None if is_mgr else (random.choice(managers) if managers else None)
        eid, _ = s.get_or_create(
            "employees",
            defaults=dict(
                org_id=org_id, name=p["name"],
                role="Manager" if is_mgr else random.choice(roles),
                team=random.choice(teams),
                manager_id=manager_id,
                hire_date=p["hire"],
                status=random.choices(["active", "on_leave", "terminated"], [88, 7, 5])[0],
                data_contract_version="hr.v1",
                created_at=rand_dt(700),
            ),
            external_id=external_id,
        )
        emp_ids.append(eid)
        if is_mgr:
            managers.append(eid)

    # identifiants externes + absences (idempotent via external_id)
    integrations = ["bamboohr", "personio", "slack", "google_calendar"]
    ext_rows, abs_rows = [], []
    for eid in emp_ids:
        for key in random.sample(integrations, k=random.randint(1, 2)):
            ext_rows.append(dict(org_id=org_id, integration_key=key,
                                 external_id=f"{key}-{org_id}-{eid}",
                                 employee_id=eid, created_at=utcnow(), updated_at=utcnow()))
        for k in range(random.randint(0, 4)):
            start = date.today() - timedelta(days=random.randint(0, 300))
            while start.weekday() >= 5 or start in holidays:
                start += timedelta(days=1)
            half = random.random() < 0.15
            length = 1 if half else random.randint(1, 10)
            abs_rows.append(dict(org_id=org_id, employee_id=eid, source_key="bamboohr",
                                 external_id=f"abs-{eid}-{k}", start_date=start,
                                 end_date=start + timedelta(days=length - 1),
                                 status=random.choice(["approved", "pending", "rejected",
                                                       "cancelled", "unknown"]),
                                 duration_days=0.5 if half else float(length),
                                 is_half_day=half, contract_version="hr.v1",
                                 source_updated_at=rand_dt(60), synced_at=utcnow()))
    s.many("employee_external_ids",
           s.filtered_rows("employee_external_ids", ext_rows,
                           ["org_id", "integration_key", "external_id"]))
    s.many("employee_absences",
           s.filtered_rows("employee_absences", abs_rows,
                           ["org_id", "source_key", "external_id"]))

    # intégrations (déjà protégé par exists sur credentials)
    for key in integrations:
        if s.exists("integration_credentials", org_id=org_id, integration_key=key):
            continue
        ok = random.random() < 0.8
        s.one("integration_credentials", org_id=org_id, integration_key=key,
              encrypted_credentials=fake_encrypt(json.dumps({"api_key": fake.sha1()})),
              updated_by=admin,
              connection_status="connected" if ok else "error",
              connection_checked_at=rand_dt(5),
              connection_error=None if ok else "401 Unauthorized",
              created_at=utcnow(), updated_at=utcnow())
        status = random.choice(["idle", "queued", "running", "succeeded", "failed"])
        s.one("integration_syncs", org_id=org_id, integration_key=key, status=status,
              task_id=str(uuid.uuid4()), records_synced=random.randint(0, 500),
              absence_records_synced=random.randint(0, 200),
              data_coverage={"employees": round(random.uniform(.6, 1), 2),
                             "absences": round(random.uniform(.4, 1), 2)},
              last_started_at=rand_dt(7),
              last_success_at=rand_dt(14) if status != "failed" else None,
              last_error="Timeout API" if status == "failed" else None,
              created_at=utcnow(), updated_at=utcnow())

    # réunions (idempotent : pas de clé naturelle -> on ne crée que si meetings est vide pour l'org)
    names = [p["name"] for p in people]
    mt = s.t["meetings"]
    existing_meetings = s.conn.execute(
        select(func.count()).select_from(mt).where(mt.c.org_id == org_id)
    ).scalar() or 0
    if existing_meetings == 0:
        for _ in range(args.meetings):
            analyzed = rand_dt(90)
            s.one("meetings", org_id=org_id,
                  source=random.choice(["zoom", "teams", "meet"]),
                  title=fake.sentence(nb_words=4).rstrip("."),
                  participants=random.sample(names, k=min(4, len(names))),
                  transcript=" ".join(random.sample(texts, k=min(6, len(texts)))),
                  decisions=[fake.sentence() for _ in range(random.randint(1, 3))],
                  action_items=[{"owner": random.choice(names), "task": fake.sentence(),
                                 "due": str(fake.future_date("+30d"))}
                                for _ in range(random.randint(1, 4))],
                  risk_signals=random.sample(["turnover", "burnout", "conflit",
                                              "désengagement", "surcharge"],
                                             k=random.randint(0, 3)),
                  analyzed_at=analyzed,
                  reviewed_at=analyzed + timedelta(days=1) if random.random() < .5 else None,
                  reviewed_by=admin if random.random() < .5 else None,
                  created_at=analyzed - timedelta(hours=1))
    else:
        print(f"  [info] {existing_meetings} réunion(s) déjà présente(s) pour org {org_id} -> skip")

    # signaux (pas de clé naturelle : on n'insère que si l'employé n'a pas déjà de signaux)
    sig_types = ["engagement", "overtime_hours", "absence_rate", "survey_sentiment",
                 "meeting_load", "pulse_nps"]
    sig_rows, drivers_pool = [], ["overtime", "low_engagement", "absences",
                                  "no_promotion", "manager_change"]
    st = s.t["signals"]
    rst = s.t["risk_scores"]
    for eid in emp_ids:
        already = s.conn.execute(
            select(func.count()).select_from(st).where(st.c.employee_id == eid)
        ).scalar() or 0
        if already == 0:
            for _ in range(args.signals_per_employee):
                sig_rows.append(dict(employee_id=eid,
                                     source=random.choice(["hris", "survey", "calendar", "slack"]),
                                     type=random.choice(sig_types),
                                     value=round(random.uniform(0, 100), 2),
                                     weight=round(random.uniform(.5, 2), 2),
                                     captured_at=rand_dt(120)))
        rs_already = s.conn.execute(
            select(func.count()).select_from(rst).where(rst.c.employee_id == eid)
        ).scalar() or 0
        if rs_already == 0:
            score = round(random.uniform(0, 100), 1)
            level = ("low" if score < 30 else "medium" if score < 60
                     else "high" if score < 80 else "critical")
            s.one("risk_scores", employee_id=eid, score=score, level=level,
                  confidence=round(random.uniform(.5, .99), 2),
                  drivers=[{"name": d, "impact": round(random.uniform(0, 1), 2)}
                           for d in random.sample(drivers_pool, 3)],
                  canonical_context={"tenure_months": random.randint(1, 120),
                                     "team": random.choice(teams)},
                  recommendation=random.choice(["one_to_one", "workload_review",
                                                "career_discussion", "monitor"]),
                  created_at=rand_dt(30))
    s.many("signals", sig_rows)

    # insights (idempotent : créés une seule fois par org)
    it = s.t["insights"]
    existing_insights = s.conn.execute(
        select(func.count()).select_from(it).where(it.c.org_id == org_id)
    ).scalar() or 0
    if existing_insights == 0:
        for _ in range(10):
            s.one("insights", org_id=org_id,
                  type=random.choice(["turnover", "absenteeism", "engagement"]),
                  title=fake.sentence(nb_words=6), description=fake.paragraph(2),
                  data={"value": round(random.uniform(0, 100), 1)},
                  severity=random.choice(["info", "warning", "critical"]),
                  created_at=rand_dt(60))

    # onboarding (idempotent via (org_id, employee_id))
    task_titles = [("Signer le contrat", "admin", "hr"),
                   ("Configurer le poste", "it", "it"),
                   ("Rencontre avec l'équipe", "culture", "manager"),
                   ("Formation sécurité", "compliance", "employee"),
                   ("Point à 30 jours", "feedback", "manager")]
    sample_size = max(1, len(emp_ids) // 5)
    for eid in random.sample(emp_ids, k=min(sample_size, len(emp_ids))):
        pid, created = s.get_or_create(
            "onboarding_plans",
            defaults=dict(
                org_id=org_id, employee_id=eid,
                start_date=date.today() - timedelta(days=random.randint(0, 60)),
                status="active", created_by=admin,
                created_at=utcnow(), updated_at=utcnow(), completed_at=None,
            ),
            org_id=org_id, employee_id=eid,
        )
        if not created:
            continue
        done = random.random() < 0.3
        # met à jour le plan si done
        if done:
            s.conn.execute(
                s.t["onboarding_plans"].update().where(s.t["onboarding_plans"].c.id == pid)
                .values(status="completed", completed_at=utcnow(), updated_at=utcnow())
            )
        for order, (title, cat, owner) in enumerate(task_titles):
            s.one("onboarding_tasks", plan_id=pid, title=title,
                  description=fake.sentence(), category=cat,
                  owner_type=owner, due_date=date.today() + timedelta(days=order * 10),
                  status="done" if done or random.random() < .4 else "pending",
                  sort_order=order, created_at=utcnow(),
                  completed_at=utcnow() if done else None)

    # gouvernance : audit_events (idempotent : seulement si aucun pour l'org)
    ae = s.t["audit_events"]
    existing_ae = s.conn.execute(
        select(func.count()).select_from(ae).where(ae.c.org_id == org_id)
    ).scalar() or 0
    if existing_ae == 0:
        s.many("audit_events", [
            dict(org_id=org_id, actor_user_id=random.choice(user_ids),
                 event_type=random.choice(["login", "employee.update", "sync.start", "export.csv"]),
                 resource_type=random.choice(["employee", "meeting", "integration"]),
                 resource_id=str(random.randint(1, 99)),
                 outcome=random.choice(["success", "success", "denied"]),
                 details={"ip": fake.ipv4()}, created_at=rand_dt(60))
            for _ in range(args.audit_events)
        ])

    for name in ["SMTP_PASSWORD", "WEBHOOK_SECRET", "OPENAI_API_KEY"]:
        if s.exists("organization_secrets", org_id=org_id, name=name):
            continue
        s.one("organization_secrets", org_id=org_id, name=name,
              description=fake.sentence(nb_words=4),
              encrypted_value=fake_encrypt(fake.sha256()),
              version=1, created_by=admin, updated_by=admin,
              created_at=utcnow(), updated_at=utcnow())

    combos = {(r, res, a) for r in ["hr", "manager", "viewer"]
              for res in ["employees", "meetings", "risk_scores"]
              for a in ["read", "write", "export"]}
    free = [c for c in sorted(combos) if not s.exists(
        "governance_access_rules", org_id=org_id,
        role=c[0], resource=c[1], action=c[2])]
    for role, res, act in random.sample(free, k=min(12, len(free))):
        s.one("governance_access_rules", org_id=org_id, role=role,
              resource=res, action=act,
              effect="deny" if act == "export" else "allow",
              enabled=True, created_by=admin,
              created_at=utcnow(), updated_at=utcnow())

    # budget policies : nom DÉTERMINISTE pour être idempotent
    budget_scopes = [("organization", None), ("model", "claude-sonnet"),
                     ("user", str(admin))]
    for i, (scope, key) in enumerate(budget_scopes):
        policy_name = f"Budget demo #{i + 1}"
        pid, created = s.get_or_create(
            "governance_budget_policies",
            defaults=dict(
                org_id=org_id, meter="llm_cost", unit="EUR",
                limit_amount=Decimal(random.choice([50, 200, 1000])),
                period=random.choice(["daily", "monthly"]),
                scope=scope, scope_key=key,
                hard_limit=random.random() < .5, enabled=True,
                created_by=admin, created_at=utcnow(), updated_at=utcnow(),
            ),
            org_id=org_id, name=policy_name,
        )
        if not created:
            continue
        s.many("governance_budget_usage", [
            dict(policy_id=pid, org_id=org_id,
                 amount=Decimal(str(round(random.uniform(.01, 5), 6))),
                 occurred_at=rand_dt(30), source="application",
                 idempotency_key=str(uuid.uuid4()),
                 metadata_json={"model": "demo"})
            for _ in range(30)
        ])

    for key in ["pii_masking", "export_limit", "llm_guardrails"]:
        if s.exists("governance_controls", org_id=org_id, control_key=key):
            continue
        s.one("governance_controls", org_id=org_id, control_key=key, enabled=True,
              mode=random.choice(["monitor", "block"]),
              settings={"threshold": random.randint(1, 10)},
              updated_by=admin, created_at=utcnow(), updated_at=utcnow())

    # approval requests : pas de clé naturelle -> seulement si aucun pour l'org
    ar = s.t["governance_approval_requests"]
    existing_ar = s.conn.execute(
        select(func.count()).select_from(ar).where(ar.c.org_id == org_id)
    ).scalar() or 0
    if existing_ar == 0:
        for _ in range(6):
            status = random.choice(["pending", "approved", "rejected"])
            s.one("governance_approval_requests", org_id=org_id,
                  action_key=random.choice(["export.employees", "delete.meeting"]),
                  resource_type="employee", resource_id=str(random.randint(1, 99)),
                  requested_by=random.choice(user_ids), status=status,
                  reviewed_by=admin if status != "pending" else None,
                  reviewer_note=fake.sentence() if status != "pending" else None,
                  created_at=rand_dt(30),
                  reviewed_at=utcnow() if status != "pending" else None)

    if not s.exists("governance_retention_policies", org_id=org_id):
        s.one("governance_retention_policies", org_id=org_id,
              meetings_days=365, signals_days=730, risk_scores_days=730,
              insights_days=365, completed_onboarding_days=180,
              updated_by=admin, created_at=utcnow(), updated_at=utcnow())

    return org_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orgs", type=int, default=2)
    ap.add_argument("--admin-email", default=None,
                    help="email d'un utilisateur existant : remplit l'organisation de cet utilisateur")
    ap.add_argument("--org-id", type=int, default=None,
                    help="remplit une organisation existante (et ses utilisateurs) au lieu d'en créer")
    ap.add_argument("--employees", type=int, default=40, help="par organisation")
    ap.add_argument("--users", type=int, default=4, help="par organisation (max 5)")
    ap.add_argument("--meetings", type=int, default=15)
    ap.add_argument("--signals-per-employee", type=int, default=8)
    ap.add_argument("--audit-events", type=int, default=50)
    ap.add_argument("--create-all", action="store_true", help="crée les tables si absentes")
    ap.add_argument("--no-api", action="store_true", help="n'appelle aucune API externe")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    random.seed(args.seed)
    Faker.seed(args.seed)

    md = load_metadata()
    load_env()
    url = get_database_url()
    print("Base cible :", url.split("@")[-1] if "@" in url else url)  # sans identifiants
    engine = create_engine(url)
    if args.create_all:
        md.create_all(engine)

    if args.no_api:
        global api_get
        api_get = lambda *a, **k: None  # noqa: E731

    holidays = fetch_holidays(date.today().year)
    texts = fetch_texts(50)

    with engine.begin() as conn:  # transaction unique : tout ou rien
        s = Seeder(conn, md)
        if args.admin_email:
            ut = md.tables["users"]
            row = conn.execute(select(ut.c.id, ut.c.org_id)
                               .where(func.lower(ut.c.email) == args.admin_email.lower())).first()
            if not row:
                raise SystemExit(f"Aucun utilisateur avec l'email {args.admin_email}")
            args.admin_id, args.org_id = row
            print(f"Utilisateur {args.admin_email} trouvé -> organisation {args.org_id}")
        for _ in range(1 if args.org_id else args.orgs):
            oid = seed_org(s, args, holidays, texts)
            print(f"Organisation {oid} remplie")
    if not args.org_id and not args.admin_email:
        print(f"Terminé. Mot de passe de tous les utilisateurs : {DEFAULT_PASSWORD}")


if __name__ == "__main__":
    main()