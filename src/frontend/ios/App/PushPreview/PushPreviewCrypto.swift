import CryptoKit
import Foundation

/**
 * Decrypts the notification preview the backend seals for this device
 * (core/services/push/preview.py): RFC 8291 message encryption ("aes128gcm",
 * the Web Push scheme) of a padded JSON `{"sender": …, "subject": …}`.
 *
 * Compiled into both the app and the Notification Service Extension.
 */
enum PushPreviewCrypto {
    enum Failure: Error {
        case malformedRecord
        case unsupportedRecordSize
        case badPadding
    }

    private static let saltLength = 16
    private static let headerLength = saltLength + 4 + 1
    private static let publicKeyLength = 65
    private static let tagLength = 16

    /// Opens one aes128gcm record sealed for `privateKey` / `authSecret`.
    static func decrypt(
        _ record: Data,
        privateKey: P256.KeyAgreement.PrivateKey,
        authSecret: Data
    ) throws -> Data {
        let bytes = [UInt8](record)
        // RFC 8188 header: salt (16) | record size (4, big endian) | idlen (1)
        // | keyid — for RFC 8291 the keyid is the sender's ephemeral public key.
        guard bytes.count > headerLength else { throw Failure.malformedRecord }
        let salt = Data(bytes[0..<saltLength])
        let recordSize = bytes[16..<20].reduce(0) { ($0 << 8) | Int($1) }
        let keyIdLength = Int(bytes[20])
        guard keyIdLength == publicKeyLength,
              bytes.count >= headerLength + keyIdLength + tagLength
        else { throw Failure.malformedRecord }
        let senderPublic = Data(bytes[headerLength..<headerLength + keyIdLength])
        let ciphertext = Data(bytes[(headerLength + keyIdLength)...])
        // The preview always fits a single record (it is padded to ≤ 2 KB).
        guard ciphertext.count <= recordSize else { throw Failure.unsupportedRecordSize }

        let sharedSecret = try privateKey.sharedSecretFromKeyAgreement(
            with: P256.KeyAgreement.PublicKey(x963Representation: senderPublic)
        )
        let keyInfo = Data("WebPush: info".utf8) + [0]
            + privateKey.publicKey.x963Representation + senderPublic
        let ikm = sharedSecret.hkdfDerivedSymmetricKey(
            using: SHA256.self, salt: authSecret, sharedInfo: keyInfo, outputByteCount: 32
        )
        let contentKey = HKDF<SHA256>.deriveKey(
            inputKeyMaterial: ikm,
            salt: salt,
            info: Data("Content-Encoding: aes128gcm".utf8) + [0],
            outputByteCount: 16
        )
        let nonce = HKDF<SHA256>.deriveKey(
            inputKeyMaterial: ikm,
            salt: salt,
            info: Data("Content-Encoding: nonce".utf8) + [0],
            outputByteCount: 12
        ).withUnsafeBytes { Data($0) }

        let padded = try AES.GCM.open(
            AES.GCM.SealedBox(
                nonce: AES.GCM.Nonce(data: nonce),
                ciphertext: ciphertext.dropLast(tagLength),
                tag: ciphertext.suffix(tagLength)
            ),
            using: contentKey
        )
        // Last-record padding: the content, a 0x02 delimiter, then zeros.
        guard let delimiter = padded.lastIndex(where: { $0 != 0 }),
              padded[delimiter] == 2
        else { throw Failure.badPadding }
        return Data(padded[padded.startIndex..<delimiter])
    }

    /// Short id of a public key: base64url(sha256(uncompressed point)[0..<8]),
    /// sent in clear as `kid` next to the sealed preview.
    static func keyId(publicKey: P256.KeyAgreement.PublicKey) -> String {
        Data(SHA256.hash(data: publicKey.x963Representation).prefix(8)).base64URLEncoded()
    }
}

extension Data {
    func base64URLEncoded() -> String {
        base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    init?(base64URLEncoded value: String) {
        var base64 = value
            .replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        base64 += String(repeating: "=", count: (4 - base64.count % 4) % 4)
        self.init(base64Encoded: base64)
    }
}
