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

  /// Owns the JupyterDisplayMessages' memory.
  private var previousDisplayMessages: [JupyterDisplayMessage] = []

  init(jupyterSession: JupyterSession) {
    self.afterSuccessfulExecutionHandlers = []
    self.parentMessageHandlers = []
    self.jupyterSession = jupyterSession
  }

  /// Register a handler to run after the kernel successfully executes a cell
  /// of user code. The handler may return messages. These messages will be
  /// sent to the Jupyter client. The KernelCommunicator takes ownership of
  /// the messages' memory.
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
  ///
  /// Caller does not own the result. The resulting JupyterDisplayMessages
  /// refer to valid memory until the next time this method is called.
  public mutating func triggerAfterSuccessfulExecution() -> [JupyterDisplayMessage] {
    for previousDisplayMessage in previousDisplayMessages {
      previousDisplayMessage.deinitialize()
    }
    previousDisplayMessages = afterSuccessfulExecutionHandlers.flatMap { $0() }
    return previousDisplayMessages
  }

  /// The kernel calls this when the parent message changes.
  public mutating func updateParentMessage(
      to parentMessage: ParentMessage) {
    for parentMessageHandler in parentMessageHandlers {
      parentMessageHandler(parentMessage)
    }
  }

  /// A single serialized display message for the Jupyter client.
  ///
  /// Refers to unmanaged memory so that the kernel can read the messages
  /// directly from memory.
  ///
  /// Clients must call `deinitialize()` after they are finished with this
  /// struct. The memory that this refers to becomes invalid when
  /// `deinitialize()` is called.
  ///
  /// TODO: We could make this a class and take advantage of Swift's reference
  //  counting. But there are some obstacles to overcome first:
  /// 1. It seems that LLDB never releases values that it has references to.
  ///    So we need to fix that to avoid leaking memory.
  /// 2. When I make this a class, LLDB can't read the contents of `parts`.
  ///    LLDB says "<read memory from {ADDRESS} failed (0 of 8 bytes read)>".
  public struct JupyterDisplayMessage {
    let parts: [UnsafeMutableBufferPointer<CChar>]

    init(parts: [[CChar]]) {
      self.parts = parts.map {
        let part = UnsafeMutableBufferPointer<CChar>.allocate(capacity: $0.count)
        part.initialize(from: $0)
        return part
      }
    }

    func deinitialize() {
      for part in parts {
        part.deallocate()
      }
    }
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
}
