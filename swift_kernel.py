#!/usr/bin/python
#
# Copyright 2018 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import tempfile
import json
import lldb
import subprocess
import sys
import os

from ipykernel.kernelbase import Kernel


class Completer:
    """Serves code-completion requests.

    This class is stateful because it needs to record the execution history
    to know how to complete using things that the user has declared and
    imported.

    There is an "intentional" bug in this class: If the user re-declares
    something, code-completion only sees the first declaration because the
    history contains both declarations and SourceKit seems to handle this by
    ignoring the second declaration. Fixing this bug might require significant
    changes to the code-completion approach. (For example, stop using SourceKit
    and start using the Swift REPL's code-completion implementation.)
    """

    def __init__(self, sourcekitten_binary, sourcekitten_env, log):
        """
        :param sourcekitten_binary: Path to sourcekitten binary.
        :param sourcekitten_env: Environment variables for sourcekitten.
        :param log: A logger with `.error` and `.warning` methods.
        """
        self.sourcekitten_binary = sourcekitten_binary
        self.sourcekitten_env = sourcekitten_env
        self.log = log
        self.successful_execution_history = []

    def record_successful_execution(self, code):
        """Call this whenever the kernel successfully executes a cell.
        :param code: The cell's code, as a python "unicode" string.
        """
        self.successful_execution_history.append(code)

    def complete(self, code, pos):
        """Returns code-completion results for `code` at `pos`.
        :param code: The code in the current cell, as a python "unicode"
                     string.
        :param pos: The number of unicode code points before the cursor.

        Returns an array of completions in SourceKit completion format.

        If there are any errors, ouputs warnings to the logger and returns an
        empty array.
        """

        if self.sourcekitten_binary is None:
          return []

        # Write all the successful execution history to a file that sourcekitten
        # can read. We do this so that sourcekitten can see all the decls and
        # imports that happened in previously-executed cells.
        codefile = tempfile.NamedTemporaryFile(prefix='jupyter-', suffix='.swift', delete=False)
        for index, execution in enumerate(self.successful_execution_history):
            codefile.write('// History %d\n' % index)
            codefile.write(execution.encode('utf8'))
            codefile.write('\n\n')

        # Write the code that the user is trying to complete to the file.
        codefile.write('// Current cell\n')
        code_offset = codefile.tell()
        codefile.write(code.encode('utf8'))
        codefile.close()

        # Sourcekitten wants the offset in bytes.
        sourcekitten_offset = code_offset + len(code[0:pos].encode('utf8'))

        # Ask sourcekitten for a completion.
        args = (self.sourcekitten_binary, 'complete', '--file', codefile.name,
                '--offset', str(sourcekitten_offset))
        process = subprocess.Popen(args, env=self.sourcekitten_env,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        output, err = process.communicate()

        # Suppress errors that say "compiler is in code completion mode",
        # because sourcekitten emits them even when everything is okay.
        # Log other errors to the logger.
        errlines = [
            '\t' + errline
            for errline
            in err.split('\n')
            if len(errline) > 0
            if errline.find('compiler is in code completion mode') == -1
        ]
        if len(errlines) > 0:
            self.log.warning('sourcekitten completion stderr:\n%s' %
                             '\n'.join(errlines))

        # Parse sourcekitten's output.
        try:
            completions = json.loads(output)
        except:
            self.log.error(
              'could not parse sourcekitten output as JSON\n\t'
              'sourcekitten command was: %s\n\t'
              'sourcekitten output was: %s\n\t'
              'try running the above sourcekitten command with the '
              'following environment variables: %s to get a more '
              'detailed error message' % (
                  ' '.join(args), output,
                  dict(self.sourcekitten_env, SOURCEKIT_LOGGING=3)))
            return []

        if len(completions) == 0:
            self.log.warning('sourcekitten did not return any completions')

        # We intentionally do not clean up the temporary file until a success,
        # so that you can use the temporary file for debugging when there are
        # exceptions.
        os.remove(codefile.name)

        return completions


class SwiftKernel(Kernel):
    implementation = 'SwiftKernel'
    implementation_version = '0.1'
    banner = ''

    language_info = {
        'name': 'swift',
        'mimetype': 'text/x-swift',
        'file_extension': '.swift',
    }

    def __init__(self, **kwargs):
        super(SwiftKernel, self).__init__(**kwargs)
        self._init_repl_process()
        self._init_completer()

    def _init_repl_process(self):
        self.debugger = lldb.SBDebugger.Create()
        self.debugger.SetAsync(False)
        if not self.debugger:
            raise Exception('Could not start debugger')

        # LLDB crashes while trying to load some Python stuff on Mac. Maybe
        # something is misconfigured? This works around the problem by telling
        # LLDB not to load the Python scripting stuff, which we don't use
        # anyways.
        self.debugger.SetScriptLanguage(lldb.eScriptLanguageNone)

        repl_swift = os.environ['REPL_SWIFT_PATH']
        self.target = self.debugger.CreateTargetWithFileAndArch(repl_swift, '')
        if not self.target:
            raise Exception('Could not create target %s' % repl_swift)

        self.main_bp = self.target.BreakpointCreateByName(
            'repl_main', self.target.GetExecutable().GetFilename())
        if not self.main_bp:
            raise Exception('Could not set breakpoint')

        self.process = self.target.LaunchSimple(None, None, os.getcwd())
        if not self.process:
            raise Exception('Could not launch process')

        self.expr_opts = lldb.SBExpressionOptions()
        swift_language = lldb.SBLanguageRuntime.GetLanguageTypeFromString(
            'swift')
        self.expr_opts.SetLanguage(swift_language)
        self.expr_opts.SetREPLMode(True)

        # Sets an infinite timeout so that users can run aribtrarily long
        # computations.
        self.expr_opts.SetTimeoutInMicroSeconds(0)

    def _init_completer(self):
      self.completer = Completer(
          os.environ.get('SOURCEKITTEN'),
          {
              key: os.environ[key]
              for key in [
                  'LINUX_SOURCEKIT_LIB_PATH',
                  'XCODE_DEFAULT_TOOLCHAIN_OVERRIDE'
              ]
              if key in os.environ
          },
          self.log)

    def do_execute(self, code, silent, store_history=True,
                   user_expressions=None, allow_stdin=False):
        # Execute the code.
        result = self.target.EvaluateExpression(
            code.encode('utf8'), self.expr_opts)

        # Send stdout to the client.
        while True:
            BUFFER_SIZE = 1000
            stdout_buffer = self.process.GetSTDOUT(BUFFER_SIZE)
            if len(stdout_buffer) == 0:
                break
            self.send_response(self.iopub_socket, 'stream', {
                'name': 'stdout',
                'text': stdout_buffer
            })

        if result.error.type == lldb.eErrorTypeInvalid:
            # Success, with value.
            self.completer.record_successful_execution(code)
            self.send_response(self.iopub_socket, 'execute_result', {
                'execution_count': self.execution_count,
                'data': {
                    'text/plain': result.description
                }
            })

            return {
                'status': 'ok',
                'execution_count': self.execution_count,
                'payload': [],
                'user_expressions': {}
            }
        elif result.error.type == lldb.eErrorTypeGeneric:
            # Success, without value.
            self.completer.record_successful_execution(code)
            return {
                'status': 'ok',
                'execution_count': self.execution_count,
                'payload': [],
                'user_expressions': {}
            }
        else:
            # Error!
            self.send_response(self.iopub_socket, 'error', {
                'execution_count': self.execution_count,
                'ename': '',
                'evalue': '',
                'traceback': [result.error.description],
            })

            return {
                'status': 'error',
                'execution_count': self.execution_count,
                'ename': '',
                'evalue': '',
                'traceback': [result.error.description],
            }

    def do_complete(self, code, cursor_pos):
        completions = self.completer.complete(code, cursor_pos)
        return {
            'matches': [completion['sourcetext'] for completion in completions],
            'cursor_start': cursor_pos,
            'cursor_end': cursor_pos,
        }

if __name__ == '__main__':
    from ipykernel.kernelapp import IPKernelApp
    IPKernelApp.launch_instance(kernel_class=SwiftKernel)
