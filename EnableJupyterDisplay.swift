// Copyright 2019 Google LLC
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

#if canImport(Cryptor)
import Cryptor
#endif

import Foundation

enum JupyterDisplay {
    struct Header: Encodable {
        let messageID: String
        let username: String
        let session: String
        var date: String {
            let currentDate = Date()
            let formatter = DateFormatter()
            formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSSZZZZZ"
            formatter.timeZone = TimeZone(secondsFromGMT: 0)
            formatter.locale = Locale(identifier: "en_US_POSIX")
            return formatter.string(from: currentDate)
        }
        let messageType: String
        let version: String
        private enum CodingKeys: String, CodingKey {
            case messageID = "msg_id"
            case messageType = "msg_type"
        }

        init(messageID: String = UUID().uuidString,
             username: String = "kernel",
             session: String,
             messageType: String = "display_data",
             version: String = "5.2") {
            self.messageID = messageID
            self.username = username
            self.session = session
            self.messageType = messageType
            self.version = version
        }

        var json: String {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            encoder.keyEncodingStrategy = .convertToSnakeCase
            guard let jsonData = try? encoder.encode(self) else { return "{}" }
            let jsonString = String(data: jsonData, encoding: .utf8)!
            return jsonString
        }
    }

    struct Message {
        var messageType: String = "display_data"
        var delimiter = "<IDS|MSG>"
        var key: String = ""
        var header: Header
        var metadata: String = "{}"
        var content: String = "{}"
        var hmacSignature: String {
            #if canImport(Cryptor)
            let data = Data((header.json+parentHeader+metadata+content).utf8)
            let keyData = Data(key.utf8)
            let dataHex = data.map{ String(format: "%02x", $0) }.joined()
            let keyHex = keyData.map{ String(format: "%02x", $0) }.joined()
            let hmacKey = CryptoUtils.byteArray(fromHex: keyHex)
            let hmacData: [UInt8] = CryptoUtils.byteArray(fromHex: dataHex)
            let hmac = HMAC(using: HMAC.Algorithm.sha256, key: hmacKey).update(byteArray: hmacData)?.final()
            return CryptoUtils.hexString(from: hmac!)
            #endif
            return ""
        }
        var messageParts: [KernelCommunicator.BytesReference] {
            return [
                bytes(messageType),
                bytes(delimiter),
                bytes(hmacSignature),
                bytes(header.json),
                bytes(parentHeader),
                bytes(metadata),
                bytes(content)
            ]
        }

        init(content: String = "{}") {
            header = Header(
                username: JupyterKernel.communicator.jupyterSession.username,
                session: JupyterKernel.communicator.jupyterSession.id
            )
            self.content = content
            key = JupyterKernel.communicator.jupyterSession.key
            #if !canImport(Cryptor)
            if !key.isEmpty {
                fatalError("""
                           Unable to import Cryptor to perform message signing.
                           Add Cryptor as a dependency, or disable message signing in Jupyter as follows:
                           jupyter notebook --Session.key='b\"\"'\n
                           """)
            }
            #endif
        }
    }

    struct PNGImageData: Encodable {
        let image: String
        let text = "<IPython.core.display.Image object>"
        init(base64EncodedPNG: String) {
            image = base64EncodedPNG
        }
        private enum CodingKeys: String, CodingKey {
            case image = "image/png"
            case text  = "text/plain"
        }
    }

    struct MessageContent<Data>: Encodable where Data: Encodable {
        let metadata = "{}"
        let transient = "{}"
        let data: Data
        var json: String {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            encoder.keyEncodingStrategy = .convertToSnakeCase
            guard let jsonData = try? encoder.encode(self) else { return "{}" }
            var jsonString = String(data: jsonData, encoding: .utf8)!
            return jsonString
        }
    }

    static var parentHeader = ""
    static var messages = [Message]()
}

extension JupyterDisplay {
    private static func bytes(_ bytes: String) -> KernelCommunicator.BytesReference {
        let bytes = bytes.utf8CString.dropLast()
        return KernelCommunicator.BytesReference(bytes)
    }

    private static func updateParentMessage(to parentMessage: KernelCommunicator.ParentMessage) {
        do {
            let jsonData = (parentMessage.json).data(using: .utf8, allowLossyConversion: false)
            let jsonDict = try JSONSerialization.jsonObject(with: jsonData!) as? NSDictionary
            let headerData = try JSONSerialization.data(withJSONObject: jsonDict!["header"],
                                                        options: .prettyPrinted)
            parentHeader = String(data: headerData, encoding: .utf8)!
        } catch {
            print("Error in JSON parsing!")
        }
    }

    private static func consumeDisplayMessages() -> [KernelCommunicator.JupyterDisplayMessage] {
        var displayMessages = [KernelCommunicator.JupyterDisplayMessage]()
        for message in messages {
            displayMessages.append(KernelCommunicator.JupyterDisplayMessage(parts: message.messageParts))
        }
        messages = []
        return displayMessages
    }

    static func enable() {
        JupyterKernel.communicator.handleParentMessage(updateParentMessage)
        JupyterKernel.communicator.afterSuccessfulExecution(run: consumeDisplayMessages)
    }
}

func display(base64EncodedPNG: String) {
    let pngData = JupyterDisplay.PNGImageData(base64EncodedPNG: base64EncodedPNG)
    let data = JupyterDisplay.MessageContent(data: pngData).json
    JupyterDisplay.messages.append(JupyterDisplay.Message(content: data))
}

#if canImport(SwiftPlot)
import SwiftPlot
import AGGRenderer
var __agg_renderer = AGGRenderer()
extension Plot {
  func display(size: Size = Size(width: 1000, height: 660)) {
    drawGraph(size: size, renderer: __agg_renderer)
    display(base64EncodedPNG: __agg_renderer.base64Png())
  }
}
#endif

JupyterDisplay.enable()
