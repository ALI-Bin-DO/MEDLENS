"""Offline tests for GET /admin/system and GET /admin/config (system_status.py).
Ollama, MySQL and the index are fakes (or a temporary Qdrant store); mysql.connector.connect is disabled.

Run from the project root:
    python -m unittest discover -s tests -v
"""

import asyncio
import dataclasses
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import mysql.connector
from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.controllers import admin_controller
from backend.app.main import create_app
from backend.app.repositories.monitoring_repository import MonitoringRepository
from backend.app.repositories.mysql_repository import MySQLRepository
from backend.app.repositories.vector_repository import IndexNotFoundError, VectorRepository
from backend.app.services import system_status as ss
from backend.app.services.ollama_client import OllamaError
from backend.app.services.rag.chunker import chunk_documents
from backend.app.services.rag.context_builder import expected_schema_chunks
from backend.app.services.rag.document_loader import load_documents

_no_db = mock.patch("mysql.connector.connect", side_effect=AssertionError("tests must never connect to MySQL"))
SQL_MODEL, EMBED_MODEL = "qwen3.5:4b", "nomic-embed-text:v1.5"
SECRET_PASSWORD, SECRET_USER = "pw-SECRET-4411", "user-SECRET-4411"
TAGS = {"models": [{"name": SQL_MODEL, "details": {"parameter_size": "4.2B", "quantization_level": "Q4_K_M"}},
                   {"name": EMBED_MODEL, "details": {"parameter_size": "137M", "quantization_level": "F16"}}]}


def setUpModule() -> None:
    _no_db.start()


def tearDownModule() -> None:
    _no_db.stop()


def settings(**overrides):
    base = dict(ollama_sql_model=SQL_MODEL, ollama_embedding_model=EMBED_MODEL, mysql_readonly_user=SECRET_USER,
                mysql_readonly_password=SECRET_PASSWORD, mysql_host="db.internal.example", system_status_cache_seconds=15,
                system_probe_timeout_seconds=1)
    base.update(overrides)
    return dataclasses.replace(get_settings(), **base)


class FakeVectors:
    def __init__(self, points=17, size=768, model=EMBED_MODEL, schema=None, missing=False):
        self.points, self.size, self.model, self.missing = points, size, model, missing
        self.schema = expected_schema_chunks() if schema is None else schema
        self.lock = threading.RLock()

    def info(self):
        if self.missing:
            raise IndexNotFoundError("missing")
        return {"points": self.points, "vector_size": self.size, "distance": "Cosine",
                "metadata": {"embedding_model": self.model, "vector_size": self.size}}

    def count_by_type(self, types):
        return {"schema": 5, "documentation": 7, "example": 5}

    def get_chunks_by_type(self, chunk_type):
        return self.schema


class FakeMySQL:
    def __init__(self, error=None, delay=0.0):
        self.error, self.delay, self.calls = error, delay, 0

    def check_connectivity(self, timeout_seconds):
        self.calls += 1
        time.sleep(self.delay)
        if self.error:
            raise self.error


class OllamaFake:
    """Replaces get_json/post_json in system_status; records every path used."""

    def __init__(self, tags=TAGS, ps=None, version="0.40.8", error=None, show_size=768):
        self.tags, self.ps, self.version, self.error, self.show_size = tags, ps or {"models": []}, version, error, show_size
        self.paths = []

    def get_json(self, base, path, timeout, error=OllamaError):
        self.paths.append(path)
        if self.error:
            raise self.error
        return {"/api/version": {"version": self.version}, "/api/tags": self.tags, "/api/ps": self.ps}[path]

    def post_json(self, base, path, body, timeout, error=OllamaError):
        self.paths.append(path)
        return {"model_info": {"nomic-bert.embedding_length": self.show_size}}

    def patch(self):
        return mock.patch.multiple(ss, get_json=self.get_json, post_json=self.post_json)


def service(cfg=None, vectors="default", mysql_repo=None, clock=time.monotonic):
    vectors = FakeVectors() if vectors == "default" else vectors
    return ss.SystemStatusService(cfg or settings(), lambda: vectors, mysql_repo or FakeMySQL(), "0.1.0", time.time(), clock)


def snapshot(svc, ollama=None):
    with (ollama or OllamaFake()).patch():
        return asyncio.run(svc.get()).components


class HealthyStack(unittest.TestCase):
    def test_all_components_and_installed_but_unloaded_models(self) -> None:
        c = snapshot(service())
        self.assertEqual((c.api.status, c.ollama.status, c.mysql.status, c.index.status), ("ok", "ok", "ok", "ok"))
        for m in (c.models.sql, c.models.embedding):
            self.assertEqual((m.status, m.reason, m.installed, m.loaded, m.inference_verified),
                             ("ok", "installed_not_loaded", True, False, False))
        self.assertEqual((c.models.sql.parameter_size, c.models.sql.quantization), ("4.2B", "Q4_K_M"))
        self.assertEqual((c.index.points, c.index.counts_by_type), (17, {"schema": 5, "documentation": 7, "example": 5}))
        self.assertEqual((c.index.vector_size, c.index.expected_vector_size, c.index.vector_size_matches), (768, 768, True))
        self.assertEqual((c.index.embedding_model_matches, c.index.schema_matches_schema_sql), (True, True))
        self.assertEqual(c.ollama.loaded_models, [])

    def test_loaded_model_is_reported(self) -> None:
        c = snapshot(service(), OllamaFake(ps={"models": [{"name": SQL_MODEL}]}))
        self.assertEqual((c.models.sql.loaded, c.models.sql.reason), (True, "installed_loaded"))
        self.assertEqual((c.models.embedding.loaded, c.models.embedding.status), (False, "ok"))
        self.assertEqual(c.ollama.loaded_models, [SQL_MODEL])


