"""Read-only system and synchronization status for the Tools screen."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from football_analytics.database.models import (
    Competition,
    CompetitionSeason,
    DataQualityIssue,
    DataSource,
    SyncRun,
    SyncRunItem,
)


@dataclass(frozen=True, slots=True)
class SyncHistoryItem:
    started_at: datetime
    mode: str
    status: str
    requests_count: int
    inserted_count: int
    updated_count: int
    error_count: int


@dataclass(frozen=True, slots=True)
class SourceStatusItem:
    name: str
    priority: int
    requires_api_key: bool
    last_success_at: datetime | None
    attribution: str | None


@dataclass(frozen=True, slots=True)
class SyncErrorItem:
    started_at: datetime
    competition_code: str | None
    source_name: str
    error_code: str | None
    message: str | None


@dataclass(frozen=True, slots=True)
class ToolsSnapshot:
    database_version: str
    database_name: str
    latest_data_update: datetime | None
    sync_history: list[SyncHistoryItem]
    sources: list[SourceStatusItem]
    open_quality_issues: int
    recent_errors: list[SyncErrorItem]


class ToolsService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_snapshot(self) -> ToolsSnapshot:
        database_version = self.session.execute(text("SELECT version()")).scalar_one()
        database_name = self.session.execute(text("SELECT current_database()")).scalar_one()
        latest_data_update = self.session.scalar(select(func.max(CompetitionSeason.last_synced_at)))
        sync_history = [
            SyncHistoryItem(
                run.started_at,
                run.mode,
                run.status,
                run.requests_count,
                run.inserted_count,
                run.updated_count,
                run.error_count,
            )
            for run in self.session.scalars(
                select(SyncRun).order_by(SyncRun.started_at.desc()).limit(15)
            ).all()
        ]
        source_rows = self.session.execute(
            select(DataSource, func.max(SyncRun.finished_at).label("last_success"))
            .outerjoin(SyncRunItem, SyncRunItem.source_id == DataSource.id)
            .outerjoin(
                SyncRun,
                (SyncRun.id == SyncRunItem.sync_run_id) & (SyncRunItem.status == "SUCCESS"),
            )
            .group_by(DataSource.id)
            .order_by(DataSource.priority)
        ).all()
        sources = [
            SourceStatusItem(
                source.name,
                source.priority,
                source.requires_api_key,
                last_success,
                source.attribution_text or source.license_name,
            )
            for source, last_success in source_rows
        ]
        open_issues = self.session.scalar(
            select(func.count(DataQualityIssue.id)).where(DataQualityIssue.status == "OPEN")
        )
        errors = self.session.execute(
            select(
                SyncRun.started_at,
                Competition.code,
                DataSource.name,
                SyncRunItem.error_code,
                SyncRunItem.message,
            )
            .join(SyncRunItem, SyncRunItem.sync_run_id == SyncRun.id)
            .outerjoin(Competition, Competition.id == SyncRunItem.competition_id)
            .join(DataSource, DataSource.id == SyncRunItem.source_id)
            .where(SyncRunItem.status == "ERROR")
            .order_by(SyncRun.started_at.desc())
            .limit(20)
        ).all()
        recent_errors = [SyncErrorItem(*row) for row in errors]
        return ToolsSnapshot(
            database_version,
            database_name,
            latest_data_update,
            sync_history,
            sources,
            open_issues or 0,
            recent_errors,
        )
