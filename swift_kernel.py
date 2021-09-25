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
import sqlite3
import json
import random

from hashlib import sha256
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

    """A description of the value, e.g.
         (Int) $R0 = 64"""
    def value_description(self):
        stream = lldb.SBStream()
        self.result.GetDescription(stream)
        return stream.GetData()

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

    # Sends stdout to the jupyter client, replacing the ANSI sequence for
    # clearing the whole display with a 'clear_output' message to the jupyter
    # client.
    def _send_stdout(self, stdout):
        clear_sequence = '\033[2J'
        clear_sequence_index = stdout.find(clear_sequence)
        if clear_sequence_index != -1:
            self._send_stdout(stdout[:clear_sequence_index])
            self.kernel.send_response(
                self.kernel.iopub_socket, 'clear_output', {'wait': False})
            self._send_stdout(
                stdout[clear_sequence_index + len(clear_sequence):])
        else:
            self.kernel.send_response(self.kernel.iopub_socket, 'stream', {
                'name': 'stdout',
                'text': stdout
            })

    def _get_and_send_stdout(self):
        stdout = ''.join([buf for buf in self._get_stdout()])
        if len(stdout) > 0:
            self.had_stdout = True
            self._send_stdout(stdout)

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

        self._init_repl_process()
        self._init_kernel_communicator()
        self._init_int_bitwidth()
        self._init_sigint_handler()

        # We do completion by default when the toolchain has the
        # SBTarget.CompleteCode API.
        # The user can disable/enable using "%disableCompletion" and
        # "%enableCompletion".
        self.completion_enabled = hasattr(self.target, 'CompleteCode')

    def _init_repl_process(self):
        self.debugger = lldb.SBDebugger.Create()
        if not self.debugger:
            raise Exception('Could not start debugger')
        self.debugger.SetAsync(False)

        if hasattr(self, 'swift_module_search_paths'):
            for swift_module_search_path in self.swift_module_search_paths:
                self.debugger.HandleCommand("settings append target.swift-module-search-paths " + swift_module_search_path)


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

        # Turn off "disable ASLR" because it uses the "personality" syscall in
        # a way that is forbidden by the default Docker security policy.
        launch_info = self.target.GetLaunchInfo()
        launch_flags = launch_info.GetLaunchFlags()
        launch_info.SetLaunchFlags(launch_flags & ~lldb.eLaunchFlagDisableASLR)
        self.target.SetLaunchInfo(launch_info)

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
        self._int_bitwidth = int(result.result.GetData().GetSignedInt32(lldb.SBError(), 0))

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
        extra_include_commands = []
        all_bazel_deps = []
        user_install_location = None
        for index, line in enumerate(code.split('\n')):
            line = self._process_system_command_line(line)
            line, install_location = self._process_install_location_line(line)
            line, swiftpm_flags = self._process_install_swiftpm_flags_line(
                    line)
            all_swiftpm_flags += swiftpm_flags
            line, packages = self._process_install_line(index, line)
            line, bazel_deps = self._process_bazel_line(index, line)
            line, extra_include_command = \
                self._process_extra_include_command_line(line)
            if extra_include_command:
                extra_include_commands.append(extra_include_command)
            processed_lines.append(line)
            all_packages += packages
            all_bazel_deps += bazel_deps
            if install_location: user_install_location = install_location

        if len(all_packages) > 0 or len(all_swiftpm_flags) > 0:
            if len(all_bazel_deps) > 0:
                raise PackageInstallException(
                        'Cannot install SwiftPM packages at the same time with Bazel packages.')
            self._install_packages(all_packages, all_swiftpm_flags,
                                   extra_include_commands,
                                   user_install_location)
        elif len(all_bazel_deps) > 0:
            if len(extra_include_commands) > 0:
                raise PackageInstallException(
                        'Cannot specify extra include commands %s for Bazel packages.' % extra_include_commands)
            if user_install_location is not None:
                raise PackageInstallException(
                        'Cannot specify user install location %s for Bazel packages.' % user_install_location)
            self._install_bazel_deps(all_bazel_deps)
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

    def _process_extra_include_command_line(self, line):
        extra_include_command_match = re.match(
                r'^\s*%install-extra-include-command (.*)$', line)
        if extra_include_command_match is None:
            return line, None

        extra_include_command = extra_include_command_match.group(1)

        return '', extra_include_command

    def _process_install_swiftpm_flags_line(self, line):
        install_swiftpm_flags_match = re.match(
                r'^\s*%install-swiftpm-flags (.*)$', line)
        if install_swiftpm_flags_match is None:
            return line, []
        flags = shlex.split(install_swiftpm_flags_match.group(1))
        return '', flags

    def _process_bazel_line(self, line_index, line):
        bazel_match = re.match(r'^\s*%bazel (.*)$', line)
        if bazel_match is None:
            return line, []
        path = shlex.split(bazel_match.group(1))
        return '', path

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

    def _process_system_command_line(self, line):                  
        system_match = re.match(r'^\s*%system (.*)$', line)
        if system_match is None:
            return line

        if hasattr(self, 'debugger'):
            raise PackageInstallException(
                    'System commands can only run in the first cell.')

        rest_of_line = system_match.group(1)
        process = subprocess.Popen(rest_of_line,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            shell=True)
        process.wait()
        command_result = process.stdout.read().decode('utf-8')
        self.send_response(self.iopub_socket, 'stream', {
            'name': 'stdout',
            'text': '%s' % command_result
        })
        return ''

    def _link_extra_includes(self, swift_module_search_path, include_dir):
        for include_file in os.listdir(include_dir):
            link_name = os.path.join(swift_module_search_path, include_file)
            target = os.path.join(include_dir, include_file)
            try:
                if stat.S_ISLNK(os.lstat(link_name).st_mode):
                    os.unlink(link_name)
            except FileNotFoundError as e:
                pass
            except Error as e:
                raise PackageInstallException(
                        'Failed to stat scratchwork base path: %s' % str(e))
            os.symlink(target, link_name)

    def _dlopen_shared_lib(self, lib_filename):
        dynamic_load_code = textwrap.dedent("""\
            import func Glibc.dlopen
            import var Glibc.RTLD_NOW
            dlopen(%s, RTLD_NOW)
        """ % json.dumps(lib_filename))
        dynamic_load_result = self._execute(dynamic_load_code)
        if not isinstance(dynamic_load_result, SuccessWithValue):
            raise PackageInstallException(
                    'Install Error: dlopen error: %s' % \
                            str(dynamic_load_result))
        if dynamic_load_result.value_description().endswith('nil'):
            raise PackageInstallException('Install Error: dlopen error. Run '
                                        '`String(cString: dlerror())` to see '
                                        'the error message.')

    def _install_packages(self, packages, swiftpm_flags, extra_include_commands,
                          user_install_location):
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

        swift_package_path = os.environ.get('SWIFT_PACKAGE_PATH')
        if swift_package_path is None:
            raise PackageInstallException(
                    'Install Error: Cannot install packages because '
                    'SWIFT_PACKAGE_PATH is not specified.')

        package_install_scratchwork_base = tempfile.mkdtemp()
        package_install_scratchwork_base = os.path.join(package_install_scratchwork_base, 'swift-install')
        swift_module_search_path = os.path.join(package_install_scratchwork_base,
                                            'modules')
        self.swift_module_search_paths = [swift_module_search_path]

        scratchwork_base_path = os.path.dirname(swift_module_search_path)
        package_base_path = os.path.join(scratchwork_base_path, 'package')

        # If the user has specified a custom install location, make a link from
        # the scratchwork base path to it.
        if user_install_location is not None:
            # symlink to the specified location
            # Remove existing base if it is already a symlink
            os.makedirs(user_install_location, exist_ok=True)
            try:
                if stat.S_ISLNK(os.lstat(scratchwork_base_path).st_mode):
                    os.unlink(scratchwork_base_path)
            except FileNotFoundError as e:
                pass
            except Error as e:
                raise PackageInstallException(
                        'Failed to stat scratchwork base path: %s' % str(e))
            os.symlink(user_install_location, scratchwork_base_path,
                       target_is_directory=True)

        # Make the directory containing our synthesized package.
        os.makedirs(package_base_path, exist_ok=True)

        # Make the directory containing our built modules and other includes.
        os.makedirs(swift_module_search_path, exist_ok=True)

        # Make links from the install location to extra includes.
        for include_command in extra_include_commands:
            result = subprocess.run(include_command, shell=True,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            if result.returncode != 0:
                raise PackageInstallException(
                        '%%install-extra-include-command returned nonzero '
                        'exit code: %d\nStdout:\n%s\nStderr:\n%s\n' % (
                                result.returncode,
                                result.stdout.decode('utf8'),
                                result.stderr.decode('utf8')))
            include_dirs = shlex.split(result.stdout.decode('utf8'))
            for include_dir in include_dirs:
                if include_dir[0:2] != '-I':
                    self.log.warn(
                            'Non "-I" output from '
                            '%%install-extra-include-command: %s' % include_dir)
                    continue
                include_dir = include_dir[2:]
                self._link_extra_includes(swift_module_search_path, include_dir)

        # Summary of how this works:
        # - create a SwiftPM package that depends on all the packages that
        #   the user requested
        # - ask SwiftPM to build that package
        # - copy all the .swiftmodule and module.modulemap files that SwiftPM
        #   created to SWIFT_IMPORT_SEARCH_PATH
        # - dlopen the .so file that SwiftPM created

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

        # TODO(TF-1179): Remove this workaround after fixing SwiftPM.
        swiftpm_env = os.environ
        libuuid_path = '/lib/x86_64-linux-gnu/libuuid.so.1'
        if os.path.isfile(libuuid_path):
            swiftpm_env['LD_PRELOAD'] = libuuid_path

        build_p = subprocess.Popen([swift_build_path] + swiftpm_flags,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,
                                   cwd=package_base_path,
                                   env=swiftpm_env)
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
                cwd=package_base_path,
                env=swiftpm_env)
        bin_dir = show_bin_path_result.stdout.decode('utf8').strip()
        lib_filename = os.path.join(bin_dir, 'libjupyterInstalledPackages.so')

        # == Copy .swiftmodule and modulemap files to SWIFT_IMPORT_SEARCH_PATH ==

        # Search for build.db.
        build_db_candidates = [
            os.path.join(bin_dir, '..', 'build.db'),
            os.path.join(package_base_path, '.build', 'build.db'),
        ]
        build_db_file = next(filter(os.path.exists, build_db_candidates), None)
        if build_db_file is None:
            raise PackageInstallException('build.db is missing')

        # Execute swift-package show-dependencies to get all dependencies' paths
        dependencies_result = subprocess.run(
            [swift_package_path, 'show-dependencies', '--format', 'json'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=package_base_path,
            env=swiftpm_env)
        dependencies_json = dependencies_result.stdout.decode('utf8')
        dependencies_obj = json.loads(dependencies_json)

        def flatten_deps_paths(dep):
            paths = []
            paths.append(dep["path"])
            if dep["dependencies"]:
                for d in dep["dependencies"]:
                    paths.extend(flatten_deps_paths(d))
            return paths

        # Make list of paths where we expect .swiftmodule and .modulemap files of dependencies
        dependencies_paths = [package_base_path]
        dependencies_paths = flatten_deps_paths(dependencies_obj)
        dependencies_paths = list(set(dependencies_paths))

        def is_valid_dependency(path):
            for p in dependencies_paths:
                if path.startswith(p): return True
            return False

        # Query to get build files list from build.db
        # SUBSTR because string starts with "N" (why?)
        SQL_FILES_SELECT = "SELECT SUBSTR(key, 2) FROM 'key_names' WHERE key LIKE ?"

        # Connect to build.db
        db_connection = sqlite3.connect(build_db_file)
        cursor = db_connection.cursor()

        # Process *.swiftmodules files
        cursor.execute(SQL_FILES_SELECT, ['%.swiftmodule'])
        swift_modules = [row[0] for row in cursor.fetchall() if is_valid_dependency(row[0])]
        for filename in swift_modules:
            shutil.copy(filename, swift_module_search_path)

        # Process modulemap files
        cursor.execute(SQL_FILES_SELECT, ['%/module.modulemap'])
        modulemap_files = [row[0] for row in cursor.fetchall() if is_valid_dependency(row[0])]
        for index, filename in enumerate(modulemap_files):
            # Create a separate directory for each modulemap file because the
            # ClangImporter requires that they are all named
            # "module.modulemap".
            # Use the module name to prevent two modulemaps for the same
            # depndency ending up in multiple directories after several
            # installations, causing the kernel to end up in a bad state.
            # Make all relative header paths in module.modulemap absolute
            # because we copy file to different location.

            src_folder, src_filename = os.path.split(filename)
            with open(filename, encoding='utf8') as file:
                modulemap_contents = file.read()
                modulemap_contents = re.sub(
                    r'header\s+"(.*?)"',
                    lambda m: 'header "%s"' %
                        (m.group(1) if os.path.isabs(m.group(1)) else os.path.abspath(os.path.join(src_folder, m.group(1)))),
                    modulemap_contents
                )

                module_match = re.match(r'module\s+([^\s]+)\s.*{', modulemap_contents)
                module_name = module_match.group(1) if module_match is not None else str(index)
                modulemap_dest = os.path.join(swift_module_search_path, 'modulemap-%s' % module_name)
                os.makedirs(modulemap_dest, exist_ok=True)
                dst_path = os.path.join(modulemap_dest, src_filename)

                with open(dst_path, 'w', encoding='utf8') as outfile:
                    outfile.write(modulemap_contents)

        # == dlopen the shared lib ==

        self.send_response(self.iopub_socket, 'stream', {
            'name': 'stdout',
            'text': 'Initializing Swift...\n'
        })
        self._init_swift()
        self._dlopen_shared_lib(lib_filename)

        self.send_response(self.iopub_socket, 'stream', {
            'name': 'stdout',
            'text': 'Installation complete!\n'
        })
        self.already_installed_packages = True

    def _install_bazel_deps(self, packages):
        if len(packages) == 0:
            return

        if hasattr(self, 'debugger'):
            raise PackageInstallException(
                    'Install Error: Packages can only be installed during the '
                    'first cell execution. Restart the kernel to install '
                    'packages.')

        # Install Bazel packages from local workspace.
        # This follows how we install packages from Swift Package Manager.
        # 1. We create a phantom BUILD target of libjupyterInstalledPackages.so (note
        #    that we need to use custom cc_whole_archive_library to make sure all
        #    symbols are properly preserved). 
        # 2. We query all swift_library targets, and try to find the *.swiftmodule
        #    file. If we found, add the path to the swift_module_search_paths. Note
        #    that we don't move them around in fear of relative directory path issues.
        # 3. We query all cc_library targets, and try to find module.modulemaps file.
        #    If we found, add to the swift_module_search_paths.
        # 4. This doesn't cover some paths we simply added through -I during compilation.
        #    Hence, we queried the action graph to find the arguments we passed for
        #    each swift_library target, and add these paths to swift_module_search_paths
        #    as well.
        # 5. After initialize Swift REPL with the swift_module_search_paths, dlopen
        #    previous built .so file.
        # 6. And we are done! Now you can `import SwiftDependency` as you want in the
        #    notebook.

        pwd = os.getcwd()
        workspace = subprocess.check_output(["bazel", "info", "workspace"]).decode("utf-8").strip()
        output_base = os.path.join(tempfile.gettempdir(), "ibzl", sha256(workspace.encode('utf-8')).hexdigest())
        bazel_bin_dir = subprocess.check_output(["bazel", "info", "bazel-bin"]).decode("utf-8").strip()
        execution_root = subprocess.check_output(["bazel", "info", "execution_root"]).decode("utf-8").strip()
        # Switch to the workspace root, in this way, we can launch the notebook from whatever
        # subdirectory.
        os.chdir(workspace)
        tmpdir = ''.join(random.choice(string.ascii_letters) for i in range(5))
        bazel_dep_target_path = os.path.join(workspace, '.ibzlnb', tmpdir)
        os.makedirs(bazel_dep_target_path, exist_ok=True)
        self.bazel_dep_target_path = bazel_dep_target_path

        # swift_binary doesn't official support build dynamic libraries. Doing so
        # by adding -shared. Note also that we use gold linker to match what
        # Swift Package Manager uses. The default linker has issues with Swift symbols.
        BUILD_template = textwrap.dedent("""\
            load("@build_bazel_rules_swift//swift:swift.bzl", "swift_binary")
            load(":whole_archive.bzl", "cc_whole_archive_library")
            cc_whole_archive_library(
                name = "jupyterInstalledPackages",
                deps = [%s]
            )
            swift_binary(
                name = "libjupyterInstalledPackages.so",
                linkopts = ["-shared", "-fuse-ld=gold"],
                deps = [":jupyterInstalledPackages"]
            )
        """)

        packages_dep = ''
        packages_human_description = ''
        for package in packages:
            unquoted_package = package.strip()
            if len(unquoted_package) == 0:
                continue
            packages_dep += '"%s",\n' % unquoted_package
            packages_human_description += '|-%s\n' % unquoted_package

        self.send_response(self.iopub_socket, 'stream', {
            'name': 'stdout',
            'text': 'Installing packages:\n%s' % packages_human_description
        })
        self.send_response(self.iopub_socket, 'stream', {
            'name': 'stdout',
            'text': 'Working in: %s\n' % bazel_dep_target_path
        })

        BUILD = BUILD_template % (packages_dep)
        whole_archive_bzl = os.path.join(os.path.dirname(sys.argv[0]), 'whole_archive.bzl')
        shutil.copy(whole_archive_bzl, bazel_dep_target_path)
        with open('%s/BUILD.bazel' % bazel_dep_target_path, 'w') as f:
            f.write(BUILD)
        subprocess.check_output(["bazel", "build", "//.ibzlnb/%s:libjupyterInstalledPackages.so" % tmpdir])
        env = dict(os.environ, CC="clang")
        result = subprocess.check_output(["bazel", "--output_base=%s" % output_base, "query", "kind(swift_library, deps(//.ibzlnb/%s:libjupyterInstalledPackages.so))" % tmpdir], env=env).decode("utf-8")
        # Process *.swiftmodules files
        swift_module_search_paths = set()
        for dep in result.split("\n"):
            dep = dep.strip()
            if len(dep) == 0:
                continue
            result = json.loads(subprocess.check_output(["bazel", "aquery", "mnemonic(SwiftCompile, %s)" % dep, "--output=jsonproto", "--include_artifacts=false"], env=env).decode("utf-8"))
            for action in result['actions']:
                for i, arg in enumerate(action['arguments']):
                    if arg.startswith("-I"):
                        swift_module_search_paths.add(os.path.join(execution_root, arg[2:]))
                    elif arg.startswith("-iquote"):
                        swift_module_search_paths.add(os.path.join(execution_root, arg[7:]))
                    elif arg.startswith("-isystem"):
                        swift_module_search_paths.add(os.path.join(execution_root, arg[8:]))
                    elif arg == "-emit-module-path":
                        swift_module = action["arguments"][i + 1]
            swift_module = os.path.join(execution_root, swift_module)
            if os.path.exists(swift_module):
                swift_module_search_paths.add(os.path.dirname(swift_module))
                self.send_response(self.iopub_socket, 'stream', {
                    'name': 'stdout',
                    'text': 'swiftmodule: %s\n' % swift_module
                })
        result = subprocess.check_output(["bazel", "--output_base=%s" % output_base, "query", "kind(cc_library, deps(//.ibzlnb/%s:libjupyterInstalledPackages.so))" % tmpdir], env=env).decode("utf-8")
        # Process *.modulemaps
        for dep in result.split("\n"):
            dep = dep.strip()
            if len(dep) == 0:
                continue
            if dep[0] == '@':
                parts = dep[1:].split("//")
                modulemaps = '/'.join(filter(lambda x: len(x) > 0, ['external', parts[0]] + parts[1].split(":"))) + ".modulemaps"
            else:
                parts = dep.split("//")
                modulemaps = '/'.join(filter(lambda x: len(x) > 0, [parts[0]] + parts[1].split(":"))) + ".modulemaps"
            modulemaps = os.path.join(bazel_bin_dir, modulemaps)
            if os.path.exists(modulemaps):
                swift_module_search_paths.add(os.path.dirname(modulemaps))
                self.send_response(self.iopub_socket, 'stream', {
                    'name': 'stdout',
                    'text': 'modulemaps: %s\n' % modulemaps
                })
        lib_filename = os.path.join(bazel_bin_dir, '.ibzlnb', tmpdir, 'libjupyterInstalledPackages.so')
        self.swift_module_search_paths = list(swift_module_search_paths)

        # == dlopen the shared lib ==

        self.send_response(self.iopub_socket, 'stream', {
            'name': 'stdout',
            'text': 'Initializing Swift...\n'
        })
        self._init_swift()
        self._dlopen_shared_lib(lib_filename)

        self.send_response(self.iopub_socket, 'stream', {
            'name': 'stdout',
            'text': 'Installation complete!\n'
        })
        # Switch back to whatever we previous from.
        os.chdir(pwd)
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
        get_address_error = lldb.SBError()
        address = sbvalue \
                .GetChildMemberWithName('address') \
                .GetData() \
                .GetAddress(get_address_error, 0)
        if get_address_error.Fail():
            raise Exception('getting address: %s' % str(get_address_error))

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
        data = self.process.ReadMemory(address, count, get_data_error)
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

        # Return early if the code is empty or whitespace, to avoid
        # initializing Swift and preventing package installs.
        if len(code) == 0 or code.isspace():
            return {
                'status': 'ok',
                'execution_count': self.execution_count,
                'payload': [],
                'user_expressions': {}
            }

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
            # TODO(#112): Make this show expression values again.
            self.send_response(self.iopub_socket, 'execute_result', {
                'execution_count': self.execution_count,
                'data': {
                    'text/plain': 'Use `print()` to show values.\n'
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

    def do_shutdown(self, restart):
        # Need to cleanup the temporary Bazel dependency path.
        if hasattr(self, 'bazel_dep_target_path'):
            shutil.rmtree(self.bazel_dep_target_path)

if __name__ == '__main__':
    # Jupyter sends us SIGINT when the user requests execution interruption.
    # Here, we block all threads from receiving the SIGINT, so that we can
    # handle it in a specific handler thread.
    if hasattr(signal, 'pthread_sigmask'): # Not supported in Windows
        signal.pthread_sigmask(signal.SIG_BLOCK, [signal.SIGINT])

    from ipykernel.kernelapp import IPKernelApp
    # We pass the kernel name as a command-line arg, since Jupyter gives those
    # highest priority (in particular overriding any system-wide config).
    IPKernelApp.launch_instance(
        argv=sys.argv + ['--IPKernelApp.kernel_class=__main__.SwiftKernel'])
