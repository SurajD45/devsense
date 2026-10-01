"""
Tests for Requirement-Aware Code Investigation
==============================================
Validates deterministic correlation between CodeObservation items
and RequirementAnalysis acceptance criteria without creating Findings
or making verification decisions.
"""

import unittest
from pydantic import ValidationError

from app.investigation.schemas import (
    AnalyzedCriterion,
    CodeInvestigationResult,
    CodeObservation,
    CodeObservationType,
    FileReviewPriority,
    RequirementAnalysis,
    RequirementAwareCodeInvestigationInput,
)
from app.investigation.service import (
    CodeInvestigationService,
    RequirementAwareCodeInvestigationService,
)


# =========================================================================
# Schema Tests
# =========================================================================


class TestRequirementAwareCodeInvestigationInputSchema(unittest.TestCase):
    """Tests for RequirementAwareCodeInvestigationInput DTO."""

    def setUp(self):
        self.req_analysis = RequirementAnalysis(
            requirement_id="REQ-1",
            summary="Requirement analysis: Payment processing.",
            criteria=[
                AnalyzedCriterion(
                    criterion_id="AC-1",
                    interpretation="Validate payment amount",
                    verification_points=["Verify: payment amount is positive"],
                ),
            ],
        )

    def test_valid_input(self):
        input_data = RequirementAwareCodeInvestigationInput(
            requirement_analysis=self.req_analysis,
            file_path="app/payment/service.py",
            file_content="def process(): pass",
            review_priority=FileReviewPriority.PRIMARY,
        )
        self.assertEqual(input_data.file_path, "app/payment/service.py")
        self.assertEqual(input_data.file_content, "def process(): pass")
        self.assertEqual(input_data.review_priority, FileReviewPriority.PRIMARY)

    def test_alias_content_field(self):
        input_data = RequirementAwareCodeInvestigationInput(
            requirement_analysis=self.req_analysis,
            file_path="app/payment/service.py",
            content="def process(): pass",
        )
        self.assertEqual(input_data.file_content, "def process(): pass")

    def test_empty_file_path_rejected(self):
        with self.assertRaises(ValidationError):
            RequirementAwareCodeInvestigationInput(
                requirement_analysis=self.req_analysis,
                file_path="",
                file_content="pass",
            )

    def test_whitespace_file_path_rejected(self):
        with self.assertRaises(ValidationError):
            RequirementAwareCodeInvestigationInput(
                requirement_analysis=self.req_analysis,
                file_path="   ",
                file_content="pass",
            )


class TestCodeObservationCriteriaFields(unittest.TestCase):
    """Tests for criterion_id and criterion_ids on CodeObservation."""

    def test_default_is_none_and_empty_list(self):
        obs = CodeObservation(
            observation_id="CODE-1",
            file_path="app/main.py",
            description="Test observation",
            observation_type=CodeObservationType.LOGIC,
        )
        self.assertIsNone(obs.criterion_id)
        self.assertEqual(obs.criterion_ids, [])

    def test_setting_criterion_id_populates_criterion_ids(self):
        obs = CodeObservation(
            observation_id="CODE-1",
            file_path="app/main.py",
            description="Test observation",
            observation_type=CodeObservationType.LOGIC,
            criterion_id="AC-1",
        )
        self.assertEqual(obs.criterion_id, "AC-1")
        self.assertEqual(obs.criterion_ids, ["AC-1"])

    def test_setting_criterion_ids_populates_criterion_id(self):
        obs = CodeObservation(
            observation_id="CODE-1",
            file_path="app/main.py",
            description="Test observation",
            observation_type=CodeObservationType.LOGIC,
            criterion_ids=["AC-1", "AC-2"],
        )
        self.assertEqual(obs.criterion_id, "AC-1")
        self.assertEqual(obs.criterion_ids, ["AC-1", "AC-2"])

    def test_invalid_criterion_id_rejected(self):
        for invalid_id in ["invalid", "ac-1", "CRIT-1", "123", ""]:
            with self.assertRaises(ValidationError):
                CodeObservation(
                    observation_id="CODE-1",
                    file_path="app/main.py",
                    description="Test",
                    observation_type=CodeObservationType.LOGIC,
                    criterion_id=invalid_id,
                )

    def test_invalid_criterion_ids_item_rejected(self):
        with self.assertRaises(ValidationError):
            CodeObservation(
                observation_id="CODE-1",
                file_path="app/main.py",
                description="Test",
                observation_type=CodeObservationType.LOGIC,
                criterion_ids=["AC-1", "bad-id"],
            )

    def test_duplicates_in_criterion_ids_deduplicated(self):
        obs = CodeObservation(
            observation_id="CODE-1",
            file_path="app/main.py",
            description="Test",
            observation_type=CodeObservationType.LOGIC,
            criterion_ids=["AC-1", "AC-2", "AC-1"],
        )
        self.assertEqual(obs.criterion_ids, ["AC-1", "AC-2"])


