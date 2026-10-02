"""Contract tests for the published API surface.

These run without a database or a client fixture on purpose. They check the
*contract* -- the shape Dev 1 generates a client from -- and a contract that only
holds when the database happens to be populated is not a contract.

The frozen artefact is `docs/openapi-v1.json`. Regenerating must produce it
byte-for-byte modulo key order. That test is the reason the document is committed
at all: a generated spec that drifts on every run is worse than none, because
reviewers assume it is current. If a test here fails after an intended change,
regenerate and commit the document in the same commit -- never edit it by hand.

Run: `python -m unittest discover tests`
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import Any

from app.api.v1.errors import is_pending
from app.api.v1.router import api_router
from app.main import create_app

FROZEN_SPEC = Path(__file__).resolve().parents[2] / "docs" / "openapi-v1.json"

METHODS = ("get", "post", "put", "patch", "delete")

# Field-name tokens that must never appear on an Orion-derived payload. Orion
# proposes hypotheses with evidence; it does not decide. A field here would mean
# the API had started answering the question the tool exists to leave open.
FORBIDDEN_TOKENS = frozenset(
    {
        "verdict",
        "compliance",
        "compliant",
        "noncompliant",
        "violation",
        "violations",
        "grade",
        "graded",
        "passfail",
        "sanction",
    }
)

# `VerdictIn`, `VerdictOut` and `Verdict` are the *human's* record, entered through
# `POST /verdicts`. They are not derived by Orion, so they are the one place a
# verdict word is legitimate. Scoped to the schema's own name rather than the whole
# document, so `EntitySummaryOut.verdict` would still fail.
HUMAN_RECORD_SCHEMAS = frozenset({"VerdictIn", "VerdictOut", "Verdict"})


def field_tokens(name: str) -> set[str]:
    """Split a field name into lowercase word tokens.

    `snake_case`, `camelCase` and `kebab-case` all collapse to the same set, so a
    rule written against one naming style catches the other two. A bare substring
    test would flag `upgrade_guidance` as a grade field, and a check that cries
    wolf gets switched off.
    """
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return set(re.split(r"[^A-Za-z0-9]+", spaced.lower())) - {""}


def operations(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Every `(METHOD, path, operation)` in the document, methods lowercased."""
    return [
        (method.upper(), path, op)
        for path, ops in sorted(spec["paths"].items())
        for method, op in sorted(ops.items())
        if method in METHODS
    ]


class OpenAPIFrozen(unittest.TestCase):
    """`docs/openapi-v1.json` is the published contract."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = create_app().openapi()

    def test_regeneration_matches_the_committed_document(self) -> None:
        self.assertTrue(
            FROZEN_SPEC.exists(),
            f"{FROZEN_SPEC} is missing. The frozen contract is committed; a spec "
            "that only exists in memory is not something a frontend can build "
            "against.",
        )
        committed = json.loads(FROZEN_SPEC.read_text(encoding="utf-8"))
        self.assertEqual(
            json.dumps(self.spec, sort_keys=True, indent=2),
            json.dumps(committed, sort_keys=True, indent=2),
            "The generated spec no longer matches docs/openapi-v1.json. If the "
            "change was intended, regenerate and commit the document in the same "
            "commit.",
        )

    def test_generation_is_deterministic(self) -> None:
        """Two generations in one process must agree, not merely be close."""
        first = json.dumps(create_app().openapi(), sort_keys=True)
        second = json.dumps(create_app().openapi(), sort_keys=True)
        self.assertEqual(first, second)

    def test_every_operation_is_documented(self) -> None:
        """A route with no description gives a frontend author nothing to build
        against, which is the whole reason these routes are published before the
        services behind them exist."""
        undocumented = [
            f"{method} {path}"
            for method, path, op in operations(self.spec)
            if not op.get("summary") or not op.get("description")
        ]
        self.assertEqual([], undocumented, "operations missing summary/description")

    def test_every_operation_is_tagged_and_typed(self) -> None:
        untagged = [
            f"{method} {path}"
            for method, path, op in operations(self.spec)
            if not op.get("tags")
        ]
        self.assertEqual([], untagged)

        untyped = [
            f"{method} {path}"
            for method, path, op in operations(self.spec)
            if not any(
                (op.get("responses", {}).get(code) or {}).get("content")
                for code in ("200", "201", "202")
            )
        ]
        self.assertEqual([], untyped, "operations with no documented success body")

    def test_declares_openapi_31(self) -> None:
        self.assertTrue(self.spec["openapi"].startswith("3.1"))


class NoVerdictPayloads(unittest.TestCase):
    """Orion never issues a conclusion. Only a human does, via `POST /verdicts`."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = create_app().openapi()

    def test_no_derived_schema_carries_a_conclusion_field(self) -> None:
        violations: list[str] = []
        for schema, definition in sorted(self.spec["components"]["schemas"].items()):
            if schema in HUMAN_RECORD_SCHEMAS:
                continue
            for prop in definition.get("properties") or {}:
                hits = field_tokens(prop) & FORBIDDEN_TOKENS
                if hits:
                    violations.append(f"{schema}.{prop} ({','.join(sorted(hits))})")
        self.assertEqual(
            [],
            violations,
            "Orion-derived payloads must not carry a verdict, compliance or grade "
            "field. A supervisor's own recorded conclusion is the exception and "
            "lives only in the HUMAN_RECORD_SCHEMAS allowlist.",
        )

    def test_the_human_record_is_documented_as_a_human_record(self) -> None:
        op = self.spec["paths"]["/api/v1/verdicts"]["post"]
        self.assertIn("supervisor", (op.get("description") or "").lower())

    def test_a_verdict_word_may_not_appear_on_a_read_path(self) -> None:
        """Stronger than the field scan: no *response* schema reachable from a
        GET may mention one. A route that merely happens not to declare the field
        but embeds a nested schema that does would pass the scan above."""
        read_schemas: set[str] = set()

        def collect(node: Any) -> None:
            if isinstance(node, dict):
                ref = node.get("$ref")
                if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                    read_schemas.add(ref.rsplit("/", 1)[-1])
                for value in node.values():
                    collect(value)
            elif isinstance(node, list):
                for item in node:
                    collect(item)

        for method, _path, op in operations(self.spec):
            if method == "GET":
                collect(op.get("responses", {}))

        offenders = sorted(
            f"{schema}.{prop}"
            for schema in read_schemas
            if schema not in HUMAN_RECORD_SCHEMAS
            for prop in (self.spec["components"]["schemas"]
                         .get(schema, {})
                         .get("properties") or {})
            if field_tokens(prop) & FORBIDDEN_TOKENS
        )
        self.assertEqual([], offenders)