class IndependentFailures(unittest.TestCase):
    def test_ollama_down_does_not_hide_mysql_or_index(self) -> None:
        for kind, reason in (("unavailable", "unreachable"), ("timeout", "timeout")):
            with self.subTest(kind):
                c = snapshot(service(), OllamaFake(error=OllamaError("x", kind)))
                self.assertEqual((c.ollama.status, c.ollama.reason), ("unavailable", reason))
                self.assertEqual((c.models.sql.status, c.models.sql.reason), ("unknown", f"ollama_{reason}"))
                self.assertEqual((c.mysql.status, c.index.status), ("ok", "ok"))
                self.assertIsNone(c.index.vector_size_matches, "cannot compare without Ollama metadata")

    def test_mysql_failures(self) -> None:
        for err, reason in [(mysql.connector.errors.InterfaceError(msg="x", errno=2003), "unreachable"),
                            (mysql.connector.errors.ProgrammingError(msg="x", errno=1045), "authentication_failed"),
                            (mysql.connector.errors.DatabaseError(msg="x", errno=9999), "error")]:
            with self.subTest(reason):
                c = snapshot(service(mysql_repo=FakeMySQL(error=err)))
                self.assertEqual((c.mysql.status, c.mysql.reason, c.ollama.status, c.index.status),
                                 ("unavailable", reason, "ok", "ok"))

    def test_model_not_installed(self) -> None:
        c = snapshot(service(), OllamaFake(tags={"models": [TAGS["models"][0]]}))
        self.assertEqual((c.models.embedding.status, c.models.embedding.reason, c.models.embedding.installed),
                         ("unavailable", "not_installed", False))
        self.assertEqual(c.models.sql.status, "ok")
        self.assertIsNone(c.index.expected_vector_size)


class MissingConfiguration(unittest.TestCase):
    def test_models_credentials_and_url(self) -> None:
        cfg = settings(ollama_sql_model=None, mysql_readonly_password=None)
        c = snapshot(service(cfg, mysql_repo=MySQLRepository(cfg)))
        self.assertEqual((c.models.sql.status, c.models.sql.reason), ("not_configured", "not_configured"))
        self.assertEqual((c.mysql.status, c.mysql.reason), ("not_configured", "readonly_credentials_missing"))
        cfg = settings(ollama_base_url="ftp://nowhere")
        with mock.patch.multiple(ss, get_json=ss.get_json):
            c = asyncio.run(service(cfg).get()).components
        self.assertEqual((c.ollama.status, c.ollama.reason), ("not_configured", "not_configured"))
        self.assertEqual(c.index.status, "ok")


class IndexStates(unittest.TestCase):
    def test_absent_empty_mismatched_and_stale(self) -> None:
        stale = [dataclasses.replace(c, content=c.content + " ") if c.id == "schema:billing" else c for c in expected_schema_chunks()]
        cases = [
            (None, "unavailable", "not_open"),
            (FakeVectors(missing=True), "unavailable", "collection_missing"),
            (FakeVectors(points=0), "unavailable", "empty"),
            (FakeVectors(model="other-embed"), "degraded", "embedding_model_mismatch"),
            (FakeVectors(schema=stale), "degraded", "schema_changed_rebuild_index"),
            (FakeVectors(size=384), "degraded", "vector_size_mismatch"),
        ]
        for vectors, status, reason in cases:
            with self.subTest(reason):
                c = snapshot(service(vectors=vectors))
                self.assertEqual((c.index.status, c.index.reason), (status, reason))
                self.assertEqual((c.ollama.status, c.mysql.status), ("ok", "ok"))


class Timeouts(unittest.TestCase):
    def test_slow_probe_is_bounded_and_reported_unknown(self) -> None:
        with mock.patch.object(ss, "probe_bound", return_value=0.3):
            started = time.perf_counter()
            c = snapshot(service(mysql_repo=FakeMySQL(delay=2.0)))
            elapsed = time.perf_counter() - started
        self.assertEqual((c.mysql.status, c.mysql.reason), ("unknown", "probe_timeout"))
        self.assertEqual((c.ollama.status, c.index.status), ("ok", "ok"))
        self.assertLess(elapsed, 1.5)


