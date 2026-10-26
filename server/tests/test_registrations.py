"""Registration runs in disposable homes, never the tester's real agent clients."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from oiu.registrations import configure
from oiu.jsonc import Document


class RegistrationTests(unittest.TestCase):
    def test_all_clients_canonical_skill_aliases_legacy_migration_and_idempotence(self):
        with tempfile.TemporaryDirectory() as directory, patch('oiu.registrations.shutil.which',return_value=None):
            home=Path(directory)
            for client in ('.codex','.claude','.cursor','.commandcode','.config/opencode'):
                (home/client).mkdir(parents=True)
            old={'command':'/Applications/Open iPhone Use.app/Contents/Helpers/open-iphone-use','args':['mcp']}
            opencode=home/'.config/opencode/opencode.jsonc'
            original='''{
  // Keep the owner's model and comments, including URL-like strings.
  "model": "provider/model", "url": "https://example.com/*ok*/",
  "mcp": { /* another client */ "other": {"enabled": true,},
    "open-iphone-use": {"command": ["/Applications/Open iPhone Use.app/old"]},
  },
}
'''
            opencode.write_text(original)
            commandcode=home/'.commandcode/mcp.json'
            commandcode.write_text(json.dumps({'other':'keep','mcpServers':{'open-iphone-use':old}}))
            for client in ('.agents','.claude','.commandcode','.codex'):
                skill=home/client/'skills/open-iphone-use'
                skill.mkdir(parents=True)
                (skill/'SKILL.md').write_text('Old /Applications/Open iPhone Use.app/skill')
                (skill/'SKILL.md.open-iphone-use-backup').write_text('stale backup')
            old_dir=home/'.agents/skills/legacy-iphone-app'
            old_dir.mkdir(); (old_dir/'SKILL.md').write_text('/Applications/Open iPhone Use.app/old')
            broken=old_dir.parent/'old-app-alias'; broken.symlink_to('/Applications/Open iPhone Use.app/Contents/Resources/skill',target_is_directory=True)
            source=home/'source.md'; source.write_text('Canonical skill')
            with contextlib.redirect_stdout(io.StringIO()) as output: configure(home,Path('/venv/bin/open-iphone-use'),source)
            canonical=home/'.agents/skills/open-iphone-use'
            self.assertEqual((canonical/'SKILL.md').read_text(),'Canonical skill')
            self.assertEqual(canonical.stat().st_mode&0o777,0o755)
            self.assertEqual((canonical/'SKILL.md').stat().st_mode&0o777,0o644)
            for client in ('.claude','.commandcode'):
                alias=home/client/'skills/open-iphone-use'
                self.assertEqual(os.readlink(alias),'../../.agents/skills/open-iphone-use')
                self.assertEqual(alias.resolve(),canonical.resolve())
            self.assertFalse(old_dir.exists())
            self.assertFalse(broken.is_symlink())
            self.assertFalse(list(home.rglob('*.open-iphone-use-backup')))
            editor=Document(opencode.read_text())
            self.assertEqual(editor.value(('mcp','open-iphone-use')),
                             {'type':'local','command':['/venv/bin/open-iphone-use','mcp'],'enabled':True})
            self.assertIn('// Keep the owner',opencode.read_text())
            self.assertIn('/* another client */',opencode.read_text())
            self.assertEqual(editor.value(('url',)),'https://example.com/*ok*/')
            value=json.loads(commandcode.read_text())
            self.assertEqual(value['mcpServers']['open-iphone-use']['transport'],'stdio')
            self.assertEqual(value['other'],'keep')
            backups=list((home/'.local/share/open-iphone-use/registrations').glob('*.backup'))
            self.assertTrue(any(p.read_text()==original for p in backups))
            before=opencode.read_text()
            configure(home,Path('/venv/bin/open-iphone-use'),source)
            self.assertEqual(opencode.read_text(),before)
            configure(home,Path('/venv/bin/open-iphone-use'),uninstall=True)
            self.assertNotIn('open-iphone-use',Document(opencode.read_text()).value(('mcp',)))
            self.assertFalse(canonical.exists())
            self.assertFalse((home/'.claude/skills/open-iphone-use').is_symlink())
            self.assertIn('Registered clients:',output.getvalue())

    def test_jsonc_nested_add_remove_and_invalid_input(self):
        for original in ('{}','{ /* hi */ "model": "x" }','{"mcp":{/* owner */}}'):
            updated=Document(original).set(('mcp','open-iphone-use'),{'enabled':True})
            self.assertTrue(Document(updated).value(('mcp','open-iphone-use'))['enabled'])
            removed=Document(updated).remove(('mcp','open-iphone-use'))
            self.assertNotIn('open-iphone-use',Document(removed).value(('mcp',)))
        for original in ('{"x":1,"x":2}','{bad}', '{ /* unclosed'):
            with self.assertRaises((ValueError,IndexError)): Document(original)

    def test_invalid_config_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory, patch('oiu.registrations.shutil.which',return_value=None):
            home=Path(directory); root=home/'.config/opencode'; root.mkdir(parents=True)
            config=root/'opencode.jsonc'; config.write_text('{ invalid')
            with self.assertRaises(ValueError): configure(home,Path('/venv/bin/open-iphone-use'),home/'missing')
            self.assertEqual(config.read_text(),'{ invalid')

    def test_windows_alias_copy_fallback(self):
        from oiu.registrations import install_skill
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory); (home/'.commandcode').mkdir()
            source=home/'source'; source.write_text('skill')
            manifest={'skills':{}}
            with patch('oiu.registrations.os',types.SimpleNamespace(name='nt',path=os.path)),patch.object(Path,'symlink_to',side_effect=OSError('no privilege')),patch('oiu.registrations.subprocess.run',side_effect=subprocess.CalledProcessError(1,'mklink')):
                install_skill(home,source,manifest)
            self.assertEqual((home/'.commandcode/skills/open-iphone-use/SKILL.md').read_text(),'skill')
