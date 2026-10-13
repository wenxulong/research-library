"""Vue静态页面接入测试，不访问正式数据库或外部模型。"""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import api

class TestFrontendServing(unittest.TestCase):
    def setUp(self):
        self.directory=self.enterContext(TemporaryDirectory())
        self.root=Path(self.directory)
        self.enterContext(patch.object(api,'FRONTEND_FILE',self.root/'index.html'))
        self.client=TestClient(api.app)
        self.addCleanup(self.client.close)

    def test_missing_build_has_actionable_503(self):
        response=self.client.get('/app')
        self.assertEqual(response.status_code,503)
        self.assertIn('npm run build',response.json()['detail'])

    def test_both_app_urls_serve_compiled_entry(self):
        (self.root/'index.html').write_text('<html><body>Vue build entry</body></html>')
        for path in ['/app','/app/']:
            with self.subTest(path=path):
                response=self.client.get(path)
                self.assertEqual(response.status_code,200)
                self.assertIn('Vue build entry',response.text)
                self.assertEqual(response.headers['cache-control'],'no-cache')

    def test_assets_mount_serves_only_asset_directory(self):
        assets=self.root/'assets';assets.mkdir();(assets/'sample.js').write_text('console.log("ok")')
        (self.root/'private.txt').write_text('not public')
        static=next(route.app for route in api.app.routes if getattr(route,'name',None)=='frontend-assets')
        with patch.object(static,'directory',str(assets)),patch.object(static,'all_directories',[str(assets)]),patch.object(static,'config_checked',True):
            self.assertEqual(self.client.get('/app/assets/sample.js').status_code,200)
            self.assertEqual(self.client.get('/app/assets/missing.js').status_code,404)
            self.assertEqual(self.client.get('/app/assets/%2e%2e/private.txt').status_code,404)

if __name__=='__main__':
    unittest.main()
