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

import argparse
import json
import os
import platform
import sys

from jupyter_client.kernelspec import KernelSpecManager
from IPython.utils.tempdir import TemporaryDirectory

kernel_code_name_allowed_chars = "-."


def get_kernel_code_name(kernel_name):
    """
    Returns a valid kernel code name (like `swift-for-tensorflow`)
    from a kernel display name (like `Swift for TensorFlow`).
    """

    kernel_code_name = kernel_name.lower().replace(" ", kernel_code_name_allowed_chars[0])
    kernel_code_name = "".join(list(filter(lambda x: x.isalnum() or x in kernel_code_name_allowed_chars, kernel_code_name)))
    return kernel_code_name


def make_kernel_env(args):
    """Returns environment varialbes that tell the kernel where things are."""

    kernel_env = {}
    kernel_env['LLDB_USE_SERIALIZATION'] = '1'

    if args.swift_toolchain is not None:
        # Use a prebuilt Swift toolchain.
        if platform.system() == 'Linux':
            kernel_env['PYTHONPATH'] = '%s/usr/lib/python2.7/site-packages' % args.swift_toolchain
            kernel_env['LD_LIBRARY_PATH'] = '%s/usr/lib/swift/linux' % args.swift_toolchain
            kernel_env['REPL_SWIFT_PATH'] = '%s/usr/bin/repl_swift' % args.swift_toolchain

            # Points SourceKitten at SourceKit.
            kernel_env['LINUX_SOURCEKIT_LIB_PATH'] = '%s/usr/lib' % args.swift_toolchain
        elif platform.system() == 'Darwin':
            kernel_env['PYTHONPATH'] = '%s/System/Library/PrivateFrameworks/LLDB.framework/Resources/Python' % args.swift_toolchain
            kernel_env['LD_LIBRARY_PATH'] = '%s/usr/lib/swift/macosx' % args.swift_toolchain
            kernel_env['REPL_SWIFT_PATH'] = '%s/System/Library/PrivateFrameworks/LLDB.framework/Resources/repl_swift' % args.swift_toolchain

            # Points SourceKitten at SourceKit.
            kernel_env['XCODE_DEFAULT_TOOLCHAIN_OVERRIDE'] = '%s/usr/lib' % args.swift_toolchain
        else:
            raise Exception('Unknown system %s' % platform.system())

    elif args.swift_build is not None:
        # Use a build dir created by build-script.

        # TODO: Make this work on macos
        if platform.system() != 'Linux':
            raise Exception('build-script build dir only implemented on Linux')

        swift_build_dir = '%s/swift-linux-x86_64' % args.swift_build
        lldb_build_dir = '%s/lldb-linux-x86_64' % args.swift_build

        kernel_env['PYTHONPATH'] = '%s/lib/python2.7/site-packages' % lldb_build_dir
        kernel_env['LD_LIBRARY_PATH'] = '%s/lib/swift/linux' % swift_build_dir
        kernel_env['REPL_SWIFT_PATH'] = '%s/bin/repl_swift' % lldb_build_dir

        # Points SourceKitten at SourceKit.
        kernel_env['LINUX_SOURCEKIT_LIB_PATH'] = '%s/lib' % swift_build_dir

    elif args.xcode_path is not None:
        # Use an Xcode provided Swift toolchain.

        if platform.system() != 'Darwin':
            raise Exception('Xcode support is only available on Darwin')

        lldb_framework = '%s/Contents/SharedFrameworks/LLDB.framework' % args.xcode_path
        xcode_toolchain = '%s/Contents/Developer/Toolchains/XcodeDefault.xctoolchain' % args.xcode_path

        kernel_env['PYTHONPATH'] = '%s/Resources/Python' % lldb_framework
        kernel_env['REPL_SWIFT_PATH'] = '%s/Resources/repl_swift' % lldb_framework
        kernel_env['LD_LIBRARY_PATH'] = '%s/usr/lib/swift/macosx' % xcode_toolchain

        # Points SourceKitten at SourceKit.
        kernel_env['XCODE_DEFAULT_TOOLCHAIN_OVERRIDE'] = '%s/usr/lib' % xcode_toolchain

    if args.sourcekitten is not None:
        kernel_env['SOURCEKITTEN'] = args.sourcekitten

    if args.swift_python_version is not None:
        kernel_env['PYTHON_VERSION'] = args.swift_python_version
    if args.swift_python_library is not None:
        kernel_env['PYTHON_LIBRARY'] = args.swift_python_library

    return kernel_env


