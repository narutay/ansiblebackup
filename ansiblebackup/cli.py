import sys
import os
import stat

from ansible.cli import CLI
from ansible.errors import AnsibleError, AnsibleOptionsError
from ansible.playbook.block import Block
from ansible.playbook.play_context import PlayContext

from ansiblebackup.backup_executor import BackupExecutor
from ansiblebackup import __prog__, __version__

try:
    from __main__ import display
except ImportError:
    from ansible.utils.display import Display
    display = Display()

class BackupCLI(CLI):
    '''
    Ansible Backup CLI
    '''

    def parse(self):

        # create parser for CLI options
        parser = CLI.base_parser(
            usage="%s [options] playbook.yml [playbook2 ...]" % __prog__,
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
            desc="Backup Ansible playbooks.",
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

        if len(self.args) == 0:\
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

        # flush fact cache if requested
        if self.options.flush_cache:
            self._flush_cache(inventory, variable_manager)

        # create the playbook executor, which manages running the plays via a task queue manager
        pbex = BackupExecutor(playbooks=self.args, inventory=inventory, variable_manager=variable_manager, loader=loader, options=self.options,
                                passwords=passwords)
        pbex.run()


    def _flush_cache(self, inventory, variable_manager):
        for host in inventory.list_hosts():
            hostname = host.get_name()
            variable_manager.clear_facts(hostname)

def main():
    cli = BackupCLI(sys.argv)
    cli.parse()
    cli.run()

if __name__ == "__main__":
    main()