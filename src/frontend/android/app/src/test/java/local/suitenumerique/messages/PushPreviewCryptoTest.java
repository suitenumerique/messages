package local.suitenumerique.messages;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.security.AlgorithmParameters;
import java.security.GeneralSecurityException;
import java.security.KeyFactory;
import java.security.interfaces.ECPrivateKey;
import java.security.interfaces.ECPublicKey;
import java.security.spec.ECGenParameterSpec;
import java.security.spec.ECParameterSpec;
import java.security.spec.ECPoint;
import java.security.spec.ECPrivateKeySpec;
import java.security.spec.ECPublicKeySpec;
import java.util.Arrays;
import java.util.Base64;

import org.junit.Test;

/**
 * Decrypts a preview sealed by the backend itself (core/services/push/preview.py,
 * encrypt_for_device over pad_json) for a known device key: if the two sides
 * drift on the RFC 8291 derivation, every preview silently falls back to the
 * generic banner, so pin it with a real vector.
 */
public class PushPreviewCryptoTest {

    private static final String PRIVATE_SCALAR = "ce178813259021301a4fc7c03205eca03f8e7af25795a1d2df8072a529a7a09e";
    private static final String P256DH = "BLdA4QEv5IekZjQVZ5IT6dKlTSdZvauMdgzXOE6NlIGA9Ane7jeeAlgw5XvTO54ma8H96oCj2aBzads2W4BsLBg";
    private static final String AUTH = "NwkJ6eYO9mOlVGfsEU4XFQ";
    private static final String KID = "dpnUgAaUJac";
    private static final String ENC =
        "W6AAvfyO_cpEA01PYCSUzQAAEABBBBjsqSAvvPY_OPGQcKyyr_bF3Aj7KkjU1VVj2ckm1uLNyG6mj8DpKHsRy3JSkrAKFOMHc_5YMmFdDXaKTm5kmZgZiNUTWoXdlxo92mdakBajuOAgUzJ7fBSAwHAk16EcqhEQAe_n75lzCb2yh9zUeolkeF6iPLNrCnBs-xGZmiBBLB19ypjwHhwUKahsWuG9x6CNSgGrUuVF-a5cRz0mhGIacr28DUoE6NNJHFiqUu8VCQpop2PuCMCYNAR-YvtKVgfTcQ8ySFCyMBXTIg-MkakhYJlkuyBKKEKmfkyWrzu5r2hG94Zr-dJzvPGjpwiMwPBAHlCBcFGGEi0bsTyZ7B2ESOmbW243Eu7rTVIcNhWCE-cRYA5NiELBaMEy_cAzhSPGCwT1ibgS8XiUX6gQ8jPgky-9cwfuRjWZZJFLgeJc_RWIshXLAUont1joVkyJKeahHFWK92ar-LiuBlyLwluQfVgnta93hcIoKejlkdMjl9DfLdDDPfxBA-Eiv8NEtw2XLDnnfx2i29MFT-db5p9MlmSNenO2s6X2TQX9kcZWh7notqJnbTdAS8rbbWVY9E3UNO6nYl779VIefc3H0yr7QD6J3PTSWLEuYIG7IGjAXENQsBAgawYBSrx7QmqWJGiYMvTApjvmQp3OfUg3cYHwltfineVVoFlJiyso3Rni2j77UCqk36VTVqiYzYlnbcU4I_7W4rZnnCxDUBcN3eA0E_0eOkykQqAjpwZc2NIEPAQ5DkXDc9LWmWYfw2xX2s10qAg_kdsMMXDqJYDLah3K";
    private static final String PREVIEW = "{\"sender\":\"Alice Été\",\"subject\":\"Réunion 😀\"}";
    /** pad_json's smallest bucket. */
    private static final int PADDED_LENGTH = 512;

    private static final Base64.Decoder URL = Base64.getUrlDecoder();

    @Test
    public void decryptsThePreviewSealedByTheBackend() throws Exception {
        byte[] plaintext = PushPreviewCrypto.decrypt(URL.decode(ENC), privateKey(), publicKey(), URL.decode(AUTH));

        assertEquals(PADDED_LENGTH, plaintext.length);
        assertEquals(PREVIEW, new String(plaintext, StandardCharsets.UTF_8).trim());
    }

    @Test
    public void computesTheBackendKeyId() throws Exception {
        assertArrayEquals(URL.decode(KID), PushPreviewCrypto.keyId(publicKey()));
        assertArrayEquals(URL.decode(P256DH), PushPreviewCrypto.encodePublicKey(publicKey()));
    }

    @Test
    public void rejectsATamperedRecord() throws Exception {
        byte[] record = URL.decode(ENC);
        record[record.length - 1] ^= 1;
        assertThrows(
            GeneralSecurityException.class,
            () -> PushPreviewCrypto.decrypt(record, privateKey(), publicKey(), URL.decode(AUTH))
        );
    }

    @Test
    public void rejectsTheWrongAuthSecret() throws Exception {
        assertThrows(
            GeneralSecurityException.class,
            () -> PushPreviewCrypto.decrypt(URL.decode(ENC), privateKey(), publicKey(), new byte[16])
        );
    }

    private static ECParameterSpec p256() throws GeneralSecurityException {
        AlgorithmParameters parameters = AlgorithmParameters.getInstance("EC");
        parameters.init(new ECGenParameterSpec("secp256r1"));
        return parameters.getParameterSpec(ECParameterSpec.class);
    }

    private static ECPrivateKey privateKey() throws GeneralSecurityException {
        return (ECPrivateKey) KeyFactory.getInstance("EC")
            .generatePrivate(new ECPrivateKeySpec(new BigInteger(PRIVATE_SCALAR, 16), p256()));
    }

    private static ECPublicKey publicKey() throws GeneralSecurityException {
        byte[] point = URL.decode(P256DH);
        ECPoint w = new ECPoint(
            new BigInteger(1, Arrays.copyOfRange(point, 1, 33)),
            new BigInteger(1, Arrays.copyOfRange(point, 33, 65))
        );
        return (ECPublicKey) KeyFactory.getInstance("EC").generatePublic(new ECPublicKeySpec(w, p256()));
    }
}
