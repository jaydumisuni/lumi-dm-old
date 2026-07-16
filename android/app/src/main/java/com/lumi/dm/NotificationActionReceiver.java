package com.lumi.dm;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

import com.lumi.dm.engine.DownloadEngine;

public class NotificationActionReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent.getAction();
        if (DownloadMonitorService.ACTION_PAUSE.equals(action)) {
            DownloadEngine.get().pauseAll();
        } else if (DownloadMonitorService.ACTION_RESUME.equals(action)) {
            DownloadEngine.get().resumeAll();
        }
    }
}
