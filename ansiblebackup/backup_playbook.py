import os

from ansible import constants as C
from ansible.errors import AnsibleParserError
from ansible.module_utils._text import to_text, to_native
from ansible.playbook import Playbook
from ansible.module_utils.parsing.convert_bool import boolean
from ansible.plugins.loader import get_all_plugin_loaders
from ansible.playbook.playbook_include import PlaybookInclude

from ansiblebackup.backup_play import BackupPlay

try:
    from __main__ import display
except ImportError:
    from ansible.utils.display import Display
    display = Display()

class BackupPlaybook(Playbook):

    @staticmethod
    def load(file_name, variable_manager=None, loader=None):
        pb = BackupPlaybook(loader=loader)
        pb._load_playbook_data(file_name=file_name, variable_manager=variable_manager)
        return pb

    def _load_playbook_data(self, file_name, variable_manager, vars=None):

        if os.path.isabs(file_name):
            self._basedir = os.path.dirname(file_name)
        else:
            self._basedir = os.path.normpath(os.path.join(self._basedir, os.path.dirname(file_name)))

        # set the loaders basedir
        cur_basedir = self._loader.get_basedir()
        self._loader.set_basedir(self._basedir)

        self._file_name = file_name

        # dynamically load any plugins from the playbook directory
        for name, obj in get_all_plugin_loaders():
            if obj.subdir:
                plugin_path = os.path.join(self._basedir, obj.subdir)
                if os.path.isdir(plugin_path):
                    obj.add_directory(plugin_path)

        try:
            ds = self._loader.load_from_file(os.path.basename(file_name))
        except UnicodeDecodeError as e:
            raise AnsibleParserError("Could not read playbook (%s) due to encoding issues: %s" % (file_name, to_native(e)))

        if not isinstance(ds, list):
            # restore the basedir in case this error is caught and handled
            self._loader.set_basedir(cur_basedir)
            raise AnsibleParserError("playbooks must be a list of plays", obj=ds)

        # Parse the playbook entries. For plays, we simply parse them
        # using the Play() object, and includes are parsed using the
        # PlaybookInclude() object
        for entry in ds:
            if not isinstance(entry, dict):
                # restore the basedir in case this error is caught and handled
                self._loader.set_basedir(cur_basedir)
                raise AnsibleParserError("playbook entries must be either a valid play or an include statement", obj=entry)

            if any(action in entry for action in ('import_playbook', 'include')):
                if 'include' in entry:
                    display.deprecated("'include' for playbook includes. You should use 'import_playbook' instead", version="2.8")
                pb = PlaybookInclude.load(entry, basedir=self._basedir, variable_manager=variable_manager, loader=self._loader)
                if pb is not None:
                    self._entries.extend(pb._entries)
                else:
                    which = entry.get('import_playbook', entry.get('include', entry))
                    display.display("skipping playbook '%s' due to conditional test failure" % which, color=C.COLOR_SKIP)
            else:
                entry_obj = BackupPlay.load(entry, variable_manager=variable_manager, loader=self._loader, vars=vars)
                self._entries.append(entry_obj)

        # we're done, so restore the old basedir in the loader
        self._loader.set_basedir(cur_basedir)
