import Cryptor
import Foundation

struct Header: Codable {
    let msg_id: String
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
    let msg_type: String
    let version: String

    public init(msg_id: String = UUID().uuidString, username: String = "kernel", session: String, msg_type: String = "display_data", version: String = "5.2") {
        self.msg_id = msg_id
        self.username = username
        self.session = session
        self.msg_type = msg_type
        self.version = version
    }

    public func toJSON() -> String {
        do {
            let jsonData = try JSONEncoder().encode(self)
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
        let data = Data((header.toJSON()+parentHeader+metaData+content).utf8)
        let keyData = Data(key.utf8)
        let dataHex = data.map{String(format: "%02x", $0)}.joined()
        let keyHex = keyData.map{String(format: "%02x", $0)}.joined()
        let hmacKey = CryptoUtils.byteArray(fromHex: keyHex)
        let hmacData: [UInt8] = CryptoUtils.byteArray(fromHex: dataHex)
        let hmac = HMAC(using: HMAC.Algorithm.sha256, key: hmacKey).update(byteArray: hmacData)?.final()
        return CryptoUtils.hexString(from: hmac!)
    }
    var messageParts: [KernelCommunicator.BytesReference] {
        var parts = [KernelCommunicator.BytesReference]()
        parts.append(bytes(msgType))
        parts.append(bytes(delimiter))
        parts.append(bytes(hmacSignature))
        parts.append(bytes(header.toJSON()))
        parts.append(bytes(parentHeader))
        parts.append(bytes(metaData))
        parts.append(bytes(content))
        return parts
    }
    init(content: String = "{}") {
        header = Header(username: JupyterKernel.communicator.jupyterSession.username, session: JupyterKernel.communicator.jupyterSession.id)
        self.content = content
        key = JupyterKernel.communicator.jupyterSession.key
    }
}

enum JupyterDisplay {
    static var parentHeader: String = ""
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
    let data = "{\"data\":{\"image/png\":\""+base64EncodedPNG+"\\n\",\"text/plain\":\"<IPython.core.display.Image object>\"},\"metadata\":{},\"transient\":{}}"
    JupyterDisplay.messages.append(Message(content: data))
}
