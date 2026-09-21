"""
Tests for CodeInvestigationService and CodeInvestigation schemas
================================================================
Validates deterministic source code observations (TODO markers, bare excepts,
debug prints, credential patterns) and observation DTO contracts.
"""

import unittest
from pydantic import ValidationError

from app.investigation.schemas import (
    CodeInvestigationResult,
    CodeObservation,
    CodeObservationType,
    ObservationType,
)
from app.investigation.service import CodeInvestigationService


# =========================================================================
# Schema Tests
# =========================================================================


class TestCodeObservationTypeEnum(unittest.TestCase):
    """Tests for CodeObservationType enum values and behavior."""

    def test_enum_values(self):
        expected = {
            "LOGIC",
            "CONTROL_FLOW",
            "DATA_FLOW",
            "API",
            "ERROR_HANDLING",
            "SECURITY",
            "CONFIGURATION",
            "TESTABILITY",
            "UNKNOWN",
        }
        actual = {t.value for t in CodeObservationType}
        self.assertEqual(actual, expected)

    def test_alias_is_identical_class(self):
        self.assertIs(ObservationType, CodeObservationType)

    def test_enum_from_string(self):
        self.assertEqual(CodeObservationType("SECURITY"), CodeObservationType.SECURITY)
        self.assertEqual(CodeObservationType("LOGIC"), CodeObservationType.LOGIC)

    def test_invalid_type_rejected(self):
        with self.assertRaises(ValueError):
            CodeObservationType("NON_EXISTENT")


