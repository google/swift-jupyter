#if canImport(Cryptor)
import Cryptor
#endif

import Foundation

enum JupyterDisplay {
    struct Header: Codable {
        let msgId: String
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
        let msgType: String
        let version: String

        public init(
            msgId: String = UUID().uuidString,
            username: String = "kernel",
            session: String,
            msgType: String = "display_data",
            version: String = "5.2"
            ) {
            self.msgId = msgId
            self.username = username
            self.session = session
            self.msgType = msgType
            self.version = version
        }

        public func toJSON() -> String {
            do {
                let encoder = JSONEncoder()
                encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
                encoder.keyEncodingStrategy = .convertToSnakeCase
                let jsonData = try encoder.encode(self)
                let jsonString = String(data: jsonData, encoding: .utf8)!
                return jsonString
            }
            catch {
                return "{}"
            }
        }
    }

    struct Message {
        var msgType: String = "display_data"
        var delimiter = "<IDS|MSG>"
        var key: String = ""
        var header: Header
        var metaData: String = "{}"
        var content: String = "{}"
        var hmacSignature: String {
            #if canImport(Cryptor)
            let data = Data((header.toJSON()+parentHeader+metaData+content).utf8)
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
                bytes(msgType),
                bytes(delimiter),
                bytes(hmacSignature),
                bytes(header.toJSON()),
                bytes(parentHeader),
                bytes(metaData),
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
            if (!key.isEmpty) {
                fatalError("""
                            Unable to import Cryptor to perform message signing.
                            Add Cryptor as a dependency, or disable message signing in Jupyter as follows:
                            jupyter notebook --Session.key='b\"\"'\n
                            """
                )
            }
            #endif
        }
    }

    struct PNGImageData: Codable {
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

    struct MessageContent: Codable {
        let metadata = "{}"
        let transient = "{}"
        let data: PNGImageData
        init(base64EncodedPNG: String) {
            data = PNGImageData(base64EncodedPNG: base64EncodedPNG)
        }
        public func toJSON() -> String {
            do {
                let encoder = JSONEncoder()
                encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
                encoder.keyEncodingStrategy = .convertToSnakeCase
                let jsonData = try encoder.encode(self)
                var jsonString = String(data: jsonData, encoding: .utf8)!
                return jsonString
            } catch {
                return "{}"
            }
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
            let headerData = try JSONSerialization.data(withJSONObject: jsonDict!["header"], options: .prettyPrinted)
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

JupyterDisplay.enable()

func display(base64EncodedPNG: String) {
    let data = JupyterDisplay.MessageContent(base64EncodedPNG: base64EncodedPNG).toJSON()
    JupyterDisplay.messages.append(JupyterDisplay.Message(content: data))
}
