"""验证目录迁移后的启动入口与根目录资源定位。"""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from research_library.core.config import PROJECT_ROOT
from research_library.db.db_migrations import ROOT
from research_library.agents.paper_writing_skill import SKILL_PATH
from research_library.main import FRONTEND_DIST, app
from research_library.workers.celery_app import celery_app
from manage import worker_command


class TestPackageLayout(unittest.TestCase):
    def test_installed_package_imports_outside_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, '-I', '-c',
                 'from research_library.core.config import PROJECT_ROOT; print(PROJECT_ROOT)'],
                cwd=directory, capture_output=True, text=True, check=True,
            )
        self.assertEqual(Path(result.stdout.strip()), PROJECT_ROOT)

    def test_resources_still_resolve_at_repository_root(self):
        self.assertEqual(ROOT, PROJECT_ROOT)
        self.assertEqual(FRONTEND_DIST, PROJECT_ROOT / 'frontend' / 'dist')
        self.assertTrue((ROOT / 'migrations' / 'env.py').is_file())
        self.assertTrue(SKILL_PATH.is_file())

    def test_worker_entrypoint_registers_existing_task_names(self):
        command = worker_command()
        self.assertEqual(command[command.index('-A') + 1],
                         'research_library.workers.celery_app')
        celery_app.loader.import_default_modules()
        self.assertIn('reports.run_report_job', celery_app.tasks)
        self.assertIn('papers.run_preparation_job', celery_app.tasks)

    def test_all_public_endpoints_are_unique_after_router_split(self):
        paths = app.openapi()['paths']
        self.assertIn('/report-jobs/{job_id}/claims/{claim_id}/investigations', paths)
        self.assertIn('/paper-drafts/{draft_id}/expand', paths)
        self.assertIn('/health/ready', paths)
        operation_ids = [operation['operationId']
                         for methods in paths.values()
                         for operation in methods.values()]
        self.assertEqual(len(operation_ids), len(set(operation_ids)))
