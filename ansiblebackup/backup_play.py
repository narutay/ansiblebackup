import os
from datetime import datetime
from copy import deepcopy
from ansible.playbook.play import Play
from ansible.playbook.task import Task
from ansible.playbook.block import Block

class BackupPlay(Play):

    SUPPORTED_MODULE_ARGS = {
        "file" : ["path", "dest", "name"],
        "copy" : ["dest"],
        "template": ["dest"],
        "ini_file": ["path", "dest"]
    }
    # "assemble", "ini_file", "lineinfile"

    ALLOWED_ACTION = [
        'meta',
        'include_vars',
        'import_role', 'include_role',
        'import_playbook',
        'set_fact', 'set_stats'
    ]
    # TODO: support include_tasks

    def __init__(self):
        super(BackupPlay, self).__init__()

        dt = datetime.now()
        dt_str = dt.strftime('backup/%Y%m%d%H%M%S')
        self.dest = "%s/%s" % (os.getcwd(), dt_str)

    @staticmethod
    def load(data, variable_manager=None, loader=None, vars=None):
        '''
        Override and change Play class in load method.
        '''
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
        Override and wrap compile method.
        '''
        block_list = super().compile()
        new_block_list = []

        for block in block_list:
            new_block = self._backup_compilie_block(block)
            if len(new_block.block) > 0:
                new_block_list.append(new_block)

        return new_block_list

    def _backup_compilie_block(self, block):
        '''
        Compile and return backup task list,
        compied original playbooks.
        '''
        new_block = deepcopy(block)
        new_block.block = []
        for task in block.block:
            if isinstance(task, Task):
                supported_action_name = self.SUPPORTED_MODULE_ARGS.keys()

                # if action name is file, ignore state option in directory and absent
                if task.action == 'file':
                    fetch_path = self._get_dest_path(task)
                    fetch_task = self._make_fetch_task(task, fetch_path)
                    if 'state' in task.args:
                        if not task.args['state'] in ('directory', 'absent'):
                            new_block.block.append(fetch_task)
                    else:
                        new_block.block.append(fetch_task)
                    #stat_task = self._make_stat_task(task, fetch_path)
                    #new_block.block.append(stat_task)
                #suppored action eg. copy, template
                elif task.action in supported_action_name:
                    fetch_path = self._get_dest_path(task)
                    fetch_task = self._make_fetch_task(task, fetch_path)
                    new_block.block.append(fetch_task)
                    #stat_task = self._make_stat_task(task, fetch_path)
                    #new_block.block.append(stat_task)
                #allowed action eg. meta, import_tasks
                elif task.action in self.ALLOWED_ACTION:
                    new_block.block.append(task)
                # TODO: support include_tasks
            # if Block object, compile tasks recursion
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
                msg = '%s module is must not be directory' % task.action
                raise Exception(msg)

        return dest_path

    def _make_fetch_task(self, task, path):
        fetch_task = deepcopy(task)
        fetch_task.name = "<fetch|%s> %s" % (task.action, task.name)
        fetch_task.action = "fetch"
        fetch_task.args = {
            'src': path,
            'dest': self.dest
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
