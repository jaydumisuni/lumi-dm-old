package com.lumi.dm.engine;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.RandomAccessFile;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;

import okhttp3.ConnectionPool;
import okhttp3.Dispatcher;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.ResponseBody;

public class HttpDownloadTask implements Runnable {

    private static final int  BUFFER_SIZE     = 1_048_576;  // 1 MB
    private static final int  CONNECT_TIMEOUT = 15_000;
    private static final int  READ_TIMEOUT    = 30_000;
    private static final int  MAX_RETRIES     = 3;
    private static final long MIN_PARALLEL    = 2 * 1024 * 1024;

    private static final int MAX_SEGS =
        Math.min(16, Math.max(4, Runtime.getRuntime().availableProcessors() * 4));

    // Shared HTTP/2 client — all tasks reuse TCP connections
    private static final OkHttpClient CLIENT;
    static {
        Dispatcher dispatcher = new Dispatcher();
        dispatcher.setMaxRequests(MAX_SEGS * 4);
        dispatcher.setMaxRequestsPerHost(MAX_SEGS);
        CLIENT = new OkHttpClient.Builder()
                .connectTimeout(CONNECT_TIMEOUT, TimeUnit.MILLISECONDS)
                .readTimeout(READ_TIMEOUT, TimeUnit.MILLISECONDS)
                .connectionPool(new ConnectionPool(MAX_SEGS + 4, 5, TimeUnit.MINUTES))
                .dispatcher(dispatcher)
                .followRedirects(true)
                .build();
    }

    private final DownloadJob             job;
    private final DownloadEngine.Callback callback;

    private volatile boolean pauseRequested  = false;
    private volatile boolean cancelRequested = false;

    public HttpDownloadTask(DownloadJob job, DownloadEngine.Callback callback) {
        this.job      = job;
        this.callback = callback;
    }

    public void pause()  { pauseRequested  = true; }
    public void resume() { pauseRequested  = false; }
    public void cancel() { cancelRequested = true; pauseRequested = false; }

    // Thrown when the server returns 401/403/410 — triggers a URL refresh attempt
    private static class LinkExpiredException extends IOException {
        final int code;
        LinkExpiredException(int code) {
            super("HTTP " + code + " — link expired or access denied");
            this.code = code;
        }
    }

    @Override
    public void run() {
        File targetDir = new File(job.targetDir);
        if (!targetDir.exists()) targetDir.mkdirs();

        File partFile  = new File(targetDir, job.filename + ".LUMIDM.part");
        File finalFile = new File(targetDir, job.filename);

        job.status = DownloadJob.Status.RUNNING;
        callback.onUpdate(job);

        try {
            long[] probe    = probe(job.url);
            long   total    = probe[0];
            boolean canRange = probe[1] == 1;

            job.totalBytes = total > 0 ? total : 0;

            if (canRange && total >= MIN_PARALLEL) {
                downloadParallel(partFile, finalFile, total);
            } else {
                downloadSingle(partFile, finalFile);
            }
        } catch (Exception e) {
            if (!cancelRequested) fail(e.getMessage() != null ? e.getMessage() : "Download error");
        }
    }

    // ── Parallel ──────────────────────────────────────────────────────────────

