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


class SuccessWithoutValue(ExecutionResultSuccess):
    """The code executed successfully, and did not produce a value."""
    def __init__(self, stdout):
        self.stdout = stdout # str

    def __repr__(self):
        return 'SuccessWithoutValue(stdout=%s)' % repr(self.stdout)


class SuccessWithValue(ExecutionResultSuccess):
    """The code executed successfully, and produced a value."""
    def __init__(self, stdout, result):
        self.stdout = stdout # str
        self.result = result # SBValue

    def __repr__(self):
        return 'SuccessWithValue(stdout=%s, result=%s, description=%s)' % (
                repr(self.stdout), repr(self.result),
                repr(self.result.description))


class PreprocessorError(ExecutionResultError):
    """There was an error preprocessing the code."""
    def __init__(self, exception):
        self.exception = exception # PreprocessorException

    def description(self):
        return str(self.exception)

    def __repr__(self):
        return 'PreprocessorError(exception=%s)' % repr(self.exception)


class PreprocessorException(Exception):
    pass


class SwiftError(ExecutionResultError):
    """There was a compile or runtime error."""
    def __init__(self, stdout, result):
        self.stdout = stdout # str
        self.result = result # SBValue

    def description(self):
        return self.result.error.description

    def __repr__(self):
        return 'SwiftError(stdout=%s, result=%s, description=%s)' % (
                repr(self.stdout), repr(self.result),
                repr(self.description()))


