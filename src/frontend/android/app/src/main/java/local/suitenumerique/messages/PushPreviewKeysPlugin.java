package local.suitenumerique.messages;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * Hands the web app the public key notification previews must be sealed for
 * (features/native/push.ts registers it with the device), rotating it when
 * due, and wipes the keyring on a voluntary logout / device sign-out.
 * The private keys never leave the device keyring (see PushPreviewKeyring).
 */
@CapacitorPlugin(name = "PushPreviewKeys")
public class PushPreviewKeysPlugin extends Plugin {

    @PluginMethod
    public void getKeys(PluginCall call) {
        String userId = call.getString("userId");
        if (userId == null || userId.isEmpty()) {
            call.reject("userId is required");
            return;
        }
        try {
            String[] keys = new PushPreviewKeyring(getContext()).currentPublicKeys(userId, System.currentTimeMillis());
            JSObject result = new JSObject();
            result.put("p256dh", keys[0]);
            result.put("auth", keys[1]);
            call.resolve(result);
        } catch (Exception e) {
            call.reject("Unable to provide the notification preview keys");
        }
    }

    @PluginMethod
    public void clear(PluginCall call) {
        new PushPreviewKeyring(getContext()).clear();
        call.resolve();
    }
}
