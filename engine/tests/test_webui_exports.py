"""Public HTTP downloads consume the independent exporter in both reading modes."""
import io
import xml.etree.ElementTree as ET
import zipfile
from test_webui_contracts import WebFixture

class WebExportTests(WebFixture):
    def test_epub_and_fanqie_preserve_selected_version(self):
        self.book()
        for version,word,excluded in [('draft','草稿','定稿'),('final','定稿','草稿')]:
            for fmt in ('epub','fanqie'):
                with self.subTest(version=version,fmt=fmt):
                    status,headers,body=self.download(fmt=fmt,version=version)
                    self.assertEqual(status,200)
                    with zipfile.ZipFile(io.BytesIO(body)) as archive:
                        self.assertIsNone(archive.testzip())
                        if fmt=='epub':
                            self.assertEqual(archive.read('mimetype'),b'application/epub+zip')
                            content=''.join(ET.fromstring(archive.read('OEBPS/chapter-001.xhtml')).itertext())
                        else:
                            names=archive.namelist();self.assertEqual(len(names),2)
                            self.assertTrue(all(n.startswith('fanqie/') and n.endswith('.txt') for n in names))
                            content=archive.read(names[0]).decode('utf-8')
                        self.assertIn(word+'1',content);self.assertNotIn(excluded+'1',content)
    def test_incomplete_final_never_exports_epub_or_fanqie(self):
        folder=self.book();(folder/'final/chapter-002.md').unlink()
        for fmt in ('epub','fanqie'):self.assertEqual(self.download(fmt=fmt,version='final')[0],400)
