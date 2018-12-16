# (c) 2012, Michael DeHaan <michael.dehaan@gmail.com>
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

########################################################

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import os
import stat

from ansible.cli import CLI
from ansible.errors import AnsibleError, AnsibleOptionsError
from ansible.executor.playbook_executor import PlaybookExecutor
from ansible.executor.task_queue_manager import TaskQueueManager
from ansible.playbook.block import Block
from ansible.playbook.play_context import PlayContext
from ansible.parsing.dataloader import DataLoader

from ansible.executor.play_iterator import PlayIterator
from ansible.template import Templar
from ansible.vars.hostvars import HostVars
from ansible.plugins.loader import strategy_loader
from ansible.vars.reserved import warn_if_reserved

from ansible.playbook import Playbook
from ansible.module_utils.parsing.convert_bool import boolean

from ansible import constants as C
from ansible.errors import AnsibleParserError
from ansible.module_utils._text import to_text, to_native
from ansible.playbook.play import Play
from ansible.playbook.playbook_include import PlaybookInclude
from ansible.plugins.loader import get_all_plugin_loaders

from ansible.playbook.task import Task

from copy import deepcopy

try:
    from __main__ import display
except ImportError:
    from ansible.utils.display import Display
    display = Display()

class BackupPlay(Play):

    SUPPORTED_MODULE_ARGS = {
        "file" : ["path", "dest", "name"],
        "copy" : ["dest"],
        "template": ["dest"],
        "ini_file": ["path", "dest"]
    }
    # "assemble", "ini_file", "lineinfile"]

    ALLOWED_ACTION = [
        'meta',
        'include_vars',
        'import_role', 'include_role',
        'import_playbook',
        'set_fact', 'set_stats'
    ]
    # TODO: include_tasksに対応する

    @staticmethod
    def load(data, variable_manager=None, loader=None, vars=None):
        if ('name' not in data or data['name'] is None) and 'hosts' in data:
            if isinstance(data['hosts'], list):
                data['name'] = ','.join(data['hosts'])
            else:
                data['name'] = data['hosts']
        p = BackupPlay()
        if vars:
            p.vars = vars.copy()
        return p.load_data(data, variable_manager=variable_manager, loader=loader)

    def compile(self):
        '''
        Compiles and returns the task list for this play, compiled from the
        roles (which are themselves compiled recursively) and/or the list of
        tasks specified in the play.
        '''

        # create a block containing a single flush handlers meta
        # task, so we can be sure to run handlers at certain points
        # of the playbook execution
        flush_block = Block.load(
            data={'meta': 'flush_handlers'},
            play=self,
            variable_manager=self._variable_manager,
            loader=self._loader
        )

        block_list = []

        block_list.extend(self.pre_tasks)
        block_list.append(flush_block)
        block_list.extend(self._compile_roles())
        block_list.extend(self.tasks)
        block_list.append(flush_block)
        block_list.extend(self.post_tasks)
        block_list.append(flush_block)

        new_block_list = []

        for block in block_list:
            new_block = self._backup_compilie_block(block)
            if len(new_block.block) > 0:
                new_block_list.append(new_block)

        return new_block_list

    def _backup_compilie_block(self, block):
        new_block = deepcopy(block)
        new_block.block = []
        for task in block.block:
            if isinstance(task, Task):
                supported_action_name = self.SUPPORTED_MODULE_ARGS.keys()
                if task.action == 'file':
                    fetch_path = self._get_dest_path(task)
                    fetch_task = self._make_fetch_task(task, fetch_path)
                    stat_task = self._make_stat_task(task, fetch_path)
                    if 'state' in task.args:
                        if not task.args['state'] in ('directory', 'absent'):
                            new_block.block.append(fetch_task)
                    else:
                        new_block.block.append(fetch_task)
                    new_block.block.append(stat_task)
                elif task.action in supported_action_name:
                    fetch_path = self._get_dest_path(task)
                    fetch_task = self._make_fetch_task(task, fetch_path)
                    new_block.block.append(fetch_task)
                    stat_task = self._make_stat_task(task, fetch_path)
                    new_block.block.append(stat_task)
                elif task.action in self.ALLOWED_ACTION:
                    new_block.block.append(task)
                # TODO: include_tasksに対応する
            elif isinstance(block, Block):
                task = self._backup_compilie_block(task)
                new_block.block.append(task)
            else:
                new_block.block.append(task)
        return new_block

    def _get_dest_path(self, task):
        dest_path = ""
        for arg in self.SUPPORTED_MODULE_ARGS[task.action]:
            if arg in task.args:
                dest_path = task.args[arg]
                break
            msg = '%s module require [%s] in args' % (task.action, self.SUPPORTED_MODULE_ARGS[task.action])
            raise Exception(msg)

        if dest_path.endswith("/"):
            if 'src' in task.args:
                file_name = os.path.basename(task.args['src'])
                dest_path = os.path.join(dest_path, file_name)
            else:
                msg = '%s module is must not e' % (task.action, self.SUPPORTED_MODULE_ARGS[task.action])
                raise Exception(msg)

        return dest_path

    def _make_fetch_task(self, task, path):
        fetch_task = deepcopy(task)
        fetch_task.name = "<fetch|%s> %s" % (task.action, task.name)
        fetch_task.action = "fetch"
        fetch_task.args = {
            'src': path,
            'dest': './backup'
        }
        fetch_task.changed_when = []
        fetch_task.failed_when = []
        fetch_task.notify = None

        return fetch_task

    def _make_stat_task(self, task, path):
        stat_task = deepcopy(task)
        stat_task.name = "<stat|%s> %s" % (task.action, task.name)
        stat_task.action = "stat"
        stat_task.args = {
            'path': path,
        }
        stat_task.changed_when = []
        stat_task.failed_when = []
        stat_task.notify = None

        return stat_task

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

