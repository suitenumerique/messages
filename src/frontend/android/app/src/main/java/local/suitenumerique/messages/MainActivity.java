package local.suitenumerique.messages;

import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.webkit.WebView;

import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowInsetsCompat;

import com.getcapacitor.BridgeActivity;
import com.getcapacitor.WebViewListener;

import java.util.Locale;

public class MainActivity extends BridgeActivity {

    /**
     * Publish the device safe-area insets as CSS variables, and shrink the
     * webview for the on-screen keyboard on the Android versions where the
     * system no longer does it.
     *
     * Capacitor 8 normally does both itself (SystemBars insetsHandling=css),
     * but its listener also re-applies the keyboard inset on top of the
     * system window resize, shrinking the webview by twice the keyboard
     * height on Android < 15 (capacitor#8181) — so it is disabled in
     * capacitor.config.ts. This listener replaces the two parts we need, and
     * only those: the CSS variables, and a keyboard inset applied strictly
     * where windowSoftInputMode=adjustResize stops working.
     */
    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        WebView webView = bridge.getWebView();
        float density = getResources().getDisplayMetrics().density;

        ViewCompat.setOnApplyWindowInsetsListener((View) webView.getParent(), (v, insets) -> {
            Insets bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout()
            );
            boolean keyboardVisible = insets.isVisible(WindowInsetsCompat.Type.ime());

            // Android 15+ draws every app edge to edge whatever it asks for,
            // and an edge-to-edge window is never resized by the keyboard:
            // adjustResize is dead there, so the webview keeps its full height
            // and anything pinned to its bottom (the composer toolbar) hides
            // behind the keyboard. Resize it here instead. Below 15 the system
            // still resizes the window, and padding here would shrink it twice
            // — that is capacitor#8181, which the disabled listener fixed.
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.VANILLA_ICE_CREAM) {
                int imeBottom = insets.getInsets(WindowInsetsCompat.Type.ime()).bottom;
                v.setPadding(0, 0, 0, keyboardVisible ? imeBottom : 0);
            }

            // The keyboard sits over the gesture bar, so an open keyboard
            // leaves no bottom system bar to clear: keeping the inset would
            // float the bars above it by the height of a navigation bar that
            // is no longer visible.
            float bottom = keyboardVisible ? 0f : bars.bottom / density;
            String js = String.format(
                Locale.US,
                "document.documentElement.style.setProperty('--safe-area-inset-top','%.1fpx');" +
                "document.documentElement.style.setProperty('--safe-area-inset-right','%.1fpx');" +
                "document.documentElement.style.setProperty('--safe-area-inset-bottom','%.1fpx');" +
                "document.documentElement.style.setProperty('--safe-area-inset-left','%.1fpx');",
                bars.top / density,
                bars.right / density,
                bottom,
                bars.left / density
            );
            webView.post(() -> webView.evaluateJavascript(js, null));
            return insets;
        });

        // Inline styles do not survive a document swap: re-request an insets
        // pass on every navigation (initial load, OTA bundle reload).
        bridge.addWebViewListener(
            new WebViewListener() {
                @Override
                public void onPageCommitVisible(WebView view, String url) {
                    view.requestApplyInsets();
                }
            }
        );
    }
}