def validate_kernel_env(kernel_env):
    """Validates that the env vars refer to things that actually exist."""

    if not os.path.isdir(kernel_env['PYTHONPATH']):
        raise Exception('lldb python libs not found at %s' %
                        kernel_env['PYTHONPATH'])
    if not os.path.isdir(kernel_env['LD_LIBRARY_PATH']):
        raise Exception('swift libs not found at %s' %
                        kernel_env['LD_LIBRARY_PATH'])
    if not os.path.isfile(kernel_env['REPL_SWIFT_PATH']):
        raise Exception('repl_swift binary not found at %s' %
                        kernel_env['REPL_SWIFT_PATH'])

    if 'SOURCEKITTEN' in kernel_env \
        and not os.path.isfile(kernel_env['SOURCEKITTEN']):
        raise Exception('sourcekitten not found at %s' % kernel_env['SOURCEKITTEN'])
    if 'LINUX_SOURCEKIT_LIB_PATH' in kernel_env \
        and not os.path.isfile(os.path.join(
            kernel_env['LINUX_SOURCEKIT_LIB_PATH'], 'libsourcekitdInProc.so')):
        raise Exception('libsourcekitdInProc.so not found at %s' %
                        kernel_env['LINUX_SOURCEKIT_LIB_PATH'])
    if 'XCODE_DEFAULT_TOOLCHAIN_OVERRIDE' in kernel_env \
        and not os.path.isdir(kernel_env['XCODE_DEFAULT_TOOLCHAIN_OVERRIDE']):
        raise Exception('xcode toolchain override not found at %s' %
                        kernel_env['XCODE_DEFAULT_TOOLCHAIN_OVERRIDE'])


def main():
    args = parse_args()
    kernel_env = make_kernel_env(args)
    validate_kernel_env(kernel_env)

    script_dir = os.path.dirname(os.path.realpath(sys.argv[0]))
    kernel_json = {
        'argv': [
                args.python_interpreter,
                '%s/swift_kernel.py' % script_dir,
                '-f',
                '{connection_file}',
        ],
        'display_name': args.kernel_name,
        'language': 'swift',
        'env': kernel_env,
    }
    
    print('kernel.json:\n%s\n' % json.dumps(kernel_json, indent=2))

    kernel_code_name = get_kernel_code_name(args.kernel_name)

    with TemporaryDirectory() as td:
        os.chmod(td, 0o755)
        with open(os.path.join(td, 'kernel.json'), 'w') as f:
            json.dump(kernel_json, f, indent=2)
        KernelSpecManager().install_kernel_spec(
            td, kernel_code_name, user=args.user, prefix=args.prefix)

    print('Registered kernel \'{}\' as \'{}\'!'.format(args.kernel_name, kernel_code_name))


def parse_args():
    parser = argparse.ArgumentParser(
            description='Register KernelSpec for Swift Kernel')

    parser.add_argument(
        '--kernel-name',
        help='Kernel display name',
        default='Swift'
    )

    prefix_locations = parser.add_mutually_exclusive_group()
    prefix_locations.add_argument(
        '--user',
        help='Register KernelSpec in user homedirectory',
        action='store_true')
    prefix_locations.add_argument(
        '--sys-prefix',
        help='Register KernelSpec in sys.prefix. Useful in conda / virtualenv',
        action='store_true',
        dest='sys_prefix')
    prefix_locations.add_argument(
        '--prefix',
        help='Register KernelSpec in this prefix',
        default=None)

    swift_locations = parser.add_mutually_exclusive_group(required=True)
    swift_locations.add_argument(
        '--swift-toolchain',
        help='Path to a prebuilt swift toolchain')
    swift_locations.add_argument(
        '--swift-build',
        help='Path to build-script build directory, containing swift and lldb')
    swift_locations.add_argument(
        '--xcode-path',
        help='Path to Xcode app bundle')

    parser.add_argument(
        '--sourcekitten',
        help='Path to sourcekitten binary (enables code completion)')

    parser.add_argument(
        '--python-interpreter',
        help='Path to a python interpreter to run the kernel. Must be ' +
             'python2. If you omit this, we will use the interpreter ' +
             'that is running register.py.')

    parser.add_argument(
        '--swift-python-version',
        help='direct Swift\'s Python interop library to use this version of ' +
             'Python')
    parser.add_argument(
        '--swift-python-library',
        help='direct Swift\'s Python interop library to use this Python ' +
             'library')

    args = parser.parse_args()
    if args.sys_prefix:
        args.prefix = sys.prefix
    if args.swift_toolchain is not None:
        args.swift_toolchain = os.path.realpath(args.swift_toolchain)
    if args.swift_build is not None:
        args.swift_build = os.path.realpath(args.swift_build)
    if args.xcode_path is not None:
        args.xcode_path = os.path.realpath(args.xcode_path)
    if args.sourcekitten is not None:
        args.sourcekitten = os.path.realpath(args.sourcekitten)
    if args.python_interpreter is None:
        args.python_interpreter = sys.executable
    args.python_interpreter = os.path.realpath(args.python_interpreter)
    if args.swift_python_version is not None and \
            args.swift_python_library is not None:
        raise Exception('Should not specify --swift-python-version and ' +
                        '--swift-python-library at the same time.')
    return args


if __name__ == '__main__':
    main()
