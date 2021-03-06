#!/usr/bin/python2
# OpenPOWER Automated Test Project
#
# Contributors Listed Below - COPYRIGHT 2018
# [+] International Business Machines Corp.
#
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied. See the License for the specific language governing
# permissions and limitations under the License.

import re
import sys
import time
import pexpect
import subprocess
import json

from OpTestUtil import OpTestUtil
from Exceptions import CommandFailed
from common.OpTestError import OpTestError
from OpTestConstants import OpTestConstants as BMC_CONST
import OpTestSystem
try:
    from common import OPexpect
except ImportError:
    import OPexpect

class ConsoleState():
    DISCONNECTED = 0
    CONNECTED = 1

def set_system_to_UNKNOWN(system):
    s = system.get_state()
    system.set_state(OpTestSystem.OpSystemState.UNKNOWN)
    return s

class OpTestSSH():
    def __init__(self, host, username, password, logfile=sys.stdout, port=22,
            prompt=None, check_ssh_keys=False, known_hosts_file=None, use_default_bash=None):
        self.state = ConsoleState.DISCONNECTED
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.logfile = logfile
        self.prompt = prompt
        self.check_ssh_keys=check_ssh_keys
        self.known_hosts_file=known_hosts_file
        self.use_default_bash = use_default_bash
        self.system = None

    def set_system(self, system):
        self.system = system

    def terminate(self):
        if self.state == ConsoleState.CONNECTED:
            self.console.terminate()
            self.state = ConsoleState.DISCONNECTED

    def close(self):
        if self.state == ConsoleState.DISCONNECTED:
            return
        try:
            self.console.send("\r")
            self.console.send('~.')
            self.console.expect(pexpect.EOF)
            self.console.close()
        except pexpect.ExceptionPexpect:
            raise "SSH Console: failed to close ssh console"
        self.console.terminate()
        self.state = ConsoleState.DISCONNECTED

    def connect(self):
        if self.state == ConsoleState.CONNECTED:
            self.console.terminate()
            self.state = ConsoleState.DISCONNECTED

        cmd = ("sshpass -p %s " % (self.password)
               + " ssh"
               + " -p %s" % str(self.port)
               + " -l %s %s" % (self.username, self.host)
               + " -o PubkeyAuthentication=no"
               )

        if not self.check_ssh_keys:
            cmd = (cmd
                    + " -q"
                    + " -o 'UserKnownHostsFile=/dev/null' "
                    + " -o 'StrictHostKeyChecking=no'"
                    )
        elif self.known_hosts_file:
            cmd = (cmd + " -o UserKnownHostsFile=" + self.known_hosts_file)

        print cmd
        consoleChild = OPexpect.spawn(cmd,logfile=self.logfile,
                failure_callback=set_system_to_UNKNOWN,
                failure_callback_data=self.system)
        self.state = ConsoleState.CONNECTED
        self.console = consoleChild
        # Users expecting "Host IPMI" will reference console.sol so make it available
        self.sol = self.console
        self.set_unique_prompt(consoleChild)
        return consoleChild

    def set_unique_prompt(self, console):
        if self.port == 2200:
            return
        if self.use_default_bash:
            console.sendline("exec bash --norc --noprofile")

        if self.prompt:
            prompt = self.prompt
        else:
            prompt = "\[console-pexpect\]#"
        expect_prompt = prompt + "$"

        console.sendline('PS1=' + prompt)
        console.expect("\n") # from us, because echo

        # Check for an early EOF - this can happen if we had a bad ssh host key
        # console.isalive() can still return True in this case if called
        # quickly enough.
        try:
            console.expect([expect_prompt], timeout=60)
            output = console.before
        except pexpect.EOF as cf:
            print cf
            if self.check_ssh_keys:
                raise Exception("SSH session exited early - bad host key?")
            else:
                raise Exception("SSH session exited early!")

    def get_console(self):
        if self.state == ConsoleState.DISCONNECTED:
            self.connect()

        count = 0
        while (not self.console.isalive()):
            print '# Reconnecting'
            if (count > 0):
                time.sleep(1)
            self.connect()
            count += 1
            if count > 120:
                raise "SSH: not able to get console"

        return self.console

    def run_command(self, command, timeout=60):
        console = self.get_console()
        if self.prompt:
            prompt = self.prompt
        else:
            prompt = "\[console-pexpect\]#"
        expect_prompt = prompt + "$"

        console.sendline(command)
        console.expect("\n") # from us
        rc = None
        output = None
        exitcode = None
        try:
            rc = console.expect([expect_prompt], timeout=timeout)
            output = console.before
            console.sendline("echo $?")
            console.expect("\n") # from us
            rc = console.expect([expect_prompt], timeout=timeout)
            exitcode = int(console.before)
        except pexpect.TIMEOUT as e:
            print e
            print "# TIMEOUT waiting for command to finish."
            print "# Attempting to control-c"
            try:
                console.sendcontrol('c')
                rc = console.expect(["\[console-pexpect\]#$"], 10)
                if rc == 0:
                    raise CommandFailed(command, "TIMEOUT", -1)
            except pexpect.TIMEOUT:
                print "# Timeout trying to kill timed-out command."
                print "# Failing current command and attempting to continue"
                self.terminate()
                raise CommandFailed("ssh -p %u" % self.port, "timeout", -1)
            raise e

        if rc == 0:
            res = output
            res = res.splitlines()
            if exitcode != 0:
                raise CommandFailed(command, res, exitcode)
            return res
        else:
            res = console.before
            res = res.split(command)
            return res[-1].splitlines()

    # This command just runs and returns the ouput & ignores the failure
    # A straight copy of what's in OpTestIPMI
    def run_command_ignore_fail(self, command, timeout=60):
        try:
            output = self.run_command(command, timeout)
        except CommandFailed as cf:
            output = cf.output
        return output
