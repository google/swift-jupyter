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

/// A struct with functions that the kernel and the code running inside the
/// kernel use to talk to each other.
///
/// Note that it would be more Jupyter-y for the communication to happen over
/// ZeroMQ. This is not currently possible, because ZeroMQ sends messages
/// asynchronously using IO threads, and LLDB pauses those IO threads, which
/// prevents them from sending the messages.
public struct KernelCommunicator {
  private var afterSuccessfulExecutionHandlers: [() -> [JupyterDisplayMessage]]
  private var parentMessageHandlers: [(ParentMessage) -> ()]

  public let jupyterSession: JupyterSession

  init(jupyterSession: JupyterSession) {
    self.afterSuccessfulExecutionHandlers = []
    self.parentMessageHandlers = []
    self.jupyterSession = jupyterSession
  }

  /// Register a handler to run after the kernel successfully executes a cell
  /// of user code. The handler may return messages. These messages will be
  /// send to the Jupyter client.
  public mutating func afterSuccessfulExecution(
      run handler: @escaping () -> [JupyterDisplayMessage]) {
    afterSuccessfulExecutionHandlers.append(handler)
  }

  /// Register a handler to run when the parent message changes.
  public mutating func handleParentMessage(
      _ handler: @escaping (ParentMessage) -> ()) {
    parentMessageHandlers.append(handler)
  }

  /// The kernel calls this after successfully executing a cell of user code.
  public func triggerAfterSuccessfulExecution() -> [JupyterDisplayMessage] {
    return afterSuccessfulExecutionHandlers.flatMap { $0() }
  }

  /// The kernel calls this when the parent message changes.
  public mutating func updateParentMessage(
      to parentMessage: ParentMessage) {
    for parentMessageHandler in parentMessageHandlers {
      parentMessageHandler(parentMessage)
    }
  }
}

/// A single serialized display message for the Jupyter client.
public struct JupyterDisplayMessage {
  public let parts: [[CChar]]
}

/// ParentMessage identifies the request that causes things to happen.
/// This lets Jupyter, for example, know which cell to display graphics
/// messages in.
public struct ParentMessage {
  let json: String
}

/// The data necessary to identify and sign outgoing jupyter messages.
public struct JupyterSession {
  let id: String
  let key: String
  let username: String
}
