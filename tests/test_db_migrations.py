"""迁移测试只使用独立 SQLite，禁止正式数据库连接。"""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import shutil
import unittest

from alembic import command
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

import research_library.db.model as model
from research_library.db.database import Base
import research_library.db.db_migrations as migrations


class TestDatabaseMigrations(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        self.latest_revision = ScriptDirectory.from_config(migrations.migration_config()).get_current_head()
        # env.py 必须复用传入连接；若误用正式引擎，测试立即失败。
        self.enterContext(patch('research_library.db.database.engine.connect', side_effect=AssertionError("禁止正式连接")))

    def version(self):
        with self.engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_heads()

    def assert_unversioned(self):
        self.assertEqual(self.version(), ())

    def seed_legacy(self):
        # 旧库必须来自固定历史结构，不能使用不断增加字段的当前模型。
        with self.engine.begin() as connection:
            command.upgrade(migrations.migration_config(connection), migrations.PREPARATION_REVISION)
            connection.execute(text("DROP TABLE alembic_version"))
            connection.execute(text("INSERT INTO papers (id,title,author,year,notes) VALUES (9,'原论文','作者',2025,'保留备注')"))
            connection.execute(text("INSERT INTO research_tasks (id,title,question,status,result) VALUES (10,'原任务','原问题','completed','原分析')"))

    def assert_records(self):
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT title,notes FROM papers WHERE id=9")).one(), ("原论文", "保留备注"))
            self.assertEqual(connection.execute(text("SELECT result FROM research_tasks WHERE id=10")).scalar_one(), "原分析")

    def test_empty_database_replays_frozen_initial_revision(self):
        self.assertEqual(migrations.initialize_database(self.engine), self.latest_revision)
        self.assertEqual(set(inspect(self.engine).get_table_names()), set(Base.metadata.tables) | {"alembic_version"})
        with self.engine.connect() as connection:
            self.assertEqual(migrations.schema_differences(connection, Base.metadata), [])

    def test_frozen_baseline_matches_replayed_initial_revision(self):
        with self.engine.begin() as connection:
            command.upgrade(migrations.migration_config(connection), migrations.BASELINE_REVISION)
            self.assertEqual(migrations.schema_differences(connection), [])

    def test_legacy_adoption_does_not_execute_create_table_upgrade(self):
        self.seed_legacy()
        migrations.initialize_database(self.engine)
        self.assertEqual(self.version(), (self.latest_revision,))
        self.assert_records()

    def test_repeated_initialization_preserves_data_and_version(self):
        self.seed_legacy()
        migrations.initialize_database(self.engine)
        migrations.initialize_database(self.engine)
        self.assert_records()
        self.assertEqual(self.version(), (self.latest_revision,))

    def test_ten_table_legacy_schema_upgrades_and_preserves_data(self):
        self.seed_legacy()
        model.PaperPreparationJob.__table__.drop(self.engine)
        migrations.initialize_database(self.engine)
        self.assertEqual(self.version(), (self.latest_revision,))
        self.assertIn("paper_preparation_jobs", inspect(self.engine).get_table_names())
        self.assert_records()

    def test_missing_table_is_rejected_before_stamp(self):
        self.seed_legacy()
        model.ResearchTaskComparisonReport.__table__.drop(self.engine)
        with self.assertRaises(migrations.SchemaMismatchError):
            migrations.initialize_database(self.engine)
        self.assert_unversioned()
        self.assert_records()

    def test_missing_column_is_rejected_before_stamp(self):
        self.seed_legacy()
        with self.engine.begin() as connection:
            connection.execute(text("ALTER TABLE research_tasks DROP COLUMN review_result"))
        with self.assertRaises(migrations.SchemaMismatchError):
            migrations.initialize_database(self.engine)
        self.assert_unversioned()
        self.assert_records()

    def test_extra_column_is_rejected_before_stamp(self):
        self.seed_legacy()
        with self.engine.begin() as connection:
            connection.execute(text("ALTER TABLE papers ADD COLUMN extra TEXT"))
        with self.assertRaises(migrations.SchemaMismatchError):
            migrations.initialize_database(self.engine)
        self.assert_unversioned()

    def test_missing_index_is_rejected_before_stamp(self):
        self.seed_legacy()
        with self.engine.begin() as connection:
            connection.execute(text("DROP INDEX ix_report_jobs_task_id"))
        with self.assertRaises(migrations.SchemaMismatchError):
            migrations.initialize_database(self.engine)
        self.assert_unversioned()

    def test_extra_table_is_rejected_before_stamp(self):
        self.seed_legacy()
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TABLE unexpected (id INTEGER PRIMARY KEY)"))
        with self.assertRaises(migrations.SchemaMismatchError):
            migrations.initialize_database(self.engine)
        self.assert_unversioned()

    def test_unknown_registered_version_is_rejected(self):
        self.seed_legacy()
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
            connection.execute(text("INSERT INTO alembic_version VALUES ('unknown_version')"))
        with self.assertRaises(Exception):
            migrations.initialize_database(self.engine)
        self.assertEqual(self.version(), ("unknown_version",))
        self.assert_records()

    def test_empty_version_table_is_not_mistaken_for_migrated_database(self):
        self.seed_legacy()
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
        migrations.initialize_database(self.engine)
        self.assertEqual(self.version(), (self.latest_revision,))
        self.assert_records()

    def test_defaults_normalize_only_known_equivalent_time_expressions(self):
        for value in ["CURRENT_TIMESTAMP", "(CURRENT_TIMESTAMP)", "current_timestamp()", "now()"]:
            self.assertEqual(migrations.normalize_default(value), "current_timestamp")
        self.assertIsNone(migrations.normalize_default(None))
        self.assertNotEqual(migrations.normalize_default("'pending'"), migrations.normalize_default("'queued'"))

    def test_primary_key_and_default_mismatches_are_reported(self):
        Base.metadata.create_all(self.engine)
        with self.engine.connect() as connection:
            inspector = inspect(connection)
            real_pk = inspector.get_pk_constraint
            real_columns = inspector.get_columns
            def pk(name, **kwargs):
                value = real_pk(name, **kwargs)
                return {**value, "constrained_columns": []} if name == "papers" else value
            def columns(name, **kwargs):
                values = real_columns(name, **kwargs)
                if name == "papers":
                    values = [{**value, "default": "'unexpected'"} if value["name"] == "notes" else value for value in values]
                return values
            with patch('research_library.db.db_migrations.inspect', return_value=inspector), patch.object(inspector, "get_pk_constraint", side_effect=pk), patch.object(inspector, "get_columns", side_effect=columns):
                differences = migrations.schema_differences(connection)
            self.assertTrue(any(value[0] == "primary_key" for value in differences))
            self.assertTrue(any(value[0] == "server_default" for value in differences))

    def test_adoption_stamps_initial_then_executes_future_upgrade(self):
        self.seed_legacy()
        with TemporaryDirectory() as directory:
            script_dir = Path(directory) / "migrations"
            shutil.copytree(migrations.ROOT / "migrations", script_dir)
            (script_dir / "versions/test_future.py").write_text(
                "from alembic import op\nimport sqlalchemy as sa\nrevision='test_future'\n"
                f"down_revision={self.latest_revision!r}\n"
                "def upgrade():\n    op.add_column('papers', sa.Column('test_future', sa.Text(), nullable=True))\n"
                "def downgrade():\n    op.drop_column('papers', 'test_future')\n"
            )
            real_config = migrations.migration_config
            def config(connection=None):
                result = real_config(connection)
                result.set_main_option("script_location", str(script_dir))
                return result
            with patch('research_library.db.db_migrations.migration_config', side_effect=config):
                self.assertEqual(migrations.initialize_database(self.engine), "test_future")
                self.assertEqual(migrations.initialize_database(self.engine), "test_future")
            self.assertIn("test_future", {c["name"] for c in inspect(self.engine).get_columns("papers")})
            self.assert_records()

    def test_legacy_commands_delegate_to_unified_initializer(self):
        import importlib
        for name in ['scripts.init_analysis_versions', 'scripts.init_report_jobs', 'scripts.add_review_result_column']:
            with self.subTest(name=name):
                module = importlib.import_module(name)
                with patch.object(module, 'init_db') as initialize:
                    module.main()
                    initialize.assert_called_once_with()

    def test_alembic_check_passes_after_upgrade(self):
        migrations.initialize_database(self.engine)
        with self.engine.begin() as connection:
            command.check(migrations.migration_config(connection))

    def test_plan_column_upgrade_preserves_existing_selection(self):
        with self.engine.begin() as connection:
            command.upgrade(migrations.migration_config(connection), "0003_research_questions")
            connection.execute(text(
                "INSERT INTO research_tasks (id,title,question,status) VALUES (10,'原任务','原问题','completed')"
            ))
            connection.execute(text(
                "INSERT INTO report_jobs (id,task_id,status,stage,analysis_snapshot) "
                "VALUES (12,10,'succeeded','finished','{}')"
            ))
            connection.execute(text(
                "INSERT INTO research_question_sets "
                "(id,task_id,report_job_id,candidates,sources,selected_candidate_number,selected_question) "
                "VALUES (7,10,12,:candidates,'[]',1,'用户已选的问题')"
            ), {"candidates": '[{"research_question":"原候选"}]'})
            command.upgrade(migrations.migration_config(connection), "head")
            selected, question, plan = connection.execute(text(
                "SELECT selected_candidate_number,selected_question,plan FROM research_question_sets WHERE id=7"
            )).one()
            self.assertEqual((selected, question, plan), (1, "用户已选的问题", None))
            self.assertEqual(migrations.schema_differences(connection, Base.metadata), [])


if __name__ == "__main__":
    unittest.main()
