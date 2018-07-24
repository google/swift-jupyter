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
#if os(macOS) || os(iOS) || os(watchOS) || os(tvOS)
import func Darwin.C.dlopen
#else
import func Glibc.dlopen
#endif
dlopen("libpython2.7.so", RTLD_NOW | RTLD_GLOBAL)

enum IPythonDisplay {
  static var socket: PythonObject = Python.None
  static var shell: PythonObject = Python.None
}

func enableIPythonDisplay() {
  let json = Python.import("json")

  let swift_shell = Python.import("swift_shell")
  let socketAndShell = swift_shell.create_shell(
    username: JupyterKernel.communicator.jupyterSession.username,
    session_id: JupyterKernel.communicator.jupyterSession.id,
    key: JupyterKernel.communicator.jupyterSession.key)
  IPythonDisplay.socket = socketAndShell[0]
  IPythonDisplay.shell = socketAndShell[1]

  func updateParentMessage(to parentMessage: ParentMessage) {
    IPythonDisplay.shell.set_parent(json.loads(parentMessage.json))
  }
  JupyterKernel.communicator.handleParentMessage(updateParentMessage)

  func consumeDisplayMessages() -> [JupyterDisplayMessage] {
    func bytes(_ py: PythonObject) -> [CChar] {
      // faster not-yet-introduced method
      // return py.swiftBytes!

      // slow placeholder implementation
      return py.map { (el) in
        return CChar(bitPattern: UInt8(Python.ord(el))!)
      }
    }

    let displayMessages = IPythonDisplay.socket.messages.map {
      JupyterDisplayMessage(parts: $0.map { bytes($0) })
    }
    IPythonDisplay.socket.messages = []
    return displayMessages
  }
  JupyterKernel.communicator.afterSuccessfulExecution(
    run: consumeDisplayMessages)
}

enableIPythonDisplay()
