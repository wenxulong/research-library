"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestResearchTaskPaperAPI(unittest.TestCase):
    """测试研究任务与论文的关联接口。"""
    def setUp(self):
        """在每个测试前准备独立测试环境。"""
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)

    def _create_test_paper(self):
        """创建测试论文并返回响应数据。"""
        response = client.post(
            "/papers",
            json={
                "title": "RAG测试论文",
                "author": "测试作者",
                "year": 2025,
                "notes": "用于测试任务和论文的关联",
            },
        )

        self.assertEqual(response.status_code, 201)
        return response.json()

    def _create_test_task(self):
        """创建测试研究任务并返回响应数据。"""
        response = client.post(
            "/research-tasks",
            json={
                "title": "RAG分块策略调研",
                "question": "不同分块策略如何影响检索效果？",
            },
        )

        self.assertEqual(response.status_code, 201)
        return response.json()

    def _add_test_link(self, task_id, paper_id):
        """建立测试任务与论文的关联。"""
        response = client.post(
            f"/research-tasks/{task_id}/papers/{paper_id}",
            json={
                "note": "这是一篇核心参考论文",
            },
        )

        self.assertEqual(response.status_code, 201)
        return response.json()

    def test_add_paper_to_research_task(self):
        """验证该场景的实际结果与预期一致。"""
        paper = self._create_test_paper()
        task = self._create_test_task()

        response = client.post(
            f"/research-tasks/{task['id']}/papers/{paper['id']}",
            json={
                "note": "RAG领域的重要论文",
            },
        )

        result = response.json()

        self.assertEqual(response.status_code, 201)
        self.assertIsInstance(result["id"], int)
        self.assertEqual(result["task_id"], task["id"])
        self.assertEqual(result["paper_id"], paper["id"])
        self.assertEqual(result["note"], "RAG领域的重要论文")
        self.assertEqual(result["paper"], paper)

    def test_read_research_task_papers(self):
        """验证该场景的实际结果与预期一致。"""
        paper = self._create_test_paper()
        task = self._create_test_task()
        link = self._add_test_link(task["id"], paper["id"])

        response = client.get(
            f"/research-tasks/{task['id']}/papers"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [link])

    def test_add_duplicate_paper_to_research_task(self):
        """验证该场景的实际结果与预期一致。"""
        paper = self._create_test_paper()
        task = self._create_test_task()

        self._add_test_link(task["id"], paper["id"])

        response = client.post(
            f"/research-tasks/{task['id']}/papers/{paper['id']}",
            json={
                "note": "重复添加",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            {"detail": "这篇论文已经加入该研究任务"},
        )

    def test_add_paper_to_missing_research_task(self):
        """验证该场景的实际结果与预期一致。"""
        paper = self._create_test_paper()

        response = client.post(
            f"/research-tasks/999999/papers/{paper['id']}",
            json={
                "note": "任务不存在",
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"detail": "没有找到编号为 999999 的研究任务"},
        )

    def test_add_missing_paper_to_research_task(self):
        """验证该场景的实际结果与预期一致。"""
        task = self._create_test_task()

        response = client.post(
            f"/research-tasks/{task['id']}/papers/999999",
            json={
                "note": "论文不存在",
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"detail": "没有找到编号为 999999 的论文"},
        )

    def test_read_papers_for_missing_research_task(self):
        """验证该场景的实际结果与预期一致。"""
        response = client.get(
            "/research-tasks/999999/papers"
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"detail": "没有找到编号为 999999 的研究任务"},
        )

    def test_remove_paper_from_research_task(self):
        """验证该场景的实际结果与预期一致。"""
        paper = self._create_test_paper()
        task = self._create_test_task()
        link = self._add_test_link(task["id"], paper["id"])

        delete_response = client.delete(
            f"/research-tasks/{task['id']}/papers/{paper['id']}"
        )

        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(
            delete_response.json(),
            {
                "message": "论文已从研究任务中移除",
                "deleted_link": link,
            },
        )

        get_response = client.get(
            f"/research-tasks/{task['id']}/papers"
        )

        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(get_response.json(), [])

    def test_remove_missing_paper_link(self):
        """验证该场景的实际结果与预期一致。"""
        paper = self._create_test_paper()
        task = self._create_test_task()

        response = client.delete(
            f"/research-tasks/{task['id']}/papers/{paper['id']}"
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"detail": "这篇论文没有加入该研究任务"},
        )
