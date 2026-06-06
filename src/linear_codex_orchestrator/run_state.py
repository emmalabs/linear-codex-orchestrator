from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .config import config_path


@dataclass(frozen=True)
class IssueRunState:
    issue_id: str
    issue_identifier: str
    workspace_path: str
    branch: str
    stage: str
    plan: str = ""
    implementation_summary: str = ""


def read_issue_run_state(issue_id: str, workspace_path: Path) -> IssueRunState | None:
    with connect() as connection:
        ensure_run_state_schema(connection)
        row = connection.execute(
            """
            select issue_id, issue_identifier, workspace_path, branch, stage, plan, implementation_summary
            from issue_run_state
            where issue_id = ? and workspace_path = ?
            """,
            (issue_id, str(workspace_path)),
        ).fetchone()
    if row is None:
        return None
    return IssueRunState(
        issue_id=str(row[0]),
        issue_identifier=str(row[1]),
        workspace_path=str(row[2]),
        branch=str(row[3]),
        stage=str(row[4]),
        plan=str(row[5] or ""),
        implementation_summary=str(row[6] or ""),
    )


def write_issue_run_state(
    issue_id: str,
    issue_identifier: str,
    workspace_path: Path,
    branch: str,
    stage: str,
    *,
    plan: str = "",
    implementation_summary: str = "",
) -> None:
    with connect() as connection:
        ensure_run_state_schema(connection)
        connection.execute(
            """
            insert into issue_run_state(
              issue_id, issue_identifier, workspace_path, branch, stage, plan, implementation_summary, updated_at
            )
            values (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            on conflict(issue_id, workspace_path) do update set
              issue_identifier = excluded.issue_identifier,
              branch = excluded.branch,
              stage = excluded.stage,
              plan = case
                when excluded.plan != '' then excluded.plan
                else issue_run_state.plan
              end,
              implementation_summary = case
                when excluded.implementation_summary != '' then excluded.implementation_summary
                else issue_run_state.implementation_summary
              end,
              updated_at = excluded.updated_at
            """,
            (
                issue_id,
                issue_identifier,
                str(workspace_path),
                branch,
                stage,
                plan,
                implementation_summary,
            ),
        )


def clear_issue_run_state(issue_id: str, workspace_path: Path) -> None:
    with connect() as connection:
        ensure_run_state_schema(connection)
        connection.execute(
            "delete from issue_run_state where issue_id = ? and workspace_path = ?",
            (issue_id, str(workspace_path)),
        )


def ensure_run_state_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        create table if not exists issue_run_state (
          issue_id text not null,
          issue_identifier text not null,
          workspace_path text not null,
          branch text not null,
          stage text not null,
          plan text not null default '',
          implementation_summary text not null default '',
          updated_at text not null default (datetime('now')),
          primary key(issue_id, workspace_path)
        )
        """
    )


def connect() -> sqlite3.Connection:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path)
