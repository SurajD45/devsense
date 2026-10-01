"""
Tests for ChangeImpactAnalysisService and its schemas
======================================================
Validates classification of PR changed files against a
RequirementAnalysis, including schema validation rules.
"""

import unittest
from pydantic import ValidationError

from app.domain.models import ChangedFile
from app.investigation.schemas import (
    AnalyzedCriterion,
    ChangeImpactAnalysis,
    ChangedFileClassification,
    FileClassification,
    RequirementAnalysis,
)
from app.investigation.service import ChangeImpactAnalysisService


# =========================================================================
# Schema Tests
# =========================================================================


class TestFileClassificationEnum(unittest.TestCase):
    """Tests for the FileClassification enum."""

    def test_primary_value(self):
        self.assertEqual(FileClassification.PRIMARY.value, "PRIMARY")

    def test_secondary_value(self):
        self.assertEqual(FileClassification.SECONDARY.value, "SECONDARY")

    def test_enum_from_string(self):
        self.assertEqual(FileClassification("PRIMARY"), FileClassification.PRIMARY)
        self.assertEqual(FileClassification("SECONDARY"), FileClassification.SECONDARY)

    def test_invalid_value_rejected(self):
        with self.assertRaises(ValueError):
            FileClassification("UNKNOWN")


class TestChangedFileClassificationSchema(unittest.TestCase):
    """Tests for the ChangedFileClassification DTO."""

    def test_valid_primary_classification(self):
        c = ChangedFileClassification(
            file_path="app/payment/service.py",
            classification=FileClassification.PRIMARY,
            reasons=["Path terms match requirement analysis: payment, service"],
        )
        self.assertEqual(c.file_path, "app/payment/service.py")
        self.assertEqual(c.classification, FileClassification.PRIMARY)
        self.assertEqual(len(c.reasons), 1)

    def test_valid_secondary_classification(self):
        c = ChangedFileClassification(
            file_path="docs/README.md",
            classification=FileClassification.SECONDARY,
            reasons=["Documentation file included in changeset"],
        )
        self.assertEqual(c.classification, FileClassification.SECONDARY)

    def test_classification_from_string(self):
        c = ChangedFileClassification(
            file_path="app/main.py",
            classification="PRIMARY",
            reasons=["match"],
        )
        self.assertEqual(c.classification, FileClassification.PRIMARY)

    def test_empty_file_path_rejected(self):
        with self.assertRaises(ValidationError):
            ChangedFileClassification(
                file_path="   ",
                classification=FileClassification.SECONDARY,
                reasons=["reason"],
            )

    def test_missing_file_path_rejected(self):
        with self.assertRaises(ValidationError):
            ChangedFileClassification(
                classification=FileClassification.SECONDARY,
                reasons=["reason"],
            )

    def test_empty_reasons_list_rejected(self):
        with self.assertRaises(ValidationError):
            ChangedFileClassification(
                file_path="app/main.py",
                classification=FileClassification.SECONDARY,
                reasons=[],
            )

    def test_whitespace_only_reason_rejected(self):
        with self.assertRaises(ValidationError):
            ChangedFileClassification(
                file_path="app/main.py",
                classification=FileClassification.SECONDARY,
                reasons=["   "],
            )

    def test_invalid_classification_value_rejected(self):
        with self.assertRaises(ValidationError):
            ChangedFileClassification(
                file_path="app/main.py",
                classification="UNKNOWN",
                reasons=["reason"],
            )

    def test_reasons_whitespace_stripped(self):
        c = ChangedFileClassification(
            file_path="app/main.py",
            classification=FileClassification.SECONDARY,
            reasons=["  reason one  ", "  reason two  "],
        )
        self.assertEqual(c.reasons, ["reason one", "reason two"])

    def test_model_dump(self):
        c = ChangedFileClassification(
            file_path="app/main.py",
            classification=FileClassification.PRIMARY,
            reasons=["match"],
        )
        data = c.model_dump()
        self.assertEqual(data["file_path"], "app/main.py")
        self.assertEqual(data["classification"], "PRIMARY")
        self.assertEqual(data["reasons"], ["match"])


class TestChangeImpactAnalysisSchema(unittest.TestCase):
    """Tests for the ChangeImpactAnalysis DTO."""

    def test_valid_analysis(self):
        a = ChangeImpactAnalysis(
            classifications=[
                ChangedFileClassification(
                    file_path="app/payment.py",
                    classification=FileClassification.PRIMARY,
                    reasons=["match"],
                ),
                ChangedFileClassification(
                    file_path="docs/README.md",
                    classification=FileClassification.SECONDARY,
                    reasons=["documentation"],
                ),
            ]
        )
        self.assertEqual(len(a.classifications), 2)

    def test_default_empty_classifications(self):
        a = ChangeImpactAnalysis()
        self.assertEqual(a.classifications, [])

    def test_duplicate_file_path_rejected(self):
        with self.assertRaises(ValidationError):
            ChangeImpactAnalysis(
                classifications=[
                    ChangedFileClassification(
                        file_path="app/main.py",
                        classification=FileClassification.PRIMARY,
                        reasons=["match"],
                    ),
                    ChangedFileClassification(
                        file_path="app/main.py",
                        classification=FileClassification.SECONDARY,
                        reasons=["duplicate"],
                    ),
                ]
            )


