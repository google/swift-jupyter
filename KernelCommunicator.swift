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
 * A struct with functions that the kernel and the code running inside the
 * kernel use to talk to each other.
 *
 * Note that it would be more Juptyer-y for the communication to happen over
 * ZeroMQ. This is not currently possible, because ZeroMQ sends messages
 * asynchronously using IO threads, and LLDB pauses those IO threads, which
 * prevents them from sending the messages.
 */
public struct KernelCommunicator {
  private var afterSuccessfulExecutionHandlers: [() -> JuptyerMessages]
  private var parentMessageHandlers: [(ParentMessage) -> ()]

  public let juptyerSession: JuptyerSession

  init(juptyerSession: JuptyerSession) {
    self.afterSuccessfulExecutionHandlers = []
    self.parentMessageHandlers = []
    self.juptyerSession = juptyerSession
  }

  /*
   * Register a handler to run after the kernel successfully executes a cell
   * of user code. The handler may return messages. These messages will be
   * send to the Juptyer client.
   */
  public mutating func afterSuccessfulExecution(
      run handler: @escaping () -> JuptyerMessages) {
    afterSuccessfulExecutionHandlers.append(handler)
  }

  /*
   * Register a handler to run when the parent message changes.
   */
  public mutating func handleParentMessage(
      with handler: @escaping (ParentMessage) -> ()) {
    parentMessageHandlers.append(handler)
  }

  /*
   * The kernel calls this after successfully executing a cell of user code.
   */
  public func triggerAfterSuccessfulExecution() -> JuptyerMessages {
    return afterSuccessfulExecutionHandlers.reduce(JuptyerMessages()) {
      (messages, handler) in messages + handler()
    }
  }

  /*
   * The kernel calls this when the parent message changes.
   */
  public mutating func setParentMessage(
      to parentMessage: ParentMessage) {
    for parentMessageHandler in parentMessageHandlers {
      parentMessageHandler(parentMessage)
    }
  }
}

/*
 * A single serialized display message for the Juptyer client.
 */
public struct JuptyerDisplayMessage {
  public let parts: [[CChar]]
}

/*
 * A collection of serialized messages for the Jupyter client.
 */
public struct JuptyerMessages {
  public let displayMessages: [JuptyerDisplayMessage]

  init() {
    self.displayMessages = []
  }

  init(displayMessages: [JuptyerDisplayMessage]) {
    self.displayMessages = displayMessages
  }

  static func +(a: JuptyerMessages, b: JuptyerMessages) -> JuptyerMessages {
    return JuptyerMessages(
      displayMessages: a.displayMessages + b.displayMessages)
  }
}

/*
 * ParentMessage identifies the request that causes things to happen.
 * This lets Juptyer, for example, know which cell to display graphics
 * messages in.
 */
public struct ParentMessage {
  let json: String
}

/*
 * The data necessary to identify and sign outgoing juptyer messages.
 */
public struct JuptyerSession {
  let id: String
  let key: String
  let username: String
}
