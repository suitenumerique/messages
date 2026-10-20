package local.suitenumerique.messages;

import java.math.BigInteger;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.KeyFactory;
import java.security.MessageDigest;
import java.security.interfaces.ECPrivateKey;
import java.security.interfaces.ECPublicKey;
import java.security.spec.ECParameterSpec;
import java.security.spec.ECPoint;
import java.security.spec.ECPublicKeySpec;
import java.util.Arrays;

import javax.crypto.Cipher;
import javax.crypto.KeyAgreement;
import javax.crypto.Mac;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/**
 * Decrypts the notification preview the backend seals for this device
 * (core/services/push/preview.py): RFC 8291 message encryption ("aes128gcm",
 * the Web Push scheme) of a padded JSON {"sender": …, "subject": …}.
 *
 * Plain JCA, no Android API, so it is unit-tested on the JVM
 * (PushPreviewCryptoTest) against a vector sealed by the backend.
 */
final class PushPreviewCrypto {

    private static final int SALT_LENGTH = 16;
    private static final int HEADER_LENGTH = SALT_LENGTH + 4 + 1;
    private static final int PUBLIC_KEY_LENGTH = 65;
    private static final int TAG_LENGTH = 16;

    private PushPreviewCrypto() {}

    /** Opens one aes128gcm record sealed for {@code privateKey} / {@code authSecret}. */
    static byte[] decrypt(byte[] record, ECPrivateKey privateKey, ECPublicKey publicKey, byte[] authSecret)
        throws GeneralSecurityException {
        // RFC 8188 header: salt (16) | record size (4, big endian) | idlen (1)
        // | keyid — for RFC 8291 the keyid is the sender's ephemeral public key.
        if (record.length <= HEADER_LENGTH) {
            throw new GeneralSecurityException("Malformed record");
        }
        byte[] salt = Arrays.copyOfRange(record, 0, SALT_LENGTH);
        int recordSize = ByteBuffer.wrap(record, SALT_LENGTH, 4).getInt();
        int keyIdLength = record[20] & 0xff;
        if (keyIdLength != PUBLIC_KEY_LENGTH || record.length < HEADER_LENGTH + keyIdLength + TAG_LENGTH) {
            throw new GeneralSecurityException("Malformed record");
        }
        byte[] senderPublic = Arrays.copyOfRange(record, HEADER_LENGTH, HEADER_LENGTH + keyIdLength);
        byte[] ciphertext = Arrays.copyOfRange(record, HEADER_LENGTH + keyIdLength, record.length);
        // The preview always fits a single record (it is padded to <= 2 KB).
        if (ciphertext.length > recordSize) {
            throw new GeneralSecurityException("Multi-record content is not supported");
        }

        KeyAgreement agreement = KeyAgreement.getInstance("ECDH");
        agreement.init(privateKey);
        agreement.doPhase(decodePublicKey(senderPublic, publicKey.getParams()), true);
        byte[] sharedSecret = agreement.generateSecret();

        byte[] keyInfo = concat(
            "WebPush: info".getBytes(StandardCharsets.US_ASCII),
            new byte[] {0},
            encodePublicKey(publicKey),
            senderPublic
        );
        byte[] ikm = hkdf(authSecret, sharedSecret, keyInfo, 32);
        byte[] contentKey = hkdf(salt, ikm, info("Content-Encoding: aes128gcm"), 16);
        byte[] nonce = hkdf(salt, ikm, info("Content-Encoding: nonce"), 12);

        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, new SecretKeySpec(contentKey, "AES"), new GCMParameterSpec(TAG_LENGTH * 8, nonce));
        byte[] padded = cipher.doFinal(ciphertext);

        // Last-record padding: the content, a 0x02 delimiter, then zeros.
        int delimiter = padded.length - 1;
        while (delimiter >= 0 && padded[delimiter] == 0) {
            delimiter--;
        }
        if (delimiter < 0 || padded[delimiter] != 2) {
            throw new GeneralSecurityException("Bad padding");
        }
        return Arrays.copyOf(padded, delimiter);
    }

    /** Uncompressed point (0x04 | X | Y) of a P-256 public key. */
    static byte[] encodePublicKey(ECPublicKey key) {
        byte[] point = new byte[PUBLIC_KEY_LENGTH];
        point[0] = 0x04;
        writeCoordinate(key.getW().getAffineX(), point, 1);
        writeCoordinate(key.getW().getAffineY(), point, 33);
        return point;
    }

    /** Short id of a public key: the first 8 bytes of sha256(uncompressed point),
     * sent base64url-encoded as {@code kid} next to the sealed preview. */
    static byte[] keyId(ECPublicKey key) throws GeneralSecurityException {
        return Arrays.copyOf(MessageDigest.getInstance("SHA-256").digest(encodePublicKey(key)), 8);
    }

    private static ECPublicKey decodePublicKey(byte[] point, ECParameterSpec params) throws GeneralSecurityException {
        if (point.length != PUBLIC_KEY_LENGTH || point[0] != 0x04) {
            throw new GeneralSecurityException("Not an uncompressed P-256 point");
        }
        ECPoint w = new ECPoint(
            new BigInteger(1, Arrays.copyOfRange(point, 1, 33)),
            new BigInteger(1, Arrays.copyOfRange(point, 33, 65))
        );
        return (ECPublicKey) KeyFactory.getInstance("EC").generatePublic(new ECPublicKeySpec(w, params));
    }

    private static void writeCoordinate(BigInteger value, byte[] out, int offset) {
        byte[] raw = value.toByteArray();
        // toByteArray() may add a sign byte or drop leading zeros.
        int length = Math.min(raw.length, 32);
        System.arraycopy(raw, raw.length - length, out, offset + 32 - length, length);
    }

    /** RFC 5869 HKDF-SHA256, for an output no longer than one hash block. */
    private static byte[] hkdf(byte[] salt, byte[] ikm, byte[] info, int length) throws GeneralSecurityException {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(salt, "HmacSHA256"));
        byte[] prk = mac.doFinal(ikm);
        mac.init(new SecretKeySpec(prk, "HmacSHA256"));
        mac.update(info);
        mac.update((byte) 1);
        return Arrays.copyOf(mac.doFinal(), length);
    }

    private static byte[] info(String label) {
        return concat(label.getBytes(StandardCharsets.US_ASCII), new byte[] {0});
    }

    private static byte[] concat(byte[]... parts) {
        int length = 0;
        for (byte[] part : parts) {
            length += part.length;
        }
        byte[] out = new byte[length];
        int offset = 0;
        for (byte[] part : parts) {
            System.arraycopy(part, 0, out, offset, part.length);
            offset += part.length;
        }
        return out;
    }
}