class PendingRoutesAreHonest(unittest.TestCase):
    """A route registered ahead of its service must say so, in the document and
    in the response, and must never look like it works."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = create_app().openapi()

    def test_marker_and_route_agree(self) -> None:
        """`is_pending` reads the same `x-orion` marker `GET /api/v1` reports, so
        the index a frontend reads cannot disagree with what the routes do."""
        marked = {
            f"{method.upper()} {path}"
            for path, ops in self.spec["paths"].items()
            for method, op in ops.items()
            if method in METHODS
            and (op.get("x-orion") or {}).get("status") == "pending"
        }
        from_router = {
            f"{method.upper()} /api/v1{route.path}"
            for route in api_router.routes
            if is_pending(route)
            for method in route.methods
            if method not in ("HEAD", "OPTIONS")
        }
        self.assertEqual(from_router, marked)

    def test_pending_marker_names_service_and_phase(self) -> None:
        incomplete = [
            f"{method} {path}"
            for method, path, op in operations(self.spec)
            if (op.get("x-orion") or {}).get("status") == "pending"
            and not (op["x-orion"].get("service") and op["x-orion"].get("phase"))
        ]
        self.assertEqual([], incomplete)

    def test_pending_routes_document_their_503(self) -> None:
        undocumented = [
            f"{method} {path}"
            for method, path, op in operations(self.spec)
            if (op.get("x-orion") or {}).get("status") == "pending"
            and "503" not in (op.get("responses") or {})
        ]
        self.assertEqual([], undocumented)

    def test_live_routes_carry_no_pending_marker(self) -> None:
        marked_live = [
            f"{method} {path}"
            for method, path, op in operations(self.spec)
            if not (op.get("x-orion") or {}).get("status")
            and op.get("x-orion")
        ]
        self.assertEqual([], marked_live)


class NullIsNotZero(unittest.TestCase):
    """A dimension that could not be assessed has no number, and the schema must
    permit its absence rather than forcing a 0.0 that the UI would chart."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = create_app().openapi()

    def test_assessable_scored_fields_are_nullable(self) -> None:
        required_scores = [
            f"{schema}.{prop}"
            for schema in ("EntityListItem", "DimensionScoreOut")
            for prop, definition in (self.spec["components"]["schemas"]
                                     .get(schema, {})
                                     .get("properties") or {}).items()
            if prop in ("score", "egi", "nsi", "dts", "sap")
            and "score" not in (definition or {})
            and prop in (definition or {}).get("required", [])
        ]
        self.assertEqual([], required_scores)

    def test_sap_tier_can_be_not_assessable(self) -> None:
        """`not_assessable` must be a state of its own, not a low tier. The enum is
        the frozen record of that decision, so it is asserted against the document
        rather than against the Python enum."""
        tier = self.spec["components"]["schemas"].get("AttentionTier", {})
        self.assertIn("not_assessable", tier.get("enum", []))
        for level in ("T1", "T2", "T3", "T4"):
            self.assertIn(level, tier.get("enum", []))
        self.assertEqual(
            tier.get("enum"),
            ["T1", "T2", "T3", "T4", "not_assessable"],
            "the tier set changed; re-check every place that branches on it",
        )
        listed = self.spec["components"]["schemas"]["EntityListItem"]["properties"]
        self.assertEqual(
            "#/components/schemas/AttentionTier",
            listed["sap_tier"].get("$ref"),
        )


def tearDownModule() -> None:  # noqa: N802
    from tests import cleanup

    cleanup()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()