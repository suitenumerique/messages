package local.suitenumerique.messages;

import android.app.ActivityManager;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Intent;
import android.os.Build;
import android.util.Base64;

import androidx.annotation.NonNull;
import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;

import com.capacitorjs.plugins.pushnotifications.MessagingService;
import com.google.firebase.messaging.RemoteMessage;

import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.util.Map;

/**
 * Renders the pushes that carry a sealed notification preview.
 *
 * Those arrive data-only (core/services/push/fcm.py): with a notification
 * block Android would display it itself, without ever running app code that
 * could decrypt the preview. So this service posts the banner — sender and
 * subject when the preview opens with a key of the device keyring
 * (PushPreviewKeyring), the generic strings otherwise — exactly once per push,
 * which Android requires of high-priority data messages.
 *
 * It replaces the push plugin's own service in the manifest and extends it, so
 * the plugin keeps receiving tokens and forwarding pushes to the web app.
 * Pushes with a notification block are still rendered by the system, and, like
 * them, nothing is posted while the app is in the foreground (docs §6).
 */
public class PushPreviewMessagingService extends MessagingService {

    /** Mirror of ANDROID_NOTIFICATION_CHANNEL_ID (features/native/push.ts). */
    static final String CHANNEL_ID = "new_messages";

    @Override
    public void onMessageReceived(@NonNull RemoteMessage message) {
        super.onMessageReceived(message);
        Map<String, String> data = message.getData();
        if (message.getNotification() != null || !"new_message".equals(data.get("type")) || isInForeground()) {
            return;
        }
        NotificationManagerCompat manager = NotificationManagerCompat.from(this);
        if (!manager.areNotificationsEnabled()) {
            return;
        }
        ensureChannel();

        String genericTitle = getString(R.string.new_message_title);
        String genericBody = getString(R.string.new_message_body);
        String title = genericTitle;
        String body = genericBody;
        JSONObject preview = openPreview(data);
        if (preview != null) {
            String sender = preview.optString("sender");
            title = sender.isEmpty() ? title : sender;
            body = preview.optString("subject");
        }

        String threadTag = "thread-" + data.get("thread_id");
        NotificationCompat.Builder notification = new NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_notification)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(new NotificationCompat.BigTextStyle().bigText(body))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .setContentIntent(tapIntent(message, threadTag))
            // Sender and subject are sensitive: on a lock screen set to hide
            // sensitive content, Android shows this public version — the
            // generic banner — instead of a bare "Contents hidden".
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setPublicVersion(
                new NotificationCompat.Builder(this, CHANNEL_ID)
                    .setSmallIcon(R.drawable.ic_stat_notification)
                    .setContentTitle(genericTitle)
                    .setContentText(genericBody)
                    .build()
            );
        String unreadCount = data.get("unread_count");
        if (unreadCount != null) {
            try {
                notification.setNumber(Integer.parseInt(unreadCount));
            } catch (NumberFormatException e) {
                // No badge count.
            }
        }
        try {
            // One banner per thread, superseded by the next message (the
            // collapse key's on-device counterpart).
            manager.notify(threadTag, 0, notification.build());
        } catch (SecurityException e) {
            // Notification permission revoked meanwhile.
        }
    }

    private JSONObject openPreview(Map<String, String> data) {
        String enc = data.get("enc");
        String kid = data.get("kid");
        if (enc == null || kid == null) {
            return null;
        }
        PushPreviewKeyring.Key key = new PushPreviewKeyring(this).key(kid);
        if (key == null) {
            return null;
        }
        try {
            byte[] plaintext = PushPreviewCrypto.decrypt(
                Base64.decode(enc, Base64.URL_SAFE | Base64.NO_WRAP | Base64.NO_PADDING),
                key.privateKey,
                key.publicKey,
                key.authSecret
            );
            // Trailing padding spaces are valid JSON whitespace.
            return new JSONObject(new String(plaintext, StandardCharsets.UTF_8));
        } catch (Exception e) {
            return null;
        }
    }

    /**
     * Opens the app the way a system-rendered FCM banner does: the push data
     * as extras plus "google.message_id", which the push plugin turns into the
     * pushNotificationActionPerformed event the web app routes from.
     */
    private PendingIntent tapIntent(RemoteMessage message, String threadTag) {
        Intent intent = getPackageManager().getLaunchIntentForPackage(getPackageName());
        if (intent == null) {
            intent = new Intent(this, MainActivity.class);
        }
        intent.addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        for (Map.Entry<String, String> entry : message.getData().entrySet()) {
            if (!"enc".equals(entry.getKey()) && !"kid".equals(entry.getKey())) {
                intent.putExtra(entry.getKey(), entry.getValue());
            }
        }
        String messageId = message.getMessageId();
        intent.putExtra("google.message_id", messageId != null ? messageId : threadTag);
        return PendingIntent.getActivity(
            this,
            threadTag.hashCode(),
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
    }

    /** The web app creates the channel when push is enabled; recreate it if
     * it is missing, or Android 8+ would drop the notification. */
    private void ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null && manager.getNotificationChannel(CHANNEL_ID) == null) {
            manager.createNotificationChannel(
                new NotificationChannel(
                    CHANNEL_ID,
                    getString(R.string.new_messages_channel_name),
                    NotificationManager.IMPORTANCE_HIGH
                )
            );
        }
    }

    private static boolean isInForeground() {
        ActivityManager.RunningAppProcessInfo state = new ActivityManager.RunningAppProcessInfo();
        ActivityManager.getMyMemoryState(state);
        return state.importance == ActivityManager.RunningAppProcessInfo.IMPORTANCE_FOREGROUND;
    }
}
