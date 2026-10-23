"""Offline tests for the data catalog (catalog_service.py, GET /catalog/...).
MySQL, Ollama (urllib) and Qdrant are made to fail if touched; source files are read as text only.

Run from the project root:
    python -m unittest discover -s tests -v
"""

import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from backend.app.config import DATA_CATALOG_FILE, SCHEMA_FILE
from backend.app.controllers import catalog_controller
from backend.app.main import create_app
from backend.app.services import catalog_service as cs
from backend.app.services.catalog_service import CatalogService, CatalogUnavailableError

FORBIDDEN = [
    mock.patch("mysql.connector.connect", side_effect=AssertionError("catalog must not connect to MySQL")),
    mock.patch("urllib.request.urlopen", side_effect=AssertionError("catalog must not call Ollama")),
    mock.patch("qdrant_client.QdrantClient.__init__", side_effect=AssertionError("catalog must not open Qdrant")),
]


def temp_monitoring():
    """A throwaway monitoring store, so tests never write to storage/monitoring."""
    import tempfile
    from pathlib import Path as _Path

    from backend.app.repositories.monitoring_repository import MonitoringRepository
    return MonitoringRepository(_Path(tempfile.mkdtemp()) / "monitoring.sqlite3", 30, 1000)


def setUpModule() -> None:
    for patcher in FORBIDDEN:
        patcher.start()


def tearDownModule() -> None:
    for patcher in FORBIDDEN:
        patcher.stop()


def schema_text_facts() -> dict[str, dict[str, tuple[bool, list[str] | None]]]:
    """Independent line-based reading of schema.sql: {table: {column: (nullable, enum values)}}."""
    facts, table = {}, None
    for line in SCHEMA_FILE.read_text(encoding="utf-8").splitlines():
        if m := re.match(r"CREATE TABLE (\w+)", line):
            table = m.group(1)
            facts[table] = {}
        elif table and (m := re.match(r"\s+([a-z_]+)\s+(\w+)(\(([^)]*)\))?.*?\b(NOT NULL|NULL)\b", line)):
            name, base, _, params, null = m.groups()
            enum = re.findall(r"'([^']*)'", params) if base == "ENUM" else None
            facts[table][name] = (null == "NULL", enum)
    return facts


