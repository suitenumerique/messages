package local.suitenumerique.messages;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.security.GeneralSecurityException;
import java.security.KeyFactory;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.KeyStore;
import java.security.SecureRandom;
import java.security.interfaces.ECPrivateKey;
import java.security.interfaces.ECPublicKey;
import java.security.spec.ECGenParameterSpec;
import java.security.spec.PKCS8EncodedKeySpec;
import java.security.spec.X509EncodedKeySpec;
import java.util.Arrays;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/**
 * The key pairs notification previews are sealed for, shared by the
 * PushPreviewKeys plugin (which rotates them and hands the current public key
 * to the web app for registration) and PushPreviewMessagingService (which
 * only reads them to decrypt).
 *
 * Rotation contract (docs/push-notifications.md §5.1): a new pair every
 * ROTATION_INTERVAL_MS; the previous private key is kept GRACE_PERIOD_MS
 * longer — longer than the backend's PUSH_TTL_SECONDS (1 day), after which no
 * push sealed for it can still arrive. The keyring belongs to one account:
 * another account registering on the device starts from scratch.
 *
 * Secrets (private keys, auth secrets) are stored encrypted by a
 * non-exportable AES key of the Android Keystore; the app is excluded from
 * backups (android:allowBackup="false"). ECDH itself runs in software: the
 * Keystore only offers key agreement from Android 12, the app supports 7.
 */
final class PushPreviewKeyring {

    static final long ROTATION_INTERVAL_MS = 30L * 24 * 3600 * 1000;
    static final long GRACE_PERIOD_MS = 2L * 24 * 3600 * 1000;

    private static final String PREFERENCES = "push_preview_keyring";
    private static final String RING = "ring";
    private static final String WRAPPING_KEY_ALIAS = "push_preview_wrap";
    private static final String KEYSTORE = "AndroidKeyStore";
    private static final int GCM_IV_LENGTH = 12;
    private static final int URL_SAFE = Base64.URL_SAFE | Base64.NO_WRAP | Base64.NO_PADDING;

    // The plugin and the messaging service each hold their own instance.
    private static final Object LOCK = new Object();

    static final class Key {
        final ECPrivateKey privateKey;
        final ECPublicKey publicKey;
        final byte[] authSecret;

        Key(ECPrivateKey privateKey, ECPublicKey publicKey, byte[] authSecret) {
            this.privateKey = privateKey;
            this.publicKey = publicKey;
            this.authSecret = authSecret;
        }
    }

    private final SharedPreferences preferences;

