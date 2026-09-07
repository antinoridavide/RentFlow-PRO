package it.rentflow.pro;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.ViewGroup;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private WebView webView;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Handler main = new Handler(Looper.getMainLooper());
    private ValueCallback<Uri[]> fileCallback;
    private static final int FILE_CHOOSER = 1001;
    private static final String HOME = "http://127.0.0.1:5000/";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        webView = new WebView(this);
        setContentView(webView, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(true);
        s.setBuiltInZoomControls(false);
        s.setDisplayZoomControls(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                String host = uri.getHost();
                if ("127.0.0.1".equals(host) || "localhost".equals(host)) return false;
                startActivity(new Intent(Intent.ACTION_VIEW, uri));
                return true;
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView webView, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                Intent intent = params.createIntent();
                try {
                    startActivityForResult(intent, FILE_CHOOSER);
                    return true;
                } catch (Exception e) {
                    fileCallback = null;
                    Toast.makeText(MainActivity.this, "Impossibile aprire il selettore file", Toast.LENGTH_LONG).show();
                    return false;
                }
            }
        });

        startPythonServer();
    }

    private void startPythonServer() {
        executor.execute(() -> {
            try {
                if (!Python.isStarted()) Python.start(new AndroidPlatform(this));
                Python py = Python.getInstance();
                PyObject module = py.getModule("rentflow.app");
                module.callAttr("start_android", getFilesDir().getAbsolutePath());
                waitForServer();
            } catch (Throwable t) {
                main.post(() -> Toast.makeText(this, "Errore avvio RentFlow: " + t.getMessage(), Toast.LENGTH_LONG).show());
            }
        });
    }

    private void waitForServer() {
        for (int i = 0; i < 80; i++) {
            try {
                HttpURLConnection c = (HttpURLConnection) new URL(HOME).openConnection();
                c.setConnectTimeout(250);
                c.setReadTimeout(250);
                c.connect();
                int code = c.getResponseCode();
                c.disconnect();
                if (code >= 200 && code < 500) {
                    main.post(() -> webView.loadUrl(HOME));
                    return;
                }
            } catch (Exception ignored) { }
            try { Thread.sleep(150); } catch (InterruptedException ignored) { }
        }
        main.post(() -> Toast.makeText(this, "Il server locale non ha risposto.", Toast.LENGTH_LONG).show());
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == FILE_CHOOSER && fileCallback != null) {
            Uri[] result = WebChromeClient.FileChooserParams.parseResult(resultCode, data);
            fileCallback.onReceiveValue(result);
            fileCallback = null;
        }
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        if (webView != null) webView.destroy();
        executor.shutdownNow();
        super.onDestroy();
    }
}