class TestCodeObservationSchema(unittest.TestCase):
    """Tests for CodeObservation DTO validation and consistency."""

    def test_valid_observation(self):
        obs = CodeObservation(
            observation_id="CODE-1",
            file_path="app/payment/service.py",
            description="Bare exception handler observed; potential exception swallowing",
            symbol="process_payment",
            start_line=45,
            end_line=45,
            observation_type=CodeObservationType.ERROR_HANDLING,
        )
        self.assertEqual(obs.observation_id, "CODE-1")
        self.assertEqual(obs.file_path, "app/payment/service.py")
        self.assertEqual(obs.symbol, "process_payment")
        self.assertEqual(obs.start_line, 45)
        self.assertEqual(obs.end_line, 45)
        self.assertEqual(obs.observation_type, CodeObservationType.ERROR_HANDLING)

    def test_observation_id_format_validation(self):
        # Valid formats
        for valid_id in ["CODE-1", "CODE-2", "CODE-42", "CODE-999"]:
            obs = CodeObservation(
                observation_id=valid_id,
                file_path="app/main.py",
                description="TODO marker observed",
                observation_type=CodeObservationType.LOGIC,
            )
            self.assertEqual(obs.observation_id, valid_id)

        # Invalid formats
        for invalid_id in ["code-1", "OBS-1", "FIND-1", "EV-1", "123", "", "CODE-"]:
            with self.assertRaises(ValidationError):
                CodeObservation(
                    observation_id=invalid_id,
                    file_path="app/main.py",
                    description="TODO marker observed",
                    observation_type=CodeObservationType.LOGIC,
                )

    def test_empty_file_path_rejected(self):
        with self.assertRaises(ValidationError):
            CodeObservation(
                observation_id="CODE-1",
                file_path="",
                description="A test observation",
                observation_type=CodeObservationType.LOGIC,
            )

    def test_whitespace_file_path_rejected(self):
        with self.assertRaises(ValidationError):
            CodeObservation(
                observation_id="CODE-1",
                file_path="   ",
                description="A test observation",
                observation_type=CodeObservationType.LOGIC,
            )

    def test_empty_description_rejected(self):
        with self.assertRaises(ValidationError):
            CodeObservation(
                observation_id="CODE-1",
                file_path="app/main.py",
                description="",
                observation_type=CodeObservationType.LOGIC,
            )

    def test_whitespace_description_rejected(self):
        with self.assertRaises(ValidationError):
            CodeObservation(
                observation_id="CODE-1",
                file_path="app/main.py",
                description="   ",
                observation_type=CodeObservationType.LOGIC,
            )

    def test_symbol_normalization(self):
        # None remains None
        obs = CodeObservation(
            observation_id="CODE-1",
            file_path="app/main.py",
            description="Test",
            symbol=None,
            observation_type=CodeObservationType.LOGIC,
        )
        self.assertIsNone(obs.symbol)

        # Whitespace-only becomes None
        obs2 = CodeObservation(
            observation_id="CODE-1",
            file_path="app/main.py",
            description="Test",
            symbol="   ",
            observation_type=CodeObservationType.LOGIC,
        )
        self.assertIsNone(obs2.symbol)

        # Non-empty is trimmed
        obs3 = CodeObservation(
            observation_id="CODE-1",
            file_path="app/main.py",
            description="Test",
            symbol="  my_func  ",
            observation_type=CodeObservationType.LOGIC,
        )
        self.assertEqual(obs3.symbol, "my_func")

    def test_line_numbers_must_be_ge_1(self):
        with self.assertRaises(ValidationError):
            CodeObservation(
                observation_id="CODE-1",
                file_path="app/main.py",
                description="Test",
                start_line=0,
                observation_type=CodeObservationType.LOGIC,
            )
        with self.assertRaises(ValidationError):
            CodeObservation(
                observation_id="CODE-1",
                file_path="app/main.py",
                description="Test",
                end_line=0,
                observation_type=CodeObservationType.LOGIC,
            )

    def test_end_line_must_be_ge_start_line(self):
        with self.assertRaises(ValidationError) as ctx:
            CodeObservation(
                observation_id="CODE-1",
                file_path="app/main.py",
                description="Test",
                start_line=50,
                end_line=40,
                observation_type=CodeObservationType.LOGIC,
            )
        self.assertIn("must be >= start_line", str(ctx.exception))

    def test_single_line_numbers_allowed(self):
        # Only start_line
        obs1 = CodeObservation(
            observation_id="CODE-1",
            file_path="app/main.py",
            description="Test",
            start_line=10,
            observation_type=CodeObservationType.LOGIC,
        )
        self.assertEqual(obs1.start_line, 10)
        self.assertIsNone(obs1.end_line)

        # Only end_line
        obs2 = CodeObservation(
            observation_id="CODE-1",
            file_path="app/main.py",
            description="Test",
            end_line=10,
            observation_type=CodeObservationType.LOGIC,
        )
        self.assertEqual(obs2.end_line, 10)
        self.assertIsNone(obs2.start_line)


class TestCodeInvestigationResultSchema(unittest.TestCase):
    """Tests for CodeInvestigationResult container validation."""

    def test_valid_result(self):
        res = CodeInvestigationResult(
            file_path="app/services/order.py",
            observations=[
                CodeObservation(
                    observation_id="CODE-1",
                    file_path="app/services/order.py",
                    description="TODO marker observed: check inventory",
                    observation_type=CodeObservationType.LOGIC,
                ),
            ],
        )
        self.assertEqual(res.file_path, "app/services/order.py")
        self.assertEqual(len(res.observations), 1)

    def test_duplicate_observation_id_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            CodeInvestigationResult(
                file_path="app/main.py",
                observations=[
                    CodeObservation(
                        observation_id="CODE-1",
                        file_path="app/main.py",
                        description="Obs 1",
                        observation_type=CodeObservationType.LOGIC,
                    ),
                    CodeObservation(
                        observation_id="CODE-1",
                        file_path="app/main.py",
                        description="Obs 2",
                        observation_type=CodeObservationType.SECURITY,
                    ),
                ],
            )
        self.assertIn("duplicate", str(ctx.exception).lower())

    def test_mismatched_observation_file_path_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            CodeInvestigationResult(
                file_path="app/main.py",
                observations=[
                    CodeObservation(
                        observation_id="CODE-1",
                        file_path="app/other.py",
                        description="Obs 1",
                        observation_type=CodeObservationType.LOGIC,
                    ),
                ],
            )
        self.assertIn("does not match result file_path", str(ctx.exception))

    def test_serialization_round_trip(self):
        res = CodeInvestigationResult(
            file_path="app/api/auth.py",
            observations=[
                CodeObservation(
                    observation_id="CODE-1",
                    file_path="app/api/auth.py",
                    description="Potential hard-coded credential pattern observed",
                    symbol="login",
                    start_line=15,
                    end_line=15,
                    observation_type=CodeObservationType.SECURITY,
                )
            ],
        )
        data = res.model_dump()
        self.assertEqual(data["file_path"], "app/api/auth.py")
        self.assertEqual(len(data["observations"]), 1)
        obs_data = data["observations"][0]
        self.assertEqual(obs_data["observation_id"], "CODE-1")
        self.assertEqual(obs_data["symbol"], "login")
        self.assertEqual(obs_data["observation_type"], "SECURITY")