class BackupTaskQueueManager(TaskQueueManager):

    def run(self, play):
        '''
        Iterates over the roles/tasks in a play, using the given (or default)
        strategy for queueing tasks. The default is the linear strategy, which
        operates like classic Ansible by keeping all hosts in lock-step with
        a given task (meaning no hosts move on to the next task until all hosts
        are done with the current task).
        '''

        if not self._callbacks_loaded:
            self.load_callbacks()

        all_vars = self._variable_manager.get_vars(play=play)
        warn_if_reserved(all_vars)
        templar = Templar(loader=self._loader, variables=all_vars)

        new_play = play.copy()
        new_play.post_validate(templar)
        new_play.handlers = new_play.compile_roles_handlers() + new_play.handlers

        self.hostvars = HostVars(
            inventory=self._inventory,
            variable_manager=self._variable_manager,
            loader=self._loader,
        )

        play_context = PlayContext(new_play, self._options, self.passwords, self._connection_lockfile.fileno())
        for callback_plugin in self._callback_plugins:
            if hasattr(callback_plugin, 'set_play_context'):
                callback_plugin.set_play_context(play_context)

        self.send_callback('v2_playbook_on_play_start', new_play)

        # initialize the shared dictionary containing the notified handlers
        self._initialize_notified_handlers(new_play)

        # build the iterator
        iterator = PlayIterator(
            inventory=self._inventory,
            play=new_play,
            play_context=play_context,
            variable_manager=self._variable_manager,
            all_vars=all_vars,
            start_at_done=self._start_at_done,
        )

        # adjust to # of workers to configured forks or size of batch, whatever is lower
        self._initialize_processes(min(self._options.forks, iterator.batch_size))

        # load the specified strategy (or the default linear one)
        strategy = strategy_loader.get(new_play.strategy, self)
        if strategy is None:
            raise AnsibleError("Invalid play strategy specified: %s" % new_play.strategy, obj=play._ds)

        # Because the TQM may survive multiple play runs, we start by marking
        # any hosts as failed in the iterator here which may have been marked
        # as failed in previous runs. Then we clear the internal list of failed
        # hosts so we know what failed this round.
        for host_name in self._failed_hosts.keys():
            host = self._inventory.get_host(host_name)
            iterator.mark_host_failed(host)

        self.clear_failed_hosts()

        # during initialization, the PlayContext will clear the start_at_task
        # field to signal that a matching task was found, so check that here
        # and remember it so we don't try to skip tasks on future plays
        if getattr(self._options, 'start_at_task', None) is not None and play_context.start_at_task is None:
            self._start_at_done = True

        # and run the play using the strategy and cleanup on way out
        play_return = strategy.run(iterator, play_context)

        # now re-save the hosts that failed from the iterator to our internal list
        for host_name in iterator.get_failed_hosts():
            self._failed_hosts[host_name] = True

        strategy.cleanup()
        self._cleanup_processes()
        return play_return


