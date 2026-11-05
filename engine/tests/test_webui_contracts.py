"""Behavior-preserving split of test_webui_contracts.py."""

from support_webui_contracts import *


class BoundaryTestsPart1(BoundaryTests):
    def test_ui_import_boundary_allows_only_authorized_topic_transport(self):
        violations = []
        for path in [ENGINE / "tools/webui.py", *WEBUI.glob("*.py")]:
            tree = ast.parse(path.read_text("utf-8"))
            allowed = set()
            if path == WEBUI / "jobs.py":
                for function in tree.body:
                    if isinstance(function, ast.FunctionDef) and function.name == "topic_worker":
                        for node in function.body:
                            if (
                                isinstance(node, ast.ImportFrom)
                                and node.level == 0
                                and (node.module, tuple(a.name for a in node.names))
                                in {("deai.client", ("MiniMax",)), ("deai.io", ("parse_json",))}
                            ):
                                allowed.add(node)
            for node in ast.walk(tree):
                names = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else []
                )
                for name in names:
                    if name.split(".")[0] in {"bookengine", "deai", "bestseller"} and node not in allowed:
                        violations.append(f"{path.name}:{node.lineno}: {name}")
        self.assertEqual(
            violations, [], "Outside authorized topic transport, use public CLIs: " + ", ".join(violations)
        )

    def test_import_and_cli_help_work_with_engine_imports_forbidden(self):
        script = """
import importlib.abc, runpy, sys
class DenyEngine(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'bookengine', 'deai', 'bestseller'}:
            raise AssertionError('forbidden engine import: ' + fullname)
sys.meta_path.insert(0, DenyEngine())
sys.path.insert(0, str(__import__('pathlib').Path(sys.argv[1]).parents[1]))
sys.path.insert(0, sys.argv[1])
import library, jobs, server
sys.argv = [sys.argv[2], '--help']
runpy.run_path(sys.argv[0], run_name='__main__')
"""
        result = subprocess.run(
            [sys.executable, "-B", "-I", "-c", script, str(WEBUI), str(ENGINE / "tools/webui.py")],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("--budget", result.stdout)
