#!/usr/bin/env python3
"""Independent package sizes, forbidden dependencies and small-scope checks."""
import ast
from pathlib import Path
import re
import sys
root=Path(__file__).resolve().parents[1]
limits={'bookengine':5000,'deai':800,'webui':1200,'exporter':400}
errors=[];cross=[];imports=[];words=[];maximum=(0,'none')
blocked=re.compile(r'provenance|custody|receipt|sha256|fingerprint|credential_scan|visual_qa|render_v',re.I)
for package,limit in limits.items():
    count=0
    paths=set((root/package).rglob('*.py'))
    if package=='webui':paths.update((root/'tools/webui').rglob('*.py'));paths.update([root/'tools/webui.py'] if (root/'tools/webui.py').exists() else [])
    for path in sorted(paths):
        if {'tests','eval','__pycache__'}&set(path.parts):continue
        source=path.read_text();lines=len(source.splitlines());count+=lines
        if lines>maximum[0]:maximum=(lines,str(path.relative_to(root)))
        words.extend(f'{path.name}:{m[0]}' for m in blocked.finditer(source))
        try:tree=ast.parse(source)
        except SyntaxError:errors.append(f'{path.name}:syntax');continue
        for node in ast.walk(tree):
            mods=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
            if isinstance(node,ast.Call) and isinstance(node.func,(ast.Name,ast.Attribute)) and (node.func.id if isinstance(node.func,ast.Name) else node.func.attr) in ('__import__','import_module') and node.args and isinstance(node.args[0],ast.Constant):mods.append(str(node.args[0].value))
            for mod in mods:
                if 'bestseller' in mod.split('.'):imports.append(f'{path.name}:{mod}')
                if package=='deai' and 'bookengine' in mod.split('.') or package=='bookengine' and 'deai' in mod.split('.'):cross.append(f'{package}:{mod}')
    print(f'{package}_lines={count} (limit={limit})')
    if count>limit:errors.append(f'{package} exceeds {limit}')
print(f'max_file_lines={maximum[0]} ({maximum[1]})')
print('imports_bestseller='+(', '.join(imports) or 'no'))
print('cross_imports='+(', '.join(cross) or 'none'))
print('forbidden_keywords='+(', '.join(words) or 'none'))
if maximum[0]>500:errors.append('file exceeds 500')
if imports or cross or words:errors.append('dependency/scope violation')
if len(Path(__file__).read_text().splitlines())>80:errors.append('scope tool exceeds 80')
print('FAIL: '+'; '.join(errors) if errors else 'PASS: package limits and independence')
sys.exit(bool(errors))
