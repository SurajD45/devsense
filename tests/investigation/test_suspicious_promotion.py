"""
Tests for SuspiciousFilePromotionService and its schemas
=========================================================
Validates deterministic promotion screening of SECONDARY changed files
for deeper investigation.
"""

import unittest
from pydantic import ValidationError

from app.domain.models import ChangedFile
from app.investigation.schemas import (
    ChangeImpactAnalysis,
    ChangedFileClassification,
    ChangedFileReview,
    FileClassification,
    FilePromotionAnalysis,
    FileReviewAnalysis,
    FileReviewItem,
    FileReviewPriority,
    PrioritizedFile,
)
from app.investigation.service import (
    ChangeImpactAnalysisService,
    FilePromotionService,
    SuspiciousFilePromotionService,
)


# =========================================================================
# Schema Tests
# =========================================================================


class TestFileReviewPriorityEnum(unittest.TestCase):
    """Tests for FileReviewPriority enum values and behavior."""

    def test_enum_values(self):
        self.assertEqual(FileReviewPriority.PRIMARY.value, "PRIMARY")
        self.assertEqual(FileReviewPriority.SECONDARY.value, "SECONDARY")
        self.assertEqual(FileReviewPriority.PROMOTED.value, "PROMOTED")

    def test_enum_from_string(self):
        self.assertEqual(FileReviewPriority("PRIMARY"), FileReviewPriority.PRIMARY)
        self.assertEqual(FileReviewPriority("SECONDARY"), FileReviewPriority.SECONDARY)
        self.assertEqual(FileReviewPriority("PROMOTED"), FileReviewPriority.PROMOTED)

    def test_invalid_priority_rejected(self):
        with self.assertRaises(ValueError):
            FileReviewPriority("UNKNOWN")

    def test_promoted_does_not_mean_buggy_semantic(self):
        """PROMOTED is a review priority, distinct from bug or error states."""
        self.assertIn("PROMOTED", [p.value for p in FileReviewPriority])


