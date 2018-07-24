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

/*
 * Hooks IPython to the KernelCommunicator, so that it can send display
 * messages to Juptyer.
 */

import Python

// Workaround SR-7757.
#if os(macOS) || os(iOS) || os(watchOS) || os(tvOS)
import func Darwin.C.dlopen
#else
import func Glibc.dlopen
#endif
dlopen("libpython2.7.so", RTLD_NOW | RTLD_GLOBAL)

var _socket: PythonObject = Python.None
var _shell: PythonObject = Python.None

func enableIPythonDisplay() {
  let json = Python.import("json")

  let swift_shell = Python.import("swift_shell")
  let socketAndShell = swift_shell.create_shell(
    username: _kernelCommunicator.juptyerSession.username,
    session_id: _kernelCommunicator.juptyerSession.id,
    key: _kernelCommunicator.juptyerSession.key)
  _socket = socketAndShell[0]
  _shell = socketAndShell[1]

  func setParentMessage(parentMessage: ParentMessage) {
    _shell.set_parent(json.loads(parentMessage.json))
  }
  _kernelCommunicator.handleParentMessage(with: setParentMessage)

  func getDisplayMessages() -> JuptyerMessages {
    func getBytes(_ py: PythonObject) -> [CChar] {
      // faster not-yet-introduced method
      // return py.swiftBytes!

      // slow placeholder implementation
      return py.map { (el) in
        return CChar(bitPattern: UInt8(Python.ord(el))!)
      }
    }

    let displayMessages = _socket.messages.map {
      JuptyerDisplayMessage(parts: $0.map { getBytes($0) })
    }
    _socket.messages = []
    return JuptyerMessages(displayMessages: displayMessages)
  }
  _kernelCommunicator.afterSuccessfulExecution(run: getDisplayMessages)
}

enableIPythonDisplay()
