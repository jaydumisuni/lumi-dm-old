package com.lumi.dm;

import android.os.Handler;

import java.io.InputStream;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;

public class NetSpeedProbe {

    private static final String[] PROBE_URLS = {
        "https://speed.cloudflare.com/__down?bytes=5000000",
        "https://bouygues.testdebit.info/5M/5M.iso",
        "https://speed.hetzner.de/5MB.bin",
    };

    private static final OkHttpClient CLIENT = new OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .build();

    private static volatile long       _lastBps = 0;
    private static final AtomicBoolean _running = new AtomicBoolean(false);

    public interface Callback { void onResult(long bps); }

    public static long getLastBps() { return _lastBps; }

    public static void probe(Handler handler, Callback cb) {
        if (!_running.compareAndSet(false, true)) return;
        new Thread(() -> {
            long result = 0;
            for (String url : PROBE_URLS) {
                try {
                    Request req = new Request.Builder().url(url)
                            .header("User-Agent", "Lumi-DM/1.0").build();
                    long t0 = System.nanoTime();
                    try (Response r = CLIENT.newCall(req).execute()) {
                        if (!r.isSuccessful() || r.body() == null) continue;
                        byte[] buf = new byte[131_072];
                        long read = 0;
                        InputStream is = r.body().byteStream();
                        int n;
                        while ((n = is.read(buf)) != -1) read += n;
                        long ns = System.nanoTime() - t0;
                        if (ns > 0 && read > 100_000)
                            result = (long) (read / (ns / 1e9));
                    }
                    if (result > 100_000) break;
                } catch (Exception ignored) {}
            }
            if (result > 100_000) _lastBps = result;
            _running.set(false);
            final long bps = _lastBps;
            if (cb != null && handler != null) handler.post(() -> cb.onResult(bps));
        }, "net-speed-probe").start();
    }
}
