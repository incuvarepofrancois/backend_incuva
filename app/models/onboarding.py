from datetime import datetime

from app import db


class OnboardingPlan(db.Model):
    __tablename__ = "onboarding_plans"

    id = db.Column(db.Integer, primary_key=True)
    org_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False, index=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    start_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="active")
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

    employee = db.relationship("Employee")
    tasks = db.relationship(
        "OnboardingTask",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="OnboardingTask.sort_order, OnboardingTask.id",
        lazy="selectin",
    )

    def to_dict(self):
        total = len(self.tasks)
        completed = sum(task.status == "completed" for task in self.tasks)
        return {
            "id": self.id,
            "org_id": self.org_id,
            "employee_id": self.employee_id,
            "employee": self.employee.to_dict() if self.employee else None,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "status": self.status,
            "progress": round(completed / total * 100) if total else 0,
            "completed_tasks": completed,
            "total_tasks": total,
            "tasks": [task.to_dict() for task in self.tasks],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class OnboardingTask(db.Model):
    __tablename__ = "onboarding_tasks"

    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey("onboarding_plans.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.String(1000), nullable=True)
    category = db.Column(db.String(50), nullable=False, default="general")
    owner_type = db.Column(db.String(20), nullable=False, default="manager")
    due_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

    plan = db.relationship("OnboardingPlan", back_populates="tasks")

    def to_dict(self):
        return {
            "id": self.id,
            "plan_id": self.plan_id,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "owner_type": self.owner_type,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "status": self.status,
            "sort_order": self.sort_order,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
