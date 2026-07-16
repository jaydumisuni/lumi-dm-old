package com.lumi.dm;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;

import androidx.core.app.NotificationCompat;

import com.lumi.dm.engine.DownloadEngine;
import com.lumi.dm.engine.DownloadJob;

import java.util.List;

public class DownloadMonitorService extends Service implements DownloadEngine.Listener {

    public static final String CHANNEL_ID    = "LUMIDM_downloads";
    public static final String ACTION_PAUSE  = "com.lumi.dm.PAUSE_ALL";
    public static final String ACTION_RESUME = "com.lumi.dm.RESUME_ALL";
    public static final int    NOTIF_ID      = 1001;

    private static final long PROBE_INTERVAL_MS = 5 * 60 * 1000L; // re-probe every 5 min

    private final Handler handler = new Handler(Looper.getMainLooper());

    // Periodic probe — runs every 5 min while service is alive
    private final Runnable probeRunnable = new Runnable() {
        @Override public void run() {
            NetSpeedProbe.probe(handler, bps -> updateNotification());
            handler.postDelayed(this, PROBE_INTERVAL_MS);
        }
    };

    // After all downloads finish: demote from ongoing after 8s, stay as idle notif
    private final Runnable allDoneRunnable = () -> updateNotification();

    @Override
    public void onCreate() {
        super.onCreate();
        createChannel();
        DownloadEngine.get().init(this);
        DownloadEngine.get().addListener(this);
        // Show immediately so startForeground is called before any async work
        startForeground(NOTIF_ID, buildIdleNotif());
        // First probe on start, then periodic
        NetSpeedProbe.probe(handler, bps -> updateNotification());
        handler.postDelayed(probeRunnable, PROBE_INTERVAL_MS);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        handler.post(this::updateNotification);
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        handler.removeCallbacks(probeRunnable);
        handler.removeCallbacks(allDoneRunnable);
        DownloadEngine.get().removeListener(this);
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) { return null; }

    @Override
    public void onJobsChanged() {
        handler.post(this::updateNotification);
    }

    private void updateNotification() {
        List<DownloadJob> jobs = DownloadEngine.get().getJobs();
        int  active = 0, paused = 0, done = 0;
        long speed = 0, totalBytes = 0, dlBytes = 0;

        for (DownloadJob j : jobs) {
            switch (j.status) {
                case RUNNING:
                    active++;
                    speed      += j.speedBps;
                    totalBytes += j.totalBytes;
                    dlBytes    += j.downloadedBytes;
                    break;
                case PAUSED:
                    paused++;
                    totalBytes += j.totalBytes;
                    dlBytes    += j.downloadedBytes;
                    break;
                case COMPLETED:
                    done++;
                    break;
                default:
                    break;
            }
        }

        int     pct      = (totalBytes > 0) ? (int) (dlBytes * 100L / totalBytes) : -1;
        boolean isPaused = (active == 0 && paused > 0);
        boolean allDone  = (!jobs.isEmpty() && active == 0 && paused == 0 && done > 0);
        boolean isIdle   = jobs.isEmpty() || (active == 0 && paused == 0 && !allDone);

        Notification n;
        if (isIdle) {
            handler.removeCallbacks(allDoneRunnable);
            n = buildIdleNotif();
        } else if (allDone) {
            handler.removeCallbacks(allDoneRunnable);
            String title = "✓ " + done + " download" + (done != 1 ? "s" : "") + " complete";
            String text  = fmtSpeed(0) + " · tap to open";
            n = buildNotif(title, text, 100, false, true, false);
            // After 8s, drop back to idle notification
            handler.postDelayed(allDoneRunnable, 8_000);
        } else if (active > 0) {
            String title = "⬇ " + fmtSpeed(speed) + (pct >= 0 ? "  ·  " + pct + "%" : "");
            String text  = active + " downloading"
                    + (paused > 0 ? "  ·  " + paused + " paused" : "")
                    + (done   > 0 ? "  ·  " + done   + " done"   : "");
            n = buildNotif(title, text, pct, false, false, true);
        } else {
            // Paused
            String title = "⏸ Paused  ·  " + paused + " file" + (paused != 1 ? "s" : "");
            String text  = (pct >= 0 ? pct + "%  ·  " : "") + (done > 0 ? done + " done" : "");
            n = buildNotif(title, text, pct, true, false, true);
        }

        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (nm != null) nm.notify(NOTIF_ID, n);
    }

    // Idle notification: shows internet speed (or app name if no probe result yet)
    private Notification buildIdleNotif() {
        long capBps  = NetSpeedProbe.getLastBps();
        String title = capBps > 100_000 ? "↓ " + fmtMbps(capBps) : "Lumi DM";
        String text  = "No active downloads";
        return buildNotif(title, text, -1, false, false, false);
    }

    private Notification buildNotif(String title, String text, int pct,
                                    boolean isPaused, boolean allDone, boolean ongoing) {
        Intent openI = new Intent(this, MainActivity.class);
        openI.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent openPi = PendingIntent.getActivity(this, 0, openI,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        NotificationCompat.Builder b = new NotificationCompat.Builder(this, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_notification)
                .setContentTitle(title)
                .setContentText(text)
                .setContentIntent(openPi)
                .setOngoing(ongoing)
                .setOnlyAlertOnce(true)
                .setShowWhen(false)
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .setVisibility(NotificationCompat.VISIBILITY_PUBLIC);

        if (pct >= 0) b.setProgress(100, pct, false);

        if (!isPaused && !allDone && pct > 0 && pct < 100) {
            Intent pi = new Intent(ACTION_PAUSE);
            pi.setPackage(getPackageName());
            PendingIntent ppi = PendingIntent.getBroadcast(this, 1, pi,
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            b.addAction(android.R.drawable.ic_media_pause, "Pause all", ppi);
        }

        if (isPaused) {
            Intent ri = new Intent(ACTION_RESUME);
            ri.setPackage(getPackageName());
            PendingIntent rpi = PendingIntent.getBroadcast(this, 2, ri,
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            b.addAction(android.R.drawable.ic_media_play, "Resume all", rpi);
        }

        return b.build();
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel ch = new NotificationChannel(
                    CHANNEL_ID, "Download Progress", NotificationManager.IMPORTANCE_LOW);
            ch.setDescription("Live download speed and progress");
            ch.setShowBadge(false);
            ch.setSound(null, null);
            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null) nm.createNotificationChannel(ch);
        }
    }

    static String fmtSpeed(long bps) {
        if (bps >= 1_048_576) return String.format("%.1f MB/s", bps / 1_048_576.0);
        if (bps >= 1_024)     return String.format("%d KB/s",   bps / 1_024);
        return bps + " B/s";
    }

    static String fmtMbps(long bps) {
        double mbps = (bps * 8.0) / 1_000_000;
        if (mbps >= 1) return String.format("%.0f Mbps", mbps);
        return String.format("%.0f Kbps", (bps * 8.0) / 1_000);
    }
}
