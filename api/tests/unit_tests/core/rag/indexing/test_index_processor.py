import datetime
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.rag.index_processor.constant.index_type import IndexTechniqueType
from core.rag.index_processor.index_processor import IndexProcessor
from models.dataset import Dataset, Document


class TestIndexProcessor:
    def test_format_preview_supports_qa_preview_shape(self) -> None:
        preview = IndexProcessor().format_preview(
            "qa_model",
            {"qa_chunks": [{"question": "Q1", "answer": "A1"}]},
        )

        assert preview.chunk_structure == "qa_model"
        assert preview.total_segments == 1
        assert len(preview.qa_preview) == 1
        assert preview.qa_preview[0].question == "Q1"
        assert preview.qa_preview[0].answer == "A1"

    def test_index_and_clean_scopes_document_and_segments_to_dataset_owner(self) -> None:
        dataset = SimpleNamespace(
            id="dataset-1",
            tenant_id="tenant-1",
            name="Dataset",
            chunk_structure="text_model",
            summary_index_setting=None,
        )
        document = SimpleNamespace(
            id="document-1",
            name="Document",
            created_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
            indexing_latency=None,
            indexing_status=None,
            completed_at=None,
            word_count=0,
            need_summary=False,
        )
        initial_session = MagicMock()
        initial_session.scalar.side_effect = [dataset, document]
        initial_session.scalars.return_value.all.return_value = [SimpleNamespace(index_node_id="node-1")]
        delete_session = MagicMock()
        delete_session.begin.return_value = nullcontext()
        final_session = MagicMock()
        final_session.begin.return_value = nullcontext()
        final_session.scalar.return_value = 3
        session_contexts = iter(
            [nullcontext(initial_session), nullcontext(delete_session), nullcontext(final_session)]
        )

        with (
            patch(
                "core.rag.index_processor.index_processor.session_factory.create_session",
                side_effect=lambda: next(session_contexts),
            ),
            patch("core.rag.index_processor.index_processor.IndexProcessorFactory") as processor_factory,
        ):
            IndexProcessor().index_and_clean(
                dataset_id="dataset-1",
                document_id="document-1",
                original_document_id="original-document",
                chunks={},
                batch="batch-1",
            )

        document_statement = initial_session.scalar.call_args_list[1].args[0]
        segment_statement = initial_session.scalars.call_args.args[0]
        delete_statement = delete_session.execute.call_args.args[0]
        word_count_statement = final_session.scalar.call_args.args[0]
        update_statement = final_session.execute.call_args.args[0]
        document_owner = {"document-1", "dataset-1", "tenant-1"}
        original_owner = {"original-document", "dataset-1", "tenant-1"}

        assert document_owner <= set(document_statement.compile().params.values())
        assert original_owner <= set(segment_statement.compile().params.values())
        assert original_owner <= set(delete_statement.compile().params.values())
        assert document_owner <= set(word_count_statement.compile().params.values())
        assert document_owner <= set(update_statement.compile().params.values())
        processor_factory.return_value.init_index_processor.return_value.clean.assert_called_once_with(
            dataset,
            ["node-1"],
            with_keywords=True,
            delete_child_chunks=True,
        )

    def test_get_preview_output_scopes_document_to_dataset_owner(self) -> None:
        dataset = SimpleNamespace(
            id="dataset-1",
            tenant_id="tenant-1",
            indexing_technique=IndexTechniqueType.ECONOMY,
            summary_index_setting=None,
        )
        document = SimpleNamespace(doc_language="English")
        session = MagicMock()
        session.scalar.side_effect = [dataset, document]

        with (
            patch(
                "core.rag.index_processor.index_processor.session_factory.create_session",
                return_value=nullcontext(session),
            ),
            patch.object(IndexProcessor, "format_preview", return_value=MagicMock()) as format_preview,
        ):
            result = IndexProcessor().get_preview_output(
                chunks={},
                dataset_id="dataset-1",
                document_id="document-1",
                chunk_structure="text_model",
                summary_index_setting=None,
            )

        document_statement = session.scalar.call_args_list[1].args[0]
        assert {"document-1", "dataset-1", "tenant-1"} <= set(document_statement.compile().params.values())
        assert result is format_preview.return_value
        assert document_statement.column_descriptions[0]["entity"] is Document
        assert session.scalar.call_args_list[0].args[0].column_descriptions[0]["entity"] is Dataset
