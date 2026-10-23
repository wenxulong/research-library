"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestMySQLPaperLockProtocol(unittest.TestCase):
    """模拟MySQL连接协议，验证取锁和释放规则；不证明真实MySQL互斥。"""

    def _database(self, acquired=1, released=1, database="test_library"):
        from unittest.mock import MagicMock
        connection = MagicMock()
        connection.scalar.side_effect = [acquired, released]
        engine = SimpleNamespace(dialect=SimpleNamespace(name="mysql"),
                                 url=SimpleNamespace(database=database))
        engine.engine = engine
        context = MagicMock()
        context.__enter__.return_value = connection
        engine.connect = Mock(return_value=context)
        db = Mock()
        db.get_bind.return_value = engine
        return db, connection

    def test_mysql_uses_same_connection_and_name_for_acquire_and_release(self):
        from research_library.services.paper_operation_guard import paper_operation_lock
        db, connection = self._database()
        with paper_operation_lock(db, 9):
            db.commit()
            self.assertEqual(connection.scalar.call_count, 1)
        calls = connection.scalar.call_args_list
        self.assertIn("GET_LOCK", str(calls[0].args[0]))
        self.assertIn("RELEASE_LOCK", str(calls[1].args[0]))
        self.assertEqual(calls[0].args[1], calls[1].args[1])
        self.assertLessEqual(len(calls[0].args[1]["lock_name"]), 64)
        connection.invalidate.assert_not_called()

    def test_same_database_and_paper_share_name_other_papers_have_other_names(self):
        from research_library.services.paper_operation_guard import paper_operation_lock
        names = []
        for database, paper_id in (("library", 9), ("library", 9), ("library", 10), ("other", 9)):
            db, connection = self._database(database=database)
            with paper_operation_lock(db, paper_id):
                pass
            names.append(connection.scalar.call_args_list[0].args[1]["lock_name"])
        self.assertEqual(names[0], names[1])
        self.assertNotEqual(names[0], names[2])
        self.assertNotEqual(names[0], names[3])

    def test_busy_or_unavailable_lock_does_not_execute_body(self):
        from research_library.services.paper_operation_guard import paper_operation_lock, PaperOperationBusyError
        for value, error_type in ((0, PaperOperationBusyError), (None, RuntimeError)):
            with self.subTest(value=value):
                db, connection = self._database(acquired=value)
                with self.assertRaises(error_type):
                    with paper_operation_lock(db, 9):
                        self.fail("未取得锁不能执行操作")
                self.assertEqual(connection.scalar.call_count, 1)

    def test_business_exception_still_releases_lock(self):
        from research_library.services.paper_operation_guard import paper_operation_lock
        db, connection = self._database()
        with self.assertRaisesRegex(ValueError, "业务失败"):
            with paper_operation_lock(db, 9):
                raise ValueError("业务失败")
        self.assertEqual(connection.scalar.call_count, 2)
        connection.invalidate.assert_not_called()

    def test_acquire_exception_invalidates_connection_without_executing_body(self):
        from research_library.services.paper_operation_guard import paper_operation_lock
        db, connection = self._database()
        connection.scalar.side_effect = RuntimeError("取得锁时连接中断")
        with self.assertRaisesRegex(RuntimeError, "连接中断"):
            with paper_operation_lock(db, 9):
                self.fail("取得锁失败不能执行操作")
        connection.invalidate.assert_called_once()

    def test_release_failure_invalidates_connection(self):
        from research_library.services.paper_operation_guard import paper_operation_lock
        db, connection = self._database(released=0)
        with self.assertRaisesRegex(RuntimeError, "释放"):
            with paper_operation_lock(db, 9):
                pass
        self.assertTrue(connection.invalidate.called)
