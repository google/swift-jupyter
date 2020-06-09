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

  private var previousDisplayMessages: [JupyterDisplayMessage] = []

  init(jupyterSession: JupyterSession) {
    self.afterSuccessfulExecutionHandlers = []
    self.parentMessageHandlers = []
    self.jupyterSession = jupyterSession
  }

  /// Register a handler to run after the kernel successfully executes a cell
  /// of user code. The handler may return messages. These messages will be
  /// sent to the Jupyter client.
  public mutating func afterSuccessfulExecution(
      run handler: @escaping () -> [JupyterDisplayMessage]) {
    afterSuccessfulExecutionHandlers.append(handler)
  }

  /// Register a handler to run when the parent message changes.
  public mutating func handleParentMessage(_ handler: @escaping (ParentMessage) -> ()) {
    parentMessageHandlers.append(handler)
  }

  /// The kernel calls this after successfully executing a cell of user code.
  /// Returns an array of messages, where each message is returned as an array
  /// of parts, where each part is returned as an address to the memory containing the part's
  /// bytes and a count of the number of bytes.
  public mutating func triggerAfterSuccessfulExecution() -> [[(address: UInt, count: Int)]] {
    // Keep a reference to the messages, so that their `.unsafeBufferPointer`
    // stays valid while the kernel is reading from them.
    previousDisplayMessages = afterSuccessfulExecutionHandlers.flatMap { $0() }
    return previousDisplayMessages.map { message in
      return message.parts.map { part in
        let b = part.unsafeBufferPointer
        return (address: UInt(bitPattern: b.baseAddress), count: b.count)
      }
    }
  }

  /// The kernel calls this when the parent message changes.
  public mutating func updateParentMessage(to parentMessage: ParentMessage) {
    for parentMessageHandler in parentMessageHandlers {
      parentMessageHandler(parentMessage)
    }
  }

  /// A single serialized display message for the Jupyter client.
  /// Corresponds to a ZeroMQ "multipart message".
  public struct JupyterDisplayMessage {
    let parts: [BytesReference]
  }

  /// A reference to memory containing bytes.
  ///
  /// As long as there is a strong reference to an instance, that instance's
  /// `unsafeBufferPointer` refers to memory containing the bytes passed to
  /// that instance's constructor.
  ///
  /// We use this so that we can give the kernel a memory location that it can
  /// read bytes from.
  public class BytesReference {
    private var bytes: ContiguousArray<CChar>

    init<S: Sequence>(_ bytes: S) where S.Element == CChar {
      // Construct our own array and copy `bytes` into it, so that no one
      // else aliases the underlying memory.
      self.bytes = []
      self.bytes.append(contentsOf: bytes)
    }

    public var unsafeBufferPointer: UnsafeBufferPointer<CChar> {
      // We have tried very hard to make the pointer stay valid outside the
      // closure:
      // - No one else aliases the underlying memory.
      // - The comment on this class reminds users that the memory may become
      //   invalid after all references to the BytesReference instance are
      //   released.
      return bytes.withUnsafeBufferPointer { $0 }
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
