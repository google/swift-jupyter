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

import glob
import json
import lldb
import os
import stat
import re
import shlex
import shutil
import signal
import string
import subprocess
import sys
import tempfile
import textwrap
import time
import threading

from ipykernel.kernelbase import Kernel
from jupyter_client.jsonutil import squash_dates
from tornado import ioloop


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
    def __repr__(self):
        return 'SuccessWithoutValue()'


class SuccessWithValue(ExecutionResultSuccess):
    """The code executed successfully, and produced a value."""
    def __init__(self, result):
        self.result = result # SBValue

    def __repr__(self):
        return 'SuccessWithValue(result=%s, description=%s)' % (
            repr(self.result), repr(self.result.description))


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


class PackageInstallException(Exception):
    pass


class SwiftError(ExecutionResultError):
    """There was a compile or runtime error."""
    def __init__(self, result):
        self.result = result # SBValue

    def description(self):
        return self.result.error.description

    def __repr__(self):
        return 'SwiftError(result=%s, description=%s)' % (
            repr(self.result), repr(self.description()))


class SIGINTHandler(threading.Thread):
    """Interrupts currently-executing code whenever the process receives a
       SIGINT."""

    daemon = True

    def __init__(self, kernel):
        super(SIGINTHandler, self).__init__()
        self.kernel = kernel

    def run(self):
        try:
            while True:
                signal.sigwait([signal.SIGINT])
                self.kernel.process.SendAsyncInterrupt()
        except Exception as e:
            self.kernel.log.error('Exception in SIGINTHandler: %s' % str(e))


