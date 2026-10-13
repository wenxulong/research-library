"""配置、健康检查、启动入口测试；所有外部探测都替换为独立模拟。"""
from dataclasses import replace
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
import health_service as health
from health_api import router
import manage


class TestUnifiedConfiguration(unittest.TestCase):
    def test_defaults_preserve_existing_runtime_behavior(self):
        value = config.load_settings({})
        self.assertEqual((value.embedding_model, value.deepseek_model, value.celery_queue),
                         ("qwen3-embedding:0.6b", "deepseek-flash", "report_jobs"))
        self.assertEqual((value.embedding_timeout, value.deepseek_timeout, value.max_upload_mb), (120, 300, 20))
        self.assertEqual(value.upload_dir, config.PROJECT_ROOT / "uploads/papers")

    def test_override_values_and_normalize_urls(self):
        value = config.load_settings({"API_PORT": "9001", "OLLAMA_BASE_URL": "http://local:11434/",
                                      "LOG_LEVEL": "WARNING", "DEEPSEEK_MAX_RETRIES": "0",
                                      "CELERY_QUEUE": "custom", "MAX_UPLOAD_MB": "8"})
        self.assertEqual((value.api_port,value.ollama_base_url,value.log_level,value.deepseek_max_retries,value.celery_queue,value.max_upload_mb),
                         (9001,"http://local:11434","warning",0,"custom",8))

    def test_missing_optional_values_use_defaults(self):
        self.assertEqual(config.load_settings({"EMBEDDING_TIMEOUT": " "}).embedding_timeout, 120)

    def test_empty_dictionary_does_not_read_real_credentials(self):
        value = config.load_settings({})
        self.assertEqual(value.database_url, "")
        self.assertEqual(value.redis_url, "")

    def test_relative_paths_do_not_depend_on_current_directory(self):
        value = config.load_settings({"UPLOAD_DIR": "data/pdfs", "LOG_DIR": "data/logs"})
        self.assertEqual(value.upload_dir, config.PROJECT_ROOT / "data/pdfs")
        self.assertEqual(value.log_dir, config.PROJECT_ROOT / "data/logs")

    def test_absolute_paths_are_preserved(self):
        with TemporaryDirectory() as directory:
            self.assertEqual(config.load_settings({"UPLOAD_DIR": directory}).upload_dir, Path(directory).resolve())

    def test_invalid_integer_values_fail_without_echoing_input(self):
        for name,value in [("API_PORT","0"),("API_PORT","65536"),("MAX_UPLOAD_MB","-1"),("DEEPSEEK_MAX_RETRIES","-1"),("DEEPSEEK_JSON_MAX_TOKENS","secret-value")]:
            with self.subTest(name=name,value=value), self.assertRaises(RuntimeError) as caught:
                config.load_settings({name:value})
            self.assertIn(name, str(caught.exception))
            self.assertNotIn("secret-value", str(caught.exception))

    def test_invalid_timeout_values_fail(self):
        for value in ["0","-1","nan","inf","text"]:
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                config.load_settings({"HEALTH_TIMEOUT":value})

    def test_invalid_log_level_fails(self):
        with self.assertRaisesRegex(RuntimeError,"LOG_LEVEL"):
            config.load_settings({"LOG_LEVEL":"unknown"})

    def test_repr_hides_connection_credentials_and_api_key(self):
        value=config.load_settings({"DATABASE_URL":"mysql://user:db-secret@server/db", "REDIS_URL":"redis://:redis-secret@server/0"})
        self.assertNotIn("db-secret",repr(value)); self.assertNotIn("redis-secret",repr(value))
        self.assertFalse(hasattr(value,"deepseek_api_key"))

    def test_api_key_required_only_at_model_call(self):
        with patch.dict(os.environ,{},clear=True):
            config.load_settings({})
            self.assertEqual(config.get_deepseek_api_key(required=False),"")
            with self.assertRaisesRegex(RuntimeError,"DEEPSEEK_API_KEY"):
                config.get_deepseek_api_key()

    def test_api_key_is_read_at_call_time(self):
        with patch.dict(os.environ,{"DEEPSEEK_API_KEY":"test-key"}):
            self.assertEqual(config.get_deepseek_api_key(),"test-key")