class CacheAndCoalescing(unittest.TestCase):
    def test_cached_for_ttl_then_refreshed(self) -> None:
        now = [100.0]
        mysql_repo = FakeMySQL()
        svc = service(mysql_repo=mysql_repo, clock=lambda: now[0])

        async def scenario():
            first = await svc.get()
            now[0] += 10
            second = await svc.get()
            now[0] += 6
            third = await svc.get()
            return first, second, third

        with OllamaFake().patch():
            first, second, third = asyncio.run(scenario())
        self.assertIs(first, second)
        self.assertIsNot(second, third)
        self.assertEqual(mysql_repo.calls, 2)
        self.assertEqual(first.cache_ttl_seconds, 15)

    def test_concurrent_refreshes_run_probes_once(self) -> None:
        mysql_repo = FakeMySQL(delay=0.2)
        svc = service(mysql_repo=mysql_repo)

        async def scenario():
            return await asyncio.gather(*(svc.get() for _ in range(6)))

        ollama = OllamaFake()
        with ollama.patch():
            results = asyncio.run(scenario())
        self.assertEqual(mysql_repo.calls, 1)
        self.assertTrue(all(r is results[0] for r in results))
        self.assertEqual(ollama.paths.count("/api/version"), 1)


class NoInferenceNoMutation(unittest.TestCase):
    def test_only_metadata_endpoints_are_called(self) -> None:
        paths = []

        def fake_urlopen(request, timeout=None):
            path = request.full_url.split("11434", 1)[-1]
            paths.append((request.get_method(), path))
            body = {"/api/version": {"version": "0.40.8"}, "/api/tags": TAGS, "/api/ps": {"models": []},
                    "/api/show": {"model_info": {"nomic-bert.embedding_length": 768}}}[path]
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(body).encode()
            return response

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            asyncio.run(service().get())
        self.assertEqual(sorted(set(paths)), [("GET", "/api/ps"), ("GET", "/api/tags"), ("GET", "/api/version"),
                                              ("POST", "/api/show")])
        self.assertFalse({"/api/chat", "/api/generate", "/api/embed", "/api/pull", "/api/delete"} & {p for _, p in paths})

    def test_real_index_is_read_without_changes_or_second_client(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        try:
            cfg = settings(qdrant_path=tmp)
            chunks = chunk_documents(load_documents())
            with VectorRepository(cfg) as repo:
                repo.sync(chunks, [[float(i % 7 + 1), 1.0, 0.5] for i in range(len(chunks))], EMBED_MODEL)
                before = repo.info()
                with mock.patch("qdrant_client.QdrantClient.__init__", side_effect=AssertionError("second client")), \
                        mock.patch.object(VectorRepository, "sync", side_effect=AssertionError("index modified")):
                    c = snapshot(ss.SystemStatusService(cfg, lambda: repo, FakeMySQL(), "0.1.0", time.time()),
                                 OllamaFake(show_size=3))
                self.assertEqual(repo.info(), before)
            expected = {t: sum(1 for ch in chunks if ch.type == t) for t in ("schema", "documentation", "example")}
            self.assertEqual((c.index.status, c.index.points, c.index.counts_by_type), ("ok", len(chunks), expected))
            self.assertEqual((c.index.vector_size, c.index.vector_size_matches, c.index.schema_matches_schema_sql), (3, True, True))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class Endpoints(unittest.TestCase):
    def setUp(self) -> None:
        cfg = settings()
        self.app = create_app(cfg, open_vector_store=False,
                              monitoring=MonitoringRepository(Path(tempfile.mkdtemp()) / "m.sqlite3", 30, 100))
        self.svc = ss.SystemStatusService(cfg, lambda: FakeVectors(), FakeMySQL(), "0.1.0", time.time())
        self.app.dependency_overrides[admin_controller.get_system_status] = lambda: self.svc
        self.client = TestClient(self.app)

    def test_config_is_an_exact_allowlist_without_secrets(self) -> None:
        body = self.client.get("/admin/config").json()
        self.assertEqual(set(body), {"models", "rag", "sql_generation", "query_execution", "charts", "monitoring"})
        self.assertEqual(body["models"], {"sql_model": SQL_MODEL, "embedding_model": EMBED_MODEL})
        self.assertEqual(body["rag"]["context_strategy"], "full schema + retrieved documentation/examples")
        self.assertEqual(set(body["query_execution"]), {"max_execution_ms", "row_cap"})
        self.assertEqual(body["sql_generation"]["max_corrections"], 1)

    def test_no_secrets_hosts_urls_or_paths(self) -> None:
        with OllamaFake().patch():
            text = self.client.get("/admin/system").text + self.client.get("/admin/config").text
        for leaked in (SECRET_PASSWORD, SECRET_USER, "db.internal.example", "11434", "127.0.0.1", "storage", ":\\", "localhost"):
            self.assertNotIn(leaked, text)

    def test_system_endpoint_and_health(self) -> None:
        with OllamaFake().patch():
            body = self.client.get("/admin/system").json()
        self.assertEqual(set(body["components"]), {"api", "ollama", "models", "mysql", "index"})
        self.assertIn("+00:00", body["checked_at"])
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})

    def test_startup_does_not_probe(self) -> None:
        mysql_repo = FakeMySQL()
        self.svc._mysql = mysql_repo
        with TestClient(self.app) as client:
            self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(mysql_repo.calls, 0)


if __name__ == "__main__":
    unittest.main()
