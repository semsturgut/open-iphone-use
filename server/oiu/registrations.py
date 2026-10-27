"""Merge only this product's MCP entry; keep backups and restore only unmodified entries."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tomlkit
import subprocess
import stat

from .jsonc import Document

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
    opencode=home/'.config/opencode/opencode.json'
    jsonc=opencode.with_suffix('.jsonc')
    if jsonc.exists(): opencode=jsonc
    if opencode.parent.exists() or shutil.which('opencode'): candidates.append((opencode,'jsonc','mcp'))
    commandcode=home/'.commandcode/mcp.json'
    if commandcode.parent.exists() or shutil.which('commandcode'): candidates.append((commandcode,'json','mcpServers'))
    registered=[]
    # Uninstall considers all recorded paths even if a client executable was removed.
    if uninstall: candidates=[(Path(path),r['format'],r['key']) for path,r in manifest['entries'].items()]
    for path,format_,key in candidates:
        try:
            if path.is_symlink(): raise ValueError('config is a symlink; refusing to replace it')
            original=path.read_text() if path.exists() else ''
            editor=Document(original) if format_=='jsonc' else None
            doc=tomlkit.parse(original) if format_=='toml' else (editor.value(()) if editor else (json.loads(original) if original.strip() else {}))
            group=doc.get(key,{})
            if not hasattr(group,'get'): raise ValueError(f'{key} must be a mapping')
            existing=group.get(NAME)
            plain=existing.unwrap() if hasattr(existing,'unwrap') else existing
            record=manifest['entries'].get(str(path))
            if uninstall:
                if plain!=record['installed']:
                    print(f'Kept user-modified MCP registration: {path}'); continue
                if record['previous'] is None or legacy(record['previous']): del doc[key][NAME]
                elif format_=='toml': doc[key][NAME]=tomlkit.parse(record['previous'])['entry']
                else: doc[key][NAME]=record['previous']
                del manifest['entries'][str(path)]
            else:
                value={'command':str(executable),'args':['mcp']}
                if key=='mcp': value={'type':'local','command':[str(executable),'mcp'],'enabled':True}
                if path==commandcode: value.update(transport='stdio',enabled=True)
                if format_=='toml': value['tool_timeout_sec']=120
                if record and plain not in (record['installed'],value) and not legacy(plain):
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
            if editor:
                updated=editor.set((key,NAME),doc[key][NAME]) if NAME in doc.get(key,{}) else editor.remove((key,NAME))
            else: updated=tomlkit.dumps(doc) if format_=='toml' else json.dumps(doc,indent=2)+'\n'
            if updated!=original:
                if original:
                    digest=hashlib.sha256((str(path)+'\0'+original).encode()).hexdigest()
                    backup=state/(digest+'.backup')
                    if not backup.exists(): private_file(backup,original.encode())
                if path.exists() and path.read_text()!=original: raise ValueError('config changed concurrently')
                path.parent.mkdir(parents=True,exist_ok=True)
                private_file(path,updated.encode())
            registered.append({codex:'Codex',claude:'Claude Code',cursor:'Cursor',opencode:'OpenCode',commandcode:'Command Code'}.get(path,path.parent.name))
            atomic_json(manifest_path,manifest)
        except (ValueError,TypeError,OSError,IndexError) as error:
            raise ValueError(f'Could not safely update {path}: {error}. Existing configuration was preserved; fix it and re-run setup') from error
    install_skill(home, skill, manifest, uninstall)
    atomic_json(manifest_path,manifest)
    print(('Removed clients: ' if uninstall else 'Registered clients: ')+(', '.join(registered) or 'none detected')+'. Restart clients to reload MCP/skill; approval settings are preserved.')


def legacy(value):
    return 'Open iPhone Use.app/' in json.dumps(value)


def remove_directory(path):
    # Unlink the alias itself, never recurse through a symlink/junction.
    if path.is_symlink(): path.unlink()
    elif os.name=='nt' and path.exists() and getattr(path.lstat(),'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT: path.rmdir()
    elif path.exists(): shutil.rmtree(path)


def install_skill(home, source, manifest, uninstall=False):
    canonical=home/'.agents/skills'/NAME
    aliases=[home/client/'skills'/NAME for client in ('.claude','.commandcode')]
    if uninstall:
        for alias in aliases:
            if alias.is_symlink() and alias.resolve()==canonical.resolve(): alias.unlink()
            elif str(alias) in manifest.get('aliases',[]): remove_directory(alias)
        path=canonical/'SKILL.md'
        record=manifest['skills'].get(str(path))
        if path.exists() and record and hashlib.sha256(path.read_bytes()).hexdigest()==record['installed_hash']:
            remove_directory(canonical)
        manifest['skills']={}; manifest['aliases']=[]
        return
    if source is None:
        # setup's managed runtime source, editable checkout, then canonical installed copy.
        import sys
        sources=[Path(sys.executable).parent.parent.parent/'agent-skill.md',
                 Path(__file__).resolve().parents[2]/'macos/Resources/open-iphone-use-skill/SKILL.md',canonical/'SKILL.md']
        source=next((p for p in sources if p.is_file()),None)
        if source is None: raise ValueError('Skill source not found; re-run setup or supply --skill PATH')
    content=Path(source).read_bytes()  # Read before removing stale aliases/copies.
    roots=[home/client/'skills' for client in ('.agents','.claude','.commandcode','.codex','.cursor')]
    for root in roots:
        if not root.exists(): continue
        for backup in root.rglob('*.open-iphone-use-backup'):
            if backup.is_file() or backup.is_symlink(): backup.unlink()
        for old in root.iterdir():
            if old==canonical: continue
            app_alias=old.is_symlink() and legacy(os.readlink(old))
            app_copy=(old/'SKILL.md').is_file() and legacy((old/'SKILL.md').read_text())
            if old.name==NAME or app_alias or app_copy:
                remove_directory(old)
    # Replace canonical directory links/old copies, retaining a single source of truth.
    remove_directory(canonical)
    canonical.mkdir(parents=True,mode=0o755)
    canonical.chmod(0o755)
    path=canonical/'SKILL.md'
    path.write_bytes(content); path.chmod(0o644)
    manifest['skills']={str(path):{'installed_hash':hashlib.sha256(content).hexdigest(),'previous':None}}
    manifest['aliases']=[]
    for alias in aliases:
        client=alias.parent.parent
        if not client.exists() and not (client==home/'.claude' and (home/'.claude.json').exists()): continue
        alias.parent.mkdir(parents=True,exist_ok=True)
        relative=os.path.relpath(canonical,alias.parent)
        try: alias.symlink_to(relative,target_is_directory=True)
        except OSError:
            if os.name!='nt': raise
            try:
                # Junctions need no developer mode / symlink privilege on Windows.
                subprocess.run(['cmd','/d','/c','mklink','/J',str(alias),str(canonical)],check=True,capture_output=True)
            except (OSError,subprocess.CalledProcessError): shutil.copytree(canonical,alias)
        manifest['aliases'].append(str(alias))
    # Remove obsolete product backup files beside client configs too.
    for client in ('','.agents','.claude','.commandcode','.codex','.cursor','.config/opencode'):
        root=home/client
        if root.exists():
            for backup in root.glob('*.open-iphone-use-backup'):
                if backup.is_file() or backup.is_symlink(): backup.unlink()
    print('Skill: ~/.agents/skills/open-iphone-use/SKILL.md (client aliases share this copy)')