    PushPreviewKeyring(Context context) {
        preferences = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE);
    }

    /** Rotates if due and returns the {p256dh, auth} pair to register for {@code userId}. */
    String[] currentPublicKeys(String userId, long now) throws GeneralSecurityException, JSONException {
        synchronized (LOCK) {
            JSONObject stored = load();
            JSONArray storedKeys = stored != null && userId.equals(stored.optString("userId"))
                ? stored.getJSONArray("keys")
                // Another account's keys are dropped outright, grace period included.
                : new JSONArray();

            JSONArray keys = new JSONArray();
            for (int i = 0; i < storedKeys.length(); i++) {
                JSONObject key = storedKeys.getJSONObject(i);
                if (isHeld(key, now)) {
                    keys.put(key);
                }
            }
            JSONObject current = keys.length() > 0 ? keys.getJSONObject(0) : null;
            boolean currentIsFresh = current != null
                && !current.has("retiredAt")
                && now - current.getLong("createdAt") < ROTATION_INTERVAL_MS;

            if (!currentIsFresh) {
                for (int i = 0; i < keys.length(); i++) {
                    if (!keys.getJSONObject(i).has("retiredAt")) {
                        keys.getJSONObject(i).put("retiredAt", now);
                    }
                }
                current = generate(now);
                JSONArray rotated = new JSONArray().put(current);
                for (int i = 0; i < keys.length(); i++) {
                    rotated.put(keys.getJSONObject(i));
                }
                keys = rotated;
            }
            preferences.edit()
                .putString(RING, new JSONObject().put("userId", userId).put("keys", keys).toString())
                .apply();

            Key key = decode(current);
            return new String[] {
                Base64.encodeToString(PushPreviewCrypto.encodePublicKey(key.publicKey), URL_SAFE),
                Base64.encodeToString(key.authSecret, URL_SAFE),
            };
        }
    }

    /**
     * The key a preview was sealed for, if still held (current or in grace), else
     * null. A key past its grace period no longer opens anything, even before the
     * app's next launch deletes it.
     */
    Key key(String kid, long now) {
        synchronized (LOCK) {
            try {
                JSONObject stored = load();
                if (stored == null) {
                    return null;
                }
                JSONArray keys = stored.getJSONArray("keys");
                for (int i = 0; i < keys.length(); i++) {
                    JSONObject entry = keys.getJSONObject(i);
                    if (kid.equals(entry.optString("kid")) && isHeld(entry, now)) {
                        return decode(entry);
                    }
                }
            } catch (GeneralSecurityException | JSONException e) {
                // Unreadable keyring (e.g. Keystore key lost): no preview.
            }
            return null;
        }
    }

    /** Current, or retired less than GRACE_PERIOD_MS ago. */
    static boolean isHeld(JSONObject key, long now) throws JSONException {
        return !key.has("retiredAt") || now - key.getLong("retiredAt") < GRACE_PERIOD_MS;
    }

    void clear() {
        synchronized (LOCK) {
            preferences.edit().remove(RING).apply();
        }
    }

    private JSONObject load() throws JSONException {
        String raw = preferences.getString(RING, null);
        return raw == null ? null : new JSONObject(raw);
    }

    private JSONObject generate(long now) throws GeneralSecurityException, JSONException {
        KeyPairGenerator generator = KeyPairGenerator.getInstance("EC");
        generator.initialize(new ECGenParameterSpec("secp256r1"));
        KeyPair pair = generator.generateKeyPair();
        byte[] authSecret = new byte[16];
        new SecureRandom().nextBytes(authSecret);
        ECPublicKey publicKey = (ECPublicKey) pair.getPublic();
        return new JSONObject()
            .put("kid", Base64.encodeToString(PushPreviewCrypto.keyId(publicKey), URL_SAFE))
            .put("publicKey", Base64.encodeToString(publicKey.getEncoded(), Base64.NO_WRAP))
            .put("privateKey", wrap(pair.getPrivate().getEncoded()))
            .put("auth", wrap(authSecret))
            .put("createdAt", now);
    }

    private Key decode(JSONObject entry) throws GeneralSecurityException, JSONException {
        KeyFactory factory = KeyFactory.getInstance("EC");
        return new Key(
            (ECPrivateKey) factory.generatePrivate(new PKCS8EncodedKeySpec(unwrap(entry.getString("privateKey")))),
            (ECPublicKey) factory.generatePublic(
                new X509EncodedKeySpec(Base64.decode(entry.getString("publicKey"), Base64.NO_WRAP))
            ),
            unwrap(entry.getString("auth"))
        );
    }

    private static String wrap(byte[] secret) throws GeneralSecurityException {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, wrappingKey());
        byte[] iv = cipher.getIV();
        byte[] sealed = cipher.doFinal(secret);
        byte[] out = Arrays.copyOf(iv, iv.length + sealed.length);
        System.arraycopy(sealed, 0, out, iv.length, sealed.length);
        return Base64.encodeToString(out, Base64.NO_WRAP);
    }

    private static byte[] unwrap(String value) throws GeneralSecurityException {
        byte[] raw = Base64.decode(value, Base64.NO_WRAP);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(
            Cipher.DECRYPT_MODE,
            wrappingKey(),
            new GCMParameterSpec(128, Arrays.copyOfRange(raw, 0, GCM_IV_LENGTH))
        );
        return cipher.doFinal(raw, GCM_IV_LENGTH, raw.length - GCM_IV_LENGTH);
    }

    private static SecretKey wrappingKey() throws GeneralSecurityException {
        KeyStore keyStore = KeyStore.getInstance(KEYSTORE);
        try {
            keyStore.load(null);
        } catch (java.io.IOException e) {
            throw new GeneralSecurityException(e);
        }
        java.security.Key existing = keyStore.getKey(WRAPPING_KEY_ALIAS, null);
        if (existing instanceof SecretKey) {
            return (SecretKey) existing;
        }
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE);
        generator.init(
            new KeyGenParameterSpec.Builder(
                WRAPPING_KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build()
        );
        return generator.generateKey();
    }
}