class TestHealthChecks(unittest.TestCase):
    def setUp(self):
        value=replace(config.load_settings({}),database_url="sqlite://",redis_url="redis://127.0.0.1/0")
        self.enterContext(patch.object(health,"settings",value))

    def test_exception_details_do_not_leak(self):
        result=health.safe_check(Mock(side_effect=RuntimeError("mysql://user:SECRET@private-host/db")))
        self.assertEqual(result,{"status":"error","message":"检查失败","error_type":"RuntimeError"})
        self.assertNotIn("SECRET",json.dumps(result)); self.assertNotIn("private-host",json.dumps(result))

    def test_successful_database_check_compares_migration_heads(self):
        from unittest.mock import MagicMock
        engine=MagicMock()
        with patch.object(health,"create_engine",return_value=engine), patch.object(health.MigrationContext,"configure") as migration, patch.object(health.ScriptDirectory,"from_config") as scripts:
            migration.return_value.get_current_heads.return_value=("0002_paper_preparation",)
            scripts.return_value.get_heads.return_value=["0002_paper_preparation"]
            self.assertEqual(health.check_database()["status"],"ok")
        engine.dispose.assert_called_once()

    def test_database_version_mismatch_fails_readiness(self):
        from unittest.mock import MagicMock
        engine=MagicMock()
        with patch.object(health,"create_engine",return_value=engine), patch.object(health.MigrationContext,"configure") as migration, patch.object(health.ScriptDirectory,"from_config") as scripts:
            migration.return_value.get_current_heads.return_value=()
            scripts.return_value.get_heads.return_value=["0002_paper_preparation"]
            self.assertEqual(health.check_database()["status"],"error")
        engine.dispose.assert_called_once()

    def test_database_connection_failure_disposes_engine(self):
        engine=Mock(); engine.connect.side_effect=OSError("private connection")
        with patch.object(health,"create_engine",return_value=engine):
            self.assertEqual(health.safe_check(health.check_database)["status"],"error")
        engine.dispose.assert_called_once()

    def test_mysql_probe_uses_bounded_timeouts(self):
        value=replace(health.settings,database_url="mysql+pymysql://user:test@localhost/db",health_timeout=3)
        with patch.object(health,"settings",value),patch.object(health,"create_engine",side_effect=RuntimeError("stop before connect")) as factory:
            health.safe_check(health.check_database)
        self.assertEqual(factory.call_args.kwargs["connect_args"],{"connect_timeout":3,"read_timeout":3,"write_timeout":3})

    def test_redis_check_has_timeouts_and_only_pings(self):
        from unittest.mock import MagicMock
        client=MagicMock()
        with patch.object(health.Redis,"from_url",return_value=client) as factory:
            self.assertEqual(health.check_redis()["status"],"ok")
        client.__enter__.return_value.ping.assert_called_once()
        self.assertEqual(factory.call_args.kwargs["socket_timeout"],2)

    def test_ollama_checks_model_list_without_embedding_request(self):
        payload=io.BytesIO(json.dumps({"models":[{"name":"qwen3-embedding:0.6b"}]}).encode())
        with patch.object(health,"urlopen",return_value=payload) as request:
            self.assertEqual(health.check_ollama()["status"],"ok")
        self.assertTrue(request.call_args.args[0].endswith("/api/tags"))

    def test_ollama_missing_model_reports_error(self):
        with patch.object(health,"urlopen",return_value=io.BytesIO(b'{"models":[]}')):
            self.assertEqual(health.check_ollama()["status"],"error")

    def worker_probe(self,registered,queues):
        app=Mock(); inspector=app.control.inspect.return_value
        inspector.registered.return_value=registered; inspector.active_queues.return_value=queues
        with patch.object(health,"Celery",return_value=app):
            result=health.check_worker()
        app.close.assert_called_once()
        return result

    def test_worker_requires_tasks_and_queue_on_same_worker(self):
        result=self.worker_probe({"valid":list(health.REQUIRED_TASKS)}, {"valid":[{"name":"report_jobs"}]})
        self.assertEqual(result["status"],"ok")

    def test_worker_reply_timeout_is_unknown_not_claimed_dead(self):
        self.assertEqual(self.worker_probe({}, {})["status"],"unknown")

    def test_task_and_queue_on_different_workers_do_not_pass(self):
        result=self.worker_probe({"a":list(health.REQUIRED_TASKS),"b":[]}, {"a":[{"name":"wrong"}],"b":[{"name":"report_jobs"}]})
        self.assertEqual(result["status"],"unknown")

    def test_old_worker_missing_preparation_task_does_not_pass(self):
        result=self.worker_probe({"old":["reports.run_report_job"]}, {"old":[{"name":"report_jobs"}]})
        self.assertEqual(result["status"],"unknown")

    def test_all_dependencies_ok_report_ready(self):
        with patch.object(health,"check_database",return_value={"status":"ok"}),patch.object(health,"check_redis",return_value={"status":"ok"}),patch.object(health,"check_ollama",return_value={"status":"ok"}),patch.object(health,"check_worker",return_value={"status":"ok"}),patch.object(health,"get_deepseek_api_key",return_value=""):
            result=health.build_health_report()
        self.assertEqual(result["status"],"ok")
        self.assertFalse(result["capabilities"]["deepseek_configured"])

    def test_redis_failure_skips_worker_and_preserves_other_checks(self):
        with patch.object(health,"check_database",return_value={"status":"ok"}),patch.object(health,"check_redis",side_effect=OSError("SECRET")),patch.object(health,"check_ollama",return_value={"status":"ok"}),patch.object(health,"check_worker") as worker:
            result=health.build_health_report()
        worker.assert_not_called(); self.assertEqual(result["status"],"degraded")
        self.assertEqual(result["checks"]["worker"]["status"],"unknown")
        self.assertNotIn("SECRET",json.dumps(result))


