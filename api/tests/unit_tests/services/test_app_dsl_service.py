from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest

from graphon.enums import BuiltinNodeTypes
from models import App
from services import app_dsl_service
from services.app_dsl_service import AppDslService
from services.entities.dsl_entities import ImportStatus
from services.errors.app import WorkflowAgentNodeDslExportUnsupportedError


def test_import_app_rejects_oversized_yaml_content_before_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("services.app_dsl_service.DSL_MAX_SIZE", 3)
    service = AppDslService(session=Mock())
    account = Mock(current_tenant_id="tenant-1")

    result = service.import_app(account=account, import_mode="yaml-content", yaml_content="你你")

    assert result.status == ImportStatus.FAILED
    assert result.error == "File size exceeds the limit of 10MB"


def test_import_app_rejects_oversized_yaml_url_bytes_before_decode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("services.app_dsl_service.DSL_MAX_SIZE", 1)
    response = Mock()
    response.raise_for_status.return_value = None
    response.content = b"\xff\xff"
    monkeypatch.setattr("services.app_dsl_service.remote_fetcher.make_request", Mock(return_value=response))
    service = AppDslService(session=Mock())

    result = service.import_app(
        account=Mock(current_tenant_id="tenant-1"),
        import_mode="yaml-url",
        yaml_url="https://example.com/app.yaml",
    )

    assert result.status == ImportStatus.FAILED
    assert result.error == "File size exceeds the limit of 10MB"


def test_import_app_returns_decode_error_for_invalid_yaml_url_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    response = Mock()
    response.raise_for_status.return_value = None
    response.content = b"\xff"
    monkeypatch.setattr("services.app_dsl_service.remote_fetcher.make_request", Mock(return_value=response))
    service = AppDslService(session=Mock())

    result = service.import_app(
        account=Mock(current_tenant_id="tenant-1"),
        import_mode="yaml-url",
        yaml_url="https://example.com/app.yaml",
    )

    assert result.status == ImportStatus.FAILED
    assert "utf-8" in result.error


def test_append_workflow_export_data_rejects_agent_v2_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = SimpleNamespace(
        to_dict=lambda *, include_secret: {
            "graph": {
                "nodes": [
                    {
                        "id": "agent-node",
                        "data": {
                            "type": BuiltinNodeTypes.AGENT,
                            "version": "2",
                        },
                    }
                ]
            }
        }
    )
    workflow_service = Mock()
    workflow_service.get_draft_workflow.return_value = workflow
    monkeypatch.setattr(app_dsl_service, "WorkflowService", lambda: workflow_service)
    monkeypatch.setattr(AppDslService, "_extract_dependencies_from_workflow", Mock(return_value=[]))
    monkeypatch.setattr(app_dsl_service.DependenciesAnalysisService, "generate_dependencies", Mock(return_value=[]))

    with pytest.raises(WorkflowAgentNodeDslExportUnsupportedError, match="Agent v2 nodes"):
        AppDslService._append_workflow_export_data(
            export_data={},
            app_model=cast(App, SimpleNamespace(tenant_id="tenant-1")),
            include_secret=False,
            session=Mock(),
        )
