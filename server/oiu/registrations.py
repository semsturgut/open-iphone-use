"""Merge only this product's MCP entry; keep backups and restore only unmodified entries."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import tomlkit

from .control import atomic_json
from .bootstrap import private_file
from .permissions import secure_directory

NAME='open-iphone-use'


def configure(home, executable, skill=None, uninstall=False):
    home=Path(home)
    state=home/'.local/share/open-iphone-use/registrations'
    secure_directory(state)
    manifest_path=state/'manifest.json'
    manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else {'entries':{},'skills':{}}
    candidates=[]
    codex=home/'.codex/config.toml'
    if codex.parent.exists() or shutil.which('codex'): candidates.append((codex,'toml','mcp_servers'))
    claude=home/'.claude.json'
    if claude.exists() or (home/'.claude').exists() or shutil.which('claude'): candidates.append((claude,'json','mcpServers'))
    cursor=home/'.cursor/mcp.json'
    if cursor.parent.exists() or shutil.which('cursor'): candidates.append((cursor,'json','mcpServers'))
    # Uninstall considers all recorded paths even if a client executable was removed.
    if uninstall: candidates=[(Path(path),r['format'],r['key']) for path,r in manifest['entries'].items()]
    for path,format_,key in candidates:
        try:
            if path.is_symlink(): raise ValueError('config is a symlink; refusing to replace it')
            original=path.read_text() if path.exists() else ''
            doc=tomlkit.parse(original) if format_=='toml' else (json.loads(original) if original.strip() else {})
            group=doc.get(key,{})
            if not hasattr(group,'get'): raise ValueError(f'{key} must be a mapping')
            existing=group.get(NAME)
            plain=existing.unwrap() if hasattr(existing,'unwrap') else existing
            record=manifest['entries'].get(str(path))
            if uninstall:
                if plain!=record['installed']:
                    print(f'Kept user-modified MCP registration: {path}'); continue
                if record['previous'] is None: del doc[key][NAME]
                elif format_=='toml': doc[key][NAME]=tomlkit.parse(record['previous'])['entry']
                else: doc[key][NAME]=record['previous']
                del manifest['entries'][str(path)]
            else:
                value={'command':str(executable),'args':['mcp']}
                if format_=='toml': value['tool_timeout_sec']=120
                if record and plain not in (record['installed'],value):
                    print(f'Kept user-modified MCP registration: {path}'); continue
                if not record:
                    previous=(tomlkit.dumps({'entry':existing}) if format_=='toml' and existing is not None else plain)
                    record={'format':format_,'key':key,'previous':previous,'installed':value}
                    if original:
                        backup=state/(hashlib.sha256(str(path).encode()).hexdigest()+'.backup')
                        if not backup.exists(): private_file(backup,original.encode())
                record['installed']=value
                manifest['entries'][str(path)]=record
                if key not in doc: doc[key]=tomlkit.table() if format_=='toml' else {}
                doc[key][NAME]=value
            updated=tomlkit.dumps(doc) if format_=='toml' else json.dumps(doc,indent=2)+'\n'
            if updated!=original:
                if path.exists() and path.read_text()!=original: raise ValueError('config changed concurrently')
                path.parent.mkdir(parents=True,exist_ok=True)
                private_file(path,updated.encode())
            print(('Removed' if uninstall else 'Registered')+f' MCP entry: {path}')
            atomic_json(manifest_path,manifest)
        except (ValueError,TypeError,OSError) as error:
            raise ValueError(f'Could not safely update {path}: {error}. Existing configuration was preserved; fix it and re-run setup') from error
    if uninstall:
        targets=[Path(path) for path in manifest['skills']]
    else:
        if not skill: raise ValueError('Skill source required for installation')
        targets=[home/'.agents/skills/open-iphone-use/SKILL.md']
        if (home/'.claude').exists() or claude.exists(): targets.append(home/'.claude/skills/open-iphone-use/SKILL.md')
    content=Path(skill).read_bytes() if skill and not uninstall else b''
    for path in targets:
        if path.is_symlink(): raise ValueError(f'Skill is a symlink; refusing to replace {path}')
        old=path.read_bytes() if path.exists() else None
        record=manifest['skills'].get(str(path))
        if uninstall:
            if old is None or hashlib.sha256(old).hexdigest()!=record['installed_hash']:
                print(f'Kept user-modified skill: {path}'); continue
            if record['previous'] is None: path.unlink()
            else: private_file(path,base64.b64decode(record['previous']))
            del manifest['skills'][str(path)]
        else:
            if record and old is not None and hashlib.sha256(old).hexdigest()!=record['installed_hash']:
                print(f'Kept user-modified skill: {path}'); continue
            if not record: record={'previous':base64.b64encode(old).decode() if old is not None else None}
            record['installed_hash']=hashlib.sha256(content).hexdigest()
            path.parent.mkdir(parents=True,exist_ok=True)
            if old!=content: private_file(path,content)
            manifest['skills'][str(path)]=record
        atomic_json(manifest_path,manifest)
    print('Agent clients may need a restart. Existing approval settings are preserved.')