class TestHealthRoutes(unittest.TestCase):
    def setUp(self):
        app=FastAPI(); app.include_router(router); self.client=TestClient(app)
        self.addCleanup(self.client.close)

    def test_live_does_not_probe_services(self):
        with patch.object(health,"build_health_report",side_effect=AssertionError("不能探测依赖")):
            response=self.client.get("/health/live")
        self.assertEqual(response.status_code,200); self.assertEqual(response.json(),{"status":"ok"})

    def test_ready_success_returns_200(self):
        with patch.object(health,"build_health_report",return_value={"status":"ok","checks":{}}):
            self.assertEqual(self.client.get("/health/ready").status_code,200)

    def test_ready_degraded_returns_503_with_details(self):
        report={"status":"degraded","checks":{"worker":{"status":"unknown","message":"未确认"}}}
        with patch.object(health,"build_health_report",return_value=report):
            response=self.client.get("/health/ready")
        self.assertEqual(response.status_code,503); self.assertEqual(response.json(),report)


class TestBackendCommands(unittest.TestCase):
    def setUp(self):
        self.directory=self.enterContext(TemporaryDirectory())
        value=replace(config.load_settings({}),log_dir=Path(self.directory))
        self.enterContext(patch.object(manage,"settings",value))
        self.enterContext(patch.object(manage.os,"chdir"))

    def test_check_exit_code_reflects_status(self):
        for status,code in [("ok",0),("degraded",1)]:
            with self.subTest(status=status),patch.object(health,"build_health_report",return_value={"status":status}),patch("builtins.print"):
                self.assertEqual(manage.main(["check"]),code)

    def test_init_dispatches_unified_migrations(self):
        with patch("init_db.init_db") as initialize:
            self.assertEqual(manage.main(["init"]),0)
        initialize.assert_called_once_with()

    def test_api_uses_configured_host_port_and_file_logging(self):
        with patch("uvicorn.run") as run:
            self.assertEqual(manage.main(["api"]),0)
        self.assertEqual(run.call_args.kwargs["host"],"127.0.0.1")
        self.assertEqual(run.call_args.kwargs["port"],8000)
        handler=run.call_args.kwargs["log_config"]["handlers"]["file"]
        self.assertEqual(handler["filename"],str(Path(self.directory)/"api.log"))
        self.assertEqual(handler["backupCount"],3)

    def test_worker_uses_same_queue_and_exec_without_extra_parent(self):
        value=replace(manage.settings,celery_queue="custom-queue")
        with patch.object(manage,"settings",value),patch.object(manage.os,"execv") as execute:
            self.assertEqual(manage.main(["worker"]),0)
        self.assertIn("--queues=custom-queue",execute.call_args.args[1])
        self.assertIn("--pool=solo",execute.call_args.args[1])
        self.assertIn("--logfile="+str(Path(self.directory)/"worker.log"),execute.call_args.args[1])


if __name__=="__main__":
    unittest.main()
