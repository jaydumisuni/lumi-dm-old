package com.lumi.dm;

import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ImageButton;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.recyclerview.widget.DiffUtil;
import androidx.recyclerview.widget.ListAdapter;
import androidx.recyclerview.widget.RecyclerView;

import com.lumi.dm.engine.DownloadEngine;
import com.lumi.dm.engine.DownloadJob;

public class DownloadAdapter extends ListAdapter<DownloadJob, DownloadAdapter.VH> {

    private static final int COLOR_BLUE   = 0xFF4f9ef8;
    private static final int COLOR_YELLOW = 0xFFfbbf24;
    private static final int COLOR_GREEN  = 0xFF5ed19d;
    private static final int COLOR_RED    = 0xFFf87171;
    private static final int COLOR_DIM    = 0x33FFFFFF;
    private static final int COLOR_MUTED  = 0xFF8a8f9a;

    DownloadAdapter() {
        super(new DiffUtil.ItemCallback<DownloadJob>() {
            @Override
            public boolean areItemsTheSame(@NonNull DownloadJob a, @NonNull DownloadJob b) {
                return a.id.equals(b.id);
            }
            @Override
            public boolean areContentsTheSame(@NonNull DownloadJob a, @NonNull DownloadJob b) {
                return a.status == b.status
                        && a.downloadedBytes == b.downloadedBytes
                        && a.speedBps == b.speedBps;
            }
        });
    }

    @NonNull
    @Override
    public VH onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
        View v = LayoutInflater.from(parent.getContext())
                .inflate(R.layout.item_download, parent, false);
        return new VH(v);
    }

    @Override
    public void onBindViewHolder(@NonNull VH h, int pos) {
        h.bind(getItem(pos));
    }

    static class VH extends RecyclerView.ViewHolder {
        final ProgressRingView ring;
        final TextView         tvSpeed, tvName, tvSub;
        final ImageButton      btnDelete;

        VH(View v) {
            super(v);
            ring      = v.findViewById(R.id.ring);
            tvSpeed   = v.findViewById(R.id.tv_speed);
            tvName    = v.findViewById(R.id.tv_name);
            tvSub     = v.findViewById(R.id.tv_sub);
            btnDelete = v.findViewById(R.id.btn_delete);
        }

        void bind(DownloadJob j) {
            tvName.setText(j.filename != null ? j.filename : j.url);
            int pct = j.progressPercent();

            switch (j.status) {
                case RUNNING: {
                    ring.setProgress(pct);
                    ring.setRingColor(COLOR_BLUE);
                    ring.setIcon("⏸");
                    tvSpeed.setTextColor(COLOR_BLUE);
                    tvSpeed.setText("⬇ " + fmtSpeed(j.speedBps));
                    long eta = eta(j);
                    String sub = pct + "%";
                    if (eta > 0) sub += "  ·  " + fmtEta(eta);
                    if (j.totalBytes > 0) sub += "  ·  " + fmtSize(j.totalBytes);
                    tvSub.setText(sub);
                    ring.setOnClickListener(x -> DownloadEngine.get().pause(j.id));
                    break;
                }
                case PAUSED: {
                    ring.setProgress(pct);
                    ring.setRingColor(COLOR_YELLOW);
                    ring.setIcon("▶");
                    tvSpeed.setTextColor(COLOR_YELLOW);
                    tvSpeed.setText("⏸ Paused");
                    String sub = pct + "%";
                    if (j.totalBytes > 0)
                        sub += "  ·  " + fmtSize(j.downloadedBytes) + " / " + fmtSize(j.totalBytes);
                    tvSub.setText(sub);
                    ring.setOnClickListener(x -> DownloadEngine.get().resume(j.id));
                    break;
                }
                case COMPLETED: {
                    ring.setProgress(100);
                    ring.setRingColor(COLOR_GREEN);
                    ring.setIcon("✓");
                    tvSpeed.setTextColor(COLOR_GREEN);
                    tvSpeed.setText("✓ Complete");
                    tvSub.setText(j.totalBytes > 0 ? fmtSize(j.totalBytes) : "");
                    ring.setOnClickListener(null);
                    break;
                }
                case FAILED: {
                    ring.setProgress(pct);
                    ring.setRingColor(COLOR_RED);
                    ring.setIcon("✗");
                    tvSpeed.setTextColor(COLOR_RED);
                    tvSpeed.setText("✗ Failed");
                    tvSub.setText(j.error != null ? j.error : "Download failed");
                    ring.setOnClickListener(null);
                    break;
                }
                case CANCELLED: {
                    ring.setProgress(0);
                    ring.setRingColor(COLOR_DIM);
                    ring.setIcon("—");
                    tvSpeed.setTextColor(COLOR_MUTED);
                    tvSpeed.setText("Cancelled");
                    tvSub.setText("");
                    ring.setOnClickListener(null);
                    break;
                }
                default: { // QUEUED / PROBING / STAGED
                    ring.setProgress(0);
                    ring.setRingColor(COLOR_DIM);
                    ring.setIcon("…");
                    tvSpeed.setTextColor(COLOR_MUTED);
                    tvSpeed.setText("Queued");
                    tvSub.setText("");
                    ring.setOnClickListener(x -> DownloadEngine.get().cancel(j.id));
                    break;
                }
            }

            btnDelete.setVisibility(j.isTerminal() ? View.VISIBLE : View.GONE);
            btnDelete.setOnClickListener(x -> DownloadEngine.get().delete(j.id));
        }

        private static long eta(DownloadJob j) {
            long remaining = j.totalBytes - j.downloadedBytes;
            if (j.speedBps <= 0 || remaining <= 0) return 0;
            return remaining / j.speedBps;
        }

        static String fmtEta(long secs) {
            if (secs < 60)   return secs + "s left";
            if (secs < 3600) return (secs / 60) + "m " + (secs % 60) + "s left";
            return (secs / 3600) + "h " + ((secs % 3600) / 60) + "m left";
        }

        static String fmtSpeed(long bps) {
            if (bps >= 1_048_576) return String.format("%.1f MB/s", bps / 1_048_576.0);
            if (bps >= 1_024)     return String.format("%d KB/s",   bps / 1_024);
            return bps + " B/s";
        }

        static String fmtSize(long bytes) {
            if (bytes >= 1_073_741_824) return String.format("%.2f GB", bytes / 1_073_741_824.0);
            if (bytes >= 1_048_576)     return String.format("%.1f MB", bytes / 1_048_576.0);
            if (bytes >= 1_024)         return String.format("%d KB",   bytes / 1_024);
            return bytes + " B";
        }
    }
}
