import importlib.metadata as md
import json, shutil
from pathlib import Path
from packaging.requirements import Requirement
source=Path('/Users/<owner>/LLM/AOF/.venv/lib/python3.13/site-packages')
target=Path('/private/tmp/aof-diagnosis-20260905/cached-venv/lib/python3.13/site-packages')
root=Path('/private/tmp/aof-diagnosis-20260905/main')
queue=[(Requirement(s.split('#')[0].strip()),'') for s in (root/'requirements-dev.txt').read_text().splitlines() if s.split('#')[0].strip()]
seen=set(); packages={}
while queue:
    req,extra=queue.pop()
    if req.marker and not req.marker.evaluate({'extra':extra}): continue
    dist=md.distribution(req.name)
    if not req.specifier.contains(dist.version): raise ValueError(f'{req}: installed {dist.version}')
    key=(dist.metadata['Name'],tuple(sorted(req.extras)))
    if key in seen: continue
    seen.add(key)
    packages[dist.metadata['Name']]=dist.version
    for raw in dist.requires or []:
        for e in (req.extras or ['']): queue.append((Requirement(raw),e))
    for f in dist.files or []:
        src=Path(dist.locate_file(f)).resolve()
        try: rel=src.relative_to(source)
        except ValueError: continue
        if src.suffix=='.pth' or not src.is_file(): continue
        dst=target/rel; dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst)
print(json.dumps(packages,indent=2,sort_keys=True))