class CatalogFromRealSources(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.service = CatalogService()

    def test_exactly_the_five_tables_in_schema_order(self) -> None:
        names = [t.name for t in self.service.list_tables().tables]
        self.assertEqual(names, ["patients", "doctors", "appointments", "treatments", "billing"])

    def test_summaries_have_descriptions_grain_and_counts(self) -> None:
        billing = next(t for t in self.service.list_tables().tables if t.name == "billing")
        self.assertEqual((billing.display_name, billing.column_count), ("Billing", 7))
        self.assertEqual(billing.description, "Stores billing records associated with patients and treatments.")
        self.assertEqual(billing.grain, "One row per bill.")
        self.assertNotIn("row_count", billing.model_dump())

    def test_billing_relationships(self) -> None:
        billing = self.service.get_table("billing")
        self.assertEqual([(f.name, f.columns, f.references_table, f.references_columns) for f in billing.foreign_keys], [
            ("fk_billing_patient", ["patient_id"], "patients", ["patient_id"]),
            ("fk_billing_treatment", ["treatment_id"], "treatments", ["treatment_id"]),
        ])
        col = {c.name: c for c in billing.columns}
        self.assertEqual((col["patient_id"].references.table, col["patient_id"].references.column), ("patients", "patient_id"))
        self.assertEqual(col["treatment_id"].references.table, "treatments")
        incoming = {(r.from_table, tuple(r.from_columns)) for r in self.service.get_table("patients").referenced_by}
        self.assertEqual(incoming, {("appointments", ("patient_id",)), ("billing", ("patient_id",))})
        self.assertEqual([r.from_table for r in self.service.get_table("treatments").referenced_by], ["billing"])

    def test_email_uniqueness(self) -> None:
        patients = {c.name: c for c in self.service.get_table("patients").columns}
        doctors = {c.name: c for c in self.service.get_table("doctors").columns}
        self.assertNotIn("unique", patients["email"].key_roles)
        self.assertIn("unique", doctors["email"].key_roles)
        self.assertIn("unique", patients["insurance_number"].key_roles)
        self.assertEqual([(u.name, u.columns) for u in self.service.get_table("doctors").unique_constraints],
                         [("uq_doctors_email", ["email"])])

    def test_unique_constraints_are_separate_from_indexes(self) -> None:
        billing = self.service.get_table("billing")
        self.assertEqual(([(i.name, i.columns) for i in billing.indexes], billing.unique_constraints),
                         ([("idx_billing_date", ["bill_date"])], []))
        self.assertEqual({c.name: c.key_roles for c in billing.columns}["bill_date"], ["indexed"])

    def test_nullability_and_enums_match_schema_sql(self) -> None:
        facts = schema_text_facts()
        self.assertEqual(set(facts), {"patients", "doctors", "appointments", "treatments", "billing"})
        for table, columns in facts.items():
            detail = {c.name: c for c in self.service.get_table(table).columns}
            self.assertEqual(set(detail), set(columns), table)
            for name, (nullable, enum) in columns.items():
                with self.subTest(column=f"{table}.{name}"):
                    self.assertEqual(detail[name].nullable, nullable)
                    self.assertEqual(detail[name].allowed_values, enum)
                    self.assertEqual(detail[name].data_type == "ENUM", enum is not None)
        nullable = sorted(f"{t}.{c}" for t, cols in facts.items() for c, (n, _) in cols.items() if n)
        self.assertEqual(nullable, ["patients.email", "patients.insurance_number", "patients.insurance_provider",
                                    "treatments.description"])

    def test_types_and_descriptions(self) -> None:
        col = {c.name: c for c in self.service.get_table("billing").columns}
        self.assertEqual((col["amount"].data_type, col["amount"].description), ("DECIMAL(10, 2)", "Non-negative billed amount."))
        self.assertEqual({c.name: c.data_type for c in self.service.get_table("doctors").columns}["years_experience"],
                         "TINYINT UNSIGNED")
        self.assertEqual(col["payment_status"].allowed_values, ["Paid", "Pending", "Failed"])

    def test_no_secrets_paths_or_sql_in_output(self) -> None:
        dumped = str([self.service.get_table(t.name).model_dump() for t in self.service.list_tables().tables])
        for forbidden in ("DROP", "CREATE", "GRANT", "IDENTIFIED", "password", ".sql", ".md", ":\\", "INSERT"):
            self.assertNotIn(forbidden, dumped)


class TemporarySources(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.schema, self.catalog = self.dir / "schema.sql", self.dir / "data_catalog.md"
        shutil.copy(SCHEMA_FILE, self.schema)
        shutil.copy(DATA_CATALOG_FILE, self.catalog)

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_undocumented_fields_are_null(self) -> None:
        text = self.catalog.read_text(encoding="utf-8")
        text = text.replace("- amount: Non-negative billed amount.\n", "").replace("Purpose: Stores doctor information.\n", "")
        self.catalog.write_text(text, encoding="utf-8")
        service = CatalogService(self.schema, self.catalog)
        self.assertIsNone({c.name: c for c in service.get_table("billing").columns}["amount"].description)
        self.assertIsNone(service.get_table("doctors").description)
        self.assertEqual(service.get_table("doctors").grain, "One row per doctor.")

    def test_composite_constraints_are_preserved(self) -> None:
        self.schema.write_text(
            "CREATE TABLE parent (a INT NOT NULL, b INT NOT NULL, PRIMARY KEY (a, b)) ENGINE=InnoDB;\n"
            "CREATE TABLE child (x INT NOT NULL, y INT NOT NULL, z INT, PRIMARY KEY (x), "
            "UNIQUE KEY uq_yz (y, z), KEY idx_zy (z, y), "
            "CONSTRAINT fk_parent FOREIGN KEY (y, z) REFERENCES parent (a, b)) ENGINE=InnoDB;\n", encoding="utf-8")
        service = CatalogService(self.schema, self.catalog)
        parent, child = service.get_table("parent"), service.get_table("child")
        self.assertEqual(parent.primary_key, ["a", "b"])
        self.assertEqual([(u.name, u.columns) for u in child.unique_constraints], [("uq_yz", ["y", "z"])])
        self.assertEqual([(i.name, i.columns) for i in child.indexes], [("idx_zy", ["z", "y"])])
        self.assertEqual([(f.columns, f.references_columns) for f in child.foreign_keys], [(["y", "z"], ["a", "b"])])
        roles = {c.name: c.key_roles for c in child.columns}
        self.assertEqual(roles["y"], ["unique_part", "foreign_key", "indexed"])
        self.assertIsNone({c.name: c for c in child.columns}["y"].references, "no single-column reference for composite FK")
        self.assertTrue({c.name: c for c in child.columns}["z"].nullable)
        self.assertEqual(parent.referenced_by[0].from_columns, ["y", "z"])

    def test_missing_or_invalid_sources(self) -> None:
        cases = {
            "missing schema": lambda: self.schema.unlink(),
            "missing catalog": lambda: self.catalog.unlink(),
            "no tables": lambda: self.schema.write_text("-- nothing here\n", encoding="utf-8"),
            "unexpected statement": lambda: self.schema.write_text("CREATE INDEX i ON t (a);", encoding="utf-8"),
            "dangling foreign key": lambda: self.schema.write_text(
                "CREATE TABLE a (x INT, CONSTRAINT fk FOREIGN KEY (x) REFERENCES missing (y)) ENGINE=InnoDB;", encoding="utf-8"),
        }
        for label, breaker in cases.items():
            with self.subTest(label):
                self.setUp()
                breaker()
                with self.assertRaises(CatalogUnavailableError):
                    CatalogService(self.schema, self.catalog).list_tables()

    def test_parsed_once_and_failures_are_not_cached(self) -> None:
        self.catalog.unlink()
        service = CatalogService(self.schema, self.catalog)
        with self.assertRaises(CatalogUnavailableError):
            service.list_tables()
        shutil.copy(DATA_CATALOG_FILE, self.catalog)
        with mock.patch.object(cs, "build_catalog", wraps=cs.build_catalog) as build:
            for _ in range(3):
                service.list_tables()
                service.get_table("billing")
        self.assertEqual(build.call_count, 1)


class CatalogApi(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app(open_vector_store=False, monitoring=temp_monitoring())
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)

    def test_list_and_detail(self) -> None:
        r = self.client.get("/catalog/tables")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([t["name"] for t in r.json()["tables"]], ["patients", "doctors", "appointments", "treatments", "billing"])
        r = self.client.get("/catalog/tables/billing")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual((body["name"], body["column_count"], len(body["columns"]), body["primary_key"]), ("billing", 7, 7, ["bill_id"]))

    def test_unknown_tables_are_404(self) -> None:
        for name in ("nurses", "Billing", "billing%3B DROP TABLE billing", "information_schema", "%2E%2E"):
            with self.subTest(name):
                r = self.client.get(f"/catalog/tables/{name}")
                self.assertEqual((r.status_code, r.json()["status"]), (404, "not_found"))
        # An encoded slash is decoded by the router, so this never reaches the catalog route at all.
        r = self.client.get("/catalog/tables/..%2F..%2F.env")
        self.assertEqual(r.status_code, 404)
        self.assertNotIn("PASSWORD", r.text)

    def test_unavailable_sources_give_a_sanitized_503(self) -> None:
        self.app.dependency_overrides[catalog_controller.get_catalog_service] = lambda: CatalogService(
            Path(tempfile.gettempdir()) / "medlens-missing-schema.sql", DATA_CATALOG_FILE)
        for path in ("/catalog/tables", "/catalog/tables/billing"):
            r = self.client.get(path)
            self.assertEqual((r.status_code, r.json()), (503, {"status": "unavailable",
                                                               "message": "The data catalog is not available right now."}))
            for leaked in ("medlens-missing", "schema.sql", "Traceback", "Errno", ":\\"):
                self.assertNotIn(leaked, r.text)

    def test_chat_is_unaffected(self) -> None:
        r = self.client.post("/chat", json={"question": "Hello"})
        self.assertEqual((r.status_code, r.json()["status"]), (200, "conversation"))


if __name__ == "__main__":
    unittest.main()
