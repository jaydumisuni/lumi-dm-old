package com.lumi.dm.engine;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.io.FileWriter;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class DownloadEngine {

    public interface Callback {
        void onUpdate(DownloadJob job);
    }

    public interface Listener {
        void onJobsChanged();
    }

    private static DownloadEngine instance;

    private final Map<String, DownloadJob>         jobs      = Collections.synchronizedMap(new LinkedHashMap<>());
    private final Map<String, HttpDownloadTask>     tasks     = Collections.synchronizedMap(new LinkedHashMap<>());
    private       ExecutorService                   executor  = Executors.newFixedThreadPool(4);
    private       int                               maxConc          = 3;
    private       int                               defaultConns     = 16;
    private final List<Listener>                    listeners = new ArrayList<>();
    private final Handler                           mainHandler = new Handler(Looper.getMainLooper());
    private       Context                           appContext;
    private       String                            defaultDir;

    private boolean initialized = false;

    private DownloadEngine() {}

    public static synchronized DownloadEngine get() {
        if (instance == null) instance = new DownloadEngine();
        return instance;
    }

    public synchronized void init(Context ctx) {
        if (initialized) return;
        initialized = true;
        appContext = ctx.getApplicationContext();
        defaultDir = Environment.getExternalStoragePublicDirectory(
                Environment.DIRECTORY_DOWNLOADS).getAbsolutePath();

        SharedPreferences prefs = ctx.getSharedPreferences("LUMIDM_prefs", Context.MODE_PRIVATE);
        defaultDir   = prefs.getString("download_dir", defaultDir);
        maxConc      = prefs.getInt("max_concurrent", 3);
        defaultConns = prefs.getInt("default_connections", 16);
        recreatePool();
        loadState();
    }

    private void recreatePool() {
        if (!executor.isShutdown()) executor.shutdown();
        executor = Executors.newFixedThreadPool(Math.max(1, maxConc));
    }

    // ── Listeners ─────────────────────────────────────────────────────────────

    public void addListener(Listener l)    { listeners.add(l); }
    public void removeListener(Listener l) { listeners.remove(l); }

    private void notifyListeners() {
        mainHandler.post(() -> {
            for (Listener l : new ArrayList<>(listeners)) l.onJobsChanged();
        });
    }

    // ── Job accessors ─────────────────────────────────────────────────────────

    public List<DownloadJob> getJobs() {
        List<DownloadJob> list = new ArrayList<>(jobs.values());
        Collections.reverse(list);
        return list;
    }

    public DownloadJob getJob(String id) { return jobs.get(id); }

    // ── Add downloads ──────────────────────────────────────────────────────────

    public DownloadJob addHttp(String url, String filename, String dir) {
        if (dir == null || dir.isEmpty()) dir = defaultDir;
        if (filename == null || filename.isEmpty()) filename = guessFilename(url);
        DownloadJob job = new DownloadJob(url, filename, dir, DownloadJob.Type.HTTP);
        job.connections = defaultConns;
        enqueue(job);
        return job;
    }

    public DownloadJob addVideo(String url, String filename, String dir) {
        if (dir == null || dir.isEmpty()) dir = defaultDir;
        if (filename == null || filename.isEmpty()) filename = "video_" + System.currentTimeMillis() + ".mp4";
        DownloadJob job = new DownloadJob(url, filename, dir, DownloadJob.Type.VIDEO);
        enqueue(job);
        return job;
    }

    private void enqueue(DownloadJob job) {
        jobs.put(job.id, job);
        saveState();
        submitTask(job);
        notifyListeners();
    }

    private void submitTask(DownloadJob job) {
        if (job.type == DownloadJob.Type.HTTP || job.type == DownloadJob.Type.VIDEO) {
            HttpDownloadTask task = new HttpDownloadTask(job, updatedJob -> {
                saveState();
                notifyListeners();
            });
            tasks.put(job.id, task);
            executor.submit(task);
        }
    }

    // ── Controls ───────────────────────────────────────────────────────────────

    public void pause(String id) {
        HttpDownloadTask t = tasks.get(id);
        if (t != null) t.pause();
    }

    public void resume(String id) {
        DownloadJob job = jobs.get(id);
        if (job == null) return;
        HttpDownloadTask t = tasks.get(id);
        if (t != null) {
            t.resume();
        } else if (job.status == DownloadJob.Status.PAUSED || job.status == DownloadJob.Status.FAILED) {
            job.status = DownloadJob.Status.QUEUED;
            submitTask(job);
            notifyListeners();
        }
    }

    public void cancel(String id) {
        HttpDownloadTask t = tasks.get(id);
        if (t != null) t.cancel();
        DownloadJob job = jobs.get(id);
        if (job != null && !job.isTerminal()) {
            job.status = DownloadJob.Status.CANCELLED;
            saveState();
            notifyListeners();
        }
    }

    public void delete(String id) {
        cancel(id);
        jobs.remove(id);
        tasks.remove(id);
        saveState();
        notifyListeners();
    }

    public void pauseAll() {
        for (DownloadJob j : jobs.values()) if (j.status == DownloadJob.Status.RUNNING) pause(j.id);
    }

    public void resumeAll() {
        for (DownloadJob j : jobs.values()) if (j.status == DownloadJob.Status.PAUSED) resume(j.id);
    }

    public void clearCompleted() {
        List<String> toRemove = new ArrayList<>();
        for (DownloadJob j : jobs.values()) if (j.isTerminal()) toRemove.add(j.id);
        for (String id : toRemove) { jobs.remove(id); tasks.remove(id); }
        saveState();
        notifyListeners();
    }

    // ── Settings ───────────────────────────────────────────────────────────────

    public String getDefaultDir() { return defaultDir; }

    public void setDefaultDir(String dir) {
        defaultDir = dir;
        savePrefs();
    }

    public void setMaxConcurrent(int n) {
        maxConc = Math.max(1, Math.min(16, n));
        recreatePool();
        savePrefs();
    }

    public int getMaxConcurrent() { return maxConc; }

    public void setDefaultConnections(int n) {
        defaultConns = Math.max(1, Math.min(64, n));
        savePrefs();
    }

    public int getDefaultConnections() { return defaultConns; }

    private void savePrefs() {
        if (appContext == null) return;
        appContext.getSharedPreferences("LUMIDM_prefs", Context.MODE_PRIVATE)
            .edit()
            .putString("download_dir", defaultDir)
            .putInt("max_concurrent", maxConc)
            .putInt("default_connections", defaultConns)
            .apply();
    }

    // ── Persistence ────────────────────────────────────────────────────────────

    private File stateFile() {
        return new File(appContext.getFilesDir(), "downloads.json");
    }

    private void saveState() {
        if (appContext == null) return;
        try {
            JSONArray arr = new JSONArray();
            for (DownloadJob j : jobs.values()) {
                JSONObject o = new JSONObject();
                o.put("id",               j.id);
                o.put("url",              j.url);
                o.put("filename",         j.filename);
                o.put("targetDir",        j.targetDir);
                o.put("type",             j.type.name());
                o.put("status",           j.isTerminal() ? j.status.name() : DownloadJob.Status.PAUSED.name());
                o.put("totalBytes",       j.totalBytes);
                o.put("downloadedBytes",  j.downloadedBytes);
                o.put("connections",      j.connections);
                o.put("error",            j.error != null ? j.error : "");
                o.put("createdAt",        j.createdAt);
                arr.put(o);
            }
            try (FileWriter fw = new FileWriter(stateFile())) { fw.write(arr.toString()); }
        } catch (Exception ignored) {}
    }

    private void loadState() {
        File f = stateFile();
        if (!f.exists()) return;
        try {
            StringBuilder sb = new StringBuilder();
            try (BufferedReader br = new BufferedReader(new FileReader(f))) {
                String line;
                while ((line = br.readLine()) != null) sb.append(line);
            }
            JSONArray arr = new JSONArray(sb.toString());
            for (int i = 0; i < arr.length(); i++) {
                JSONObject o = arr.getJSONObject(i);
                DownloadJob j = new DownloadJob(
                    o.getString("url"),
                    o.getString("filename"),
                    o.getString("targetDir"),
                    DownloadJob.Type.valueOf(o.optString("type", "HTTP"))
                );
                // Reflect the saved id
                try {
                    java.lang.reflect.Field fid = DownloadJob.class.getDeclaredField("id");
                    fid.setAccessible(true);
                    fid.set(j, o.getString("id"));
                } catch (Exception ignored) {}

                j.status          = DownloadJob.Status.valueOf(o.optString("status", "PAUSED"));
                j.totalBytes      = o.optLong("totalBytes", 0);
                j.downloadedBytes = o.optLong("downloadedBytes", 0);
                j.connections     = o.optInt("connections", defaultConns);
                j.error           = o.optString("error", null);
                j.createdAt       = o.optLong("createdAt", System.currentTimeMillis());
                jobs.put(j.id, j);
            }
        } catch (Exception ignored) {}
    }

    // ── Helpers ────────────────────────────────────────────────────────────────

    private String guessFilename(String url) {
        try {
            String path = new java.net.URL(url).getPath();
            String name = path.substring(path.lastIndexOf('/') + 1);
            if (!name.isEmpty()) return name.split("\\?")[0];
        } catch (Exception ignored) {}
        return "download_" + System.currentTimeMillis();
    }

    public long totalSpeed() {
        long s = 0;
        for (DownloadJob j : jobs.values()) if (j.status == DownloadJob.Status.RUNNING) s += j.speedBps;
        return s;
    }
}
