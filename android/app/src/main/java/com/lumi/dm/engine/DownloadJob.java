package com.lumi.dm.engine;

import java.util.UUID;

public class DownloadJob {

    public enum Status { QUEUED, RUNNING, PAUSED, COMPLETED, FAILED, CANCELLED }
    public enum Type   { HTTP, VIDEO, TORRENT }

    public final String id;
    public String url;
    public String filename;
    public String targetDir;
    public Type   type;
    public String formatId;     // video format selector
    public int    connections;  // parallel connections per download (0 = engine default)
    public Status status;
    public long   totalBytes;
    public long   downloadedBytes;
    public long   speedBps;
    public String error;
    public long   createdAt;
    public long   updatedAt;

    public DownloadJob(String url, String filename, String targetDir, Type type) {
        this.id            = UUID.randomUUID().toString();
        this.url           = url;
        this.filename      = filename;
        this.targetDir     = targetDir;
        this.type          = type;
        this.status        = Status.QUEUED;
        this.totalBytes    = 0;
        this.downloadedBytes = 0;
        this.speedBps      = 0;
        this.createdAt     = System.currentTimeMillis();
        this.updatedAt     = this.createdAt;
    }

    public int progressPercent() {
        if (totalBytes <= 0) return 0;
        return (int) Math.min(100, downloadedBytes * 100L / totalBytes);
    }

    public boolean isActive() {
        return status == Status.RUNNING || status == Status.QUEUED;
    }

    public boolean isTerminal() {
        return status == Status.COMPLETED || status == Status.FAILED || status == Status.CANCELLED;
    }
}
