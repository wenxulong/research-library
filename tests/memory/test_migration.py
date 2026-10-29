"""在独立SQLite验证记忆表迁移及现有研究数据保留。"""
import unittest
from unittest.mock import patch

from alembic import command
from sqlalchemy import create_engine, event, inspect, text

from research_library.db.database import Base
import research_library.db.model  # 注册完整模型。
from research_library.db.db_migrations import migration_config, schema_differences


class TestMemoryMigration(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        self.enterContext(patch("research_library.db.database.engine.connect",
                                side_effect=AssertionError("禁止连接正式数据库")))

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

    def test_upgrade_preserves_old_task_and_matches_models(self):
        with self.engine.begin() as connection:
            command.upgrade(migration_config(connection), "0008_investigation_adoption")
            connection.execute(text("INSERT INTO research_tasks (id,title,question,status) VALUES (6,'保留任务','原问题','pending')"))
            command.upgrade(migration_config(connection), "head")
            self.assertEqual(connection.execute(text("SELECT title FROM research_tasks WHERE id=6")).scalar_one(), "保留任务")
            self.assertIn("agent_memories", inspect(connection).get_table_names())
            self.assertEqual(schema_differences(connection, Base.metadata), [])
            command.check(migration_config(connection))

    def test_downgrade_removes_only_memory_table_and_can_upgrade_again(self):
        with self.engine.begin() as connection:
            command.upgrade(migration_config(connection), "head")
            connection.execute(text("INSERT INTO research_tasks (id,title,question,status) VALUES (6,'保留任务','原问题','pending')"))
            connection.execute(text("INSERT INTO agent_memories (owner_id,research_task_id,memory_type,kind,content,origin) VALUES ('a',6,'semantic','constraint','测试','user_statement')"))
            command.downgrade(migration_config(connection), "0008_investigation_adoption")
            self.assertNotIn("agent_memories", inspect(connection).get_table_names())
            self.assertEqual(connection.execute(text("SELECT title FROM research_tasks WHERE id=6")).scalar_one(), "保留任务")
            command.upgrade(migration_config(connection), "head")
            self.assertEqual(schema_differences(connection, Base.metadata), [])
