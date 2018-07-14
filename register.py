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


def main():
    args = parse_args()

    if args.swift_toolchain is not None:
        # Use a prebuilt swift toolchain.
        if platform.system() == 'Linux':
            lldb_python = '%s/usr/lib/python2.7/site-packages' % args.swift_toolchain
            swift_libs = '%s/usr/lib/swift/linux' % args.swift_toolchain
            repl_swift = '%s/usr/bin/repl_swift' % args.swift_toolchain
        elif platform.system() == 'Darwin':
            lldb_python = '%s/System/Library/PrivateFrameworks/LLDB.framework/Versions/A/Resources/Python' % args.swift_toolchain
            swift_libs = '%s/usr/lib/swift/macosx' % args.swift_toolchain
            repl_swift = '%s/System/Library/PrivateFrameworks/LLDB.framework/Resources/repl_swift' % args.swift_toolchain
        else:
            raise Exception('Unknown system %s' % platform.system())
    else:
        # Use a build dir created by build-script.

        # TODO: Make this work on macos
        if platform.system() != 'Linux':
            raise Exception('build-script build dir only implemented on Linux')

        swift_build_dir = '%s/swift-linux-x86_64' % args.swift_build
        lldb_build_dir = '%s/lldb-linux-x86_64' % args.swift_build

        lldb_python = '%s/lib/python2.7/site-packages' % lldb_build_dir
        swift_libs = '%s/lib/swift/linux' % swift_build_dir
        repl_swift = '%s/bin/repl_swift' % lldb_build_dir

    if not os.path.isdir(lldb_python):
        raise Exception('lldb python libs not found at %s' % lldb_python)
    if not os.path.isdir(swift_libs):
        raise Exception('swift libs not found at %s' % swift_libs)
    if not os.path.isfile(repl_swift):
        raise Exception('repl_swift binary not found at %s' % repl_swift)

    script_dir = os.path.dirname(os.path.realpath(sys.argv[0]))
    kernel_json = {
        'argv': [
                sys.executable,
                '%s/swift_kernel.py' % script_dir,
                '-f',
                '{connection_file}',
        ],
        'display_name': 'Swift',
        'language': 'swift',
        'env': {
            'PYTHONPATH': lldb_python,
            'LD_LIBRARY_PATH': swift_libs,
            'REPL_SWIFT_PATH': repl_swift,
        },
    }
    print('kernel.json is\n%s' % json.dumps(kernel_json, indent=2))

    with TemporaryDirectory() as td:
        os.chmod(td, 0o755)
        with open(os.path.join(td, 'kernel.json'), 'w') as f:
            json.dump(kernel_json, f, indent=2)
        KernelSpecManager().install_kernel_spec(
            td, 'swift', user=args.user, prefix=args.prefix, replace=True)

    print('Registered kernel!')


def parse_args():
    parser = argparse.ArgumentParser(
            description='Register KernelSpec for Swift Kernel')

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

    args = parser.parse_args()
    if args.sys_prefix:
        args.prefix = sys.prefix
    if args.swift_toolchain is not None:
        args.swift_toolchain = os.path.realpath(args.swift_toolchain)
    if args.swift_build is not None:
        args.swift_build = os.path.realpath(args.swift_build)
    return args


if __name__ == '__main__':
    main()
