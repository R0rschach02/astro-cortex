package de.astrocortex.app;

import android.os.Bundle;
import android.webkit.CookieManager;
import android.webkit.WebView;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    private void allowThirdPartyCookies() {
        // Cloudflare-Access: CF_Authorization ist aus App-Sicht ein
        // Third-Party-Cookie (App-Origin https://localhost, API
        // api.teamigel.com). Capacitor 6 aktiviert Third-Party-Cookies
        // im Haupt-WebView NICHT von selbst - ohne dieses Flag wuerde
        // der WebView das Cookie nach dem Login nie mitsenden.
        try {
            WebView wv = (bridge != null) ? bridge.getWebView() : null;
            if (wv != null) {
                CookieManager cm = CookieManager.getInstance();
                cm.setAcceptThirdPartyCookies(wv, true);
                cm.setAcceptCookie(true);
            }
        } catch (Exception ignored) { }
    }

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        allowThirdPartyCookies();
    }

    @Override
    protected void onStart() {
        super.onStart();
        allowThirdPartyCookies();
    }
}
