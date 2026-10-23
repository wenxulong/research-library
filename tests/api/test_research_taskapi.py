"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestResearchTaskAPI(unittest.TestCase):
    """测试研究任务接口和状态更新规则。"""
    def setUp(self):
        """在每个测试前准备独立测试环境。"""
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)

    def _create_test_task(self):
        """创建测试研究任务并返回响应数据。"""
        response = client.post(
            "/research-tasks",
            json={
                "title": "调研大模型智能体",
                "question": "主流大模型智能体有哪些技术路线？",
            },
        )

        self.assertEqual(response.status_code, 201)
        return response.json()

    def test_create_research_task(self):
        """验证该场景的实际结果与预期一致。"""
        response = client.post(
            "/research-tasks",
            json={
                "title": "RAG分块策略调研",
                "question": "不同分块策略如何影响RAG检索效果？",
            },
        )

        result = response.json()

        self.assertEqual(response.status_code, 201)
        self.assertIsInstance(result["id"], int)
        self.assertEqual(result["title"], "RAG分块策略调研")
        self.assertEqual(
            result["question"],
            "不同分块策略如何影响RAG检索效果？",
        )
        self.assertEqual(result["status"], "pending")
        self.assertIsNone(result["result"])

    def test_read_all_research_tasks(self):
        """验证该场景的实际结果与预期一致。"""
        created_task = self._create_test_task()

        response = client.get("/research-tasks")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [created_task])

    def test_read_one_research_task(self):
        """验证该场景的实际结果与预期一致。"""
        created_task = self._create_test_task()
        task_id = created_task["id"]

        response = client.get(f"/research-tasks/{task_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), created_task)

    def test_read_missing_research_task(self):
        """验证该场景的实际结果与预期一致。"""
        response = client.get("/research-tasks/999999")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"detail": "没有找到编号为 999999 的研究任务"},
        )

    def test_update_research_task_status(self):
        """验证该场景的实际结果与预期一致。"""
        created_task = self._create_test_task()
        task_id = created_task["id"]

        response = client.patch(
            f"/research-tasks/{task_id}/status",
            json={
                "status": "completed",
                "result": "已经完成技术路线调研。",
            },
        )

        result = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(result["id"], task_id)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"], "已经完成技术路线调研。")

    def test_update_research_task_with_invalid_status(self):
        """验证该场景的实际结果与预期一致。"""
        created_task = self._create_test_task()
        task_id = created_task["id"]

        response = client.patch(
            f"/research-tasks/{task_id}/status",
            json={
                "status": "unknown",
                "result": None,
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_delete_research_task(self):
        """验证该场景的实际结果与预期一致。"""
        created_task = self._create_test_task()
        task_id = created_task["id"]

        delete_response = client.delete(f"/research-tasks/{task_id}")

        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(
            delete_response.json(),
            {
                "message": "研究任务删除成功",
                "deleted_task": created_task,
            },
        )

        get_response = client.get(f"/research-tasks/{task_id}")

        self.assertEqual(get_response.status_code, 404)