class TestFileReviewItemSchema(unittest.TestCase):
    """Tests for FileReviewItem DTO validation and consistency."""

    def test_valid_promoted_item(self):
        item = FileReviewItem(
            file_path="src/auth/middleware.py",
            priority=FileReviewPriority.PROMOTED,
            reasons=["security-sensitive file", "requires deeper investigation"],
            original_classification=FileClassification.SECONDARY,
        )
        self.assertEqual(item.file_path, "src/auth/middleware.py")
        self.assertEqual(item.priority, FileReviewPriority.PROMOTED)
        self.assertEqual(item.original_classification, FileClassification.SECONDARY)
        self.assertEqual(len(item.reasons), 2)

    def test_valid_primary_item(self):
        item = FileReviewItem(
            file_path="app/payment/retry.py",
            priority=FileReviewPriority.PRIMARY,
            reasons=["Path terms match requirement analysis: payment, retry"],
            original_classification=FileClassification.PRIMARY,
        )
        self.assertEqual(item.priority, FileReviewPriority.PRIMARY)
        self.assertEqual(item.original_classification, FileClassification.PRIMARY)

    def test_valid_secondary_item(self):
        item = FileReviewItem(
            file_path="utils/formatting.py",
            priority=FileReviewPriority.SECONDARY,
            reasons=["No direct keyword match; retained for review"],
            original_classification=FileClassification.SECONDARY,
        )
        self.assertEqual(item.priority, FileReviewPriority.SECONDARY)
        self.assertEqual(item.original_classification, FileClassification.SECONDARY)

    def test_aliases_are_identical_class(self):
        self.assertIs(PrioritizedFile, FileReviewItem)
        self.assertIs(ChangedFileReview, FileReviewItem)

    def test_default_original_classification_for_promoted(self):
        item = FileReviewItem(
            file_path="src/auth/handler.py",
            priority=FileReviewPriority.PROMOTED,
            reasons=["Security-sensitive"],
        )
        self.assertEqual(item.original_classification, FileClassification.SECONDARY)

    def test_default_original_classification_for_primary(self):
        item = FileReviewItem(
            file_path="app/payment/service.py",
            priority=FileReviewPriority.PRIMARY,
            reasons=["Direct match"],
        )
        self.assertEqual(item.original_classification, FileClassification.PRIMARY)

    def test_empty_file_path_rejected(self):
        with self.assertRaises(ValidationError):
            FileReviewItem(
                file_path="",
                priority=FileReviewPriority.PROMOTED,
                reasons=["Security-sensitive"],
            )

    def test_whitespace_file_path_rejected(self):
        with self.assertRaises(ValidationError):
            FileReviewItem(
                file_path="   ",
                priority=FileReviewPriority.PROMOTED,
                reasons=["Security-sensitive"],
            )

    def test_empty_reasons_rejected(self):
        with self.assertRaises(ValidationError):
            FileReviewItem(
                file_path="app/auth.py",
                priority=FileReviewPriority.PROMOTED,
                reasons=[],
            )

    def test_empty_reason_string_rejected(self):
        with self.assertRaises(ValidationError):
            FileReviewItem(
                file_path="app/auth.py",
                priority=FileReviewPriority.PROMOTED,
                reasons=["   "],
            )

    def test_promoted_must_have_original_secondary(self):
        """Only SECONDARY files can be promoted; PRIMARY cannot be promoted."""
        with self.assertRaises(ValidationError):
            FileReviewItem(
                file_path="app/auth.py",
                priority=FileReviewPriority.PROMOTED,
                reasons=["Security-sensitive"],
                original_classification=FileClassification.PRIMARY,
            )

    def test_primary_priority_must_have_original_primary(self):
        with self.assertRaises(ValidationError):
            FileReviewItem(
                file_path="app/service.py",
                priority=FileReviewPriority.PRIMARY,
                reasons=["Match"],
                original_classification=FileClassification.SECONDARY,
            )

    def test_model_dump_matches_expected_contract(self):
        item = FileReviewItem(
            file_path="src/auth/middleware.py",
            priority=FileReviewPriority.PROMOTED,
            reasons=["security-sensitive file", "requires deeper investigation"],
            original_classification=FileClassification.SECONDARY,
        )
        d = item.model_dump()
        self.assertEqual(d["file_path"], "src/auth/middleware.py")
        self.assertEqual(d["priority"], "PROMOTED")
        self.assertEqual(
            d["reasons"],
            ["security-sensitive file", "requires deeper investigation"],
        )
        self.assertEqual(d["original_classification"], "SECONDARY")


class TestFilePromotionAnalysisSchema(unittest.TestCase):
    """Tests for FilePromotionAnalysis container validation."""

    def test_alias_is_identical_class(self):
        self.assertIs(FileReviewAnalysis, FilePromotionAnalysis)

    def test_duplicate_file_paths_rejected(self):
        items = [
            FileReviewItem(
                file_path="app/auth.py",
                priority=FileReviewPriority.PROMOTED,
                reasons=["Security"],
            ),
            FileReviewItem(
                file_path="app/auth.py",
                priority=FileReviewPriority.SECONDARY,
                reasons=["Unrelated"],
            ),
        ]
        with self.assertRaises(ValidationError) as ctx:
            FilePromotionAnalysis(files=items)
        self.assertIn("duplicate", str(ctx.exception).lower())

    def test_helper_properties(self):
        items = [
            FileReviewItem(
                file_path="app/core.py",
                priority=FileReviewPriority.PRIMARY,
                reasons=["Primary match"],
            ),
            FileReviewItem(
                file_path="app/auth.py",
                priority=FileReviewPriority.PROMOTED,
                reasons=["Security"],
            ),
            FileReviewItem(
                file_path="docs/readme.md",
                priority=FileReviewPriority.SECONDARY,
                reasons=["Docs"],
            ),
        ]
        analysis = FilePromotionAnalysis(files=items)

        self.assertEqual(len(analysis.items), 3)
        self.assertEqual(len(analysis.primary_files), 1)
        self.assertEqual(analysis.primary_files[0].file_path, "app/core.py")
        self.assertEqual(len(analysis.promoted_files), 1)
        self.assertEqual(analysis.promoted_files[0].file_path, "app/auth.py")
        self.assertEqual(len(analysis.secondary_files), 1)
        self.assertEqual(analysis.secondary_files[0].file_path, "docs/readme.md")