# =========================================================================
# Service Tests
# =========================================================================


class TestRequirementAwareCodeInvestigation(unittest.TestCase):
    """
    Tests for RequirementAwareCodeInvestigationService and
    CodeInvestigationService.investigate_with_requirements.
    """

    def setUp(self):
        self.base_service = CodeInvestigationService()
        self.service = RequirementAwareCodeInvestigationService(self.base_service)

    def _make_requirement_analysis(self, criteria=None, summary="Requirement analysis: Payment system."):
        if criteria is None:
            criteria = [
                AnalyzedCriterion(
                    criterion_id="AC-1",
                    interpretation="Validate payment amount before processing",
                    verification_points=[
                        "Verify: payment amount validation",
                        "Verify: validation occurs before processing",
                    ],
                    potential_areas=["payment_validation"],
                ),
                AnalyzedCriterion(
                    criterion_id="AC-2",
                    interpretation="Audit logging of user session events",
                    verification_points=[
                        "Verify: audit log is emitted for each session event",
                    ],
                    potential_areas=["audit_logging"],
                ),
            ]
        return RequirementAnalysis(
            requirement_id="PAY-100",
            summary=summary,
            criteria=criteria,
        )

    # ---------------------------------------------------------------------
    # 1. Observation clearly relevant to one criterion
    # ---------------------------------------------------------------------

    def test_observation_clearly_relevant_to_one_criterion(self):
        req = self._make_requirement_analysis()
        content = """
def process():
    # TODO: validate payment amount bounds
    pass
"""
        input_data = RequirementAwareCodeInvestigationInput(
            requirement_analysis=req,
            file_path="app/payment/service.py",
            file_content=content,
        )
        result = self.service.investigate(input_data)

        # The TODO observation matches AC-1 on "validate", "payment", "amount"
        todo_obs = [o for o in result.observations if "TODO" in o.description]
        self.assertGreaterEqual(len(todo_obs), 1)
        obs = todo_obs[0]
        self.assertEqual(obs.criterion_id, "AC-1")
        self.assertEqual(obs.criterion_ids, ["AC-1"])

    # ---------------------------------------------------------------------
    # 2. Observation relevant to multiple criteria
    # ---------------------------------------------------------------------

    def test_observation_relevant_to_multiple_criteria(self):
        # Both AC-1 and AC-2 share "payment"
        req = self._make_requirement_analysis(
            criteria=[
                AnalyzedCriterion(
                    criterion_id="AC-1",
                    interpretation="Validate payment amount",
                    verification_points=["Verify: payment amount validation"],
                ),
                AnalyzedCriterion(
                    criterion_id="AC-2",
                    interpretation="Audit log payment transactions",
                    verification_points=["Verify: payment audit logging"],
                ),
            ]
        )
        content = """
def payment_handler():
    print("Executing payment transaction step")
"""
        input_data = RequirementAwareCodeInvestigationInput(
            requirement_analysis=req,
            file_path="app/payment/handler.py",
            file_content=content,
        )
        result = self.service.investigate(input_data)

        print_obs = [o for o in result.observations if o.observation_type == CodeObservationType.CONTROL_FLOW]
        self.assertEqual(len(print_obs), 1)
        obs = print_obs[0]
        # Should match both criteria
        self.assertIn("AC-1", obs.criterion_ids)
        self.assertIn("AC-2", obs.criterion_ids)
        self.assertEqual(len(obs.criterion_ids), 2)
        # Primary criterion is the top match
        self.assertIsNotNone(obs.criterion_id)

    # ---------------------------------------------------------------------
    # 3. Observation with no criterion match
    # ---------------------------------------------------------------------

    def test_observation_with_no_criterion_match(self):
        req = self._make_requirement_analysis()
        content = """
class AuthManager:
    password = "super_secret_auth_password_123"
"""
        input_data = RequirementAwareCodeInvestigationInput(
            requirement_analysis=req,
            file_path="app/auth/tokens.py",
            file_content=content,
        )
        result = self.service.investigate(input_data)

        self.assertEqual(len(result.observations), 1)
        obs = result.observations[0]
        self.assertEqual(obs.observation_type, CodeObservationType.SECURITY)
        self.assertIsNone(obs.criterion_id)
        self.assertEqual(obs.criterion_ids, [])

    # ---------------------------------------------------------------------
    # 4. Common stop words do not create false relevance
    # ---------------------------------------------------------------------

    def test_common_stopwords_do_not_create_false_relevance(self):
        req = RequirementAnalysis(
            requirement_id="REQ-99",
            summary="Requirement analysis: Core operations.",
            criteria=[
                AnalyzedCriterion(
                    criterion_id="AC-1",
                    interpretation="The operation must and should be done when following",
                    verification_points=["Verify: when this occurs it must be done"],
                )
            ],
        )
        # Content contains only stop words overlapping with the criterion text
        content = """
def helper():
    # TODO: this must and should be done when for the
    pass
"""
        input_data = RequirementAwareCodeInvestigationInput(
            requirement_analysis=req,
            file_path="utils/misc.py",
            file_content=content,
        )
        result = self.service.investigate(input_data)

        todo_obs = [o for o in result.observations if "TODO" in o.description]
        self.assertEqual(len(todo_obs), 1)
        # Stop words must not produce criterion correlation
        self.assertIsNone(todo_obs[0].criterion_id)
        self.assertEqual(todo_obs[0].criterion_ids, [])

    # ---------------------------------------------------------------------
    # 5. File-path terms contribute to relevance
    # ---------------------------------------------------------------------

    def test_file_path_terms_contribute_to_relevance(self):
        req = RequirementAnalysis(
            requirement_id="REQ-1",
            summary="Requirement analysis: Payment checkout.",
            criteria=[
                AnalyzedCriterion(
                    criterion_id="AC-1",
                    interpretation="Process checkout payments",
                    verification_points=["Verify: checkout flow processes payments"],
                )
            ],
        )
        # Generic observation description, but file path contains "checkout"
        content = """
def run():
    try:
        do_work()
    except:
        pass
"""
        input_data = RequirementAwareCodeInvestigationInput(
            requirement_analysis=req,
            file_path="app/checkout/executor.py",
            file_content=content,
        )
        result = self.service.investigate(input_data)

        bare_except_obs = [o for o in result.observations if o.observation_type == CodeObservationType.ERROR_HANDLING]
        self.assertEqual(len(bare_except_obs), 1)
        # "checkout" from file_path connects it to AC-1
        self.assertEqual(bare_except_obs[0].criterion_id, "AC-1")

    # ---------------------------------------------------------------------
    # 6. Symbol terms contribute to relevance
    # ---------------------------------------------------------------------

    def test_symbol_terms_contribute_to_relevance(self):
        req = RequirementAnalysis(
            requirement_id="REQ-1",
            summary="Requirement analysis: Refund operations.",
            criteria=[
                AnalyzedCriterion(
                    criterion_id="AC-1",
                    interpretation="Process customer refunds",
                    verification_points=["Verify: refund calculation is accurate"],
                )
            ],
        )
        # File path is generic, but function symbol has "refund"
        content = """
def calculate_refund_amount(order):
    # TODO: check tax refund eligibility
    return 0.0
"""
        input_data = RequirementAwareCodeInvestigationInput(
            requirement_analysis=req,
            file_path="app/utils/math_ops.py",
            file_content=content,
        )
        result = self.service.investigate(input_data)

        # Observation inside calculate_refund_amount inherits the symbol
        todo_obs = [o for o in result.observations if "TODO" in o.description]
        self.assertEqual(len(todo_obs), 1)
        self.assertEqual(todo_obs[0].criterion_id, "AC-1")
        self.assertEqual(todo_obs[0].symbol, "calculate_refund_amount")

    # ---------------------------------------------------------------------
    # 7. Verification-point terms contribute to relevance
    # ---------------------------------------------------------------------

    def test_verification_point_terms_contribute_to_relevance(self):
        req = RequirementAnalysis(
            requirement_id="REQ-1",
            summary="Requirement analysis: Order processing.",
            criteria=[
                AnalyzedCriterion(
                    criterion_id="AC-1",
                    interpretation="Ensure order uniqueness",
                    verification_points=["Verify: idempotency key header is enforced"],
                )
            ],
        )
        # "idempotency" is specifically in the verification points
        content = """
def check_request(request):
    # TODO: inspect idempotency header
    pass
"""
        input_data = RequirementAwareCodeInvestigationInput(
            requirement_analysis=req,
            file_path="app/middleware.py",
            file_content=content,
        )
        result = self.service.investigate(input_data)

        todo_obs = [o for o in result.observations if "TODO" in o.description]
        self.assertEqual(len(todo_obs), 1)
        self.assertEqual(todo_obs[0].criterion_id, "AC-1")

    # ---------------------------------------------------------------------
    # 8. Requirement terminology contributes to relevance
    # ---------------------------------------------------------------------

    def test_requirement_terminology_contributes_to_relevance(self):
        req = RequirementAnalysis(
            requirement_id="REQ-1",
            summary="Requirement analysis: Subscription billing system.",
            criteria=[
                AnalyzedCriterion(
                    criterion_id="AC-1",
                    interpretation="Charge recurring monthly fees",
                    verification_points=["Verify: recurring schedule runs accurately"],
                )
            ],
        )
        # Code matches "subscription" which is prominent in requirement summary
        content = """
def handle_account():
    # TODO: update subscription state
    pass
"""
        input_data = RequirementAwareCodeInvestigationInput(
            requirement_analysis=req,
            file_path="app/accounts.py",
            file_content=content,
        )
        result = self.service.investigate(input_data)

        todo_obs = [o for o in result.observations if "TODO" in o.description]
        self.assertEqual(len(todo_obs), 1)
        self.assertEqual(todo_obs[0].criterion_id, "AC-1")

    # ---------------------------------------------------------------------
    # 9. Unrelated observations remain preserved
    # ---------------------------------------------------------------------

    def test_unrelated_observations_remain_preserved(self):
        req = self._make_requirement_analysis()
        content = """
def mixed_file():
    # TODO: validate payment amount
    api_key = "secret_key_production_998877"
    try:
        pass
    except:
        pass
"""
        input_data = RequirementAwareCodeInvestigationInput(
            requirement_analysis=req,
            file_path="app/payment/mixed.py",
            file_content=content,
        )
        result = self.service.investigate(input_data)

        # All 3 observations must remain present
        self.assertEqual(len(result.observations), 3)

        todo_obs = [o for o in result.observations if "TODO" in o.description][0]
        sec_obs = [o for o in result.observations if o.observation_type == CodeObservationType.SECURITY][0]
        except_obs = [o for o in result.observations if o.observation_type == CodeObservationType.ERROR_HANDLING][0]

        # The payment-related TODO is correlated
        self.assertEqual(todo_obs.criterion_id, "AC-1")
        # The credential pattern remains preserved even without criterion match
        self.assertIsNotNone(sec_obs)

    # ---------------------------------------------------------------------
    # 10. Deterministic repeated execution
    # ---------------------------------------------------------------------

    def test_deterministic_repeated_execution(self):
        req = self._make_requirement_analysis()
        content = """
def payment_run():
    # TODO: validate payment
    print("logging payment transaction")
"""
        input_data = RequirementAwareCodeInvestigationInput(
            requirement_analysis=req,
            file_path="app/payment/run.py",
            file_content=content,
        )
        res1 = self.service.investigate(input_data)
        res2 = self.service.investigate(input_data)

        self.assertEqual(res1.model_dump(), res2.model_dump())

    # ---------------------------------------------------------------------
    # 11. Criterion IDs remain valid
    # ---------------------------------------------------------------------

    def test_criterion_ids_remain_valid(self):
        req = self._make_requirement_analysis()
        valid_criteria = {c.criterion_id for c in req.criteria}

        content = """
def audit_payment():
    # TODO: validate payment
    print("audit payment transaction")
"""
        input_data = RequirementAwareCodeInvestigationInput(
            requirement_analysis=req,
            file_path="app/payment/audit.py",
            file_content=content,
        )
        result = self.service.investigate(input_data)

        for obs in result.observations:
            for cid in obs.criterion_ids:
                self.assertIn(cid, valid_criteria)
            if obs.criterion_id:
                self.assertIn(obs.criterion_id, valid_criteria)

    # ---------------------------------------------------------------------
    # 12. No Finding objects are generated
    # ---------------------------------------------------------------------

    def test_no_finding_objects_are_generated(self):
        req = self._make_requirement_analysis()
        content = """
def test():
    # TODO: validate payment
    pass
"""
        input_data = RequirementAwareCodeInvestigationInput(
            requirement_analysis=req,
            file_path="app/payment/test.py",
            file_content=content,
        )
        result = self.service.investigate(input_data)
        data = result.model_dump()

        self.assertNotIn("findings", data)
        self.assertNotIn("finding_ids", data)
        for obs in data["observations"]:
            self.assertNotIn("finding_id", obs)
            self.assertNotIn("evidence_ids", obs)

    # ---------------------------------------------------------------------
    # 13. No VERIFIED / NOT_VERIFIED conclusions are generated
    # ---------------------------------------------------------------------

    def test_no_verification_conclusions_are_generated(self):
        req = self._make_requirement_analysis()
        content = """
def validate_payment_amount():
    # Completely correct validation code
    return True
"""
        input_data = RequirementAwareCodeInvestigationInput(
            requirement_analysis=req,
            file_path="app/payment/validator.py",
            file_content=content,
        )
        result = self.service.investigate(input_data)
        data = result.model_dump()

        # The service observes relevance, but NEVER produces verification statuses
        for obs in data["observations"]:
            self.assertNotIn("status", obs)
            self.assertNotIn("VERIFIED", str(obs))
            self.assertNotIn("NOT_VERIFIED", str(obs))
            self.assertNotIn("PARTIALLY_VERIFIED", str(obs))

    # ---------------------------------------------------------------------
    # 14. Empty requirement criteria handled cleanly
    # ---------------------------------------------------------------------

    def test_empty_requirement_criteria_handled_cleanly(self):
        req = RequirementAnalysis(
            requirement_id="REQ-1",
            summary="Requirement analysis: Empty criteria set.",
            criteria=[],
        )
        content = """
def func():
    # TODO: some task
    pass
"""
        input_data = RequirementAwareCodeInvestigationInput(
            requirement_analysis=req,
            file_path="app/code.py",
            file_content=content,
        )
        result = self.service.investigate(input_data)

        self.assertEqual(len(result.observations), 1)
        self.assertIsNone(result.observations[0].criterion_id)
        self.assertEqual(result.observations[0].criterion_ids, [])

    # ---------------------------------------------------------------------
    # Standalone correlate_observation helper test
    # ---------------------------------------------------------------------

    def test_correlate_observation_standalone(self):
        req = self._make_requirement_analysis()
        raw_obs = CodeObservation(
            observation_id="CODE-1",
            file_path="payment/service.py",
            description="Validation logic appears in payment processing flow.",
            symbol="validate_payment_amount",
            observation_type=CodeObservationType.LOGIC,
        )

        correlated = self.service.correlate_observation(raw_obs, req)

        self.assertEqual(correlated.observation_id, "CODE-1")
        self.assertEqual(correlated.criterion_id, "AC-1")
        self.assertIn("AC-1", correlated.criterion_ids)


if __name__ == "__main__":
    unittest.main()