class SwiftKernel(Kernel):
    implementation = 'SwiftKernel'
    implementation_version = '0.1'
    banner = ''

    language_info = {
        'name': 'swift',
        'mimetype': 'text/x-swift',
        'file_extension': '.swift',
        'version': '',
    }

    def __init__(self, **kwargs):
        super(SwiftKernel, self).__init__(**kwargs)
        self._init_repl_process()
        self._init_completer()
        self._init_kernel_communicator()
        self._init_int_bitwidth()

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

        repl_env = []
        script_dir = os.path.dirname(os.path.realpath(sys.argv[0]))
        repl_env.append('PYTHONPATH=%s' % script_dir)
        env_var_blacklist = [
            'PYTHONPATH',
            'REPL_SWIFT_PATH'
        ]
        for key in os.environ:
            if key in env_var_blacklist:
                continue
            repl_env.append('%s=%s' % (key, os.environ[key]))

        self.process = self.target.LaunchSimple(None,
                                                repl_env,
                                                os.getcwd())
        if not self.process:
            raise Exception('Could not launch process')

        self.expr_opts = lldb.SBExpressionOptions()
        swift_language = lldb.SBLanguageRuntime.GetLanguageTypeFromString(
            'swift')
        self.expr_opts.SetLanguage(swift_language)
        self.expr_opts.SetREPLMode(True)
        self.expr_opts.SetUnwindOnError(False)
        self.expr_opts.SetGenerateDebugInfo(True)

        # Sets an infinite timeout so that users can run aribtrarily long
        # computations.
        self.expr_opts.SetTimeoutInMicroSeconds(0)

        self.main_thread = self.process.GetThreadAtIndex(0)

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
            raise Exception('Error initing KernelCommunicator: %s' % result)

        decl_code = """
            enum JupyterKernel {
                static var communicator = KernelCommunicator(
                    jupyterSession: KernelCommunicator.JupyterSession(
                        id: %s, key: %s, username: %s))
            }
        """ % (json.dumps(self.session.session), json.dumps(self.session.key),
               json.dumps(self.session.username))
        result = self._preprocess_and_execute(decl_code)
        if isinstance(result, ExecutionResultError):
            raise Exception('Error declaring JupyterKernel: %s' % result)

    def _init_int_bitwidth(self):
        result = self._execute('Int.bitWidth')
        if not isinstance(result, SuccessWithValue):
            raise Exception('Expected value from Int.bitWidth, but got: %s' %
                            result)
        self._int_bitwidth = int(result.result.description)

    def _file_name_for_source_location(self):
        return '<Cell %d>' % self.execution_count

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
            os.path.dirname(os.path.realpath(sys.argv[0])),
            os.path.realpath("."),
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
            '#sourceLocation(file: "%s", line: %d)' % (
                self._file_name_for_source_location(), line_index + 1),
            ''
        ])

    def _execute(self, code):
        locationDirective = '#sourceLocation(file: "%s", line: 1)' % (
            self._file_name_for_source_location())
        codeWithLocationDirective = locationDirective + '\n' + code
        result = self.target.EvaluateExpression(
                codeWithLocationDirective.encode('utf8'), self.expr_opts)
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
        if not isinstance(result, SuccessWithValue):
            self.log.error(
                    'Expected value from triggerAfterSuccessfulExecution(), '
                    'but got: %s' % result)
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
        return [self._read_byte_array(part) for part in sbvalue]

    def _read_byte_array(self, sbvalue):
        get_position_error = lldb.SBError()
        position = sbvalue \
                .GetChildMemberWithName('_position') \
                .GetData() \
                .GetAddress(get_position_error, 0)
        if get_position_error.Fail():
            raise Exception('getting position: %s' % str(get_position_error))

        get_count_error = lldb.SBError()
        count_data = sbvalue \
                .GetChildMemberWithName('count') \
                .GetData()
        if self._int_bitwidth == 32:
            count = count_data.GetSignedInt32(get_count_error, 0)
        elif self._int_bitwidth == 64:
            count = count_data.GetSignedInt64(get_count_error, 0)
        else:
            raise Exception('Unsupported integer bitwidth %d' %
                            self._int_bitwidth)
        if get_count_error.Fail():
            raise Exception('getting count: %s' % str(get_count_error))

        # ReadMemory requires that count is positive, so early-return an empty
        # byte array when count is 0.
        if count == 0:
            return bytes()

        get_data_error = lldb.SBError()
        data = self.process.ReadMemory(position, count, get_data_error)
        if get_data_error.Fail():
            raise Exception('getting data: %s' % str(get_data_error))

        return data

    def _send_jupyter_messages(self, messages):
        for display_message in messages['display_messages']:
            self.iopub_socket.send_multipart(display_message)

    def _set_parent_message(self):
        result = self._execute("""
            JupyterKernel.communicator.updateParentMessage(
                to: KernelCommunicator.ParentMessage(json: %s))
        """ % json.dumps(json.dumps(squash_dates(self._parent_header))))
        if isinstance(result, ExecutionResultError):
            raise Exception('Error setting parent message: %s' % result)

    def _get_pretty_main_thread_stack_trace(self):
        stack_trace = []
        for frame in self.main_thread:
            # Do not include frames without source location information. These
            # are frames in libraries and frames that belong to the LLDB
            # expression execution implementation.
            if not frame.line_entry.file:
                continue
            # Do not include <compiler-generated> frames. These are
            # specializations of library functions.
            if frame.line_entry.file.fullpath == '<compiler-generated>':
                continue
            stack_trace.append(str(frame))
        return stack_trace

    def do_execute(self, code, silent, store_history=True,
                   user_expressions=None, allow_stdin=False):
        def make_error_message(traceback):
            return {
                'status': 'error',
                'execution_count': self.execution_count,
                'ename': '',
                'evalue': '',
                'traceback': traceback
            }

        try:
            self._set_parent_message()
        except Exception as e:
            error_message = make_error_message([
                'Kernel is in a bad state. Try restarting the kernel.',
                '',
                'Exception in `_set_parent_message`:',
                str(e)
            ])
            self.send_response(self.iopub_socket, 'error', error_message)
            return error_message

        try:
            result = self._preprocess_and_execute(code)
        except Exception as e:
            error_message = make_error_message([
                'Kernel is in a bad state. Try restarting the kernel.',
                '',
                'Exception in `_preprocess_and_execute`:',
                str(e)
            ])
            self.send_response(self.iopub_socket, 'error', error_message)
            return error_message

        if isinstance(result, ExecutionResultSuccess):
            try:
                self._after_successful_execution()
            except Exception as e:
                error_message = make_error_message([
                    'Kernel is in a bad state. Try restarting the kernel.',
                    '',
                    'Exception in `_after_successful_execution`:',
                    str(e)
                ])
                self.send_response(self.iopub_socket, 'error', error_message)
                return error_message

        # Send stdout, values/errors and status to the client.
        if isinstance(result, SuccessWithValue):
            if hasattr(result, 'stdout') and len(result.stdout) > 0:
                self.send_response(self.iopub_socket, 'stream', {
                    'name': 'stdout',
                    'text': result.stdout
                })
            self.send_response(self.iopub_socket, 'execute_result', {
                'execution_count': self.execution_count,
                'data': {
                    'text/plain': result.result.description
                },
                'metadata': {}
            })
            return {
                'status': 'ok',
                'execution_count': self.execution_count,
                'payload': [],
                'user_expressions': {}
            }
        elif isinstance(result, SuccessWithoutValue):
            if hasattr(result, 'stdout') and len(result.stdout) > 0:
                self.send_response(self.iopub_socket, 'stream', {
                    'name': 'stdout',
                    'text': result.stdout
                })
            return {
                'status': 'ok',
                'execution_count': self.execution_count,
                'payload': [],
                'user_expressions': {}
            }
        elif isinstance(result, ExecutionResultError):
            if hasattr(result, 'stdout') and len(result.stdout) > 0:
                # When there is stdout, it is a runtime error. Therefore,
                # parse the stdout to get the error message and query the LLDB
                # APIs for the stack trace.
                #
                # Note that the stdout-parsing logic assumes that the stdout
                # for a runtime error always has this form:
                #
                #   <execution stdout from before the error happened>
                #   <error message>
                #   Current stack trace:
                #     <a stack trace>
                #
                # It would be nicer if we could get the runtime error message
                # from somewhere other than stdout, so that we don't need
                # fragile text processing to parse out the part of the error
                # message that we are interested in.
                traceback = []

                # First, put the error message in the traceback. The error
                # message includes a useless stack trace (it doesn't have
                # source line info), so do not include the useles stack trace.
                for line in result.stdout.split('\n'):
                    if line.startswith('Current stack trace'):
                        break
                    traceback.append(line)

                # Next, put a useful stack trace with source line info in the
                # traceback.
                traceback.append('Current stack trace:')
                traceback += [
                    '\t%s' % frame
                    for frame in self._get_pretty_main_thread_stack_trace()
                ]

                error_message = make_error_message(traceback)
                self.send_response(self.iopub_socket, 'error', error_message)
                return error_message

            # There is no stdout, so it must be a compile error. Simply return
            # the error without trying to get a stack trace.
            error_message = make_error_message([result.description()])
            self.send_response(self.iopub_socket, 'error', error_message)
            return error_message

    def do_complete(self, code, cursor_pos):
        completions = self.completer.complete(code, cursor_pos)
        return {
            'matches': [completion['sourcetext'] for completion in completions],
            'cursor_start': cursor_pos,
            'cursor_end': cursor_pos,
        }

if __name__ == '__main__':
    from ipykernel.kernelapp import IPKernelApp
    # We pass the kernel name as a command-line arg, since Jupyter gives those
    # highest priority (in particular overriding any system-wide config).
    IPKernelApp.launch_instance(
        argv=sys.argv + ['--IPKernelApp.kernel_class=__main__.SwiftKernel'])