# =========================================================================
# Service Tests
# =========================================================================


class TestCodeInvestigationService(unittest.TestCase):
    """
    Tests for CodeInvestigationService covering all deterministic observations,
    edge cases, and semantic boundaries.
    """

    def setUp(self):
        self.service = CodeInvestigationService()

    # ---------------------------------------------------------------------
    # 1. Valid code with no observations
    # ---------------------------------------------------------------------

    def test_valid_clean_code_has_no_observations(self):
        content = """
def calculate_tax(amount: float, rate: float) -> float:
    \"\"\"Calculate sales tax for an amount.\"\"\"
    if amount < 0 or rate < 0:
        raise ValueError("Amount and rate must be non-negative")
    return round(amount * rate, 2)
"""
        result = self.service.investigate("app/tax.py", content)
        self.assertEqual(result.file_path, "app/tax.py")
        self.assertEqual(len(result.observations), 0)

    # ---------------------------------------------------------------------
    # 2. TODO / FIXME observation
    # ---------------------------------------------------------------------

    def test_todo_observation(self):
        content = """
def process_order(order_id: str):
    # TODO: implement idempotency check
    save_order(order_id)
"""
        result = self.service.investigate("app/orders.py", content)
        self.assertEqual(len(result.observations), 1)
        obs = result.observations[0]
        self.assertEqual(obs.observation_id, "CODE-1")
        self.assertEqual(obs.file_path, "app/orders.py")
        self.assertEqual(obs.observation_type, CodeObservationType.LOGIC)
        self.assertEqual(obs.start_line, 3)
        self.assertEqual(obs.end_line, 3)
        self.assertIn("TODO", obs.description)
        self.assertIn("implement idempotency check", obs.description)
        self.assertEqual(obs.symbol, "process_order")

    def test_fixme_observation(self):
        content = """
def handle_error():
    # FIXME: memory leak in buffer cleanup
    pass
"""
        result = self.service.investigate("app/buffer.py", content)
        self.assertEqual(len(result.observations), 1)
        obs = result.observations[0]
        self.assertEqual(obs.observation_type, CodeObservationType.LOGIC)
        self.assertIn("FIXME", obs.description)
        self.assertIn("memory leak", obs.description)

    # ---------------------------------------------------------------------
    # 3. Bare exception observation
    # ---------------------------------------------------------------------

    def test_bare_exception_observation(self):
        content = """
def fetch_data():
    try:
        response = http_client.get("/data")
        return response.json()
    except:
        return None
"""
        result = self.service.investigate("app/client.py", content)
        self.assertEqual(len(result.observations), 1)
        obs = result.observations[0]
        self.assertEqual(obs.observation_id, "CODE-1")
        self.assertEqual(obs.observation_type, CodeObservationType.ERROR_HANDLING)
        self.assertEqual(obs.start_line, 6)
        self.assertEqual(obs.end_line, 6)
        self.assertIn("bare exception", obs.description.lower())
        self.assertEqual(obs.symbol, "fetch_data")

    # ---------------------------------------------------------------------
    # 4. Debug print / log observation
    # ---------------------------------------------------------------------

    def test_python_print_observation(self):
        content = """
def authenticate_user(username, password):
    print(f"DEBUG: authenticating {username}")
    return True
"""
        result = self.service.investigate("app/auth.py", content)
        self.assertEqual(len(result.observations), 1)
        obs = result.observations[0]
        self.assertEqual(obs.observation_type, CodeObservationType.CONTROL_FLOW)
        self.assertEqual(obs.start_line, 3)
        self.assertIn("debug print", obs.description.lower())
        self.assertEqual(obs.symbol, "authenticate_user")

    def test_javascript_console_log_observation(self):
        content = """
function calculateTotal(items) {
    console.log("Items count:", items.length);
    return items.reduce((a, b) => a + b, 0);
}
"""
        result = self.service.investigate("src/cart.js", content)
        self.assertEqual(len(result.observations), 1)
        obs = result.observations[0]
        self.assertEqual(obs.observation_type, CodeObservationType.CONTROL_FLOW)
        self.assertEqual(obs.start_line, 3)
        self.assertEqual(obs.symbol, "calculateTotal")

    # ---------------------------------------------------------------------
    # 5. Conservative credential-pattern observation
    # ---------------------------------------------------------------------

    def test_hardcoded_api_key_observation(self):
        content = """
class PaymentGateway:
    api_key = "secret_key_production_998877"
"""
        result = self.service.investigate("app/gateway.py", content)
        self.assertEqual(len(result.observations), 1)
        obs = result.observations[0]
        self.assertEqual(obs.observation_type, CodeObservationType.SECURITY)
        self.assertEqual(obs.start_line, 3)
        self.assertEqual(obs.description, "Potential hard-coded credential pattern observed")
        self.assertEqual(obs.symbol, "PaymentGateway")

    def test_hardcoded_token_literal_observation(self):
        content = """
def get_auth_header():
    token = "ghp_1234567890abcdefghijklmnopqrstuv"
    return {"Authorization": f"Bearer {token}"}
"""
        result = self.service.investigate("app/github.py", content)
        self.assertEqual(len(result.observations), 1)
        obs = result.observations[0]
        self.assertEqual(obs.observation_type, CodeObservationType.SECURITY)
        self.assertEqual(obs.description, "Potential hard-coded credential pattern observed")

    # ---------------------------------------------------------------------
    # 6. Multiple observations
    # ---------------------------------------------------------------------

    def test_multiple_observations_in_single_file(self):
        content = """
def risky_operation():
    api_key = "secret_abc123_key456"
    print("starting operation")
    # TODO: add validation
    try:
        call_api()
    except:
        pass
"""
        result = self.service.investigate("app/risky.py", content)
        self.assertEqual(len(result.observations), 4)

        # Check sequential IDs
        self.assertEqual(result.observations[0].observation_id, "CODE-1")
        self.assertEqual(result.observations[1].observation_id, "CODE-2")
        self.assertEqual(result.observations[2].observation_id, "CODE-3")
        self.assertEqual(result.observations[3].observation_id, "CODE-4")

        # Check types
        types = [obs.observation_type for obs in result.observations]
        self.assertEqual(
            types,
            [
                CodeObservationType.SECURITY,
                CodeObservationType.CONTROL_FLOW,
                CodeObservationType.LOGIC,
                CodeObservationType.ERROR_HANDLING,
            ],
        )

        # All point to same file_path
        for obs in result.observations:
            self.assertEqual(obs.file_path, "app/risky.py")
            self.assertEqual(obs.symbol, "risky_operation")

    # ---------------------------------------------------------------------
    # 7. Empty content
    # ---------------------------------------------------------------------

    def test_empty_content(self):
        result = self.service.investigate("app/empty.py", "")
        self.assertEqual(result.file_path, "app/empty.py")
        self.assertEqual(result.observations, [])

    # ---------------------------------------------------------------------
    # 8. Whitespace-only content
    # ---------------------------------------------------------------------

    def test_whitespace_only_content(self):
        result = self.service.investigate("app/blank.py", "   \n\t  \n  ")
        self.assertEqual(result.file_path, "app/blank.py")
        self.assertEqual(result.observations, [])

    # ---------------------------------------------------------------------
    # 9. Invalid file path
    # ---------------------------------------------------------------------

    def test_empty_file_path_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.service.investigate("", "print('hi')")

    def test_whitespace_file_path_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.service.investigate("   ", "print('hi')")

    # ---------------------------------------------------------------------
    # 10. Invalid line ranges
    # ---------------------------------------------------------------------

    def test_invalid_line_ranges_rejected_by_schema(self):
        with self.assertRaises(ValidationError):
            CodeObservation(
                observation_id="CODE-1",
                file_path="app/test.py",
                description="Test",
                start_line=10,
                end_line=5,
                observation_type=CodeObservationType.LOGIC,
            )

    # ---------------------------------------------------------------------
    # 11. Deterministic repeated execution
    # ---------------------------------------------------------------------

    def test_deterministic_repeated_execution(self):
        content = """
def my_func():
    print("hello")
    # TODO: write tests
"""
        res1 = self.service.investigate("app/func.py", content)
        res2 = self.service.investigate("app/func.py", content)

        self.assertEqual(res1.model_dump(), res2.model_dump())

    # ---------------------------------------------------------------------
    # 12. Unique observation IDs
    # ---------------------------------------------------------------------

    def test_unique_observation_ids(self):
        content = """
print("1")
print("2")
print("3")
# TODO: a
# TODO: b
"""
        result = self.service.investigate("app/multi.py", content)
        ids = [obs.observation_id for obs in result.observations]
        self.assertEqual(len(ids), 5)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids, ["CODE-1", "CODE-2", "CODE-3", "CODE-4", "CODE-5"])

    # ---------------------------------------------------------------------
    # 13. Observation is NOT represented as a Finding
    # ---------------------------------------------------------------------

    def test_observation_is_not_finding(self):
        content = """
def insecure():
    password = "super_secret_password_123"
"""
        result = self.service.investigate("app/sec.py", content)
        data = result.model_dump()

        self.assertNotIn("findings", data)
        self.assertNotIn("finding_ids", data)

        obs_data = data["observations"][0]
        # Findings have finding_id, status, evidence_ids
        self.assertNotIn("finding_id", obs_data)
        self.assertNotIn("status", obs_data)
        self.assertNotIn("evidence_ids", obs_data)
        self.assertIn("observation_id", obs_data)
        self.assertIn("observation_type", obs_data)

    # ---------------------------------------------------------------------
    # 14. Large file threshold
    # ---------------------------------------------------------------------

    def test_large_file_observation(self):
        service = CodeInvestigationService(large_file_threshold=50)
        content = "\n".join([f"x = {i}" for i in range(60)])
        result = service.investigate("app/big.py", content)

        self.assertGreaterEqual(len(result.observations), 1)
        large_obs = result.observations[0]
        self.assertEqual(large_obs.observation_type, CodeObservationType.CONFIGURATION)
        self.assertIn("Large source file observed", large_obs.description)
        self.assertEqual(large_obs.start_line, 1)
        self.assertEqual(large_obs.end_line, 60)

    def test_invalid_service_threshold_raises_value_error(self):
        with self.assertRaises(ValueError):
            CodeInvestigationService(large_file_threshold=0)
        with self.assertRaises(ValueError):
            CodeInvestigationService(large_file_threshold=-5)


if __name__ == "__main__":
    unittest.main()