class BackupPlaybookExecutor(PlaybookExecutor):

    def __init__(self, playbooks, inventory, variable_manager, loader, options, passwords):
        super().__init__(playbooks, inventory, variable_manager, loader, options, passwords)
        if self._tqm is not None:
            self._tqm = BackupTaskQueueManager(inventory=inventory, variable_manager=variable_manager, loader=loader, options=options, passwords=self.passwords)

    def run(self):
        '''
        Run the given playbook, based on the settings in the play which
        may limit the runs to serialized groups, etc.
        '''

        result = 0
        entrylist = []
        entry = {}
        try:
            for playbook_path in self._playbooks:
                pb = BackupPlaybook.load(playbook_path, variable_manager=self._variable_manager, loader=self._loader)
                # FIXME: move out of inventory self._inventory.set_playbook_basedir(os.path.realpath(os.path.dirname(playbook_path)))

                if self._tqm is None:  # we are doing a listing
                    entry = {'playbook': playbook_path}
                    entry['plays'] = []
                else:
                    # make sure the tqm has callbacks loaded
                    self._tqm.load_callbacks()
                    self._tqm.send_callback('v2_playbook_on_start', pb)

                i = 1
                plays = pb.get_plays()
                display.vv(u'%d plays in %s' % (len(plays), to_text(playbook_path)))

                for play in plays:
                    if play._included_path is not None:
                        self._loader.set_basedir(play._included_path)
                    else:
                        self._loader.set_basedir(pb._basedir)

                    # clear any filters which may have been applied to the inventory
                    self._inventory.remove_restriction()

                    # Allow variables to be used in vars_prompt fields.
                    all_vars = self._variable_manager.get_vars(play=play)
                    templar = Templar(loader=self._loader, variables=all_vars)
                    setattr(play, 'vars_prompt', templar.template(play.vars_prompt))

                    if play.vars_prompt:
                        for var in play.vars_prompt:
                            vname = var['name']
                            prompt = var.get("prompt", vname)
                            default = var.get("default", None)
                            private = boolean(var.get("private", True))
                            confirm = boolean(var.get("confirm", False))
                            encrypt = var.get("encrypt", None)
                            salt_size = var.get("salt_size", None)
                            salt = var.get("salt", None)

                            if vname not in self._variable_manager.extra_vars:
                                if self._tqm:
                                    self._tqm.send_callback('v2_playbook_on_vars_prompt', vname, private, prompt, encrypt, confirm, salt_size, salt, default)
                                    play.vars[vname] = display.do_var_prompt(vname, private, prompt, encrypt, confirm, salt_size, salt, default)
                                else:  # we are either in --list-<option> or syntax check
                                    play.vars[vname] = default

                    # Post validate so any play level variables are templated
                    all_vars = self._variable_manager.get_vars(play=play)
                    templar = Templar(loader=self._loader, variables=all_vars)
                    play.post_validate(templar)

                    if self._options.syntax:
                        continue

                    if self._tqm is None:
                        # we are just doing a listing
                        entry['plays'].append(play)

                    else:
                        self._tqm._unreachable_hosts.update(self._unreachable_hosts)

                        previously_failed = len(self._tqm._failed_hosts)
                        previously_unreachable = len(self._tqm._unreachable_hosts)

                        break_play = False
                        # we are actually running plays
                        batches = self._get_serialized_batches(play)
                        if len(batches) == 0:
                            self._tqm.send_callback('v2_playbook_on_play_start', play)
                            self._tqm.send_callback('v2_playbook_on_no_hosts_matched')
                        for batch in batches:
                            # restrict the inventory to the hosts in the serialized batch
                            self._inventory.restrict_to_hosts(batch)
                            # and run it...
                            result = self._tqm.run(play=play)

                            # break the play if the result equals the special return code
                            if result & self._tqm.RUN_FAILED_BREAK_PLAY != 0:
                                result = self._tqm.RUN_FAILED_HOSTS
                                break_play = True

                            # check the number of failures here, to see if they're above the maximum
                            # failure percentage allowed, or if any errors are fatal. If either of those
                            # conditions are met, we break out, otherwise we only break out if the entire
                            # batch failed
                            failed_hosts_count = len(self._tqm._failed_hosts) + len(self._tqm._unreachable_hosts) - \
                                (previously_failed + previously_unreachable)

                            if len(batch) == failed_hosts_count:
                                break_play = True
                                break

                            # update the previous counts so they don't accumulate incorrectly
                            # over multiple serial batches
                            previously_failed += len(self._tqm._failed_hosts) - previously_failed
                            previously_unreachable += len(self._tqm._unreachable_hosts) - previously_unreachable

                            # save the unreachable hosts from this batch
                            self._unreachable_hosts.update(self._tqm._unreachable_hosts)

                        if break_play:
                            break

                    i = i + 1  # per play

                if entry:
                    entrylist.append(entry)  # per playbook

                # send the stats callback for this playbook
                if self._tqm is not None:
                    if C.RETRY_FILES_ENABLED:
                        retries = set(self._tqm._failed_hosts.keys())
                        retries.update(self._tqm._unreachable_hosts.keys())
                        retries = sorted(retries)
                        if len(retries) > 0:
                            if C.RETRY_FILES_SAVE_PATH:
                                basedir = C.RETRY_FILES_SAVE_PATH
                            elif playbook_path:
                                basedir = os.path.dirname(os.path.abspath(playbook_path))
                            else:
                                basedir = '~/'

                            (retry_name, _) = os.path.splitext(os.path.basename(playbook_path))
                            filename = os.path.join(basedir, "%s.retry" % retry_name)
                            if self._generate_retry_inventory(filename, retries):
                                display.display("\tto retry, use: --limit @%s\n" % filename)

                    self._tqm.send_callback('v2_playbook_on_stats', self._tqm._stats)

                # if the last result wasn't zero, break out of the playbook file name loop
                if result != 0:
                    break

            if entrylist:
                return entrylist

        finally:
            if self._tqm is not None:
                self._tqm.cleanup()
            if self._loader:
                self._loader.cleanup_all_tmp_files()

        if self._options.syntax:
            display.display("No issues encountered")
            return result

        return result

