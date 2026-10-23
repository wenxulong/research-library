"""从原综合测试拆分；复用独立SQLite环境。"""
from tests.fixtures.main import *

class TestPaperAPI(unittest.TestCase):
    """测试论文增删改查接口及输入校验。"""
    def setUp(self):
        """在每个测试前准备独立测试环境。"""
        Base.metadata.drop_all(bind=test_engine)
        Base.metadata.create_all(bind=test_engine)

    def _create_test_paper(self):
        """创建测试论文并返回响应数据。"""
        response = client.post(
            "/papers",
            json={
                "title": "测试论文",
                "author": "测试作者",
                "year": 2025,
                "notes": "这是一篇测试论文",
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    def test_read_root(self):
        """验证该场景的实际结果与预期一致。"""
        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"message": "个人科研资料库 API 正在运行"},
        )

    def test_read_all_papers_is_empty(self):
        """验证该场景的实际结果与预期一致。"""
        response = client.get("/papers")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_create_paper(self):
        """验证该场景的实际结果与预期一致。"""
        response = client.post(
            "/papers",
            json={
                "title": "大语言模型研究",
                "author": "张三",
                "year": 2025,
                "notes": "学习大语言模型应用",
            },
        )

        result = response.json()

        self.assertEqual(response.status_code, 201)
        self.assertIsInstance(result["id"], int)
        self.assertEqual(result["title"], "大语言模型研究")
        self.assertEqual(result["author"], "张三")
        self.assertEqual(result["year"], 2025)
        self.assertEqual(result["notes"], "学习大语言模型应用")

    def test_read_one_paper(self):
        """验证该场景的实际结果与预期一致。"""
        created_paper = self._create_test_paper()
        paper_id = created_paper["id"]

        response = client.get(f"/papers/{paper_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), created_paper)

    def test_update_paper(self):
        """验证该场景的实际结果与预期一致。"""
        created_paper = self._create_test_paper()
        paper_id = created_paper["id"]

        response = client.put(
            f"/papers/{paper_id}",
            json={
                "title": "修改后的论文",
                "author": "修改后的作者",
                "year": 2026,
                "notes": "论文内容已经修改",
            },
        )

        result = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(result["id"], paper_id)
        self.assertEqual(result["title"], "修改后的论文")
        self.assertEqual(result["author"], "修改后的作者")
        self.assertEqual(result["year"], 2026)
        self.assertEqual(result["notes"], "论文内容已经修改")

    def test_delete_paper(self):
        """验证该场景的实际结果与预期一致。"""
        created_paper = self._create_test_paper()
        paper_id = created_paper["id"]

        delete_response = client.delete(f"/papers/{paper_id}")

        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(
            delete_response.json(),
            {
                "message": "论文删除成功",
                "deleted_paper": created_paper,
            },
        )

        get_response = client.get(f"/papers/{paper_id}")

        self.assertEqual(get_response.status_code, 404)

    def test_read_missing_paper(self):
        """验证该场景的实际结果与预期一致。"""
        response = client.get("/papers/999999")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"detail": "没有找到编号为 999999 的论文"},
        )

    def test_read_paper_with_invalid_id(self):
        """验证该场景的实际结果与预期一致。"""
        response = client.get("/papers/abc")

        self.assertEqual(response.status_code, 422)

    def test_create_paper_with_invalid_data(self):
        """验证该场景的实际结果与预期一致。"""
        response = client.post(
            "/papers",
            json={
                "title": "",
                "author": "测试作者",
                "year": 25,
                "notes": "错误的数据",
            },
        )

        self.assertEqual(response.status_code, 422)