# =========================================================================
# Service Tests
# =========================================================================


class TestSuspiciousFilePromotionService(unittest.TestCase):
    """
    Tests for SuspiciousFilePromotionService covering all required signals,
    edge cases, and architecture invariants.
    """

    def setUp(self):
        self.service = SuspiciousFilePromotionService()

    def _make_changed_file(self, path: str, additions: int = 10, deletions: int = 5) -> ChangedFile:
        return ChangedFile(
            path=path,
            status="modified",
            additions=additions,
            deletions=deletions,
        )

    def _make_impact_analysis(
        self,
        classifications: list[tuple[str, FileClassification, list[str]]],
    ) -> ChangeImpactAnalysis:
        return ChangeImpactAnalysis(
            classifications=[
                ChangedFileClassification(
                    file_path=path,
                    classification=classification,
                    reasons=reasons,
                )
                for path, classification, reasons in classifications
            ]
        )

    # ---------------------------------------------------------------------
    # 1. Ordinary secondary file remains SECONDARY
    # ---------------------------------------------------------------------

    def test_ordinary_secondary_file_remains_secondary(self):
        impact = self._make_impact_analysis([
            ("utils/string_formatter.py", FileClassification.SECONDARY, ["No keyword match"]),
        ])
        files = [self._make_changed_file("utils/string_formatter.py", additions=5, deletions=2)]

        result = self.service.promote(impact, files)

        self.assertEqual(len(result.files), 1)
        item = result.files[0]
        self.assertEqual(item.file_path, "utils/string_formatter.py")
        self.assertEqual(item.priority, FileReviewPriority.SECONDARY)
        self.assertEqual(item.original_classification, FileClassification.SECONDARY)
        self.assertEqual(item.reasons, ["No keyword match"])

    # ---------------------------------------------------------------------
    # 2. Auth / security file is promoted
    # ---------------------------------------------------------------------

    def test_auth_security_file_is_promoted(self):
        test_paths = [
            "src/auth/middleware.py",
            "app/security/jwt_validator.py",
            "services/authorization.py",
            "app/permissions.py",
            "lib/access_control.py",
            "core/tokens.py",
            "app/credentials/store.py",
        ]
        for path in test_paths:
            with self.subTest(path=path):
                impact = self._make_impact_analysis([
                    (path, FileClassification.SECONDARY, ["Unrelated to requirement"]),
                ])
                files = [self._make_changed_file(path, additions=10, deletions=5)]

                result = self.service.promote(impact, files)

                self.assertEqual(len(result.files), 1)
                item = result.files[0]
                self.assertEqual(item.file_path, path)
                self.assertEqual(item.priority, FileReviewPriority.PROMOTED)
                self.assertEqual(item.original_classification, FileClassification.SECONDARY)
                self.assertTrue(any("security" in r.lower() or "access" in r.lower() for r in item.reasons))
                self.assertTrue(any("requires deeper investigation" in r.lower() for r in item.reasons))

    # ---------------------------------------------------------------------
    # 3. Migration / schema file is promoted
    # ---------------------------------------------------------------------

    def test_migration_schema_file_is_promoted(self):
        test_paths = [
            "db/migrations/001_add_column.sql",
            "alembic/versions/2026_migration.py",
            "app/schemas/order_schema.py",
            "database/schema.sql",
        ]
        for path in test_paths:
            with self.subTest(path=path):
                impact = self._make_impact_analysis([
                    (path, FileClassification.SECONDARY, ["Migration file"]),
                ])
                files = [self._make_changed_file(path, additions=15, deletions=0)]

                result = self.service.promote(impact, files)

                self.assertEqual(len(result.files), 1)
                item = result.files[0]
                self.assertEqual(item.priority, FileReviewPriority.PROMOTED)
                self.assertEqual(item.original_classification, FileClassification.SECONDARY)
                self.assertTrue(any("database" in r.lower() or "schema" in r.lower() for r in item.reasons))
                self.assertTrue(any("requires deeper investigation" in r.lower() for r in item.reasons))

    # ---------------------------------------------------------------------
    # 4. Dependency file is promoted
    # ---------------------------------------------------------------------

    def test_dependency_file_is_promoted(self):
        test_files = [
            "requirements.txt",
            "requirements-dev.txt",
            "pyproject.toml",
            "package.json",
            "package-lock.json",
            "poetry.lock",
            "uv.lock",
            "Pipfile",
            "go.mod",
            "Cargo.toml",
        ]
        for path in test_files:
            with self.subTest(path=path):
                impact = self._make_impact_analysis([
                    (path, FileClassification.SECONDARY, ["Dependency file"]),
                ])
                files = [self._make_changed_file(path, additions=2, deletions=1)]

                result = self.service.promote(impact, files)

                self.assertEqual(len(result.files), 1)
                item = result.files[0]
                self.assertEqual(item.priority, FileReviewPriority.PROMOTED)
                self.assertEqual(item.original_classification, FileClassification.SECONDARY)
                self.assertTrue(any("dependency" in r.lower() for r in item.reasons))
                self.assertTrue(any("requires deeper investigation" in r.lower() for r in item.reasons))

    # ---------------------------------------------------------------------
    # 5. CI/CD or deployment file is promoted
    # ---------------------------------------------------------------------

    def test_cicd_deployment_file_is_promoted(self):
        test_paths = [
            ".github/workflows/ci.yml",
            ".github/actions/build/action.yml",
            "Dockerfile",
            "Dockerfile.prod",
            "docker-compose.yml",
            "docker-compose.prod.yaml",
            "deploy/helm/values.yaml",
            "deployments/k8s.yaml",
            "Procfile",
        ]
        for path in test_paths:
            with self.subTest(path=path):
                impact = self._make_impact_analysis([
                    (path, FileClassification.SECONDARY, ["Infrastructure file"]),
                ])
                files = [self._make_changed_file(path, additions=5, deletions=2)]

                result = self.service.promote(impact, files)

                self.assertEqual(len(result.files), 1)
                item = result.files[0]
                self.assertEqual(item.priority, FileReviewPriority.PROMOTED)
                self.assertEqual(item.original_classification, FileClassification.SECONDARY)
                self.assertTrue(any("ci/cd" in r.lower() or "deployment" in r.lower() for r in item.reasons))
                self.assertTrue(any("requires deeper investigation" in r.lower() for r in item.reasons))

    # ---------------------------------------------------------------------
    # 6. Large change is promoted
    # ---------------------------------------------------------------------

    def test_large_change_is_promoted(self):
        path = "app/services/data_exporter.py"
        impact = self._make_impact_analysis([
            (path, FileClassification.SECONDARY, ["Unrelated service"]),
        ])
        # Default threshold is 100 lines; 70 + 40 = 110 >= 100
        files = [self._make_changed_file(path, additions=70, deletions=40)]

        result = self.service.promote(impact, files)

        self.assertEqual(len(result.files), 1)
        item = result.files[0]
        self.assertEqual(item.priority, FileReviewPriority.PROMOTED)
        self.assertTrue(any("large change" in r.lower() for r in item.reasons))
        self.assertTrue(any("110 lines" in r for r in item.reasons))
        self.assertTrue(any("requires deeper investigation" in r.lower() for r in item.reasons))

    def test_change_below_threshold_is_not_promoted_by_size(self):
        path = "app/services/tiny_helper.py"
        impact = self._make_impact_analysis([
            (path, FileClassification.SECONDARY, ["Unrelated service"]),
        ])
        # 50 + 40 = 90 < 100
        files = [self._make_changed_file(path, additions=50, deletions=40)]

        result = self.service.promote(impact, files)

        self.assertEqual(result.files[0].priority, FileReviewPriority.SECONDARY)

    def test_custom_large_change_threshold(self):
        custom_service = SuspiciousFilePromotionService(large_change_threshold=50)
        path = "app/services/medium_helper.py"
        impact = self._make_impact_analysis([
            (path, FileClassification.SECONDARY, ["Unrelated service"]),
        ])
        files = [self._make_changed_file(path, additions=30, deletions=25)]

        result = custom_service.promote(impact, files)

        self.assertEqual(result.files[0].priority, FileReviewPriority.PROMOTED)
        self.assertTrue(any("threshold: 50" in r for r in result.files[0].reasons))

    # ---------------------------------------------------------------------
    # 6b. Important application-boundary files are promoted
    # ---------------------------------------------------------------------

    def test_application_boundary_files_are_promoted(self):
        test_paths = [
            "app/api/v1/endpoints.py",
            "app/controllers/user_controller.py",
            "app/routes/billing_routes.py",
            "config/settings.yaml",
            "app/middleware/rate_limiter.py",
        ]
        for path in test_paths:
            with self.subTest(path=path):
                impact = self._make_impact_analysis([
                    (path, FileClassification.SECONDARY, ["Boundary file"]),
                ])
                files = [self._make_changed_file(path, additions=10, deletions=5)]

                result = self.service.promote(impact, files)

                self.assertEqual(result.files[0].priority, FileReviewPriority.PROMOTED)
                self.assertTrue(any(
                    "boundary" in r.lower() or "runtime" in r.lower() or "security" in r.lower()
                    for r in result.files[0].reasons
                ))

    # ---------------------------------------------------------------------
    # 7. PRIMARY file is never downgraded or incorrectly promoted
    # ---------------------------------------------------------------------

    def test_primary_file_never_downgraded_or_promoted(self):
        """
        Even if a PRIMARY file is large, an auth file, or in a boundary folder,
        it MUST remain PRIMARY.
        """
        path = "app/auth/payment_auth_handler.py"
        impact = self._make_impact_analysis([
            (path, FileClassification.PRIMARY, ["Matched requirement analysis"]),
        ])
        # 200 additions would trigger large change and auth matches security
        files = [self._make_changed_file(path, additions=200, deletions=50)]

        result = self.service.promote(impact, files)

        self.assertEqual(len(result.files), 1)
        item = result.files[0]
        self.assertEqual(item.priority, FileReviewPriority.PRIMARY)
        self.assertEqual(item.original_classification, FileClassification.PRIMARY)
        self.assertEqual(item.reasons, ["Matched requirement analysis"])

    # ---------------------------------------------------------------------
    # 8. Multiple promotion signals
    # ---------------------------------------------------------------------

    def test_multiple_promotion_signals(self):
        """
        A file matching both auth, controller boundary, and large change should
        list all signals in its promotion reasons.
        """
        path = "app/controllers/auth_controller.py"
        impact = self._make_impact_analysis([
            (path, FileClassification.SECONDARY, ["Unrelated controller"]),
        ])
        files = [self._make_changed_file(path, additions=120, deletions=30)]

        result = self.service.promote(impact, files)

        item = result.files[0]
        self.assertEqual(item.priority, FileReviewPriority.PROMOTED)
        # Should detect: security (auth), boundary (controllers), and large change (150 lines)
        self.assertTrue(any("security" in r.lower() for r in item.reasons))
        self.assertTrue(any("boundary" in r.lower() for r in item.reasons))
        self.assertTrue(any("large change" in r.lower() for r in item.reasons))
        self.assertTrue(any("requires deeper investigation" in r.lower() for r in item.reasons))
        self.assertGreaterEqual(len(item.reasons), 4)

    # ---------------------------------------------------------------------
    # 9. Promotion reasons are present
    # ---------------------------------------------------------------------

    def test_promotion_reasons_are_present_and_non_empty(self):
        impact = self._make_impact_analysis([
            ("src/auth/jwt.py", FileClassification.SECONDARY, ["Secondary"]),
            ("utils/helper.py", FileClassification.SECONDARY, ["Secondary"]),
        ])
        files = [
            self._make_changed_file("src/auth/jwt.py"),
            self._make_changed_file("utils/helper.py"),
        ]

        result = self.service.promote(impact, files)

        for item in result.files:
            self.assertGreaterEqual(len(item.reasons), 1)
            for reason in item.reasons:
                self.assertIsInstance(reason, str)
                self.assertTrue(len(reason.strip()) > 0)

    # ---------------------------------------------------------------------
    # 10. Every changed file remains represented exactly once
    # ---------------------------------------------------------------------

    def test_every_changed_file_remains_represented_exactly_once(self):
        files_spec = [
            ("app/payment/service.py", FileClassification.PRIMARY, 20, 5),
            ("app/auth/middleware.py", FileClassification.SECONDARY, 15, 3),
            ("db/migrations/002_add_index.sql", FileClassification.SECONDARY, 10, 0),
            ("package.json", FileClassification.SECONDARY, 2, 1),
            (".github/workflows/ci.yml", FileClassification.SECONDARY, 30, 10),
            ("utils/math_helper.py", FileClassification.SECONDARY, 8, 2),
            ("app/services/big_report.py", FileClassification.SECONDARY, 95, 25),
        ]
        impact = self._make_impact_analysis([
            (path, cls, ["Initial reason"]) for path, cls, _, _ in files_spec
        ])
        changed_files = [
            self._make_changed_file(path, additions=add, deletions=dele)
            for path, _, add, dele in files_spec
        ]

        result = self.service.promote(impact, changed_files)

        output_paths = [f.file_path for f in result.files]
        input_paths = [path for path, _, _, _ in files_spec]

        self.assertEqual(sorted(output_paths), sorted(input_paths))
        self.assertEqual(len(output_paths), len(input_paths))

        by_path = {f.file_path: f for f in result.files}
        self.assertEqual(by_path["app/payment/service.py"].priority, FileReviewPriority.PRIMARY)
        self.assertEqual(by_path["app/auth/middleware.py"].priority, FileReviewPriority.PROMOTED)
        self.assertEqual(by_path["db/migrations/002_add_index.sql"].priority, FileReviewPriority.PROMOTED)
        self.assertEqual(by_path["package.json"].priority, FileReviewPriority.PROMOTED)
        self.assertEqual(by_path[".github/workflows/ci.yml"].priority, FileReviewPriority.PROMOTED)
        self.assertEqual(by_path["utils/math_helper.py"].priority, FileReviewPriority.SECONDARY)
        self.assertEqual(by_path["app/services/big_report.py"].priority, FileReviewPriority.PROMOTED)

    # ---------------------------------------------------------------------
    # 11. Deterministic repeated execution
    # ---------------------------------------------------------------------

    def test_deterministic_repeated_execution(self):
        impact = self._make_impact_analysis([
            ("app/payment/core.py", FileClassification.PRIMARY, ["Matched payment"]),
            ("src/auth/tokens.py", FileClassification.SECONDARY, ["Auth tokens"]),
            ("utils/helpers.py", FileClassification.SECONDARY, ["Helpers"]),
        ])
        files = [
            self._make_changed_file("app/payment/core.py", additions=50, deletions=10),
            self._make_changed_file("src/auth/tokens.py", additions=30, deletions=5),
            self._make_changed_file("utils/helpers.py", additions=10, deletions=2),
        ]

        result_1 = self.service.promote(impact, files)
        result_2 = self.service.promote(impact, files)

        self.assertEqual(result_1.model_dump(), result_2.model_dump())

    # ---------------------------------------------------------------------
    # 12. No findings are created
    # ---------------------------------------------------------------------

    def test_no_findings_are_created(self):
        impact = self._make_impact_analysis([
            ("src/auth/middleware.py", FileClassification.SECONDARY, ["Auth file"]),
        ])
        files = [self._make_changed_file("src/auth/middleware.py")]

        result = self.service.promote(impact, files)
        data = result.model_dump()

        self.assertNotIn("findings", data)
        self.assertNotIn("finding_ids", data)
        for item in data["files"]:
            self.assertNotIn("findings", item)
            self.assertNotIn("finding_ids", item)
            self.assertNotIn("is_buggy", item)
            self.assertNotIn("violates_requirement", item)

    # ---------------------------------------------------------------------
    # 13. Invalid inputs
    # ---------------------------------------------------------------------

    def test_empty_impact_analysis_raises_value_error(self):
        impact = ChangeImpactAnalysis(classifications=[])
        files = [self._make_changed_file("app/main.py")]

        with self.assertRaises(ValueError) as ctx:
            self.service.promote(impact, files)
        self.assertIn("at least one", str(ctx.exception).lower())

    def test_empty_changed_files_raises_value_error(self):
        impact = self._make_impact_analysis([
            ("app/main.py", FileClassification.PRIMARY, ["Main"]),
        ])
        with self.assertRaises(ValueError) as ctx:
            self.service.promote(impact, [])
        self.assertIn("at least one", str(ctx.exception).lower())

    def test_file_in_analysis_missing_from_changed_files_raises_value_error(self):
        impact = self._make_impact_analysis([
            ("app/main.py", FileClassification.PRIMARY, ["Main"]),
            ("app/other.py", FileClassification.SECONDARY, ["Other"]),
        ])
        files = [self._make_changed_file("app/main.py")]

        with self.assertRaises(ValueError) as ctx:
            self.service.promote(impact, files)
        self.assertIn("not found in changed_files", str(ctx.exception))

    def test_file_in_changed_files_missing_from_analysis_raises_value_error(self):
        impact = self._make_impact_analysis([
            ("app/main.py", FileClassification.PRIMARY, ["Main"]),
        ])
        files = [
            self._make_changed_file("app/main.py"),
            self._make_changed_file("app/extra.py"),
        ]

        with self.assertRaises(ValueError) as ctx:
            self.service.promote(impact, files)
        self.assertIn("not found in impact_analysis", str(ctx.exception))

    def test_invalid_threshold_raises_value_error(self):
        with self.assertRaises(ValueError):
            SuspiciousFilePromotionService(large_change_threshold=0)
        with self.assertRaises(ValueError):
            SuspiciousFilePromotionService(large_change_threshold=-10)

    # ---------------------------------------------------------------------
    # 14. Convenience methods & aliases
    # ---------------------------------------------------------------------

    def test_service_alias(self):
        self.assertIs(FilePromotionService, SuspiciousFilePromotionService)

    def test_analyze_method_alias(self):
        impact = self._make_impact_analysis([
            ("app/service.py", FileClassification.PRIMARY, ["Primary"]),
        ])
        files = [self._make_changed_file("app/service.py")]

        res_promote = self.service.promote(impact, files)
        res_analyze = self.service.analyze(impact, files)
        self.assertEqual(res_promote.model_dump(), res_analyze.model_dump())

    def test_call_via_change_impact_analysis_service(self):
        impact_svc = ChangeImpactAnalysisService()
        impact = self._make_impact_analysis([
            ("src/auth/jwt.py", FileClassification.SECONDARY, ["Auth"]),
        ])
        files = [self._make_changed_file("src/auth/jwt.py")]

        result = impact_svc.promote_secondary_files(impact, files)
        self.assertIsInstance(result, FilePromotionAnalysis)
        self.assertEqual(result.files[0].priority, FileReviewPriority.PROMOTED)


if __name__ == "__main__":
    unittest.main()
