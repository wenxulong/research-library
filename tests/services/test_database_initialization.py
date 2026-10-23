"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestDatabaseInitialization(unittest.TestCase):
    """迁移初始化空库；不完整旧库拒绝登记版本。"""

    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        import research_library.db.init_db as init_db
        self.init = init_db
        self.enterContext(patch('research_library.db.init_db.engine', self.engine))
        self.enterContext(patch("builtins.print"))

    def test_fresh_database_creates_all_model_tables(self):
        from sqlalchemy import inspect
        self.init.init_db()
        self.assertEqual(set(inspect(self.engine).get_table_names()), set(Base.metadata.tables) | {"alembic_version"})
        self.assertIn("report_jobs", inspect(self.engine).get_table_names())
        self.assertIn("research_task_comparison_reports", inspect(self.engine).get_table_names())

    def test_missing_report_table_is_rejected_without_changing_existing_task(self):
        from research_library.db.model import ResearchTaskComparisonReport
        from sqlalchemy import inspect
        Base.metadata.create_all(self.engine)
        sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        with sessions() as db:
            task = ResearchTask(title="保留原任务", question="原问题", status="completed", result="原分析")
            db.add(task)
            db.commit()
            task_id = task.id
        ResearchTaskComparisonReport.__table__.drop(self.engine)
        from research_library.db.db_migrations import SchemaMismatchError
        with self.assertRaises(SchemaMismatchError):
            self.init.init_db()
        self.assertNotIn("research_task_comparison_reports", inspect(self.engine).get_table_names())
        self.assertNotIn("alembic_version", inspect(self.engine).get_table_names())
        with sessions() as db:
            task = db.get(ResearchTask, task_id)
            self.assertEqual((task.title, task.question, task.result, task.status), ("保留原任务", "原问题", "原分析", "completed"))