# =========================================================================
# Service Tests
# =========================================================================


class TestChangeImpactAnalysisService(unittest.TestCase):
    """Tests for ChangeImpactAnalysisService.analyze()."""

    def setUp(self):
        self.service = ChangeImpactAnalysisService()

    # -- helpers --

    def _make_analysis(self, **overrides):
        defaults = dict(
            requirement_id="PAY-142",
            summary="Requirement analysis: Add payment retry.",
            criteria=[
                AnalyzedCriterion(
                    criterion_id="AC-1",
                    interpretation="Retry failed payments up to 3 times",
                    verification_points=[
                        "Verify: retry occurs after payment failure",
                        "Verify: maximum retry count is three",
                    ],
                ),
            ],
        )
        defaults.update(overrides)
        return RequirementAnalysis(**defaults)

    def _make_changed_file(self, path, status="modified"):
        return ChangedFile(path=path, status=status)

    # -- directly relevant source file → PRIMARY --

    def test_directly_relevant_source_file_is_primary(self):
        analysis = self._make_analysis()
        files = [self._make_changed_file("app/payment/retry_handler.py")]
        result = self.service.analyze(analysis, files)

        self.assertEqual(len(result.classifications), 1)
        c = result.classifications[0]
        self.assertEqual(c.file_path, "app/payment/retry_handler.py")
        self.assertEqual(c.classification, FileClassification.PRIMARY)

    def test_file_matching_requirement_title_terms_is_primary(self):
        analysis = self._make_analysis()
        files = [self._make_changed_file("services/payment_service.py")]
        result = self.service.analyze(analysis, files)

        c = result.classifications[0]
        self.assertEqual(c.classification, FileClassification.PRIMARY)

    def test_primary_classification_includes_matching_terms(self):
        analysis = self._make_analysis()
        files = [self._make_changed_file("app/payment/retry.py")]
        result = self.service.analyze(analysis, files)

        c = result.classifications[0]
        self.assertEqual(c.classification, FileClassification.PRIMARY)
        self.assertTrue(any("payment" in r.lower() or "retry" in r.lower() for r in c.reasons))

    # -- related test file → PRIMARY --

    def test_test_file_for_primary_source_is_primary(self):
        """A test file whose inferred source is primary should be PRIMARY."""
        analysis = self._make_analysis()
        files = [
            self._make_changed_file("app/payment/retry.py"),
            self._make_changed_file("tests/payment/test_retry.py"),
        ]
        result = self.service.analyze(analysis, files)

        by_path = {c.file_path: c for c in result.classifications}
        self.assertEqual(
            by_path["app/payment/retry.py"].classification,
            FileClassification.PRIMARY,
        )
        self.assertEqual(
            by_path["tests/payment/test_retry.py"].classification,
            FileClassification.PRIMARY,
        )

    def test_test_file_matching_analysis_terms_is_primary(self):
        """A test file with path terms matching analysis terms is PRIMARY."""
        analysis = self._make_analysis()
        files = [self._make_changed_file("tests/test_payment.py")]
        result = self.service.analyze(analysis, files)

        c = result.classifications[0]
        self.assertEqual(c.classification, FileClassification.PRIMARY)

    # -- supporting documentation/configuration → SECONDARY --

    def test_documentation_file_is_secondary(self):
        analysis = self._make_analysis()
        files = [self._make_changed_file("docs/architecture.md")]
        result = self.service.analyze(analysis, files)

        c = result.classifications[0]
        self.assertEqual(c.classification, FileClassification.SECONDARY)
        self.assertTrue(any("documentation" in r.lower() for r in c.reasons))

    def test_config_file_is_secondary(self):
        analysis = self._make_analysis()
        files = [self._make_changed_file("config/settings.yaml")]
        result = self.service.analyze(analysis, files)

        c = result.classifications[0]
        self.assertEqual(c.classification, FileClassification.SECONDARY)
        self.assertTrue(any("configuration" in r.lower() for r in c.reasons))

    def test_migration_file_is_secondary(self):
        analysis = self._make_analysis()
        files = [self._make_changed_file("db/migrations/001_add_column.sql")]
        result = self.service.analyze(analysis, files)

        c = result.classifications[0]
        self.assertEqual(c.classification, FileClassification.SECONDARY)
        self.assertTrue(any("migration" in r.lower() for r in c.reasons))

    # -- unrelated changed file → SECONDARY --

    def test_unrelated_file_is_secondary_not_discarded(self):
        analysis = self._make_analysis()
        files = [self._make_changed_file("app/auth/login_handler.py")]
        result = self.service.analyze(analysis, files)

        c = result.classifications[0]
        self.assertEqual(c.classification, FileClassification.SECONDARY)
        self.assertTrue(any("retained" in r.lower() for r in c.reasons))

    def test_unrelated_test_file_is_secondary(self):
        analysis = self._make_analysis()
        files = [self._make_changed_file("tests/test_auth.py")]
        result = self.service.analyze(analysis, files)

        c = result.classifications[0]
        self.assertEqual(c.classification, FileClassification.SECONDARY)

    # -- multiple changed files --

    def test_multiple_changed_files_mixed_classification(self):
        analysis = self._make_analysis()
        files = [
            self._make_changed_file("app/payment/retry.py"),
            self._make_changed_file("app/auth/login.py"),
            self._make_changed_file("docs/README.md"),
            self._make_changed_file("tests/payment/test_retry.py"),
        ]
        result = self.service.analyze(analysis, files)

        by_path = {c.file_path: c for c in result.classifications}
        self.assertEqual(len(by_path), 4)

        self.assertEqual(
            by_path["app/payment/retry.py"].classification,
            FileClassification.PRIMARY,
        )
        self.assertEqual(
            by_path["tests/payment/test_retry.py"].classification,
            FileClassification.PRIMARY,
        )
        self.assertEqual(
            by_path["app/auth/login.py"].classification,
            FileClassification.SECONDARY,
        )
        self.assertEqual(
            by_path["docs/README.md"].classification,
            FileClassification.SECONDARY,
        )

    # -- every input file appears exactly once in the output --

    def test_every_input_file_appears_exactly_once(self):
        analysis = self._make_analysis()
        files = [
            self._make_changed_file("app/payment/service.py"),
            self._make_changed_file("app/auth/handler.py"),
            self._make_changed_file("tests/test_payment.py"),
            self._make_changed_file("docs/notes.md"),
            self._make_changed_file("config.yaml"),
            self._make_changed_file("utils/random_helper.py"),
        ]
        result = self.service.analyze(analysis, files)

        output_paths = [c.file_path for c in result.classifications]
        input_paths = [f.path for f in files]

        self.assertEqual(sorted(output_paths), sorted(input_paths))

    def test_no_extra_files_in_output(self):
        analysis = self._make_analysis()
        files = [self._make_changed_file("app/payment/retry.py")]
        result = self.service.analyze(analysis, files)

        self.assertEqual(len(result.classifications), 1)
        self.assertEqual(result.classifications[0].file_path, "app/payment/retry.py")

    # -- empty changed-file list --

    def test_empty_changed_files_raises_value_error(self):
        analysis = self._make_analysis()
        with self.assertRaises(ValueError) as ctx:
            self.service.analyze(analysis, [])
        self.assertIn("empty", str(ctx.exception).lower())

    # -- deterministic output --

    def test_deterministic_output(self):
        """Running the same input twice must produce identical output."""
        analysis = self._make_analysis()
        files = [
            self._make_changed_file("app/payment/retry.py"),
            self._make_changed_file("app/auth/login.py"),
            self._make_changed_file("tests/test_payment.py"),
        ]
        result_1 = self.service.analyze(analysis, files)
        result_2 = self.service.analyze(analysis, files)

        self.assertEqual(
            result_1.model_dump(),
            result_2.model_dump(),
        )

    # -- no findings are generated --

    def test_no_findings_in_result(self):
        """The impact analysis stage must not produce findings."""
        analysis = self._make_analysis()
        files = [self._make_changed_file("app/payment/retry.py")]
        result = self.service.analyze(analysis, files)
        data = result.model_dump()

        self.assertNotIn("findings", data)
        self.assertNotIn("finding_ids", data)

    # -- return type --

    def test_returned_object_is_change_impact_analysis(self):
        analysis = self._make_analysis()
        files = [self._make_changed_file("app/something.py")]
        result = self.service.analyze(analysis, files)

        self.assertIsInstance(result, ChangeImpactAnalysis)

    # -- service requires no external integrations --

    def test_service_requires_no_constructor_args(self):
        svc = ChangeImpactAnalysisService()
        self.assertIsNotNone(svc)

    def test_service_has_no_external_attributes(self):
        svc = ChangeImpactAnalysisService()
        self.assertEqual(len(vars(svc)), 0)

    # -- reasons are always present and non-empty --

    def test_every_classification_has_reasons(self):
        analysis = self._make_analysis()
        files = [
            self._make_changed_file("app/payment/retry.py"),
            self._make_changed_file("app/auth/handler.py"),
            self._make_changed_file("README.md"),
        ]
        result = self.service.analyze(analysis, files)

        for c in result.classifications:
            self.assertGreaterEqual(len(c.reasons), 1)
            for reason in c.reasons:
                self.assertIsInstance(reason, str)
                self.assertTrue(len(reason.strip()) > 0)

    # -- serialization --

    def test_full_round_trip_serialization(self):
        analysis = self._make_analysis()
        files = [
            self._make_changed_file("app/payment/retry.py"),
            self._make_changed_file("docs/README.md"),
        ]
        result = self.service.analyze(analysis, files)
        data = result.model_dump()

        self.assertIn("classifications", data)
        self.assertEqual(len(data["classifications"]), 2)
        for entry in data["classifications"]:
            self.assertIn("file_path", entry)
            self.assertIn("classification", entry)
            self.assertIn("reasons", entry)
            self.assertIn(entry["classification"], ("PRIMARY", "SECONDARY"))


if __name__ == "__main__":
    unittest.main()
