"""Import every model so `Base.metadata` is fully populated for Alembic
autogenerate and for `Base.metadata.create_all()` in tests."""
from app.db.base import Base
from app.models.analysis import Analysis
from app.models.finding import Finding
from app.models.github_account import GitHubInstallation, GitHubToken
from app.models.job import Job
from app.models.repository import Repository
from app.models.step import Step
from app.models.user import User
from app.models.workflow import Workflow
from app.models.workflow_run import WorkflowRun

__all__ = [
    "Base",
    "User",
    "GitHubToken",
    "GitHubInstallation",
    "Repository",
    "Workflow",
    "WorkflowRun",
    "Job",
    "Step",
    "Analysis",
    "Finding",
]
