package com.lumi.dm;

import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

import com.lumi.dm.engine.DownloadEngine;
import com.lumi.dm.engine.DownloadJob;
import com.lumi.dm.NetSpeedProbe;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Pattern;

public class DownloadsFragment extends Fragment implements DownloadEngine.Listener {

    private static final Pattern DL_EXT = Pattern.compile(
        "(?i)\\.(zip|rar|7z|gz|tar|bz2|exe|msi|apk|mp4|mkv|avi|mov|mp3|flac|wav|pdf|epub|iso|torrent)(\\?.*)?$"
    );

    private DownloadAdapter adapter;
    private View            emptyView;
    private TextView        tvTotalSpeed;

    private final Handler  clipHandler  = new Handler(Looper.getMainLooper());
    private       String   lastClipText = "";
    private final Runnable clipChecker  = new Runnable() {
        @Override public void run() {
            checkClipboard();
            clipHandler.postDelayed(this, 2000);
        }
    };

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater,
                             @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        View v = inflater.inflate(R.layout.fragment_downloads, container, false);

        emptyView    = v.findViewById(R.id.tv_empty);
        tvTotalSpeed = v.findViewById(R.id.tv_total_speed);

        adapter = new DownloadAdapter();

        RecyclerView rv = v.findViewById(R.id.rv_downloads);
        rv.setLayoutManager(new LinearLayoutManager(getContext()));
        rv.setAdapter(adapter);

        v.findViewById(R.id.btn_pause_all).setOnClickListener(x ->
                DownloadEngine.get().pauseAll());
        v.findViewById(R.id.btn_resume_all).setOnClickListener(x ->
                DownloadEngine.get().resumeAll());
        v.findViewById(R.id.btn_clear_done).setOnClickListener(x -> {
            DownloadEngine.get().clearCompleted();
            refresh();
        });

        return v;
    }

    @Override
    public void onResume() {
        super.onResume();
        DownloadEngine.get().addListener(this);
        refresh();
        clipHandler.postDelayed(clipChecker, 2000);
    }

    @Override
    public void onPause() {
        super.onPause();
        DownloadEngine.get().removeListener(this);
        clipHandler.removeCallbacks(clipChecker);
    }

    @Override
    public void onJobsChanged() {
        refresh();
    }

    private void refresh() {
        if (adapter == null) return;
        List<DownloadJob> jobs = DownloadEngine.get().getJobs();
        adapter.submitList(new ArrayList<>(jobs));
        emptyView.setVisibility(jobs.isEmpty() ? View.VISIBLE : View.GONE);

        long speed = DownloadEngine.get().totalSpeed();
        tvTotalSpeed.setVisibility(View.VISIBLE);
        if (speed > 0) {
            tvTotalSpeed.setText("⬇ " + DownloadMonitorService.fmtSpeed(speed));
        } else {
            long capBps = NetSpeedProbe.getLastBps();
            tvTotalSpeed.setText(capBps > 100_000
                    ? "↓ " + DownloadMonitorService.fmtMbps(capBps)
                    : "↓ —");
        }
    }

    private void checkClipboard() {
        Context ctx = getContext();
        if (ctx == null) return;
        try {
            ClipboardManager cm = (ClipboardManager) ctx.getSystemService(Context.CLIPBOARD_SERVICE);
            if (cm == null || !cm.hasPrimaryClip()) return;
            ClipData cd = cm.getPrimaryClip();
            if (cd == null || cd.getItemCount() == 0) return;
            String text = cd.getItemAt(0).coerceToText(ctx).toString().trim();
            if (text.equals(lastClipText) || text.length() < 10) return;
            lastClipText = text;
            boolean isHttp = text.startsWith("http://") || text.startsWith("https://");
            boolean isMagnet = text.startsWith("magnet:");
            if ((isHttp && DL_EXT.matcher(text).find()) || isMagnet) {
                offerClipboardDownload(text);
            }
        } catch (Exception ignored) {}
    }

    private void offerClipboardDownload(String url) {
        if (getActivity() == null) return;
        String filename = url.startsWith("magnet:") ? "Magnet link"
            : url.split("\\?")[0].substring(url.split("\\?")[0].lastIndexOf('/') + 1);
        new android.app.AlertDialog.Builder(requireContext())
            .setTitle("Download from clipboard?")
            .setMessage(filename.isEmpty() ? url : filename)
            .setPositiveButton("Download", (d, w) -> {
                DownloadEngine.get().addHttp(url, null, null);
                if (getActivity() instanceof MainActivity)
                    ((MainActivity) getActivity()).switchToDownloads();
            })
            .setNegativeButton("Ignore", null)
            .show();
    }
}
