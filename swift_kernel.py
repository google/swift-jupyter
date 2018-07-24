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
import re

from ipykernel.kernelbase import Kernel
from jupyter_client.jsonutil import squash_dates


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


class ExecutionResult:
    """Base class for the result of executing code."""
    pass


class ExecutionResultSuccess(ExecutionResult):
    """Base class for the result of successfully executing code."""
    pass


class ExecutionResultError(ExecutionResult):
    """Base class for the result of unsuccessfully executing code."""
    def description(self):
        raise NotImplementedError()

    def description_and_stdout(self):
        raise NotImplementedError()


class SuccessWithoutValue(ExecutionResultSuccess):
    """The code executed successfully, and did not produce a value."""
    def __init__(self, stdout):
        self.stdout = stdout # str


class SuccessWithValue(ExecutionResultSuccess):
    """The code executed successfully, and produced a value."""
    def __init__(self, stdout, result):
        self.stdout = stdout # str
        self.result = result # SBValue


class PreprocessorError(ExecutionResultError):
    """There was an error preprocessing the code."""
    def __init__(self, exception):
        self.exception = exception # PreprocessorException

    def description(self):
        return str(self.exception)

    def description_and_stdout(self):
        return self.description()


class PreprocessorException(Exception):
    pass


class SwiftError(ExecutionResultError):
    """There was a compile or runtime error."""
    def __init__(self, stdout, result):
        self.stdout = stdout # str
        self.result = result # SBValue

    def description(self):
        return self.result.error.description

    def description_and_stdout(self):
        return 'message:\n%s\n\nstdout:\n%s' % (self.result.error.description,
                                                self.stdout)


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
        self._init_kernel_communicator()

    def _init_repl_process(self):
        self.debugger = lldb.SBDebugger.Create()
        if not self.debugger:
            raise Exception('Could not start debugger')
        self.debugger.SetAsync(False)

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

        script_dir = os.path.dirname(os.path.realpath(sys.argv[0]))
        self.process = self.target.LaunchSimple(None,
                                                ['PYTHONPATH=%s' % script_dir],
                                                os.getcwd())
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

    def _init_kernel_communicator(self):
        result = self._preprocess_and_execute(
                '%include "KernelCommunicator.swift"')
        if isinstance(result, ExecutionResultError):
            self.log.error(result.description_and_stdout())

        decl_code = """
            enum JupyterKernel {
                static var communicator = KernelCommunicator(
                    jupyterSession: JupyterSession(id: %s, key: %s,
                                                   username: %s))
            }
        """ % (json.dumps(self.session.session), json.dumps(self.session.key),
               json.dumps(self.session.username))
        result = self._preprocess_and_execute(decl_code)
        if isinstance(result, ExecutionResultError):
            self.log.error(result.description_and_stdout())

    def _preprocess_and_execute(self, code):
        try:
            preprocessed = self._preprocess(code)
        except PreprocessorException as e:
            return PreprocessorError(e)

        return self._execute(preprocessed)

    def _preprocess(self, code):
        lines = code.split('\n')
        preprocessed_lines = [
                self._preprocess_line(i, line) for i, line in enumerate(lines)]
        return '\n'.join(preprocessed_lines)

    def _preprocess_line(self, line_index, line):
        include_match = re.match(r'^\s*%include (.*)$', line)
        if include_match is not None:
            return self._read_include(line_index, include_match.group(1))
        return line

    def _read_include(self, line_index, rest_of_line):
        name_match = re.match(r'^\s*"([^"]+)"\s*', rest_of_line)
        if name_match is None:
            raise PreprocessorException(
                    'Line %d: %%include must be followed by a name in quotes' % (
                            line_index + 1))
        name = name_match.group(1)

        include_paths = [
            os.path.dirname(os.path.realpath(sys.argv[0]))
        ]

        code = None
        for include_path in include_paths:
            try:
                with open(os.path.join(include_path, name), 'r') as f:
                    code = f.read()
            except IOError:
                continue

        if code is None:
            raise PreprocessorException(
                    'Line %d: Could not find "%s". Searched %s.' % (
                            line_index + 1, name, include_paths))

        return '\n'.join([
            '#sourceLocation(file: "%s", line: 1)' % name,
            code,
            '#sourceLocation(file: "<REPL>", line: %d)' % (line_index + 1),
            ''
        ])

    def _execute(self, code):
        result = self.target.EvaluateExpression(
                code.encode('utf8'), self.expr_opts)
        stdout = ''.join([buf for buf in self._get_stdout()])

        if result.error.type == lldb.eErrorTypeInvalid:
            self.completer.record_successful_execution(code)
            return SuccessWithValue(stdout, result)
        elif result.error.type == lldb.eErrorTypeGeneric:
            self.completer.record_successful_execution(code)
            return SuccessWithoutValue(stdout)
        else:
            return SwiftError(stdout, result)

    def _get_stdout(self):
        while True:
            BUFFER_SIZE = 1000
            stdout_buffer = self.process.GetSTDOUT(BUFFER_SIZE)
            if len(stdout_buffer) == 0:
                break
            yield stdout_buffer

    def _after_successful_execution(self):
        result = self._execute(
                'JupyterKernel.communicator.triggerAfterSuccessfulExecution()')
        if isinstance(result, ExecutionResultError):
            self.log.error(result.description_and_stdout())
            return
        if isinstance(result, SuccessWithoutValue):
            self.log.error(
                    'Exepcted value from triggerAfterSuccessfulExecution()')
            return

        messages = self._read_jupyter_messages(result.result)
        self._send_jupyter_messages(messages)

    def _read_jupyter_messages(self, sbvalue):
        return {
            'display_messages': [
                self._read_display_message(display_message_sbvalue)
                for display_message_sbvalue
                in sbvalue
            ]
        }

    def _read_display_message(self, sbvalue):
        parts_sbvalue = sbvalue.GetChildMemberWithName('parts')
        return [self._read_byte_array(part) for part in parts_sbvalue]

    def _read_byte_array(self, sbvalue):
        # TODO: Iterating over the bytes in Python is very slow.
        return bytes(bytearray(
                [byte_sbvalue.data.uint8[0] for byte_sbvalue in sbvalue]))

    def _send_jupyter_messages(self, messages):
        for display_message in messages['display_messages']:
            self.iopub_socket.send_multipart(display_message)

    def _set_parent_message(self):
        result = self._execute("""
            JupyterKernel.communicator.updateParentMessage(
                to: ParentMessage(json: %s))
        """ % json.dumps(json.dumps(squash_dates(self._parent_header))))
        if isinstance(result, ExecutionResultError):
            self.log.error(result.description_and_stdout())
            return

    def do_execute(self, code, silent, store_history=True,
                   user_expressions=None, allow_stdin=False):
        self._set_parent_message()

        result = self._preprocess_and_execute(code)

        if isinstance(result, ExecutionResultSuccess):
            self._after_successful_execution()

        # Send stdout to client.
        try:
            self.send_response(self.iopub_socket, 'stream', {
                'name': 'stdout',
                'text': result.stdout
            })
        except AttributeError:
            # Not all results have stdout.
            pass

        # Send values/errors and status to the client.
        if isinstance(result, SuccessWithValue):
            self.send_response(self.iopub_socket, 'execute_result', {
                'execution_count': self.execution_count,
                'data': {
                    'text/plain': result.result.description
                }
            })
            return {
                'status': 'ok',
                'execution_count': self.execution_count,
                'payload': [],
                'user_expressions': {}
            }
        elif isinstance(result, SuccessWithoutValue):
            return {
                'status': 'ok',
                'execution_count': self.execution_count,
                'payload': [],
                'user_expressions': {}
            }
        elif isinstance(result, ExecutionResultError):
            self.send_response(self.iopub_socket, 'error', {
                'execution_count': self.execution_count,
                'ename': '',
                'evalue': '',
                'traceback': [result.description()],
            })
            return {
                'status': 'error',
                'execution_count': self.execution_count,
                'ename': '',
                'evalue': '',
                'traceback': [result.description()],
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
