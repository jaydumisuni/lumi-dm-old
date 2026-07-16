package com.lumi.dm;

import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.Settings;

import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.content.ContextCompat;
import androidx.fragment.app.Fragment;

import com.google.android.material.bottomnavigation.BottomNavigationView;
import com.lumi.dm.engine.DownloadEngine;

public class MainActivity extends AppCompatActivity {

    private BottomNavigationView bottomNav;

    private final ActivityResultLauncher<String> notifPermLauncher =
            registerForActivityResult(new ActivityResultContracts.RequestPermission(),
                    granted -> startDownloadService());

    private final ActivityResultLauncher<Intent> storagePermLauncher =
            registerForActivityResult(new ActivityResultContracts.StartActivityForResult(),
                    result -> { /* permission granted or denied — continue either way */ });

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // Init engine early so fragments see the loaded state on first render
        DownloadEngine.get().init(this);

        setContentView(R.layout.activity_main);

        bottomNav = findViewById(R.id.bottom_nav);
        bottomNav.setOnItemSelectedListener(item -> {
            Fragment f;
            int id = item.getItemId();
            if (id == R.id.nav_downloads) {
                f = new DownloadsFragment();
            } else if (id == R.id.nav_add) {
                f = new NewDownloadFragment();
            } else {
                f = new SettingsFragment();
            }
            getSupportFragmentManager().beginTransaction()
                    .replace(R.id.fragment_container, f)
                    .commit();
            return true;
        });

        if (savedInstanceState == null) {
            bottomNav.setSelectedItemId(R.id.nav_downloads);
        }

        // Handle "share URL" intents from other apps
        handleShareIntent(getIntent());

        requestStoragePermission();
        requestNotifPermissionAndStart();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        handleShareIntent(intent);
    }

    // Video platform hostnames recognised for auto-analysis
    private static final java.util.Set<String> VIDEO_HOSTS = new java.util.HashSet<>(
        java.util.Arrays.asList(
            "www.youtube.com","youtube.com","youtu.be","m.youtube.com",
            "www.vimeo.com","vimeo.com",
            "www.dailymotion.com","dailymotion.com",
            "www.tiktok.com","vm.tiktok.com",
            "twitter.com","x.com",
            "www.twitch.tv","clips.twitch.tv",
            "www.instagram.com",
            "fb.watch","www.facebook.com"
        )
    );

    private void handleShareIntent(Intent intent) {
        if (intent == null) return;
        String action = intent.getAction();
        String type   = intent.getType();

        String url = null;
        boolean forceVideo = false;

        if (Intent.ACTION_SEND.equals(action) && "text/plain".equals(type)) {
            // Shared text (URL) from browser share button
            url = intent.getStringExtra(Intent.EXTRA_TEXT);
        } else if (Intent.ACTION_VIEW.equals(action)) {
            android.net.Uri data = intent.getData();
            if (data != null) {
                url = data.toString();
                // Magnet link → direct torrent (not yet supported natively, treat as HTTP)
                // Video platform URL → force video analysis
                String host = data.getHost();
                if (host != null && VIDEO_HOSTS.contains(host.toLowerCase())) {
                    forceVideo = true;
                }
            }
        }

        if (url == null || url.isEmpty()) return;

        // Detect video platform URLs from share text too
        try {
            android.net.Uri parsed = android.net.Uri.parse(url);
            String host = parsed.getHost();
            if (host != null && VIDEO_HOSTS.contains(host.toLowerCase())) forceVideo = true;
        } catch (Exception ignored) {}

        Bundle args = new Bundle();
        args.putString("url", url);
        args.putBoolean("auto_video", forceVideo);

        NewDownloadFragment f = new NewDownloadFragment();
        f.setArguments(args);
        getSupportFragmentManager().beginTransaction()
                .replace(R.id.fragment_container, f)
                .commit();
        bottomNav.setSelectedItemId(R.id.nav_add);
    }

    public void switchToDownloads() {
        bottomNav.setSelectedItemId(R.id.nav_downloads);
    }

    private void requestStoragePermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            if (!Environment.isExternalStorageManager()) {
                Intent intent = new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                        Uri.parse("package:" + getPackageName()));
                storagePermLauncher.launch(intent);
            }
        }
    }

    private void requestNotifPermissionAndStart() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                    != PackageManager.PERMISSION_GRANTED) {
                notifPermLauncher.launch(Manifest.permission.POST_NOTIFICATIONS);
                return;
            }
        }
        startDownloadService();
    }

    private void startDownloadService() {
        Intent svc = new Intent(this, DownloadMonitorService.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(svc);
        } else {
            startService(svc);
        }
    }
}
