// Copyright 2018 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

/// Hooks IPython to the KernelCommunicator, so that it can send display
/// messages to Jupyter.

import Python

// Workaround SR-7757.
#if canImport(Darwin)
import func Darwin.C.dlopen
#elseif canImport(Glibc)
import func Glibc.dlopen
#else
#error("Cannot import Darwin or Glibc!")
#endif
dlopen("libpython2.7.so", RTLD_NOW | RTLD_GLOBAL)

enum IPythonDisplay {
  static var socket: PythonObject = Python.None
  static var shell: PythonObject = Python.None

}

extension IPythonDisplay {
  private static func bytes(_ py: PythonObject) -> KernelCommunicator.BytesReference {
    // TODO: Replace with a faster implementation that reads bytes directly
    // from the python object's memory.
    let bytes = py.lazy.map { CChar(bitPattern: UInt8(Python.ord($0))!) }
    return KernelCommunicator.BytesReference(bytes)
  }

  private static func updateParentMessage( to parentMessage: KernelCommunicator.ParentMessage) {
    let json = Python.import("json")
    IPythonDisplay.shell.set_parent(json.loads(parentMessage.json))
  }

  private static func consumeDisplayMessages() -> [KernelCommunicator.JupyterDisplayMessage] {
    let displayMessages = IPythonDisplay.socket.messages.map {
      KernelCommunicator.JupyterDisplayMessage(parts: $0.map { bytes($0) })
    }
    IPythonDisplay.socket.messages = []
    return displayMessages
  }

  static func enable() {
    if IPythonDisplay.shell != Python.None {
      print("Warning: IPython display already enabled.")
      return
    }

    let swift_shell = Python.import("swift_shell")
    let socketAndShell = swift_shell.create_shell(
      username: JupyterKernel.communicator.jupyterSession.username,
      session_id: JupyterKernel.communicator.jupyterSession.id,
      key: JupyterKernel.communicator.jupyterSession.key)
    IPythonDisplay.socket = socketAndShell[0]
    IPythonDisplay.shell = socketAndShell[1]

    JupyterKernel.communicator.handleParentMessage(updateParentMessage)
    JupyterKernel.communicator.afterSuccessfulExecution(run: consumeDisplayMessages)
  }
}

IPythonDisplay.enable()
