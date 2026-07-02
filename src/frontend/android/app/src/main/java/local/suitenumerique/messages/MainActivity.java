package local.suitenumerique.messages;

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
     * Publish the device safe-area insets as CSS variables.
     *
     * Capacitor 8 normally does this itself (SystemBars insetsHandling=css),
     * but its listener also re-applies the keyboard inset on top of the
     * system window resize, shrinking the webview by twice the keyboard
     * height on Android < 15 (capacitor#8181) — so it is disabled in
     * capacitor.config.ts. This listener replaces only the part we need:
     * it injects --safe-area-inset-* and returns the insets untouched, so
     * keyboard handling stays exactly as the system does it.
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
            String js = String.format(
                Locale.US,
                "document.documentElement.style.setProperty('--safe-area-inset-top','%.1fpx');" +
                "document.documentElement.style.setProperty('--safe-area-inset-right','%.1fpx');" +
                "document.documentElement.style.setProperty('--safe-area-inset-bottom','%.1fpx');" +
                "document.documentElement.style.setProperty('--safe-area-inset-left','%.1fpx');",
                bars.top / density,
                bars.right / density,
                bars.bottom / density,
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