    private void downloadParallel(File partFile, File finalFile, long total) throws Exception {
        int  limit   = job.connections > 0 ? Math.min(job.connections, MAX_SEGS) : MAX_SEGS;
        int  segs    = Math.min(limit, (int) Math.max(1, total / (512 * 1024)));
        long segSize = total / segs;

        try (RandomAccessFile raf = new RandomAccessFile(partFile, "rw")) {
            if (partFile.length() != total) raf.setLength(total);
        }

        // Shared URL reference — any segment can refresh it for all others
        AtomicReference<String> currentUrl = new AtomicReference<>(job.url);
        AtomicLong downloaded              = new AtomicLong(0);
        AtomicLong speedBytes              = new AtomicLong(0);
        long       speedWinStart           = System.currentTimeMillis();

        ExecutorService pool    = Executors.newFixedThreadPool(segs);
        List<Future<?>> futures = new ArrayList<>();

        for (int i = 0; i < segs; i++) {
            long from = i * segSize;
            long to   = (i == segs - 1) ? total - 1 : from + segSize - 1;
            final int segIdx = i;

            futures.add(pool.submit(() -> {
                for (int attempt = 0; attempt < MAX_RETRIES; attempt++) {
                    try {
                        downloadSegment(currentUrl.get(), from, to, partFile, downloaded, speedBytes);
                        return;
                    } catch (LinkExpiredException le) {
                        if (cancelRequested) return;
                        if (attempt < MAX_RETRIES - 1) {
                            // Re-probe original URL to get a fresh redirect/CDN URL
                            String fresh = refreshUrl(job.url);
                            if (fresh != null) currentUrl.set(fresh);
                            try { Thread.sleep(1000L * (attempt + 1)); }
                            catch (InterruptedException ie) { Thread.currentThread().interrupt(); return; }
                        } else {
                            throw new RuntimeException("Segment " + segIdx + " link expired: " + le.getMessage(), le);
                        }
                    } catch (Exception e) {
                        if (cancelRequested) return;
                        if (attempt == MAX_RETRIES - 1)
                            throw new RuntimeException("Segment " + segIdx + " failed: " + e.getMessage(), e);
                        try { Thread.sleep(1000L * (attempt + 1)); }
                        catch (InterruptedException ie) { Thread.currentThread().interrupt(); return; }
                    }
                }
            }));
        }

        while (true) {
            boolean allDone = futures.stream().allMatch(f -> f.isDone());
            if (cancelRequested) {
                pool.shutdownNow();
                job.status = DownloadJob.Status.CANCELLED;
                callback.onUpdate(job);
                return;
            }
            while (pauseRequested && !cancelRequested) {
                if (job.status != DownloadJob.Status.PAUSED) {
                    job.status   = DownloadJob.Status.PAUSED;
                    job.speedBps = 0;
                    callback.onUpdate(job);
                }
                try { Thread.sleep(300); } catch (InterruptedException e) { Thread.currentThread().interrupt(); return; }
            }
            if (job.status == DownloadJob.Status.PAUSED) job.status = DownloadJob.Status.RUNNING;

            long now = System.currentTimeMillis();
            long dt  = now - speedWinStart;
            if (dt >= 500) {
                job.downloadedBytes = downloaded.get();
                job.speedBps        = (long) (speedBytes.getAndSet(0) * 1000.0 / dt);
                speedWinStart       = now;
                callback.onUpdate(job);
            }

            if (allDone) break;
            try { Thread.sleep(100); } catch (InterruptedException e) { break; }
        }
        pool.shutdown();

        for (Future<?> f : futures) {
            try { f.get(); }
            catch (Exception e) {
                if (!cancelRequested) fail(e.getCause() != null ? e.getCause().getMessage() : e.getMessage());
                return;
            }
        }

        if (cancelRequested) { job.status = DownloadJob.Status.CANCELLED; callback.onUpdate(job); return; }
        if (!renameOrCopy(partFile, finalFile)) { fail("Could not save file"); return; }

        job.downloadedBytes = total;
        job.totalBytes      = total;
        job.speedBps        = 0;
        job.status          = DownloadJob.Status.COMPLETED;
        callback.onUpdate(job);
    }

    private void downloadSegment(String urlStr, long from, long to,
                                 File partFile, AtomicLong downloaded, AtomicLong speedBytes)
            throws IOException {
        Request req = new Request.Builder()
                .url(urlStr)
                .addHeader("User-Agent", "Lumi-DM/1.0")
                .addHeader("Range", "bytes=" + from + "-" + to)
                .build();

        try (Response resp = CLIENT.newCall(req).execute()) {
            int code = resp.code();
            if (code == 401 || code == 403 || code == 410) throw new LinkExpiredException(code);
            if (code != 200 && code != 206) throw new IOException("HTTP " + code + " for segment");
            ResponseBody body = resp.body();
            if (body == null) throw new IOException("Empty response body");

            try (InputStream in = body.byteStream();
                 RandomAccessFile raf = new RandomAccessFile(partFile, "rw")) {
                raf.seek(from);
                byte[] buf = new byte[BUFFER_SIZE];
                int    read;
                while ((read = in.read(buf)) != -1) {
                    if (cancelRequested) return;
                    while (pauseRequested && !cancelRequested) {
                        try { Thread.sleep(200); }
                        catch (InterruptedException e) { Thread.currentThread().interrupt(); return; }
                    }
                    raf.write(buf, 0, read);
                    downloaded.addAndGet(read);
                    speedBytes.addAndGet(read);
                }
            }
        }
    }

    // ── Single connection ─────────────────────────────────────────────────────