class BackupCLI(CLI):
    ''' the tool to run *Ansible playbooks*, which are a configuration and multinode deployment system.
        See the project home page (https://docs.ansible.com) for more information. '''

    def parse(self):

        # create parser for CLI options
        parser = CLI.base_parser(
            usage="%prog [options] playbook.yml [playbook2 ...]",
            connect_opts=True,
            meta_opts=True,
            runas_opts=True,
            subset_opts=True,
            check_opts=True,
            inventory_opts=True,
            runtask_opts=True,
            vault_opts=True,
            fork_opts=True,
            module_opts=True,
            desc="Runs Ansible playbooks, executing the defined tasks on the targeted hosts.",
        )

        # ansible playbook specific opts
        parser.add_option('--list-tasks', dest='listtasks', action='store_true',
                          help="list all tasks that would be executed")
        parser.add_option('--list-tags', dest='listtags', action='store_true',
                          help="list all available tags")
        parser.add_option('--step', dest='step', action='store_true',
                          help="one-step-at-a-time: confirm each task before running")
        parser.add_option('--start-at-task', dest='start_at_task',
                          help="start the playbook at the task matching this name")

        self.parser = parser
        super(BackupCLI, self).parse()

        if len(self.args) == 0:
            raise AnsibleOptionsError("You must specify a playbook file to run")

        display.verbosity = self.options.verbosity
        self.validate_conflicts(runas_opts=True, vault_opts=True, fork_opts=True)

    def run(self):

        super(BackupCLI, self).run()

        # Note: slightly wrong, this is written so that implicit localhost
        # Manage passwords
        sshpass = None
        becomepass = None
        passwords = {}

        # initial error check, to make sure all specified playbooks are accessible
        # before we start running anything through the playbook executor
        for playbook in self.args:
            if not os.path.exists(playbook):
                raise AnsibleError("the playbook: %s could not be found" % playbook)
            if not (os.path.isfile(playbook) or stat.S_ISFIFO(os.stat(playbook).st_mode)):
                raise AnsibleError("the playbook: %s does not appear to be a file" % playbook)

        # don't deal with privilege escalation or passwords when we don't need to
        if not self.options.listhosts and not self.options.listtasks and not self.options.listtags and not self.options.syntax:
            self.normalize_become_options()
            (sshpass, becomepass) = self.ask_passwords()
            passwords = {'conn_pass': sshpass, 'become_pass': becomepass}

        loader, inventory, variable_manager = self._play_prereqs(self.options)

        # (which is not returned in list_hosts()) is taken into account for
        # warning if inventory is empty.  But it can't be taken into account for
        # checking if limit doesn't match any hosts.  Instead we don't worry about
        # limit if only implicit localhost was in inventory to start with.
        #
        # Fix this when we rewrite inventory by making localhost a real host (and thus show up in list_hosts())
        hosts = CLI.get_host_list(inventory, self.options.subset)

        # flush fact cache if requested
        if self.options.flush_cache:
            self._flush_cache(inventory, variable_manager)

        # create the playbook executor, which manages running the plays via a task queue manager
        pbex = BackupPlaybookExecutor(playbooks=self.args, inventory=inventory, variable_manager=variable_manager, loader=loader, options=self.options,
                                passwords=passwords)

        results = pbex.run()

        if isinstance(results, list):
            for p in results:

                display.display('\nplaybook: %s' % p['playbook'])
                for idx, play in enumerate(p['plays']):
                    if play._included_path is not None:
                        loader.set_basedir(play._included_path)
                    else:
                        pb_dir = os.path.realpath(os.path.dirname(p['playbook']))
                        loader.set_basedir(pb_dir)

                    msg = "\n  play #%d (%s): %s" % (idx + 1, ','.join(play.hosts), play.name)
                    mytags = set(play.tags)
                    msg += '\tTAGS: [%s]' % (','.join(mytags))

                    if self.options.listhosts:
                        playhosts = set(inventory.get_hosts(play.hosts))
                        msg += "\n    pattern: %s\n    hosts (%d):" % (play.hosts, len(playhosts))
                        for host in playhosts:
                            msg += "\n      %s" % host

                    display.display(msg)

                    all_tags = set()
                    if self.options.listtags or self.options.listtasks:
                        taskmsg = ''
                        if self.options.listtasks:
                            taskmsg = '    tasks:\n'

                        def _process_block(b):
                            taskmsg = ''
                            for task in b.block:
                                if isinstance(task, Block):
                                    taskmsg += _process_block(task)
                                else:
                                    if task.action == 'meta':
                                        continue

                                    all_tags.update(task.tags)
                                    if self.options.listtasks:
                                        cur_tags = list(mytags.union(set(task.tags)))
                                        cur_tags.sort()
                                        if task.name:
                                            taskmsg += "      %s" % task.get_name()
                                        else:
                                            taskmsg += "      %s" % task.action
                                        taskmsg += "\tTAGS: [%s]\n" % ', '.join(cur_tags)

                            return taskmsg

                        all_vars = variable_manager.get_vars(play=play)
                        play_context = PlayContext(play=play, options=self.options)
                        for block in play.compile():
                            block = block.filter_tagged_tasks(play_context, all_vars)
                            if not block.has_tasks():
                                continue
                            taskmsg += _process_block(block)

                        if self.options.listtags:
                            cur_tags = list(mytags.union(all_tags))
                            cur_tags.sort()
                            taskmsg += "      TASK TAGS: [%s]\n" % ', '.join(cur_tags)

                        display.display(taskmsg)

            return 0
        else:
            return results

    def _flush_cache(self, inventory, variable_manager):
        for host in inventory.list_hosts():
            hostname = host.get_name()
            variable_manager.clear_facts(hostname)