class StdoutHandler(threading.Thread):
    """Collects stdout from the Swift process and sends it to the client."""

    daemon = True

    def __init__(self, kernel):
        super(StdoutHandler, self).__init__()
        self.kernel = kernel
        self.stop_event = threading.Event()
        self.had_stdout = False

    def _get_stdout(self):
        while True:
            BUFFER_SIZE = 1000
            stdout_buffer = self.kernel.process.GetSTDOUT(BUFFER_SIZE)
            if len(stdout_buffer) == 0:
                break
            yield stdout_buffer

    def _get_and_send_stdout(self):
        stdout = ''.join([buf for buf in self._get_stdout()])
        if len(stdout) > 0:
            self.had_stdout = True
            self.kernel.send_response(self.kernel.iopub_socket, 'stream', {
                'name': 'stdout',
                'text': stdout
            })

    def run(self):
        try:
            while True:
                if self.stop_event.wait(0.1):
                    break
                self._get_and_send_stdout()
            self._get_and_send_stdout()
        except Exception as e:
            self.kernel.log.error('Exception in StdoutHandler: %s' % str(e))


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

        # We don't initialize Swift yet, so that the user has a chance to
        # "%install" packages before Swift starts. (See doc comment in
        # `_init_swift`).

        # Whether to do code completion. Since the debugger is not yet
        # initialized, we can't do code completion yet.
        self.completion_enabled = False

    def _init_swift(self):
        """Initializes Swift so that it's ready to start executing user code.

        This must happen after package installation, because the ClangImporter
        does not see modulemap files that appear after it has started."""

        self.send_response(self.iopub_socket, 'stream', {
            'name': 'stdout',
            'text': 'Initializing Swift...\n'
        })

        self._init_repl_process()
        self._init_kernel_communicator()
        self._init_int_bitwidth()
        self._init_sigint_handler()
        self._init_installed_packages()

        # We do completion by default when the toolchain has the
        # SBTarget.CompleteCode API.
        # The user can disable/enable using "%disableCompletion" and
        # "%enableCompletion".
        self.completion_enabled = hasattr(self.target, 'CompleteCode')
        
        self.send_response(self.iopub_socket, 'stream', {
            'name': 'stdout',
            'text': 'Swift initialization complete!\n'
        })


    def _init_installed_packages(self):
        # == dlopen the shared lib prepared during package install ==

        swift_import_search_path = os.environ.get('SWIFT_IMPORT_SEARCH_PATH')
        if swift_import_search_path is None:
            return
        lib_filename = os.path.join(swift_import_search_path, 'libjupyterInstalledPackages.so')
        if not os.path.isfile(lib_filename):
            return

        self.send_response(self.iopub_socket, 'stream', {
            'name': 'stdout',
            'text': 'Loading library...\n'
        })
        dynamic_load_code = textwrap.dedent("""\
            import func Glibc.dlopen
            dlopen(%s, RTLD_NOW)
        """ % json.dumps(lib_filename))
        dynamic_load_result = self._execute(dynamic_load_code)
        if not isinstance(dynamic_load_result, SuccessWithValue):
            raise PackageInstallException(
                    'Install Error: dlopen error: %s' % \
                            str(dynamic_load_result))
        if dynamic_load_result.result.description.strip() == 'nil':
            raise PackageInstallException('Install Error: dlopen error. Run '
                                        '`String(cString: dlerror())` to see '
                                        'the error message.')

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
        self.swift_language = lldb.SBLanguageRuntime.GetLanguageTypeFromString(
            'swift')
        self.expr_opts.SetLanguage(self.swift_language)
        self.expr_opts.SetREPLMode(True)
        self.expr_opts.SetUnwindOnError(False)
        self.expr_opts.SetGenerateDebugInfo(True)

        # Sets an infinite timeout so that users can run aribtrarily long
        # computations.
        self.expr_opts.SetTimeoutInMicroSeconds(0)

        self.main_thread = self.process.GetThreadAtIndex(0)

    def _init_kernel_communicator(self):
        result = self._preprocess_and_execute(
                '%include "KernelCommunicator.swift"')
        if isinstance(result, ExecutionResultError):
            raise Exception('Error initing KernelCommunicator: %s' % result)

        session_key = self.session.key.decode('utf8')
        decl_code = """
            enum JupyterKernel {
                static var communicator = KernelCommunicator(
                    jupyterSession: KernelCommunicator.JupyterSession(
                        id: %s, key: %s, username: %s))
            }
        """ % (json.dumps(self.session.session), json.dumps(session_key),
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

    def _init_sigint_handler(self):
        self.sigint_handler = SIGINTHandler(self)
        self.sigint_handler.start()

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

    def _handle_disable_completion(self):
        self.completion_enabled = False
        self.send_response(self.iopub_socket, 'stream', {
            'name': 'stdout',
            'text': 'Completion disabled!\n'
        })

    def _handle_enable_completion(self):
        if not hasattr(self.target, 'CompleteCode'):
            self.send_response(self.iopub_socket, 'stream', {
                'name': 'stdout',
                'text': 'Completion NOT enabled because toolchain does not ' +
                        'have CompleteCode API.\n'
            })
            return

        self.completion_enabled = True
        self.send_response(self.iopub_socket, 'stream', {
            'name': 'stdout',
            'text': 'Completion enabled!\n'
        })

    def _preprocess_line(self, line_index, line):
        """Returns the preprocessed line.

        Does not process "%install" directives, because those need to be
        handled before everything else."""

        include_match = re.match(r'^\s*%include (.*)$', line)
        if include_match is not None:
            return self._read_include(line_index, include_match.group(1))

        disable_completion_match = re.match(r'^\s*%disableCompletion\s*$', line)
        if disable_completion_match is not None:
            self._handle_disable_completion()
            return ''

        enable_completion_match = re.match(r'^\s*%enableCompletion\s*$', line)
        if enable_completion_match is not None:
            self._handle_enable_completion()
            return ''

        return line

    def _read_include(self, line_index, rest_of_line):
        name_match = re.match(r'^\s*"([^"]+)"\s*$', rest_of_line)
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

    def _process_installs(self, code):
        """Handles all "%install" directives, and returns `code` with all
        "%install" directives removed."""
        processed_lines = []
        all_packages = []
        all_swiftpm_flags = []
        user_install_location = None
        for index, line in enumerate(code.split('\n')):
            line, install_location = self._process_install_location_line(line)
            line, swiftpm_flags = self._process_install_swiftpm_flags_line(
                    line)
            all_swiftpm_flags += swiftpm_flags
            line, packages = self._process_install_line(index, line)
            processed_lines.append(line)
            all_packages += packages
            if install_location: user_install_location = install_location

        self._prepare_install_location(user_install_location)
        self._install_packages(all_packages, all_swiftpm_flags)
        return '\n'.join(processed_lines)

    def _process_install_location_line(self, line):
        install_location_match = re.match(
                r'^\s*%install-location (.*)$', line)
        if install_location_match is None:
            return line, None

        install_location = install_location_match.group(1)
        try:
            install_location = string.Template(install_location).substitute({"cwd": os.getcwd()})
        except KeyError as e:
            raise PackageInstallException(
                    'Line %d: Invalid template argument %s' % (line_index + 1,
                                                               str(e)))
        except ValueError as e:
            raise PackageInstallException(
                    'Line %d: %s' % (line_index + 1, str(e)))

        return '', install_location

    def _process_install_swiftpm_flags_line(self, line):
        install_swiftpm_flags_match = re.match(
                r'^\s*%install-swiftpm-flags (.*)$', line)
        if install_swiftpm_flags_match is None:
            return line, []
        flags = shlex.split(install_swiftpm_flags_match.group(1))
        return '', flags

    def _process_install_line(self, line_index, line):
        install_match = re.match(r'^\s*%install (.*)$', line)
        if install_match is None:
            return line, []

        parsed = shlex.split(install_match.group(1))
        if len(parsed) < 2:
            raise PackageInstallException(
                    'Line %d: %%install usage: SPEC PRODUCT [PRODUCT ...]' % (
                            line_index + 1))
        try:
            spec = string.Template(parsed[0]).substitute({"cwd": os.getcwd()})
        except KeyError as e:
            raise PackageInstallException(
                    'Line %d: Invalid template argument %s' % (line_index + 1,
                                                               str(e)))
        except ValueError as e:
            raise PackageInstallException(
                    'Line %d: %s' % (line_index + 1, str(e)))

        return '', [{
            'spec': spec,
            'products': parsed[1:],
        }]

    def _prepare_install_location(self, install_location):
        swift_import_search_path = os.environ.get('SWIFT_IMPORT_SEARCH_PATH')
        if swift_import_search_path is None:
            raise PackageInstallException(
                    'Install Error: Cannot install packages because '
                    'SWIFT_IMPORT_SEARCH_PATH is not specified.')

        if install_location is None:
            os.makedirs(swift_import_search_path, exist_ok=True)
            return

        os.makedirs(install_location, exist_ok=True)

        # symlink 'modules' to the user-specified location
        # Remove existing 'modules' if it is already a symlink
        try:
            if stat.S_ISLNK(os.lstat(swift_import_search_path).st_mode):
                os.unlink(swift_import_search_path)
        except FileNotFoundError as e:
            pass
        except Error as e:
            raise PackageInstallException(
                    'Line %d: %s' % (line_index + 1, str(e)))

        os.symlink(install_location, swift_import_search_path, target_is_directory=True)

    def _install_packages(self, packages, swiftpm_flags):
        if len(packages) == 0 and len(swiftpm_flags) == 0:
            return

        if hasattr(self, 'debugger'):
            raise PackageInstallException(
                    'Install Error: Packages can only be installed during the '
                    'first cell execution. Restart the kernel to install '
                    'packages.')

        swift_build_path = os.environ.get('SWIFT_BUILD_PATH')
        if swift_build_path is None:
            raise PackageInstallException(
                    'Install Error: Cannot install packages because '
                    'SWIFT_BUILD_PATH is not specified.')

        swift_import_search_path = os.environ.get('SWIFT_IMPORT_SEARCH_PATH')
        if swift_import_search_path is None:
            raise PackageInstallException(
                    'Install Error: Cannot install packages because '
                    'SWIFT_IMPORT_SEARCH_PATH is not specified.')

        install_lock_file = os.path.join(swift_import_search_path, 'install_lock')
        if os.path.exists(install_lock_file):
            raise PackageInstallException(
                    'Install Error: Cannot install packages. '
                    'Found lock file in install location. '
                    'Please, wait for other installations to finish.')

        with open(install_lock_file, 'w'): pass


        scratchwork_base_path = os.path.dirname(swift_import_search_path)
        package_base_path = os.path.join(scratchwork_base_path, 'package')
        os.makedirs(package_base_path, exist_ok=True)

        # Summary of how this works:
        # - create a SwiftPM package that depends on all the packages that
        #   the user requested
        # - ask SwiftPM to build that package
        # - copy all the .swiftmodule and module.modulemap files that SwiftPM
        #   created to SWIFT_IMPORT_SEARCH_PATH
        # - Prepare .so file created by SwiftPM to dlopen in _init_swift

        # == Create the SwiftPM package ==

        package_swift_template = textwrap.dedent("""\
            // swift-tools-version:4.2
            import PackageDescription
            let package = Package(
                name: "jupyterInstalledPackages",
                products: [
                    .library(
                        name: "jupyterInstalledPackages",
                        type: .dynamic,
                        targets: ["jupyterInstalledPackages"]),
                ],
                dependencies: [%s],
                targets: [
                    .target(
                        name: "jupyterInstalledPackages",
                        dependencies: [%s],
                        path: ".",
                        sources: ["jupyterInstalledPackages.swift"]),
                ])
        """)

        packages_specs = ''
        packages_products = ''
        packages_human_description = ''
        for package in packages:
            packages_specs += '%s,\n' % package['spec']
            packages_human_description += '\t%s\n' % package['spec']
            for target in package['products']:
                packages_products += '%s,\n' % json.dumps(target)
                packages_human_description += '\t\t%s\n' % target

        self.send_response(self.iopub_socket, 'stream', {
            'name': 'stdout',
            'text': 'Installing packages:\n%s' % packages_human_description
        })
        self.send_response(self.iopub_socket, 'stream', {
            'name': 'stdout',
            'text': 'With SwiftPM flags: %s\n' % str(swiftpm_flags)
        })
        self.send_response(self.iopub_socket, 'stream', {
            'name': 'stdout',
            'text': 'Working in: %s\n' % scratchwork_base_path
        })

        package_swift = package_swift_template % (packages_specs,
                                                  packages_products)

        with open('%s/Package.swift' % package_base_path, 'w') as f:
            f.write(package_swift)
        with open('%s/jupyterInstalledPackages.swift' % package_base_path, 'w') as f:
            f.write("// intentionally blank\n")

        # == Ask SwiftPM to build the package ==

        build_p = subprocess.Popen([swift_build_path] + swiftpm_flags,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,
                                   cwd=package_base_path)
        for build_output_line in iter(build_p.stdout.readline, b''):
            self.send_response(self.iopub_socket, 'stream', {
                'name': 'stdout',
                'text': build_output_line.decode('utf8')
            })
        build_returncode = build_p.wait()
        if build_returncode != 0:
            raise PackageInstallException(
                    'Install Error: swift-build returned nonzero exit code '
                    '%d.' % build_returncode)

        show_bin_path_result = subprocess.run(
                [swift_build_path, '--show-bin-path'] + swiftpm_flags,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=package_base_path)
        bin_dir = show_bin_path_result.stdout.decode('utf8').strip()
        lib_filename = os.path.join(bin_dir, 'libjupyterInstalledPackages.so')

        # == Copy .swiftmodule and modulemap files to SWIFT_IMPORT_SEARCH_PATH ==

        for filename in glob.glob(os.path.join(bin_dir, '*.swiftmodule')):
            shutil.copy(filename, swift_import_search_path)

        # The modulemap files appear in a few different places. Add all of
        # them.
        # TODO: Figure out if there is a principled way to figure out where
        # all the modulemap files are.
        modulemap_files = glob.glob(
                os.path.join(bin_dir, '**/module.modulemap'), recursive=True)
        modulemap_files += glob.glob(
                os.path.join(package_base_path,
                             '.build/checkouts/**/module.modulemap'),
                recursive=True)
        for index, filename in enumerate(modulemap_files):
            # Create a separate directory for each modulemap file because the
            # ClangImporter requires that they are all named
            # "module.modulemap".
            modulemap_dest = os.path.join(swift_import_search_path,
                                          'modulemap-%d' % index)
            os.makedirs(modulemap_dest, exist_ok=True)
            shutil.copy(filename, modulemap_dest)

        # Copy .so file too, so it can be loaded even if no packages were compiled
        shutil.copy(lib_filename, swift_import_search_path)

        # Remove lock file
        os.unlink(install_lock_file)

        self.already_installed_packages = True

    def _execute(self, code):
        locationDirective = '#sourceLocation(file: "%s", line: 1)' % (
            self._file_name_for_source_location())
        codeWithLocationDirective = locationDirective + '\n' + code
        result = self.target.EvaluateExpression(
                codeWithLocationDirective, self.expr_opts)

        if result.error.type == lldb.eErrorTypeInvalid:
            return SuccessWithValue(result)
        elif result.error.type == lldb.eErrorTypeGeneric:
            return SuccessWithoutValue()
        else:
            return SwiftError(result)

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

    def _make_error_message(self, traceback):
        return {
            'status': 'error',
            'execution_count': self.execution_count,
            'ename': '',
            'evalue': '',
            'traceback': traceback
        }

    def _send_exception_report(self, while_doing, e):
        error_message = self._make_error_message([
            'Kernel is in a bad state. Try restarting the kernel.',
            '',
            'Exception in `%s`:' % while_doing,
            str(e)
        ])
        self.send_response(self.iopub_socket, 'error', error_message)
        return error_message

    def _execute_cell(self, code):
        self._set_parent_message()
        result = self._preprocess_and_execute(code)
        if isinstance(result, ExecutionResultSuccess):
            self._after_successful_execution()
        return result

    def do_execute(self, code, silent, store_history=True,
                   user_expressions=None, allow_stdin=False):
        # Package installs must be done before initializing Swift (see doc
        # comment in `_init_swift`).
        try:
            code = self._process_installs(code)
        except PackageInstallException as e:
            error_message = self._make_error_message([str(e)])
            self.send_response(self.iopub_socket, 'error', error_message)
            return error_message
        except Exception as e:
            self._send_exception_report('_process_installs', e)
            raise e

        # Return early if the code is empty or whitespace, to avoid
        # initializing Swift and preventing package installs.
        if len(code) == 0 or code.isspace():
            return {
                'status': 'ok',
                'execution_count': self.execution_count,
                'payload': [],
                'user_expressions': {}
            }

        if not hasattr(self, 'debugger'):
            self._init_swift()

        # Start up a new thread to collect stdout.
        stdout_handler = StdoutHandler(self)
        stdout_handler.start()

        # Execute the cell, handle unexpected exceptions, and make sure to
        # always clean up the stdout handler.
        try:
            result = self._execute_cell(code)
        except Exception as e:
            self._send_exception_report('_execute_cell', e)
            raise e
        finally:
            stdout_handler.stop_event.set()
            stdout_handler.join()

        # Send values/errors and status to the client.
        if isinstance(result, SuccessWithValue):
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
            return {
                'status': 'ok',
                'execution_count': self.execution_count,
                'payload': [],
                'user_expressions': {}
            }
        elif isinstance(result, ExecutionResultError):
            if not self.process.is_alive:
                error_message = self._make_error_message(['Process killed'])
                self.send_response(self.iopub_socket, 'error', error_message)

                # Exit the kernel because there is no way to recover from a
                # killed process. The UI will tell the user that the kernel has
                # died and the UI will automatically restart the kernel.
                # We do the exit in a callback so that this execute request can
                # cleanly finish before the kernel exits.
                loop = ioloop.IOLoop.current()
                loop.add_timeout(time.time()+0.1, loop.stop)

                return error_message

            if stdout_handler.had_stdout:
                # When there is stdout, it is a runtime error. Stdout, which we
                # have already sent to the client, contains the error message
                # (plus some other ugly traceback that we should eventually
                # figure out how to suppress), so this block of code only needs
                # to add a traceback.
                traceback = []
                traceback.append('Current stack trace:')
                traceback += [
                    '\t%s' % frame
                    for frame in self._get_pretty_main_thread_stack_trace()
                ]

                error_message = self._make_error_message(traceback)
                self.send_response(self.iopub_socket, 'error', error_message)
                return error_message

            # There is no stdout, so it must be a compile error. Simply return
            # the error without trying to get a stack trace.
            error_message = self._make_error_message([result.description()])
            self.send_response(self.iopub_socket, 'error', error_message)
            return error_message

    def do_complete(self, code, cursor_pos):
        if not self.completion_enabled:
            return {
                'status': 'ok',
                'matches': [],
                'cursor_start': cursor_pos,
                'cursor_end': cursor_pos,
            }

        code_to_cursor = code[:cursor_pos]
        sbresponse = self.target.CompleteCode(
            self.swift_language, None, code_to_cursor)
        prefix = sbresponse.GetPrefix()
        insertable_matches = []
        for i in range(sbresponse.GetNumMatches()):
            sbmatch = sbresponse.GetMatchAtIndex(i)
            insertable_match = prefix + sbmatch.GetInsertable()
            if insertable_match.startswith("_"):
                continue
            insertable_matches.append(insertable_match)
        return {
            'status': 'ok',
            'matches': insertable_matches,
            'cursor_start': cursor_pos - len(prefix),
            'cursor_end': cursor_pos,
        }

if __name__ == '__main__':
    # Jupyter sends us SIGINT when the user requests execution interruption.
    # Here, we block all threads from receiving the SIGINT, so that we can
    # handle it in a specific handler thread.
    signal.pthread_sigmask(signal.SIG_BLOCK, [signal.SIGINT])

    from ipykernel.kernelapp import IPKernelApp
    # We pass the kernel name as a command-line arg, since Jupyter gives those
    # highest priority (in particular overriding any system-wide config).
    IPKernelApp.launch_instance(
        argv=sys.argv + ['--IPKernelApp.kernel_class=__main__.SwiftKernel'])