    private void downloadSingle(File partFile, File finalFile) throws Exception {
        long   resumeFrom  = partFile.exists() ? partFile.length() : 0;
        String currentUrl  = job.url;  // may be refreshed on 401/403/410
        job.downloadedBytes = resumeFrom;

        for (int attempt = 0; attempt < MAX_RETRIES; attempt++) {
            try {
                Request.Builder reqBuilder = new Request.Builder()
                        .url(currentUrl)
                        .addHeader("User-Agent", "Lumi-DM/1.0");
                if (resumeFrom > 0) reqBuilder.addHeader("Range", "bytes=" + resumeFrom + "-");

                try (Response resp = CLIENT.newCall(reqBuilder.build()).execute()) {
                    int code = resp.code();

                    if (code == 401 || code == 403 || code == 410) {
                        String fresh = refreshUrl(job.url);
                        if (fresh != null) currentUrl = fresh;
                        if (attempt == MAX_RETRIES - 1) { fail("HTTP " + code + " — link expired or access denied"); return; }
                        Thread.sleep(1000L * (attempt + 1));
                        continue;
                    }
                    if (code == 416) {
                        resumeFrom = 0;
                        job.downloadedBytes = 0;
                        partFile.delete();
                        continue;
                    }
                    if (code != 200 && code != 206) { fail("HTTP " + code); return; }
                    ResponseBody body = resp.body();
                    if (body == null) { fail("Empty response body"); return; }

                    String clHeader = resp.header("Content-Length");
                    if (clHeader != null) {
                        long cl = Long.parseLong(clHeader);
                        if (cl > 0) job.totalBytes = resumeFrom + cl;
                    }

                    long speedWinStart = System.currentTimeMillis();
                    long speedWinBytes = 0;

                    try (InputStream in  = body.byteStream();
                         FileOutputStream out = new FileOutputStream(partFile, resumeFrom > 0)) {
                        byte[] buf = new byte[BUFFER_SIZE];
                        int    read;
                        while ((read = in.read(buf)) != -1) {
                            if (cancelRequested) {
                                job.status = DownloadJob.Status.CANCELLED;
                                callback.onUpdate(job);
                                return;
                            }
                            while (pauseRequested && !cancelRequested) {
                                if (job.status != DownloadJob.Status.PAUSED) {
                                    job.status   = DownloadJob.Status.PAUSED;
                                    job.speedBps = 0;
                                    callback.onUpdate(job);
                                }
                                try { Thread.sleep(300); }
                                catch (InterruptedException e) { Thread.currentThread().interrupt(); return; }
                            }
                            if (job.status == DownloadJob.Status.PAUSED) job.status = DownloadJob.Status.RUNNING;
                            out.write(buf, 0, read);
                            job.downloadedBytes += read;
                            speedWinBytes       += read;
                            long now = System.currentTimeMillis();
                            long dt  = now - speedWinStart;
                            if (dt >= 500) {
                                job.speedBps  = (long) (speedWinBytes * 1000.0 / dt);
                                speedWinBytes = 0;
                                speedWinStart = now;
                                callback.onUpdate(job);
                            }
                        }
                    }
                }
                break; // success
            } catch (IOException e) {
                if (cancelRequested) { job.status = DownloadJob.Status.CANCELLED; callback.onUpdate(job); return; }
                if (attempt == MAX_RETRIES - 1) { fail(e.getMessage()); return; }
                resumeFrom = partFile.exists() ? partFile.length() : 0;
                job.downloadedBytes = resumeFrom;
                try { Thread.sleep(1000L * (attempt + 1)); }
                catch (InterruptedException ie) { Thread.currentThread().interrupt(); return; }
            }
        }

        if (cancelRequested) { job.status = DownloadJob.Status.CANCELLED; callback.onUpdate(job); return; }
        if (!renameOrCopy(partFile, finalFile)) { fail("Could not save file"); return; }

        job.downloadedBytes = job.totalBytes > 0 ? job.totalBytes : finalFile.length();
        job.totalBytes      = job.downloadedBytes;
        job.speedBps        = 0;
        job.status          = DownloadJob.Status.COMPLETED;
        callback.onUpdate(job);
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private long[] probe(String urlStr) {
        Request req = new Request.Builder()
                .url(urlStr)
                .head()
                .addHeader("User-Agent", "Lumi-DM/1.0")
                .build();
        try (Response resp = CLIENT.newCall(req).execute()) {
            String cl     = resp.header("Content-Length");
            long   length = cl != null ? Long.parseLong(cl) : -1;
            String ranges = resp.header("Accept-Ranges");
            boolean canRange = ranges != null && !ranges.equalsIgnoreCase("none");
            return new long[]{ length, canRange ? 1 : 0 };
        } catch (Exception e) {
            return new long[]{ -1, 0 };
        }
    }

    /**
     * Re-probes the original URL to follow its current redirect chain.
     * Returns the new CDN/final URL if it changed, or null if unchanged/failed.
     * Used when a segment gets 401/403/410 mid-download (expired CDN signed URL).
     */
    private String refreshUrl(String originalUrl) {
        Request req = new Request.Builder()
                .url(originalUrl)
                .head()
                .addHeader("User-Agent", "Lumi-DM/1.0")
                .build();
        try (Response resp = CLIENT.newCall(req).execute()) {
            // After following redirects, request().url() is the final URL
            String freshUrl = resp.request().url().toString();
            return freshUrl.equals(originalUrl) ? null : freshUrl;
        } catch (Exception e) {
            return null;
        }
    }

    /**
     * Renames src to dst. Falls back to a stream copy + delete when
     * File.renameTo() fails (common on Android 10+ FUSE external storage).
     */
    private boolean renameOrCopy(File src, File dst) {
        if (dst.exists()) dst.delete();
        if (src.renameTo(dst)) return true;
        try (InputStream  in  = new FileInputStream(src);
             OutputStream out = new FileOutputStream(dst)) {
            byte[] buf = new byte[BUFFER_SIZE];
            int n;
            while ((n = in.read(buf)) != -1) out.write(buf, 0, n);
        } catch (IOException e) {
            dst.delete();
            return false;
        }
        src.delete();
        return true;
    }

    private void fail(String message) {
        job.status   = DownloadJob.Status.FAILED;
        job.error    = message;
        job.speedBps = 0;
        callback.onUpdate(job);
    }
}
